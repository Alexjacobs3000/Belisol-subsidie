"""Hulpfuncties voor de bestelling / definitieve opmeting (vaak een gescande, handgeschreven PDF)."""
from __future__ import annotations

import io
import re
from typing import Optional

import pdfplumber


def lees_bestelling(data: bytes, resolutie: int = 110, max_paginas: int = 6) -> dict:
    """Geeft pagina-afbeeldingen (voor visuele controle) en - als de PDF tekst bevat - herkende velden."""
    out = {"paginas": 0, "gescand": True, "afbeeldingen": [], "velden": {}}
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        out["paginas"] = len(pdf.pages)
        tekst = "\n".join((p.extract_text() or "") for p in pdf.pages)
        out["gescand"] = len(tekst.strip()) < 50
        for page in pdf.pages[:max_paginas]:
            buf = io.BytesIO()
            page.to_image(resolution=resolutie).original.save(buf, format="PNG")
            out["afbeeldingen"].append(buf.getvalue())
    if not out["gescand"]:
        out["velden"] = _velden_uit_tekst(tekst)
    return out


def _zoek(pat: str, t: str) -> Optional[str]:
    m = re.search(pat, t, re.I | re.M)
    return m.group(1).strip() if m else None


def _velden_uit_tekst(t: str) -> dict:
    v = {
        "naam": _zoek(r"Naam\s*/?\s*Name\s*:?\s*(.+)$", t),
        "referentie": _zoek(r"Ref(?:erentie)?\.?\s*(?:Belisol)?\s*:?\s*(.+)$", t),
        "systeem": _zoek(r"(CERTIX\s*\d+|Certix\s*\d+|P\s?\d{3,4})", t),
    }
    low = t.lower()
    if any(w in low for w in ("aanslag", "flens", "t-kader")):
        v["flens_genoemd"] = True
    return {k: val for k, val in v.items() if val}
