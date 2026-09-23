"""
Parser voor thermische Uw-rapporten van leveranciers.

Ondersteund formaat (v1): het 'Uw rapport' met posities zoals
'Pos. 1, 1 St(s)' / Systeem / Hoogte / Breedte / Oppervlakte / Uw,
blok COËFFICIËNTEN (PROFIEL/BEGLAZING/PANEEL/AFSTANDHOUDER) en MATERIALEN.
Nieuwe leveranciersformaten worden toegevoegd als extra parse-functie in
`parse_report()` (zie SUPPORTED_FORMATS).
"""
from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pdfplumber
from PIL import Image

from .tekening import lees_zwarte_maten

NUM = r"(\d+(?:[.,]\d+)?)"


def _f(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    return float(s.replace(",", "."))


@dataclass
class GlassPane:
    aantal: int
    samenstelling: str
    ug: Optional[float]
    hoogte_mm: Optional[float]
    breedte_mm: Optional[float]
    oppervlakte_m2: Optional[float]
    aantal_bladen: int  # 2 = dubbel, 3 = triple


@dataclass
class Position:
    positie: int
    stuks: int
    omschrijving: str
    systeem: str
    hoogte_mm: float
    breedte_mm: float
    oppervlakte_m2: float  # per stuk, zoals op rapport
    uw: Optional[float]
    af_m2: Optional[float] = None
    uf: Optional[float] = None
    ag_m2: Optional[float] = None
    ug: Optional[float] = None
    ap_m2: Optional[float] = None
    up: Optional[float] = None
    psi_g: Optional[float] = None
    materialen: dict = field(default_factory=dict)
    glas: list = field(default_factory=list)
    tekening_png_b64: Optional[str] = None
    # zwarte maat bij de schets = maat zonder aanslag (OCR; None = niet betrouwbaar leesbaar)
    tekening_breedte_mm: Optional[float] = None
    tekening_hoogte_mm: Optional[float] = None

    @property
    def vleugel_artikels(self) -> list[str]:
        v = self.materialen.get("VLEUGEL", [])
        return [m.split()[0] for m in v if m and re.match(r"^\d{3}\.\d{3}", m)]


@dataclass
class Report:
    formaat: str
    ordernummer: Optional[str]
    referentie: Optional[str]
    referentie_prefix: Optional[str]
    vestigingscode: Optional[str]
    klantnaam: Optional[str]
    huisnummer: Optional[str]
    rapportdatum: Optional[str]
    besteller: Optional[str]
    posities: list[Position]

    def to_dict(self, include_drawings: bool = False) -> dict:
        d = asdict(self)
        if not include_drawings:
            for p in d["posities"]:
                p.pop("tekening_png_b64", None)
        return d


# --------------------------------------------------------------------------
# Formaat 1: 'Uw rapport' (List & Label, CERTIX e.a.)
# --------------------------------------------------------------------------

PAGE_HEADER = re.compile(r"^Page \d+/\d+ .*$", re.M)
POS_SPLIT = re.compile(r"^Pos\.\s*(\d+),\s*(\d+)\s*St\(s\)\s*$", re.M)
MAT_KEYS = ["KADER", "VLEUGEL", "PANEEL", "AFSTANDHOUDER", "ROEDE", "DORPEL", "BESLAG", "OPVULLING"]


def _glass_line(txt: str) -> Optional[GlassPane]:
    m = re.match(r"GLAS\s+(\d+)st\(s\)\s+(.*)$", txt.strip())
    if not m:
        return None
    aantal, rest = int(m.group(1)), m.group(2)
    comp = rest.split()[0] if rest.split() else ""
    ug = re.search(r"\bU[g]?=\s*" + NUM, rest)
    h = re.search(r"H:\s*" + NUM + r"\s*mm", rest)
    b = re.search(r"L:\s*" + NUM + r"\s*mm", rest)
    s = re.search(r"S:\s*" + NUM + r"\s*m2", rest)
    # aantal glasbladen: 'blad/spouw/blad/spouw/blad' -> 5 segmenten = 3 bladen (triple)
    segs = comp.split("/") if comp else []
    bladen = (len(segs) + 1) // 2 if segs else 0
    return GlassPane(
        aantal=aantal,
        samenstelling=comp,
        ug=_f(ug.group(1)) if ug else None,
        hoogte_mm=_f(h.group(1)) if h else None,
        breedte_mm=_f(b.group(1)) if b else None,
        oppervlakte_m2=_f(s.group(1)) if s else None,
        aantal_bladen=bladen,
    )


def _parse_block(nr: int, stuks: int, block: str) -> Position:
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    omschrijving = lines[0] if lines and not lines[0].startswith("Systeem") else ""

    main = re.search(
        r"^(.+?)\s+" + NUM + r"\s+" + NUM + r"\s+" + NUM + r"\s+" + NUM + r"\s*$",
        "\n".join(l for l in lines if not l.startswith(("PROFIEL", "BEGLAZING", "PANEEL", "AFSTAND", "GLAS", "KADER", "VLEUGEL"))),
        re.M,
    )
    if not main:
        raise ValueError(f"Positie {nr}: hoofdregel (systeem/hoogte/breedte/oppervlakte/Uw) niet gevonden")
    systeem, h, b, opp, uw = main.groups()

    def grab(pattern):
        m = re.search(pattern, block, re.M)
        return m

    prof = grab(r"^\s*PROFIEL\s+Af \(m2\)\s+" + NUM + r"\s+Uf \[W/m2K\]\s+" + NUM)
    glas = grab(r"^\s*BEGLAZING\s+Ag\s+" + NUM + r"\s+Ug \[W/m2K\]\s+" + NUM)
    pan = grab(r"^\s*PANEEL\s+Ap \(m2\)\s+" + NUM + r"(?:\s+" + NUM + r")?")
    psi = grab(r"^\s*AFSTANDHOUDER\s+Psig \[W/mK\]\s+" + NUM)

    materialen: dict[str, list[str]] = {}
    ruiten: list[GlassPane] = []
    in_mat = False
    for l in lines:
        if l.startswith("MATERIALEN"):
            in_mat = True
            continue
        if not in_mat:
            continue
        if l.startswith("GLAS"):
            g = _glass_line(l)
            if g:
                ruiten.append(g)
            continue
        for k in MAT_KEYS:
            if l.startswith(k + " ") and "Psig" not in l and "(m2)" not in l:
                materialen.setdefault(k, []).append(l[len(k):].strip())
                break

    return Position(
        positie=nr,
        stuks=stuks,
        omschrijving=omschrijving,
        systeem=systeem.strip(),
        hoogte_mm=_f(h),
        breedte_mm=_f(b),
        oppervlakte_m2=_f(opp),
        uw=_f(uw),
        af_m2=_f(prof.group(1)) if prof else None,
        uf=_f(prof.group(2)) if prof else None,
        ag_m2=_f(glas.group(1)) if glas else None,
        ug=_f(glas.group(2)) if glas else None,
        ap_m2=_f(pan.group(1)) if pan else None,
        up=_f(pan.group(2)) if pan and pan.group(2) else None,
        psi_g=_f(psi.group(1)) if psi else None,
        materialen=materialen,
        glas=ruiten,
    )


def _parse_reference(ref: Optional[str]) -> dict:
    """'C26 BNNIM Bischeshar 2063' -> prefix, vestiging, naam, huisnummer."""
    out = {"referentie_prefix": None, "vestigingscode": None, "klantnaam": None, "huisnummer": None}
    if not ref:
        return out
    m = re.match(r"^\s*(\S+)\s+([A-Z]{3,6})\s+(.+?)\s+(\d+\s*[A-Za-z]?(?:[-/]\d+)?)\s*$", ref)
    if m:
        out.update(
            referentie_prefix=m.group(1),
            vestigingscode=m.group(2),
            klantnaam=m.group(3).strip(),
            huisnummer=m.group(4).replace(" ", ""),
        )
    else:
        out["klantnaam"] = ref.strip()
    return out


def _runs(row):
    d=np.diff(np.concatenate([[0],row.astype(int),[0]]))
    s=np.where(d==1)[0]; e=np.where(d==-1)[0]
    return list(zip(s,e))
def crop_sketch(im):
    """Knipt de kozijnschets (zonder maatlijnen) uit de positietekening."""
    a=np.asarray(im.convert('L')); g=a<110; g2=a<215
    H,W=g.shape
    top=None
    for y in range(int(H*0.6)):
        rs=[r for r in _runs(g[y]) if r[1]-r[0]>=W*0.08]
        if rs:
            # longest run within the next 6 rows
            cand=[]
            for yy in range(y,min(y+6,H)):
                cand+= [(e-s,yy,s,e-1) for s,e in _runs(g[yy])]
            top=max(cand); break
    if not top: return im
    L,y0,x0,x1=top
    band=max(8,int(H*0.012))
    y=y0; y1=y0
    while y<H:
        if g2[y:y+band, x0:x1+1].any():
            y1=y; y+=1
        else:
            break
    pad=4
    return im.crop((max(x0-pad,0),max(y0-pad,0),min(x1+pad,W),min(y1+pad,H)))


def _raw_image(page, im) -> Optional[Image.Image]:
    """De ingesloten afbeelding op volle resolutie (voor OCR); anders een render op die resolutie."""
    try:
        w, h = im["srcsize"]
        data = im["stream"].get_data()
        if len(data) == w * h * 3:
            return Image.frombytes("RGB", (w, h), data)
        return Image.open(io.BytesIO(im["stream"].get_rawdata())).convert("RGB")
    except Exception:
        try:
            res = int(72 * im["srcsize"][0] / (im["x1"] - im["x0"]))
            return page.crop((im["x0"], im["top"], im["x1"], im["bottom"])).to_image(resolution=res).original.convert("RGB")
        except Exception:
            return None


def _extract_drawings(pdf: pdfplumber.PDF, n_pos: int, resolution: int = 400) -> dict[int, tuple[str, Optional[Image.Image]]]:
    """Rendert de positietekening (links in elk positieblok) als PNG base64, plus de originele afbeelding."""
    drawings: dict[int, tuple[str, Optional[Image.Image]]] = {}
    counter = 0
    for page in pdf.pages:
        headers = sorted(w["top"] for w in page.extract_words() if w["text"] == "Pos.")
        for top in headers:
            counter += 1
            cand = [
                im for im in page.images
                if im["top"] > top and im["top"] - top < 60
                and (im["x1"] - im["x0"]) < 250 and (im["bottom"] - im["top"]) > 60
            ]
            if not cand:
                continue
            im = cand[0]
            bbox = (max(im["x0"] - 2, 0), max(im["top"] - 2, 0),
                    min(im["x1"] + 2, page.width), min(im["bottom"] + 2, page.height))
            buf = io.BytesIO()
            img = page.crop(bbox).to_image(resolution=resolution).original.convert("RGB")
            sketch = crop_sketch(img)
            if sketch.size[0] > 30 and sketch.size[1] > 30:
                img = sketch
            img.save(buf, format="PNG", optimize=True)
            drawings[counter] = (base64.b64encode(buf.getvalue()).decode(), _raw_image(page, im))
    return drawings


def parse_uw_rapport(pdf: pdfplumber.PDF, text: str) -> Report:
    text = PAGE_HEADER.sub("", text)
    order = re.search(r"Ordernummer:\s*(\S+)", text)
    ref = re.search(r"Referentie:\s*(.+)", text)
    date = re.search(r"^Date\s+(\d{4}-\d{2}-\d{2})", text, re.M)
    raw = "\n".join(p.extract_text() or "" for p in pdf.pages)
    besteller = re.search(r"\(\w+\)\s*\|\s*(.+)$", raw, re.M)

    parts = POS_SPLIT.split(text)
    posities = []
    # parts = [voor, nr, stuks, blok, nr, stuks, blok, ...]
    for i in range(1, len(parts), 3):
        posities.append(_parse_block(int(parts[i]), int(parts[i + 1]), parts[i + 2]))

    drawings = _extract_drawings(pdf, len(posities))
    for idx, p in enumerate(posities, start=1):
        png, raw = drawings.get(idx, (None, None))
        p.tekening_png_b64 = png
        p.tekening_breedte_mm, p.tekening_hoogte_mm = lees_zwarte_maten(raw, p.breedte_mm, p.hoogte_mm)

    reference = ref.group(1).strip() if ref else None
    return Report(
        formaat="uw_rapport_v1",
        ordernummer=order.group(1) if order else None,
        referentie=reference,
        rapportdatum=date.group(1) if date else None,
        besteller=besteller.group(1).strip() if besteller else None,
        posities=posities,
        **_parse_reference(reference),
    )


SUPPORTED_FORMATS = {
    "uw_rapport_v1": lambda t: "Uw rapport" in t and "Pos." in t and "Oppervlakte" in t,
}


def parse_report(source) -> Report:
    """source = pad of bytes van de PDF."""
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    with pdfplumber.open(source) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        if SUPPORTED_FORMATS["uw_rapport_v1"](text):
            return parse_uw_rapport(pdf, text)
    raise ValueError(
        "Onbekend rapportformaat. Ondersteund: " + ", ".join(SUPPORTED_FORMATS)
        + ". Stuur een voorbeeld van dit leveranciersformaat door zodat het toegevoegd kan worden."
    )
