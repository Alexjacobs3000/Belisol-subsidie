"""
RVO-meldcodelijst Hoogrendementsglas (glas, isolerende deuren) als opzoektabel.

Bijwerken als RVO een nieuwe lijst publiceert:
    python -m subsidie.meldcodelijst "Meldcodelijst Hoogrendementsglas - <maand> <jaar>.xlsx"
Dit schrijft subsidie/data/meldcodelijst.json, dat de verwerker gebruikt.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional

DATA = Path(__file__).with_name("data") / "meldcodelijst.json"

CATEGORIE = {
    "triple glas u <= 0,7": "triple_glas",
    "hr++ glas u <= 1,2": "hr_plus_plus_glas",
    "isolerende deur ud <= 1,0": "deur_hoog",
    "isolerende deur ud <= 1,5": "deur_laag",
    "glas u <= 2,0": "monument_glas_2_0",
    "glas u <= 5,8": "monument_glas_5_8",
}


def _eur(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.-]", "", str(v)).replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None  # '.' = geen apart bedrag


def import_xlsx(path: str | Path, out: Path = DATA) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Meldcodes"]
    items = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not r[0]:
            continue
        cat_txt = str(r[8] or "").strip()
        items.append({
            "meldcode": str(r[0]).strip(),
            "hoofdmeldcode": (str(r[1]).strip() or None) if r[1] else None,
            "merk": str(r[2] or "").strip(),
            "model": str(r[3] or "").strip(),
            "u": float(r[4]) if isinstance(r[4], (int, float)) else None,
            "bedrag_enkel": _eur(r[5]),
            "bedrag_meerdere": _eur(r[6]),
            "bedrag_monument": _eur(r[7]),
            "categorie_rvo": cat_txt,
            "categorie": CATEGORIE.get(cat_txt.lower()),
            "woningtype": str(r[9] or "").strip(),
            "is_hoofdmeldcode": str(r[3] or "").startswith("(H)"),
        })
    data = {"bron": Path(path).name, "geimporteerd": date.today().isoformat(), "aantal": len(items), "items": items}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    load.cache_clear()
    return data


@lru_cache(maxsize=1)
def load(path: str | None = None) -> dict:
    p = Path(path) if path else DATA
    if not p.exists():
        return {"bron": None, "items": []}
    return json.loads(p.read_text(encoding="utf-8"))


def by_code(code: str) -> Optional[dict]:
    return next((i for i in load()["items"] if i["meldcode"] == code), None)


def tarieven_per_categorie() -> dict:
    """Meest voorkomende bedragen per categorie (enkel / meerdere / monument)."""
    from collections import Counter
    out = {}
    for cat in set(i["categorie"] for i in load()["items"] if i["categorie"]):
        rows = [i for i in load()["items"] if i["categorie"] == cat]
        pick = lambda k: Counter(r[k] for r in rows).most_common(1)[0][0]
        out[cat] = {"enkel": pick("bedrag_enkel"), "meerdere": pick("bedrag_meerdere"), "monument": pick("bedrag_monument")}
    return out


# --------------------------------------------------------------------------
# Glas matchen op samenstelling
# --------------------------------------------------------------------------

def glass_layers(txt: str) -> list[float]:
    """'4TF/16w/4/16w/4TFO' -> [4,16,4,16,4];  'Thermofloat 4mm/16mmAr/Float 4mm/16mmAr/VSG 33.2' -> [4,16,4,16,33.2]."""
    parts = re.split(r"[/\\]", txt)
    out = []
    for p in parts:
        m = re.search(r"(\d+(?:[.,]\d)?)", p)
        if m:
            out.append(float(m.group(1).replace(",", ".")))
    return out


def find_glass(merk: str, categorie: str, samenstelling: str) -> Optional[dict]:
    lay = glass_layers(samenstelling)
    if not lay:
        return None
    for i in load()["items"]:
        if i["merk"].lower() != merk.lower() or i["categorie"] != categorie or i["is_hoofdmeldcode"]:
            continue
        if glass_layers(i["model"]) == lay:
            return i
    return None


def hoofdmeldcode(merk: str, categorie: str) -> Optional[dict]:
    return next((i for i in load()["items"] if i["merk"].lower() == merk.lower()
                 and i["categorie"] == categorie and i["is_hoofdmeldcode"]), None)


# --------------------------------------------------------------------------
# Deuren matchen op merk + serie + Ud
# --------------------------------------------------------------------------

def _ud_range(model: str) -> Optional[tuple[float, float]]:
    t = model.replace(",", ".")
    m = re.search(r"\((\d\.\d+)\s*tot\s*(\d\.\d+)\)", t)
    if m:
        a, b = sorted(map(float, m.groups()))
        return a, b
    m = re.search(r"vanaf\s*(\d\.\d+)", t)
    if m:
        return 0.0, float(m.group(1))
    m = re.search(r"(\d\.\d+)\s*-\s*(\d\.\d+)", t)
    if m:
        a, b = sorted(map(float, m.groups()))
        return a, b
    return None


def find_door(merk: str, categorie: str, ud: float, serie: Optional[str] = None) -> tuple[Optional[dict], list[dict]]:
    """Geeft (gekozen, kandidaten). Gekozen is None als er niet precies één passende deur is."""
    cands = [i for i in load()["items"] if i["merk"].lower() == merk.lower() and i["categorie"] == categorie]
    if serie:
        cands = [i for i in cands if serie.lower() in i["model"].lower()]
    fit = []
    for i in cands:
        rng = _ud_range(i["model"])
        if rng is None or rng[0] - 1e-9 <= ud <= rng[1] + 1e-9:
            fit.append(i)
    return (fit[0] if len(fit) == 1 else None), fit


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    d = import_xlsx(sys.argv[1])
    print(f"{d['aantal']} meldcodes geïmporteerd uit {d['bron']} -> {DATA}")
