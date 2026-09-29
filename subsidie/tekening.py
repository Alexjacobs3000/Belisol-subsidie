"""
Leest de zwarte maatvoering uit de kozijnschets van het Uw-rapport (OCR met Tesseract).

Certix-rapporten tonen bij de schets meerdere maatlijnen in verschillende kleuren. De zwarte maat die het
dichtst bij het element staat is de maat ZONDER aanslag (flens); de groene is mét aanslag. Voor de subsidie
geldt de zwarte maat. De zwarte totaalmaat is het grootste zwarte getal: horizontaal = breedte,
verticaal (gedraaide tekst) = hoogte.

OCR kan getallen verkeerd lezen (bv. een maatlijn door de tekst). Daarom wordt alleen een waarde aanvaard die
plausibel is t.o.v. de tabelmaat: niet groter dan de tabelmaat en hoogstens MAX_VERSCHIL_MM kleiner.
Zonder Tesseract of zonder plausibele waarde geeft de functie None terug.
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np
from PIL import Image

MAX_VERSCHIL_MM = 100  # zwarte maat mag hoogstens zoveel kleiner zijn dan de tabelmaat
_GETAL = re.compile(r"\d{2,5}(,\d)?")

try:
    import pytesseract
    pytesseract.get_tesseract_version()
    OCR_BESCHIKBAAR = True
except Exception:  # pakket of tesseract-binary ontbreekt
    OCR_BESCHIKBAAR = False


def _lange_runs(m: np.ndarray, minlen: int) -> np.ndarray:
    """Masker van pixels die deel zijn van een horizontale run van minstens minlen pixels."""
    out = np.zeros_like(m)
    for y in np.where(m.sum(1) >= minlen)[0]:
        d = np.diff(np.concatenate([[0], m[y].astype(np.int8), [0]]))
        for a, b in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
            if b - a >= minlen:
                out[y, a:b] = True
    return out


def zwart_masker(img: Image.Image, lijnen_weg: bool = True) -> Image.Image:
    """Alleen zwarte pixels (donker, niet gekleurd) als zwart-op-wit; lange maat-/kaderlijnen eruit."""
    a = np.asarray(img.convert("RGB")).astype(int)
    mx, mn = a.max(2), a.min(2)
    m = (mx < 110) & ((mx - mn) < 35)
    if lijnen_weg:
        L = int(min(m.shape) * 0.12)
        m &= ~(_lange_runs(m, L) | _lange_runs(m.T, L).T)
    out = np.full(m.shape, 255, np.uint8)
    out[m] = 0
    return Image.fromarray(out)


def _getallen(img: Image.Image) -> list[float]:
    d = pytesseract.image_to_data(img, config="--psm 11 -c tessedit_char_whitelist=0123456789,",
                                  output_type=pytesseract.Output.DICT)
    return [float(t.strip().replace(",", ".")) for t in d["text"] if _GETAL.fullmatch(t.strip())]


def _kies(kandidaten: list[float], tabelmaat: float) -> Optional[float]:
    ok = [k for k in kandidaten if tabelmaat - MAX_VERSCHIL_MM <= k <= tabelmaat]
    return max(ok) if ok else None


def lees_zwarte_maten(img: Image.Image, breedte_tabel: float, hoogte_tabel: float) -> tuple[Optional[float], Optional[float]]:
    """(breedte, hoogte) van de zwarte totaalmaat in mm, of None per richting als die niet betrouwbaar leesbaar is."""
    if not OCR_BESCHIKBAAR or img is None:
        return None, None
    b = h = None
    for lijnen_weg in (True, False):  # eerst zonder maatlijnen; lukt dat niet, dan het origineel
        z = zwart_masker(img, lijnen_weg)
        if b is None:
            b = _kies(_getallen(z), breedte_tabel)
        if h is None:
            h = _kies(_getallen(z.rotate(-90, expand=True)), hoogte_tabel)
        if b is not None and h is not None:
            break
    return b, h


# ---------------------------------------------------------------------------------------------------------------
# Offerte-schetsen (Sales): verhouding glas / paneel binnen een element.
#
# Offerte-tekeningen zijn niet op schaal: de oppervlakte komt uit de tekst (B x H), uit de schets halen we alleen
# de VERHOUDING glas : paneel. Glas is blauw getekend; een paneel is een groot ondoorzichtig vlak binnen het kader
# (profielen zijn smal en verdwijnen bij erosie), of een glasvak met het label 'p'.
# ---------------------------------------------------------------------------------------------------------------

def _erode(m: np.ndarray, r: int) -> np.ndarray:
    """Pixel blijft als het (2r+1)²-venster rond de pixel volledig True is (via integraalbeeld)."""
    ii = np.pad(m.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    H, W = m.shape
    y0, y1 = np.clip(np.arange(H) - r, 0, H), np.clip(np.arange(H) + r + 1, 0, H)
    x0, x1 = np.clip(np.arange(W) - r, 0, W), np.clip(np.arange(W) + r + 1, 0, W)
    s = ii[y1][:, x1] - ii[y0][:, x1] - ii[y1][:, x0] + ii[y0][:, x0]
    return s == (y1 - y0)[:, None] * (x1 - x0)[None, :]


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
    return ~_erode(~m, r)


def _reconstrueer(seed: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Alle pixels van m die (4-verbonden) aan seed vastzitten."""
    while True:
        n = seed.copy()
        n[1:] |= seed[:-1]; n[:-1] |= seed[1:]; n[:, 1:] |= seed[:, :-1]; n[:, :-1] |= seed[:, 1:]
        n &= m
        if (n == seed).all():
            return seed
        seed = n


def _aan_rand(m: np.ndarray) -> np.ndarray:
    seed = np.zeros_like(m)
    seed[0, :], seed[-1, :], seed[:, 0], seed[:, -1] = m[0, :], m[-1, :], m[:, 0], m[:, -1]
    return _reconstrueer(seed, m)


def _componenten(m: np.ndarray, min_px: int = 1) -> list[np.ndarray]:
    rest, out = m.copy(), []
    while rest.any():
        y, x = np.argwhere(rest)[0]
        s = np.zeros_like(m)
        s[y, x] = True
        c = _reconstrueer(s, rest)
        rest &= ~c
        if c.sum() >= min_px:
            out.append(c)
    return out


def _langste_run(m: np.ndarray) -> tuple[int, int, int]:
    """(lengte, rij, start) van de langste aaneengesloten True-run over alle rijen."""
    best = (0, 0, 0)
    for y in np.where(m.sum(1) > best[0])[0]:
        d = np.diff(np.concatenate([[0], m[y].astype(np.int8), [0]]))
        s, e = np.where(d == 1)[0], np.where(d == -1)[0]
        i = int(np.argmax(e - s))
        if e[i] - s[i] > best[0]:
            best = (int(e[i] - s[i]), int(y), int(s[i]))
    return best


def _p_labels(sub: np.ndarray, glas: np.ndarray) -> list[tuple[int, int]]:
    """Posities van het label 'p' (paneel) in een glasvak: hoger dan breed, lus bovenaan, omringd door glas."""
    h, _ = glas.shape
    out = []
    for c in _componenten(sub.max(2) < 120, 15):
        ys, xs = np.where(c)
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        bh, bw = y1 - y0 + 1, x1 - x0 + 1
        if not (bh >= 1.3 * bw and 0.025 * h <= bh <= 0.10 * h):
            continue
        box = ~c[y0:y1 + 1, x0:x1 + 1]
        gat = box & ~_aan_rand(box)  # ingesloten wit = de lus van de p
        if gat.sum() < 3 or np.where(gat)[0].mean() > bh * 0.6:
            continue
        ring = _dilate(c, 3) & ~c
        if glas[ring].mean() >= 0.5:
            out.append((int(xs.mean()), int(ys.mean())))
    return out


def _splitsingen(cijfers: str, totaal: float, tol: float = 2) -> Optional[list[str]]:
    """Splits een cijferreeks ('5001050') in ≥ 2 maten (2-4 cijfers) die samen het totaal zijn (500 + 1050)."""
    def rec(rest, acc):
        if not rest:
            return acc if len(acc) >= 2 and abs(sum(int(x) for x in acc) - totaal) <= tol else None
        for n in (2, 3, 4):
            deel = rest[:n]
            if len(deel) == n and deel[0] != "0":
                r = rec(rest[n:], acc + [deel])
                if r:
                    return r
        return None
    return rec(cijfers, [])


def _maatketen(img: Image.Image, totaal: float, as_: str, van: int, tot: int) -> Optional[list[tuple[float, float]]]:
    """Deelmaten langs één as (onder = breedte, rechts = hoogte) als [(maat_mm, midden_px), …] waarvan de som het
    totaal is, of None. Coördinaten in pixels van het originele beeld."""
    if not OCR_BESCHIKBAAR:
        return None
    f = 3
    big = zwart_masker(img.resize((img.width * f, img.height * f), Image.LANCZOS), False)
    if as_ == "v":
        big = big.rotate(-90, expand=True)
    d = pytesseract.image_to_data(big, config="--psm 11 -c tessedit_char_whitelist=0123456789",
                                  output_type=pytesseract.Output.DICT)
    woorden = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        if not t.isdigit() or len(t) < 2:
            continue
        woorden.append((t, d["left"][i] / f, d["width"][i] / f, (d["top"][i] + d["height"][i] / 2) / f))
    # 1) één woord dat opgesplitst de som geeft; 2) meerdere woorden op dezelfde regel
    kandidaten = []
    for t, links, breed, midy in woorden:
        delen = _splitsingen(t, totaal)
        if delen:
            pos, keten = links, []
            for deel in delen:
                w = breed * len(deel) / len(t)
                keten.append((float(deel), pos + w / 2))
                pos += w
            kandidaten.append(keten)
    for t, _, _, midy in woorden:
        rij = sorted([w for w in woorden if abs(w[3] - midy) < 6 and len(w[0]) <= 4], key=lambda w: w[1])
        if len(rij) >= 2 and abs(sum(int(w[0]) for w in rij) - totaal) <= 2:
            kandidaten.append([(float(w[0]), w[1] + w[2] / 2) for w in rij])
    for keten in kandidaten:
        if as_ == "v":  # gedraaid beeld (90° rechtsom): x_rot = H - y  ->  terug naar y, van boven naar onder
            keten = [(m, img.height - c) for m, c in reversed(keten)]
        if all(van - 5 <= c <= tot + 5 for _, c in keten):
            return keten
    return None


def _as_mm(n_px: int, totaal: float, keten: Optional[list[tuple[float, float]]], start: int) -> np.ndarray:
    """mm per pixel langs een as: lineair, of stuksgewijs volgens de deelmaten (tekening niet op schaal)."""
    per = np.full(n_px, totaal / n_px)
    if not keten:
        return per
    # grenzen tussen de segmenten: elk getal staat in het midden van zijn segment; schat de grenzen vanaf links
    # en vanaf rechts en neem het gemiddelde
    midden = [c - start for _, c in keten]
    links = [0.0]
    for c in midden[:-1]:
        links.append(2 * c - links[-1])
    rechts = [float(n_px)]
    for c in reversed(midden[1:]):
        rechts.insert(0, 2 * c - rechts[0])
    grenzen = [0.0] + [(a + b) / 2 for a, b in zip(links[1:], rechts[:-1])] + [float(n_px)]
    if any(b <= a for a, b in zip(grenzen, grenzen[1:])):
        return per
    for (maat, _), a, b in zip(keten, grenzen, grenzen[1:]):
        i0, i1 = int(round(a)), int(round(b))
        if i1 > i0:
            per[i0:i1] = maat / (i1 - i0)
    return per * (totaal / per.sum())


def vulling_uit_schets(img: Image.Image, breedte_mm: Optional[float] = None,
                       hoogte_mm: Optional[float] = None) -> Optional[dict]:
    """Glas en paneel in een offerte-schets als fractie van het element (kader inbegrepen), of None.

    De tekening is niet op schaal: met de deelmaten in de tekening (bv. 500 | 1050 = 1550) wordt elke pixel per
    segment naar mm omgerekend. Zonder deelmaten wordt lineair geschaald. De totale oppervlakte komt altijd uit de
    tekst van de offerte."""
    try:
        a = np.asarray(img.convert("RGB")).astype(int)
        H, W = a.shape[:2]
        donker = a.max(2) < 90
        # element = bereik van de totaalmaatlijnen (onder: breedte, rechts: hoogte)
        lh, _, xh = _langste_run(donker[H // 2:])
        lv, _, yv = _langste_run(donker[:, W // 2:].T)
        if lh < W * 0.2 or lv < H * 0.2:
            return None
        sub = a[yv:yv + lv, xh:xh + lh]
        glas = (sub[..., 2] > 170) & (sub[..., 2] - sub[..., 0] > 35)
        if glas.mean() < 0.05:
            return None
        # glasvakken met label 'p' zijn panelen
        labels = _p_labels(sub, glas)
        glas_p = np.zeros_like(glas)
        for g in _componenten(glas, 50):
            ys, xs = np.where(g)
            if any(xs.min() <= x <= xs.max() and ys.min() <= y <= ys.max() for x, y in labels):
                glas_p |= g
        # ondoorzichtige panelen: grote vlakken (zonder omlijning) die na erosie overblijven en niet aan de rand zitten
        r = max(3, int(min(sub.shape[:2]) * 0.03))
        kern = _erode(~glas & ~(sub.max(2) < 25), r)
        kern &= ~_aan_rand(kern)
        paneel = (_dilate(kern, r + 1) & ~glas) | glas_p
        # oppervlakte per pixel in mm² (stuksgewijs volgens de deelmaten)
        heeft_paneel = bool(paneel.any())  # de deelmaten (OCR) zijn alleen nodig om panelen te verdelen
        kx = _maatketen(img, breedte_mm, "h", xh, xh + lh) if breedte_mm and heeft_paneel else None
        ky = _maatketen(img, hoogte_mm, "v", yv, yv + lv) if hoogte_mm and heeft_paneel else None
        mx = _as_mm(lh, breedte_mm or lh, kx, xh)
        my = _as_mm(lv, hoogte_mm or lv, ky, yv)
        opp = np.outer(my, mx)
        totaal = opp.sum()
        return {"glas": float(opp[glas & ~glas_p].sum() / totaal), "paneel": float(opp[paneel].sum() / totaal),
                "p_labels": len(labels), "deelmaten_b": [m for m, _ in kx] if kx else None,
                "deelmaten_h": [m for m, _ in ky] if ky else None}
    except Exception:
        return None
