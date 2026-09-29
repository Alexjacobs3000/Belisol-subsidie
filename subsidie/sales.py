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

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

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
    paneel_m2: Optional[float] = None   # totaal (alle stuks) aan panelen in het element, geschat uit de schets
    paneel_up: Optional[float] = None   # Up van het paneel (uit de detailomschrijving)
    opmerking: str = ""  # wat de lezer niet zeker wist
    tekening: Optional[str] = None      # schets uit de offerte (PNG base64) voor het klantdocument

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


def _pagina_tekst(page) -> str:
    """Tekst per regel (op y-positie, van links naar rechts), zoals pdfplumber.extract_text. pypdfium2 is hier
    ~100× sneller: offertes bevatten zware vectortekeningen waar pdfplumber seconden per pagina over doet."""
    tp = page.get_textpage()
    tekens = []
    for i in range(tp.count_chars()):
        c = tp.get_text_range(i, 1)
        if not c or c in "\r\n\x02":
            continue
        l, b, r, t = tp.get_charbox(i, loose=True)
        tekens.append(((b + t) / 2, t - b, l, r, c))
    tekens.sort(key=lambda x: -x[0])
    regels, huidig, y0 = [], [], None
    for y, h, l, r, c in tekens:
        if y0 is None or abs(y - y0) > max(1.5, h * 0.4):
            if huidig:
                regels.append(huidig)
            huidig, y0 = [], y
        huidig.append((l, r, h, c))
    if huidig:
        regels.append(huidig)
    uit = []
    for rg in regels:
        rg.sort(key=lambda x: x[0])
        tekst, vorige = "", None
        for l, r, h, c in rg:
            if vorige is not None and l - vorige > h * 0.3 and not tekst.endswith(" "):
                tekst += " "  # los tekstblok op dezelfde regel (bv. tweede kolom)
            tekst += c
            vorige = r
        uit.append(" ".join(tekst.split()))
    return "\n".join(x for x in uit if x)


def _tekst(pdf: "pdfium.PdfDocument") -> tuple[list[str], bool]:
    """Tekst per pagina; bij een scan (geen tekstlaag) via OCR. Geeft (pagina's, gescand)."""
    paginas = [_pagina_tekst(p) for p in pdf]
    if len("".join(paginas).strip()) > 50:
        return paginas, False
    from .tekening import OCR_BESCHIKBAAR
    if not OCR_BESCHIKBAAR:
        return paginas, True
    import pytesseract
    talen = "nld" if "nld" in pytesseract.get_languages() else "eng"
    return [pytesseract.image_to_string(p.render(scale=300 / 72).to_pil(), lang=talen) for p in pdf], True


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
        glas, _ = _vulling(blok, "Raam", cfg)
        if glas in ("triple_glas", "hr_plus_plus_glas") and _glastype_genoemd(blok):
            return glas, ("deur zonder Ud-waarde in de offerte: gerekend als glas en panelen; met een Ud ≤ 1,0 "
                          "telt de deur als isolerende deur")
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


def _glastype_genoemd(blok: str) -> bool:
    return bool(_UG.search(blok) or re.search(r"triple|drievoudig|hr\s*\+\+|dubbel\s*glas|isolatieglas", blok, re.I))


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


def _lees_generiek(tekst: str, gescand: bool, cfg: dict) -> dict:
    """Onbekend offerteformaat: knip op 'Pos. 1'/'Element 1'… of op reeksnamen en zoek maten/vulling in de tekst."""
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
    return {"elementen": elementen, "meldingen": meldingen}


# ---- Belisol-offerte ('Voorstel en Opdracht'): per element een pagina 'Specificaties / Post 1A - Kozijn' met
# Afmetingen, Aantal, Detailomschrijving product, Opmerkingen en een schets.
_POST = re.compile(r"^\s*Post\s+(\w+)\s*-\s*(.+?)\s*$", re.M | re.I)
_AFM = re.compile(r"Afmetingen:\s*(\d{3,5})\s*mm\s*[x×]\s*(\d{3,5})\s*mm", re.I)
_UP = re.compile(r"\bUp\s*[=:]?\s*(\d[.,]\d{1,2})", re.I)
_VOET = re.compile(r"^(?:.*\bNL\s?\d{4}\.\d{2}\.\d{3}\.B\d{2}.*|Offerte nr\..*|Belisol\. Liefde voor het vak\..*)$", re.M)
POST_TYPE = {"kozijn": "Raam", "raam": "Raam", "deur": "Deur", "schuifpui": "Schuifraam", "schuifraam": "Schuifraam",
             "vouwwand": "Vouwwand"}


def _sectie(tekst: str, kop: str, eind: tuple[str, ...]) -> str:
    m = re.search(rf"{kop}\s*:?\s*\n", tekst, re.I)
    if not m:
        return ""
    rest = tekst[m.end():]
    stops = [rest.find(e) for e in eind if rest.find(e) >= 0]
    return _VOET.sub("", rest[:min(stops)] if stops else rest).strip()


def _reeks(detail: str) -> str:
    """Reeksnaam uit de detailomschrijving: '• PVC Reeks Classix Blok (T-kaderprofiel)', '• Reeks : SlideS …' of de
    eerste opsomming ('• Duoslide (T-kaderprofiel) Buitenzicht')."""
    m = re.search(r"Reeks\s*:?\s*(.+?)\s*(?:\(|\bBuitenzicht\b|\bBinnenzicht\b|$)", detail, re.M | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"^[•·-]\s*([A-Z][\w+ -]*?)\s*(?:\(|\bBuitenzicht\b|\bBinnenzicht\b|$)", detail, re.M)
    return m.group(1).strip() if m else ""


def _schets(page) -> Optional[Image.Image]:
    """De schets van het element: de grootste afbeelding op de pagina, ook binnen formulieren (het logo linksboven is
    klein en valt af)."""
    kand = []
    for o in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=5):
        l, b, r, t = o.get_bounds()
        w, h = o.get_px_size()
        if r - l > 60 and w * h > 100_000:
            kand.append((w * h, o))
    if not kand:
        return None
    try:
        return max(kand, key=lambda x: x[0])[1].get_bitmap(render=False).to_pil().convert("RGB")
    except Exception:
        return None


def _png_b64(img, max_px: int = 360) -> str:
    import base64
    im = img.copy()
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def _lees_belisol(pdf: "pdfium.PdfDocument", paginas: list[str], cfg: dict) -> list[Element]:
    from .tekening import vulling_uit_schets
    elementen = []
    for page, t in zip(pdf, paginas):
        post = _POST.search(t)
        if not post or not t.lstrip().lower().startswith("specificaties"):
            continue
        afm, aantal = _AFM.search(t), re.search(r"Aantal:\s*(\d+)", t)
        detail = _sectie(t, "Detailomschrijving product", ("Meer uitleg over element", "Opmerkingen:"))
        opm_offerte = _sectie(t, "Opmerkingen", ("\u0000",))
        tekst = detail + "\n" + opm_offerte
        b, h = (float(afm.group(1)), float(afm.group(2))) if afm else (None, None)
        n = int(aantal.group(1)) if aantal else 1
        reeks = _reeks(detail)
        mat = _materiaal(detail)
        type_ = POST_TYPE.get(post.group(2).strip().lower()) or _type(tekst, None)
        g = gammas.kies(reeks, type_, mat) if reeks else None
        vulling, opm = _vulling(tekst, type_, cfg)
        opmerkingen = [opm] if opm else []
        if not reeks:
            opmerkingen.append("reeks niet gevonden")
        elif not g:
            opmerkingen.append(f"reeks '{reeks}' staat niet in de gamma-lijst — kies de leverancier")
        elif g.get("dubbelzinnig"):
            opmerkingen.append(f"reeks '{reeks}' bestaat bij {', '.join(g['alternatieven'])} — controleer leverancier")
        # panelen: alleen als de offerte ze noemt of de schets een 'p' (paneel) toont; verdeling uit schets + deelmaten
        up = _UP.search(tekst)
        paneel_m2 = None
        img = _schets(page)
        if img is not None and b and h and vulling != "geen":
            v = vulling_uit_schets(img, b, h)
            noemt_paneel = re.search(r"paneel|sandwich", tekst, re.I)
            if v and (noemt_paneel or v["p_labels"]) and v["paneel"] > 0.01:
                paneel_m2 = r2(v["paneel"] * b * h / 1_000_000 * n)
                deel = f" (deelmaten {' | '.join(nl(x, 0) for x in v['deelmaten_b'])})" if v.get("deelmaten_b") else ""
                opmerkingen.append(f"panelen ± {nl(paneel_m2)} m² geschat uit de tekening{deel} — controleer")
            elif noemt_paneel and not v:
                opmerkingen.append("offerte noemt een paneel, maar de tekening is niet te lezen — vul 'Paneel m²' in")
        elementen.append(Element(
            nr=len(elementen) + 1, omschrijving=f"Post {post.group(1)} – {post.group(2).strip()}",
            serie=reeks, leverancier=(g or {}).get("leverancier", ""), type=type_, breedte_mm=b, hoogte_mm=h,
            aantal=max(n, 1), vulling=vulling, paneel_m2=paneel_m2,
            paneel_up=float(up.group(1).replace(",", ".")) if up else None,
            opmerking="; ".join(opmerkingen), tekening=_png_b64(img) if img is not None else None))
    return elementen


def _klant_en_verkoper(tekst: str) -> dict:
    """Klant, adviseur en vestiging uit een Belisol-offerte (voorblad en overeenkomst)."""
    def zoek(p, flags=re.M):
        m = re.search(p, tekst, flags)
        return m
    klant, vest, adviseur = {}, {}, {}
    m = zoek(r"^Tussen:\s*(.+?)\s+en\s+(.+?)\s*$")
    if m:
        klant["naam"], vest["naam"] = m.group(1).strip(), m.group(2).strip()
    m = zoek(r"Montageadres\s*:?.*\n(.+)\n(.+)")
    if m:
        adres = re.match(r"^(.+?)\s+(\d+\s*[a-zA-Z]?(?:[-/]\d+)?)\b", m.group(1).strip())
        if adres:
            klant["straat"], klant["huisnummer"] = adres.group(1), adres.group(2).replace(" ", "")
        pc = re.match(r"^(\d{4}\s?[A-Z]{2})\s+(.+?)(?:\s+\d{4}\s?[A-Z]{2}\s.*)?$", m.group(2).strip())
        if pc:
            klant["postcode"], klant["plaats"] = pc.group(1), pc.group(2).strip()
    m = zoek(r"^(Belisol [^\n]+)\n(.+)\n(\+?[\d ]{9,})\n(\S+@\S+)$")
    if m:
        vest["weergavenaam"] = m.group(1).strip()
        klant.setdefault("naam", m.group(2).strip())
        klant["telefoon"], klant["email"] = m.group(3).strip(), m.group(4).strip()
    m = zoek(r"^(.+)\nUw Belisol adviseur\s*\nTel:\s*(.+)\nE-mail:\s*(\S+)")
    if m:
        adviseur = {"naam": m.group(1).strip(), "telefoon": m.group(2).strip(), "email": m.group(3).strip()}
    m = zoek(r"^(.+?)\s+-\s+(.+?)\s+(\d{4}\s?[A-Z]{2})\s+(.+?)\s+-\s+NL\s?[\d.]+B\d{2}")
    if m:
        vest.setdefault("naam", m.group(1).strip())
        vest["adres"], vest["postcode_plaats"] = m.group(2).strip(), f"{m.group(3)} {m.group(4).strip()}"
    m = zoek(r"^Telefoon:\s*\S+\s+(.+)$")
    if m:
        vest["telefoon"] = m.group(1).strip()
    m = zoek(r"^E-mail:\s*\S+\s+(\S+@\S+)\s*$")
    if m:
        vest["email"] = m.group(1).strip()
    nr = zoek(r"Offerte\s*nr\.?\s*:?\s*([A-Z0-9][\w/-]{3,})", re.I)
    return {"klant": klant, "vestiging": vest, "adviseur": adviseur, "offertenummer": nr.group(1) if nr else None}


def lees_offerte(pdf_bytes: bytes, cfg: Optional[dict] = None) -> dict:
    """Leest de offerte. Geeft {'elementen': [Element], 'gescand', 'meldingen', 'offertenummer', 'klant',
    'vestiging', 'adviseur', 'formaat', 'series_gevonden'}."""
    cfg = cfg or load_config()
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        paginas, gescand = _tekst(pdf)
        elementen = [] if gescand else _lees_belisol(pdf, paginas, cfg)
    finally:
        pdf.close()
    tekst = "\n".join(paginas)
    if elementen:
        uit = {"elementen": elementen, "meldingen": [], "formaat": "Belisol-offerte"}
    else:
        uit = dict(_lees_generiek(tekst, gescand, cfg), formaat="onbekend formaat")
    uit.update(_klant_en_verkoper(tekst))
    uit["gescand"] = gescand
    uit["series_gevonden"] = sorted({e.serie for e in uit["elementen"] if e.serie})
    return uit


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
        ap_m2=r2(e.paneel_m2 / max(int(e.aantal or 1), 1)) if e.paneel_m2 else None, up=e.paneel_up,
    )


def bereken(elementen: list[Element], klant: Optional[dict] = None, vestigingscode: Optional[str] = None,
            offertenummer: Optional[str] = None, cfg: Optional[dict] = None, vestiging: Optional[dict] = None,
            adviseur: Optional[dict] = None) -> dict:
    """vestiging/adviseur: gegevens uit de offerte; die gaan voor op de vestiging uit config.json."""
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
    weg = ("Geen meldcode gevonden", "Meldcode ", "Klantnaam op het leveranciersrapport", "Vestigingscode", "KVK-nummer")
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
    if vestiging:
        res["vestiging"] = dict(res["vestiging"], **{k: v for k, v in vestiging.items() if v})
        res["vestiging"].setdefault("weergavenaam", res["vestiging"].get("naam"))
    res["adviseur"] = adviseur or {}
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
