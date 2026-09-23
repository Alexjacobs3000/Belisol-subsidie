"""
ISDE-subsidieregels (glasisolatie, isolerende panelen en isolerende deuren)
toegepast op een geparsed Uw-rapport.

Bron: Belisol subsidiebrochure FEB26. Alle bedragen zijn indicatief; RVO beslist.
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

from .parser import Report, Position
from . import meldcodelijst as mcl

CONFIG_PATH = Path(__file__).with_name("config.json")

LABELS = {
    "triple_glas": "Triple glas (HR+++)",
    "hr_plus_plus_glas": "HR++ glas",
    "paneel_hoog": "Isolerend paneel in kozijn (U ≤ 0,7)",
    "paneel_laag": "Isolerend paneel in kozijn (U ≤ 1,2)",
    "deur_hoog": "Isolerende deur (Ud ≤ 1,0)",
    "deur_laag": "Isolerende deur (Ud ≤ 1,5)",
}
ORDER = ["triple_glas", "hr_plus_plus_glas", "deur_hoog", "deur_laag", "paneel_hoog", "paneel_laag"]


def load_config(path: Optional[str | Path] = None) -> dict:
    return json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8"))


def r2(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def round_m2_for_amount(x: float) -> int:
    """Brochure: eindigt het totaal op ≥ 0,50 → naar boven, < 0,50 → naar beneden."""
    return int(Decimal(str(r2(x))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def nl(x: Optional[float], dec: int = 2) -> str:
    if x is None:
        return "–"
    s = f"{x:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def eur(x: float) -> str:
    return "€ " + nl(x, 2)


# ---------------------------------------------------------------------------

def is_door(p: Position, cfg: dict) -> tuple[bool, str]:
    dc = cfg["deur_herkenning"]
    if any(a in dc["vleugel_artikels"] for a in p.vleugel_artikels):
        return True, f"vleugelprofiel {', '.join(p.vleugel_artikels)} is een deurvleugel"
    haystack = " ".join([p.omschrijving] + [m for v in p.materialen.values() for m in v]).lower()
    for w in dc["trefwoorden"]:
        if re.search(rf"\b{re.escape(w)}\b", haystack):
            return True, f"trefwoord '{w}' in omschrijving/materialen"
    if (p.materialen.get("VLEUGEL") and p.hoogte_mm >= dc["heuristiek_min_hoogte_mm"]
            and p.breedte_mm <= dc["heuristiek_max_breedte_mm"]):
        return True, "HEURISTIEK: hoog, smal element met vleugel (controleer!)"
    return False, ""


def glass_class(p: Position, cfg: dict) -> tuple[Optional[str], Optional[float]]:
    g = cfg["grenswaarden"]
    ugs = [x.ug for x in p.glas if x.ug is not None] + ([p.ug] if p.ug is not None else [])
    if not ugs:
        return None, None
    ug = max(ugs)
    if ug <= g["triple_glas_ug_max"]:
        return "triple_glas", ug
    if ug <= g["hr_plus_plus_glas_ug_max"]:
        return "hr_plus_plus_glas", ug
    return None, ug


def flens_info(p: Position, cfg: dict, bevestigd: Optional[bool] = None, mm_override: Optional[float] = None) -> dict:
    """Bepaalt of het kader een flens (aanslag/T-kader) heeft en berekent de netto maat.

    In NL telt de flens niet mee voor de subsidie-m²: aan alle zijden aftrekken.
    """
    fc = cfg.get("flens", {})
    sc = fc.get("systemen", {}).get(p.systeem.upper(), {})
    std = fc.get("standaard", {})
    kader = p.materialen.get("KADER", [])
    artikels = [k.split()[0] for k in kader if k]
    heeft, bron = False, ""
    if any(a in sc.get("flens_kaderartikels", []) for a in artikels):
        heeft, bron = True, "kaderprofiel " + ", ".join(artikels)
    else:
        txt = " ".join(kader).lower()
        for w in std.get("flens_trefwoorden", []):
            if w in txt:
                heeft, bron = True, f"trefwoord '{w}' in kaderomschrijving"
                break
    rapport_zegt = heeft
    if bevestigd is True and not heeft:
        heeft, bron = True, "bevestigd op bestelling"
    elif bevestigd is False and heeft:
        heeft, bron = False, f"geen flens volgens bestelling (rapport: {bron})"
    mm = mm_override or sc.get("flens_mm_per_zijde", std.get("flens_mm_per_zijde"))
    al_netto = sc.get("rapportmaten_zonder_flens", std.get("rapportmaten_zonder_flens", False))

    b, h = p.breedte_mm, p.hoogte_mm
    opp = p.oppervlakte_m2
    toegepast = "geen flens"
    onzeker = False
    maat_bron = "tabel"
    tekening_onleesbaar = False
    if sc.get("maat_uit_tekening"):
        # Certix: de zwarte maat bij de schets is de maat zonder aanslag en gaat altijd voor
        tb, th = getattr(p, "tekening_breedte_mm", None), getattr(p, "tekening_hoogte_mm", None)
        tekening_onleesbaar = tb is None or th is None
        b, h = tb or b, th or h
        if (b, h) != (p.breedte_mm, p.hoogte_mm):
            opp = r2(b * h / 1_000_000)
        maat_bron = "tabel (tekening niet leesbaar)" if tb is None and th is None else (
            "tekening (zwart)" if not tekening_onleesbaar else "deels tekening, deels tabel")
        toegepast = "maat zonder aanslag uit tekening" if not tekening_onleesbaar else "tabelmaat (zwarte maat niet gelezen)"
    elif heeft:
        if al_netto:
            toegepast = "flens al afgetrokken in leveranciersrapport"
        elif mm:
            b, h = b - 2 * mm, h - 2 * mm
            opp = r2(b * h / 1_000_000)
            toegepast = f"flens {mm:g} mm per zijde afgetrokken"
        else:
            toegepast = "flens aanwezig, breedte onbekend — NIET afgetrokken"
            onzeker = True
    return {
        "aanwezig": heeft, "volgens_rapport": rapport_zegt, "bron": bron, "mm_per_zijde": mm if heeft else None,
        "toegepast": toegepast, "onzeker": onzeker, "maat_bron": maat_bron, "tekening_onleesbaar": tekening_onleesbaar,
        "netto_breedte_mm": b, "netto_hoogte_mm": h, "netto_m2_per_stuk": opp,
    }


def _override_meldcode(cat: str, systeem: str, u: Optional[float], cfg: dict) -> tuple[Optional[str], str]:
    """Handmatige meldcoderegels uit config.json (vangnet als de RVO-lijst niets oplevert)."""
    groep = "deur" if cat.startswith("deur") else ("paneel" if cat.startswith("paneel") else cat)
    best, best_score, bron = None, -1, ""
    for r in cfg.get("meldcodes", {}).get("regels", []):
        if r.get("maatregel") not in (cat, groep):
            continue
        sysr = (r.get("systeem") or "*").upper()
        if sysr != "*" and sysr != (systeem or "").upper():
            continue
        if u is None:
            continue
        if "u_waarde" in r:
            if abs(u - r["u_waarde"]) > 0.005:
                continue
            score = 3
        else:
            if "u_max" in r and u > r["u_max"] + 1e-9:
                continue
            if r.get("u_min") is not None and u < r["u_min"] - 1e-9:
                continue
            score = 1
        score += 2 if sysr != "*" else 0
        if score > best_score:
            best, best_score, bron = r.get("meldcode"), score, r.get("bron", "")
    return best, bron


def supplier(systeem: str, cfg: dict) -> dict:
    lc = cfg.get("leveranciers", {})
    hit = lc.get("systemen", {}).get((systeem or "").upper())
    if hit:
        return hit
    low = (systeem or "").lower()
    hits = [(k, v) for k, v in lc.get("trefwoorden", {}).items() if not k.startswith("_") and k in low]
    out: dict = {}
    for _, v in hits:  # combineer: 'profel' geeft het merk, 'p8000' de deurserie
        out.update({k2: v2 for k2, v2 in v.items() if v2 is not None})
    return out


def resolve_meldcode(cat: str, systeem: str, u: Optional[float], cfg: dict) -> dict:
    """Meldcode voor een maatregel: eerst de RVO-meldcodelijst, dan handmatige regels."""
    res = {"meldcode": None, "bron": None, "product": None, "kandidaten": [], "niet_in_lijst": False}
    sup = supplier(systeem, cfg)
    lijst = mcl.load().get("bron") or "RVO-meldcodelijst"
    if cat in ("triple_glas", "hr_plus_plus_glas") and sup.get("glas"):
        h = mcl.hoofdmeldcode(sup["glas"], cat)
        if h:
            res.update(meldcode=h["meldcode"], product=f"{h['merk']} {h['model']}",
                       bron=f"{lijst}: hoofdmeldcode {h['merk']} – {h['categorie_rvo']}")
    elif cat.startswith("deur") and sup.get("deur") and u is not None:
        chosen, fit = mcl.find_door(sup["deur"], cat, u, sup.get("deur_serie"))
        if chosen:
            res.update(meldcode=chosen["meldcode"], product=f"{chosen['merk']} {chosen['model']}",
                       bron=f"{lijst}: {chosen['merk']} {chosen['model']}")
        else:
            res["kandidaten"] = [f"{c['meldcode']} {c['merk']} {c['model']}" for c in fit]
    if not res["meldcode"]:
        code, bron = _override_meldcode(cat, systeem, u, cfg)
        if code:
            item = mcl.by_code(code)
            res.update(meldcode=code, bron=bron, niet_in_lijst=item is None,
                       product=f"{item['merk']} {item['model']}" if item else None)
    return res


def rates_for(cat: str, oud: bool, cfg: dict) -> dict:
    """Bedragen per m² voor enkele maatregel / meerdere maatregelen / monumentale woning."""
    if oud:
        e = cfg["tarieven"]["tot_2024"][cat]
        return {"enkel": e, "meerdere": None, "monument": None}
    lst = mcl.tarieven_per_categorie().get(cat)
    if lst:
        return dict(lst)
    if cat in cfg.get("tarieven_panelen_2026", {}):
        return dict(cfg["tarieven_panelen_2026"][cat])
    e = cfg["tarieven"]["vanaf_2025"][cat]
    return {"enkel": e, "meerdere": e * 2, "monument": None}


def evaluate(report: Report, klant: Optional[dict] = None, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    klant = dict(klant or {})
    g = cfg["grenswaarden"]
    warnings: list[str] = []

    # Geen montagedatum: altijd de actuele tarieven (RVO-meldcodelijst) en het actuele minimum.
    oud = False
    scenario = "enkel"  # 'bedrag_indicatief' = bedrag bij één maatregel; alle drie de situaties staan in 'richtbedragen'
    min_m2 = g["min_m2_vanaf_2025"]

    # ---- per positie ------------------------------------------------------
    pos_out = []
    buckets: dict[str, dict] = {}
    has_iso_glass = False
    panel_mode = cfg["panelen"]["modus"]

    def add(cat: str, m2: float, p: Position, u: Optional[float]):
        mc = resolve_meldcode(cat, p.systeem, u, cfg)
        b = buckets.setdefault((cat, mc["meldcode"]), {"m2": 0.0, "posities": [], "u_waarden": [], "systemen": set(), "mc": mc})
        b["systemen"].add(p.systeem)
        b["m2"] += m2
        b["posities"].append(p.positie)
        if u is not None:
            b["u_waarden"].append(u)

    flens_conflict: list[int] = []
    for p in report.posities:
        fl = flens_info(p, cfg, klant.get("flens_bevestigd"), klant.get("flens_mm"))
        if klant.get("flens_bevestigd") is not None and fl["volgens_rapport"] != klant["flens_bevestigd"]:
            flens_conflict.append(p.positie)
        opp = r2(fl["netto_m2_per_stuk"] * p.stuks)
        if fl["tekening_onleesbaar"]:
            warnings.append(f"Positie {p.positie} ({p.omschrijving}): de zwarte maat (zonder aanslag) in de tekening kon niet "
                            f"betrouwbaar gelezen worden — de tabelmaat {nl(p.breedte_mm, 0)} × {nl(p.hoogte_mm, 0)} is gebruikt. "
                            "Controleer de maat in de tekening van het Uw-rapport.")
        if fl["onzeker"]:
            warnings.append(f"Positie {p.positie} ({p.omschrijving}): flens gevonden ({fl['bron']}) maar flensbreedte voor {p.systeem} "
                            "staat niet in config.json — oppervlakte is zonder aftrek berekend. Controleer!")
        deur, reden = is_door(p, cfg)
        gcls, ug = glass_class(p, cfg)
        if gcls:
            has_iso_glass = True
        item = {
            "positie": p.positie, "omschrijving": p.omschrijving, "stuks": p.stuks,
            "systeem": p.systeem, "hoogte_mm": p.hoogte_mm, "breedte_mm": p.breedte_mm,
            "oppervlakte_rapport_m2": r2(p.oppervlakte_m2 * p.stuks), "flens": fl,
            "oppervlakte_m2": opp, "uw": p.uw, "uf": p.uf, "ug": ug, "up": p.up,
            "paneel_m2": r2((p.ap_m2 or 0) * p.stuks) if p.ap_m2 else None,
            "glassamenstelling": sorted({x.samenstelling for x in p.glas}),
            "type": "deur" if deur else "raam",
            "type_reden": reden or "geen deurkenmerken",
            "categorie": None, "in_aanmerking": False, "opmerking": "",
        }
        if "HEURISTIEK" in reden:
            warnings.append(f"Positie {p.positie} ({p.omschrijving}) is op basis van afmetingen als deur ingedeeld — controleer dit.")

        if deur:
            ud = p.uw
            if ud is not None and ud <= g["deur_hoog_ud_max"]:
                cat = "deur_hoog"
            elif ud is not None and ud <= g["deur_laag_ud_max"]:
                cat = "deur_laag"
            else:
                cat = None
                item["opmerking"] = f"Ud {nl(ud)} > {nl(g['deur_laag_ud_max'])}: komt niet in aanmerking"
            if cat:
                item.update(categorie=cat, in_aanmerking=True)
                add(cat, opp, p, ud)
        else:
            if not gcls:
                item["opmerking"] = f"Ug {nl(ug)} voldoet niet aan HR++ (≤ {nl(g['hr_plus_plus_glas_ug_max'])})"
            else:
                raam_m2 = opp
                if panel_mode == "apart" and p.ap_m2:
                    pm2 = min(r2(p.ap_m2 * p.stuks), opp)
                    raam_m2 = r2(opp - pm2)
                    if p.up is not None and p.up <= g["paneel_hoog_u_max"]:
                        add("paneel_hoog", pm2, p, p.up)
                    elif p.up is not None and p.up <= g["paneel_laag_u_max"]:
                        add("paneel_laag", pm2, p, p.up)
                    else:
                        item["opmerking"] = "paneel voldoet niet / U-waarde paneel onbekend"
                item.update(categorie=gcls, in_aanmerking=True)
                sup = supplier(p.systeem, cfg)
                if sup.get("glas"):
                    spec = []
                    for gl in p.glas:
                        hit = mcl.find_glass(sup["glas"], gcls, gl.samenstelling)
                        if hit and hit["meldcode"] not in [x["meldcode"] for x in spec]:
                            spec.append({"meldcode": hit["meldcode"], "product": hit["model"], "u": hit["u"], "samenstelling": gl.samenstelling})
                    item["glas_meldcodes_specifiek"] = spec
                add(gcls, raam_m2, p, ug)
        pos_out.append(item)

    if flens_conflict:
        zegt = "wel" if klant.get("flens_bevestigd") else "geen"
        warnings.append(f"Volgens de bestelling is er {zegt} flens, maar het leveranciersrapport zegt het omgekeerde voor positie "
                        f"{', '.join(map(str, flens_conflict))}. De keuze van de bestelling is gevolgd — controleer de maten.")

    # ---- voorwaarden die over posities heen gaan -------------------------
    for key in list(buckets):
        if key[0] in ("deur_hoog", "deur_laag", "paneel_hoog", "paneel_laag") and not has_iso_glass:
            warnings.append(f"{LABELS[key[0]]} komt alleen in aanmerking samen met HR++ of triple glas — geen isolatieglas gevonden in dit order.")
            buckets.pop(key)

    totaal_m2 = r2(sum(b["m2"] for b in buckets.values()))
    factor = 1.0
    if totaal_m2 > g["max_m2"]:
        factor = g["max_m2"] / totaal_m2
        warnings.append(f"Totaal {nl(totaal_m2)} m² is meer dan het maximum van {nl(g['max_m2'],0)} m²: het bedrag is naar rato begrensd.")

    maatregelen = []
    keys = sorted(buckets, key=lambda k: (ORDER.index(k[0]), k[1] or "~"))
    for key in keys:
        cat, code = key
        b = buckets[key]
        m2 = r2(b["m2"])
        m2_sub = round_m2_for_amount(m2 * factor)
        u_max = max(b["u_waarden"]) if b["u_waarden"] else None
        u_label = "Ud" if cat.startswith("deur") else ("Up" if cat.startswith("paneel") else "Ug")
        mc = b["mc"]
        r = rates_for(cat, oud, cfg)
        bedragen = {}
        for sc in ("enkel", "meerdere", "monument"):
            per = r.get(sc)
            afgeleid = False
            if sc == "monument" and per is None and r.get("meerdere") is not None:
                per, afgeleid = r["meerdere"], True   # geen apart monumentbedrag -> bedrag meerdere maatregelen
            bedragen[sc] = {"per_m2": per, "totaal": round(m2_sub * per, 2) if per is not None else None,
                            "gelijk_aan_meerdere": afgeleid}
        maatregelen.append({
            "code": cat,
            "label": LABELS[cat],
            "m2": m2,
            "m2_voor_bedrag": m2_sub,
            "u_label": u_label,
            "u_waarde": u_max,
            "u_waarden": sorted(set(b["u_waarden"])),
            "meldcode": code,
            "meldcode_bron": mc["bron"],
            "meldcode_product": mc["product"],
            "meldcode_niet_in_rvo_lijst": mc["niet_in_lijst"],
            "tarief_per_m2": bedragen[scenario]["per_m2"] or bedragen["enkel"]["per_m2"],
            "bedragen": bedragen,
            "bedrag_indicatief": bedragen[scenario]["totaal"] if bedragen[scenario]["totaal"] is not None else bedragen["enkel"]["totaal"],
            "posities": b["posities"],
            "verklaring_bouwbedrijf_nodig": cat in ("deur_hoog", "paneel_hoog"),
        })
        us = ", ".join(nl(u) for u in sorted(set(b["u_waarden"])))
        if not code:
            extra = (" Mogelijke codes: " + "; ".join(mc["kandidaten"][:5]) + ".") if mc["kandidaten"] else ""
            warnings.append(f"Geen meldcode gevonden voor '{LABELS[cat]}' ({', '.join(sorted(b['systemen']))}, {u_label} {us}).{extra} "
                            "Vul de juiste code aan (config.json → leveranciers of meldcodes), of de klant kiest 'Overige'.")
        elif mc["niet_in_lijst"]:
            warnings.append(f"Meldcode {code} ({LABELS[cat]}) komt uit een handmatige regel en staat niet in {mcl.load().get('bron')}. Controleer of deze code nog geldig is.")
    # bedrag: bij meerdere meldcodes binnen één maatregel wordt per regel afgerond

    totaal_bedrag = round(sum(m["bedrag_indicatief"] or 0 for m in maatregelen), 2)
    totaal_scenario = {}
    for sc in ("enkel", "meerdere", "monument"):
        vals = [m["bedragen"][sc]["totaal"] for m in maatregelen]
        totaal_scenario[sc] = round(sum(vals), 2) if vals and all(v is not None for v in vals) else None
    voldoet_min = totaal_m2 >= min_m2
    if not voldoet_min:
        warnings.append(f"Totaal geïsoleerd oppervlak {nl(totaal_m2)} m² is lager dan het minimum van {nl(min_m2,0)} m².")

    # ---- vestiging / klant ------------------------------------------------
    vcode = report.vestigingscode or ""
    vest = dict(cfg["vestigingen"].get(vcode) or cfg["vestigingen"]["_standaard"])
    vest["code"] = vcode
    if vcode not in cfg["vestigingen"]:
        warnings.append(f"Vestigingscode '{vcode}' niet gevonden in config.json — KVK-nummer en contactgegevens ontbreken.")
    if not vest.get("kvk"):
        warnings.append("KVK-nummer van de vestiging ontbreekt.")

    naam = klant.get("naam") or report.klantnaam
    if klant.get("naam") and report.klantnaam and klant["naam"].split()[-1].lower() != report.klantnaam.lower():
        warnings.append(f"Klantnaam op het leveranciersrapport ('{report.klantnaam}') wijkt af van de opgegeven naam ('{klant['naam']}').")

    heeft_deur = any(m["code"].startswith("deur") for m in maatregelen)
    heeft_glas = any(m["code"] in ("triple_glas", "hr_plus_plus_glas") for m in maatregelen)
    po = cfg["productomschrijving"]
    if heeft_deur and heeft_glas:
        product = po["combinatie"] if any(m["code"] == "triple_glas" for m in maatregelen) else po["hr_plus_plus_glas"] + " en isolerende deur"
    elif heeft_deur:
        product = po["deur"]
    else:
        product = po["triple_glas"] if any(m["code"] == "triple_glas" for m in maatregelen) else po["hr_plus_plus_glas"]

    systemen = sorted({p.systeem for p in report.posities})
    result = {
        "status": "ok" if (voldoet_min and maatregelen and all(m["meldcode"] and not m["meldcode_niet_in_rvo_lijst"] for m in maatregelen)
                           and not any(p["flens"]["onzeker"] for p in pos_out)) else "controle_nodig",
        "bron": {
            "formaat": report.formaat, "ordernummer": report.ordernummer, "referentie": report.referentie,
            "rapportdatum": report.rapportdatum, "besteller": report.besteller,
        },
        "klant": {
            "naam": naam,
            "aanhef": klant.get("aanhef") or (f"Geachte heer/mevrouw {naam}" if naam else "Geachte klant"),
            "straat": klant.get("straat"),
            "huisnummer": klant.get("huisnummer") or report.huisnummer,
            "postcode": klant.get("postcode"),
            "plaats": klant.get("plaats"),
            "email": klant.get("email"),
        },
        "vestiging": vest,
        "productomschrijving": product,
        "systemen": systemen,
        "maatregelen": maatregelen,
        "totaal": {
            "m2": totaal_m2,
            "m2_voor_bedrag": sum(m["m2_voor_bedrag"] for m in maatregelen),
            "bedrag_indicatief": totaal_bedrag,
            "richtbedragen": totaal_scenario,
            "minimum_m2": min_m2,
            "maximum_m2": g["max_m2"],
            "voldoet_minimum": voldoet_min,
            "tarieftabel": "actueel",
        },
        "posities": pos_out,
        "waarschuwingen": warnings,
        "rvo_link": cfg["rvo_link"],
        "meldcodelijst": {"bron": mcl.load().get("bron"), "geimporteerd": mcl.load().get("geimporteerd")},
    }
    result["mail_velden"] = mail_fields(result)
    return result


def mail_fields(res: dict) -> dict:
    """Platte velden, handig voor e-mailtemplates in n8n/Zapier."""
    out = {
        "klant_naam": res["klant"]["naam"],
        "klant_adres": " ".join(x for x in [res["klant"].get("straat"), res["klant"].get("huisnummer")] if x),
        "klant_plaats": res["klant"].get("plaats"),
        "kvk_vestiging": res["vestiging"].get("kvk"),
        "vestiging_naam": res["vestiging"].get("naam"),
        "productomschrijving": res["productomschrijving"],
        "totaal_m2": nl(res["totaal"]["m2"]),
        "bedrag_indicatief": eur(res["totaal"]["bedrag_indicatief"]),
        "ordernummer": res["bron"]["ordernummer"],
        "bedrag_enkele_maatregel": eur(res["totaal"]["richtbedragen"]["enkel"]) if res["totaal"]["richtbedragen"]["enkel"] is not None else "–",
        "bedrag_meerdere_maatregelen": eur(res["totaal"]["richtbedragen"]["meerdere"]) if res["totaal"]["richtbedragen"]["meerdere"] is not None else "–",
        "bedrag_monumentale_woning": eur(res["totaal"]["richtbedragen"]["monument"]) if res["totaal"]["richtbedragen"]["monument"] is not None else "–",
    }
    seen: dict[str, int] = {}
    for m in res["maatregelen"]:
        c = m["code"]
        seen[c] = seen.get(c, 0) + 1
        if seen[c] > 1:
            c = f"{c}_{seen[c]}"
        out[f"{c}_m2"] = nl(m["m2"])
        out[f"{c}_u_waarde"] = nl(m["u_waarde"])
        out[f"{c}_meldcode"] = m["meldcode"] or "Overige"
    return out
