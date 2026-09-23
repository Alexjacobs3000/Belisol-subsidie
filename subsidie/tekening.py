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
