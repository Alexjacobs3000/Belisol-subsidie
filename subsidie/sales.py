"""
Sales-module: indicatieve ISDE-subsidie op basis van een offerte.

1. `lees_offerte()` zoekt in de offerte-PDF (tekst, of OCR bij een scan) de elementen: reeks (→ leverancier via
   de gamma-lijst), type (raam/deur/schuifraam), maten, aantal en vulling (glas/deur).
2. De verkoper controleert/vult de elementen aan in de app.
3. `bereken()` zet de elementen om naar 'virtuele posities' en gebruikt dezelfde subsidieregels als de
   administratie-module (`rules.evaluate`): minimum/maximum m², deur alleen met isolatieglas, afronding, tarieven.
4. `render_pdf()` maakt het klantdocument 'Subsidie-indicatie'.

Het lezen van de offerte is best effort: de verkoper controleert altijd de elemententabel.
"""
from __future__ import annotations

import copy
import io
import re
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Optional

import pdfplumber

from . import gammas
from .parser import Report, Position
from .rules import evaluate, load_config, nl, eur, r2, LABELS

HERE = Path(__file__).parent

# vulling → (label, Ug of Ud voor de regels, is deur)
VULLINGEN = {
    "triple_glas": ("Triple glas (Ug ≤ 0,7)", 0.7, False),
    "hr_plus_plus_glas": ("HR++ glas (Ug ≤ 1,2)", 1.2, False),
    "deur_hoog": ("Isolerende deur (Ud ≤ 1,0)", 1.0, True),
    "deur_laag": ("Isolerende deur (Ud ≤ 1,5)", 1.5, True),
    "geen": ("Niet subsidiabel", None, False),
}
TYPES = ["Raam", "Deur", "Schuifraam", "Vouwwand", "Overig"]


@dataclass
class Element:
    nr: int
    omschrijving: str = ""
    serie: str = ""
    leverancier: str = ""
    type: str = "Raam"
    breedte_mm: Optional[float] = None
    hoogte_mm: Optional[float] = None
    aantal: int = 1
    vulling: str = "hr_plus_plus_glas"
    opmerking: str = ""  # wat de lezer niet zeker wist

    @property
    def m2(self) -> Optional[float]:
        if not self.breedte_mm or not self.hoogte_mm:
            return None
        return r2(self.breedte_mm * self.hoogte_mm / 1_000_000 * (self.aantal or 1))

    def to_dict(self) -> dict:
        return dict(asdict(self), m2=self.m2)


# ------------------------------------------------------------------ offerte lezen
_BLOK = re.compile(r"^\s*(?:pos(?:itie)?\.?|element|item|artikel)\s*:?\s*(\d{1,3})\b", re.I | re.M)
_MAAT = re.compile(r"(?<!\d)(\d{3,4})(?:[.,]\d)?\s*(?:mm)?\s*[x×X*]\s*(\d{3,4})(?:[.,]\d)?\s*(?:mm)?(?!\d)")
_BREEDTE = re.compile(r"breedte\s*(?:\(mm\))?\s*[:=]?\s*(\d{3,4})", re.I)
_HOOGTE = re.compile(r"hoogte\s*(?:\(mm\))?\s*[:=]?\s*(\d{3,4})", re.I)
_AANTAL = re.compile(r"\b(?:aantal|stuks?|st\.?)\s*[:=]?\s*(\d{1,3})\b|(\d{1,3})\s*(?:st(?:uks?)?\.?|st\(s\))(?!\w)", re.I)
_UG = re.compile(r"\bUg\s*[=:]?\s*(\d[.,]\d{1,2})", re.I)
_UD = re.compile(r"\bU[dw]\s*[=:]?\s*(\d[.,]\d{1,2})", re.I)


def _tekst(pdf_bytes: bytes) -> tuple[str, bool]:
    """Tekst van de offerte; bij een scan (geen tekstlaag) via OCR. Geeft (tekst, gescand)."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        tekst = "\n".join(p.extract_text() or "" for p in pdf.pages)
        if len(tekst.strip()) > 50:
            return tekst, False
        from .tekening import OCR_BESCHIKBAAR
        if not OCR_BESCHIKBAAR:
            return tekst, True
        import pytesseract
        talen = "nld" if "nld" in pytesseract.get_languages() else "eng"
        delen = [pytesseract.image_to_string(p.to_image(resolution=300).original, lang=talen) for p in pdf.pages]
        return "\n".join(delen), True


def _vulling(blok: str, type_: str, cfg: dict) -> tuple[str, str]:
    g = cfg["grenswaarden"]
    low = blok.lower()
    if type_ == "Deur":
        m = _UD.search(blok)
        if m:
            ud = float(m.group(1).replace(",", "."))
            if ud <= g["deur_hoog_ud_max"]:
                return "deur_hoog", ""
            if ud <= g["deur_laag_ud_max"]:
                return "deur_laag", ""
            return "geen", f"Ud {nl(ud)} te hoog"
        return "deur_laag", "Ud niet gevonden: aangenomen Ud ≤ 1,5 — controleer"
    m = _UG.search(blok)
    if m:
        ug = float(m.group(1).replace(",", "."))
        if ug <= g["triple_glas_ug_max"]:
            return "triple_glas", ""
        if ug <= g["hr_plus_plus_glas_ug_max"]:
            return "hr_plus_plus_glas", ""
        return "geen", f"Ug {nl(ug)} te hoog"
    if re.search(r"triple|drievoudig|hr\s*\+\+\+|3[- ]?voudig", low):
        return "triple_glas", ""
    if re.search(r"hr\s*\+\+|dubbel\s*glas|isolatieglas", low):
        return "hr_plus_plus_glas", ""
    std = cfg.get("sales", {}).get("standaard_vulling_raam", "hr_plus_plus_glas")
    return std, f"vulling niet gevonden: aangenomen {VULLINGEN[std][0]} — controleer"


def _type(blok: str, gamma_type: Optional[str]) -> str:
    low = blok.lower()
    if re.search(r"schuif|hef[- ]?schuif|hst\b", low):
        return "Schuifraam"
    if re.search(r"vouwwand", low):
        return "Vouwwand"
    if re.search(r"\b(?:voor|achter|tuin|zij|balkon|terras)?deur\b", low) and not re.search(r"deurslot|deurklink", low):
        return "Deur"
    return gamma_type if gamma_type in TYPES and gamma_type != "Deur" else "Raam"


def _materiaal(blok: str) -> Optional[str]:
    low = blok.lower()
    for m, pat in (("PVC-ALU", r"pvc[- /]alu"), ("PVC", r"\bpvc\b|kunststof"), ("ALU", r"\balu(?:minium)?\b"), ("HOUT", r"\bhout(?:en)?\b")):
        if re.search(pat, low):
            return m
    return None


def _blokken(tekst: str) -> list[str]:
    """Knipt de offerte in elementblokken: op 'Pos. 1'/'Element 1'…, anders bij elke gevonden reeksnaam."""
    starts = [m.start() for m in _BLOK.finditer(tekst)]
    if len(starts) < 1:
        starts = [p for p, _ in gammas.vind_series(tekst)]
    if not starts:
        return []
    starts.append(len(tekst))
    return [tekst[a:b] for a, b in zip(starts, starts[1:]) if tekst[a:b].strip()]


def lees_offerte(pdf_bytes: bytes, cfg: Optional[dict] = None) -> dict:
    """Leest de offerte. Geeft {'elementen': [Element], 'gescand': bool, 'meldingen': [str], 'offertenummer'}."""
    cfg = cfg or load_config()
    tekst, gescand = _tekst(pdf_bytes)
    meldingen = []
    if gescand:
        meldingen.append("De offerte is een scan: de tekst is met OCR gelezen. Controleer de elementen extra goed.")
    elementen: list[Element] = []
    vorige_serie = None
    for blok in _blokken(tekst):
        series = gammas.vind_series(blok)
        serie = series[0][1] if series else vorige_serie
        vorige_serie = serie or vorige_serie
        maat = _MAAT.search(blok)
        b = h = None
        if maat:
            b, h = float(maat.group(1)), float(maat.group(2))
        else:
            mb, mh = _BREEDTE.search(blok), _HOOGTE.search(blok)
            b = float(mb.group(1)) if mb else None
            h = float(mh.group(1)) if mh else None
        if not serie and not (b and h):
            continue  # geen element (bv. voorwaarden, totalen)
        ma = _AANTAL.search(blok)
        aantal = int(ma.group(1) or ma.group(2)) if ma else 1
        mat = _materiaal(blok)
        g0 = gammas.kies(serie, materiaal=mat) if serie else None
        type_ = _type(blok, g0["type"] if g0 else None)
        g = gammas.kies(serie, type_, mat) if serie else None
        vulling, opm = _vulling(blok, type_, cfg)
        if g and g["type"] in ("Rolluik", "Varia", "Poort"):
            type_ = "Overig"
        if type_ == "Overig":
            vulling, opm = "geen", "geen glas-/deurisolatie"
        opmerkingen = [opm] if opm else []
        if not serie:
            opmerkingen.append("reeks niet herkend")
        elif g and g.get("dubbelzinnig"):
            opmerkingen.append(f"reeks '{serie}' bestaat bij {', '.join(g['alternatieven'])} — controleer leverancier")
        if not (b and h):
            opmerkingen.append("maten niet gevonden")
        eerste = next((l.strip() for l in blok.splitlines() if l.strip()), "")
        elementen.append(Element(
            nr=len(elementen) + 1, omschrijving=eerste[:60], serie=serie or "", leverancier=(g or {}).get("leverancier", ""),
            type=type_, breedte_mm=b, hoogte_mm=h, aantal=max(aantal, 1), vulling=vulling, opmerking="; ".join(opmerkingen)))
    if not elementen:
        meldingen.append("Er zijn geen elementen herkend in de offerte. Vul de elementen hieronder handmatig in.")
    nr = re.search(r"offerte\s*(?:nr\.?|nummer)?\s*[:#]?\s*([A-Z0-9][\w/-]{3,})", tekst, re.I)
    return {"elementen": elementen, "gescand": gescand, "meldingen": meldingen,
            "offertenummer": nr.group(1) if nr else None, "series_gevonden": sorted({s for _, s in gammas.vind_series(tekst)})}


# ------------------------------------------------------------------ berekenen
def _positie(e: Element) -> Position:
    label, u, deur = VULLINGEN.get(e.vulling, VULLINGEN["geen"])
    niet = e.vulling == "geen"
    return Position(
        positie=e.nr, stuks=max(int(e.aantal or 1), 1),
        omschrijving="deur" if deur else "element",  # 'deur' laat rules.is_door de positie als deur zien
        systeem=f"{e.leverancier or '-'} {e.serie}".strip(),  # nooit exact 'CERTIX 116' (dat is de rapportlogica)
        hoogte_mm=float(e.hoogte_mm or 0), breedte_mm=float(e.breedte_mm or 0),
        oppervlakte_m2=r2((e.breedte_mm or 0) * (e.hoogte_mm or 0) / 1_000_000),
        uw=u if deur else None, ug=None if (deur or niet) else u,
    )


def bereken(elementen: list[Element], klant: Optional[dict] = None, vestigingscode: Optional[str] = None,
            offertenummer: Optional[str] = None, cfg: Optional[dict] = None) -> dict:
    cfg = copy.deepcopy(cfg or load_config())
    # Indicatie per categorie: geen meldcodes zoeken (die volgen na de bestelling uit het thermisch rapport), zodat
    # alle elementen van dezelfde categorie samen worden afgerond, ongeacht de leverancier.
    cfg["leveranciers"] = {"systemen": {}, "trefwoorden": {}}
    cfg["meldcodes"] = {"regels": []}
    geldig = [e for e in elementen if e.breedte_mm and e.hoogte_mm]
    report = Report(formaat="offerte", ordernummer=offertenummer, referentie=None, referentie_prefix=None,
                    vestigingscode=vestigingscode, klantnaam=None, huisnummer=(klant or {}).get("huisnummer"),
                    rapportdatum=None, besteller=None, posities=[_positie(e) for e in geldig])
    res = evaluate(report, klant, cfg)

    # meldingen die over het leveranciersrapport gaan, zijn voor sales niet relevant
    weg = ("Geen meldcode gevonden", "Meldcode ", "Klantnaam op het leveranciersrapport")
    meldingen = [w for w in res["waarschuwingen"] if not w.startswith(weg)]
    for e in elementen:
        if not (e.breedte_mm and e.hoogte_mm):
            meldingen.append(f"Element {e.nr} ({e.omschrijving or e.serie or 'zonder naam'}) heeft geen maten en telt niet mee.")
        if not e.leverancier and e.vulling != "geen":
            meldingen.append(f"Element {e.nr}: leverancier onbekend — het bedrag is berekend met het standaardtarief.")
    if not res["maatregelen"]:
        meldingen.append("Geen enkel element komt in aanmerking voor subsidie.")

    cat_per_pos = {p["positie"]: p for p in res["posities"]}
    res["elementen"] = []
    for e in elementen:
        p = cat_per_pos.get(e.nr, {})
        res["elementen"].append(dict(e.to_dict(), vulling_label=VULLINGEN.get(e.vulling, VULLINGEN["geen"])[0],
                                     categorie=p.get("categorie"), in_aanmerking=bool(p.get("in_aanmerking"))))
    res["waarschuwingen"] = meldingen
    res["leveranciers"] = sorted({e.leverancier for e in elementen if e.leverancier})
    res["status"] = "ok" if res["maatregelen"] and res["totaal"]["voldoet_minimum"] else "controle_nodig"
    return res


# ------------------------------------------------------------------ document
def render_html(res: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from .report import datum_nl, eur0, eurr, KORT
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
    return env.get_template("sales.html").render(
        **res, assets=(HERE / "assets").as_uri(), vandaag=datum_nl(date.today().isoformat()),
        heeft_deur=any(m["code"].startswith("deur") for m in res["maatregelen"]),
        korte_label=KORT, labels=LABELS, nl=nl, eur=eur, eur0=eur0, eurr=eurr, rb=res["totaal"]["richtbedragen"],
    )


def render_pdf(res: dict) -> bytes:
    from weasyprint import HTML
    return HTML(string=render_html(res), base_url=str(HERE)).write_pdf()
