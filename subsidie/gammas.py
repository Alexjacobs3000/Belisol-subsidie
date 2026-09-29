"""
Gamma's (reeksen) van Belisol: reeksnaam → leverancier, materiaal en producttype.

Bron: Excel-export 'Gammas.xlsx' uit het productbeheer (kolommen z_3_Serie, zzcSupplierName, z_2_Material,
_1_Type_Txt). Wordt gebruikt door de Sales-module om in een offerte te herkennen welke elementen van welke
leverancier komen.

Bijwerken:  python -m subsidie.gammas <Gammas.xlsx>
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional

DATA = Path(__file__).parent / "data" / "gammas.json"


def _norm(s: str) -> str:
    return " ".join(str(s or "").split())


def import_xlsx(path: str | Path, out: Path = DATA) -> dict:
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True).active
    rows = ws.iter_rows(values_only=True)
    kop = {k: i for i, k in enumerate(next(rows))}
    items = set()
    for r in rows:
        serie, lev = _norm(r[kop["z_3_Serie"]]), _norm(r[kop["zzcSupplierName"]])
        if not serie or serie == "None" or not lev or lev == "None":
            continue
        items.add((serie, lev, _norm(r[kop["z_2_Material"]]), _norm(r[kop["_1_Type_Txt"]])))
    data = {
        "bron": Path(path).name,
        "geimporteerd": date.today().isoformat(),
        "items": [dict(serie=s, leverancier=l, materiaal=m, type=t) for s, l, m, t in sorted(items)],
    }
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    load.cache_clear()
    return data


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {"items": []}


def series() -> list[str]:
    return sorted({i["serie"] for i in load()["items"]}, key=str.lower)


def leveranciers() -> list[str]:
    return sorted({i["leverancier"] for i in load()["items"]}, key=str.lower)


@lru_cache(maxsize=1)
def _patronen() -> list[tuple[str, re.Pattern]]:
    """Regex per reeksnaam, langste eerst ('Classix 70 S' vóór 'Classix 70'). Spaties/'+' flexibel."""
    out = []
    for s in sorted(series(), key=len, reverse=True):
        delen = [re.escape(d) for d in s.split()]
        out.append((s, re.compile(r"(?<![\w])" + r"\s*".join(delen) + r"(?![\w+])", re.I)))
    return out


def vind_series(tekst: str) -> list[tuple[int, str]]:
    """Alle reeksnamen in een tekst als (positie, reeks), zonder overlap (langste match wint)."""
    bezet: list[tuple[int, int]] = []
    hits = []
    for s, pat in _patronen():
        for m in pat.finditer(tekst or ""):
            if any(a < m.end() and m.start() < b for a, b in bezet):
                continue
            bezet.append((m.start(), m.end()))
            hits.append((m.start(), s))
    return sorted(hits)


def kies(serie: str, type_: Optional[str] = None, materiaal: Optional[str] = None,
         leverancier: Optional[str] = None) -> Optional[dict]:
    """Beste gamma-regel voor een reeks; bij meerdere leveranciers wint de regel die het best past op
    leverancier/type/materiaal (anders de eerste, met 'dubbelzinnig' = True)."""
    kand = [i for i in load()["items"] if i["serie"].lower() == (serie or "").lower()]
    if not kand:
        return None

    def score(i):
        return ((leverancier or "").lower() == i["leverancier"].lower()) * 4 \
            + ((type_ or "").lower() == i["type"].lower()) * 2 + ((materiaal or "").lower() == i["materiaal"].lower())
    beste = max(kand, key=score)
    levs = {i["leverancier"] for i in kand}
    return dict(beste, dubbelzinnig=len(levs) > 1 and not leverancier, alternatieven=sorted(levs))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Gebruik: python -m subsidie.gammas <Gammas.xlsx>")
    d = import_xlsx(sys.argv[1])
    print(f"{len(d['items'])} gamma-regels geïmporteerd uit {d['bron']} → {DATA}")
