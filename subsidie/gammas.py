"""
Gamma's (reeksen) van Belisol: reeksnaam → leverancier, materiaal en producttype.

Bron: Excel-export 'Gammas.xlsx' uit het productbeheer (kolommen z_3_Serie, zzcSupplierName, z_2_Material,
_1_Type_Txt). Wordt gebruikt door de Sales-module om in een offerte te herkennen welke elementen van welke
leverancier komen.

Bijwerken:  python -m subsidie.gammas <Gammas.xlsx> [Gamma_lijst_alternatief.xlsx]
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


def _rijen(path: str | Path) -> list[dict]:
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True).active
    rows = ws.iter_rows(values_only=True)
    kop = next(rows)
    return [dict(zip(kop, r)) for r in rows]


def _materiaal_uit_tekst(t: str) -> str:
    low = (t or "").lower()
    for m, pat in (("PVC-ALU", r"pvc[- /]alu"), ("PVC", r"\bpvc\b"), ("ALU", r"alumin"), ("HOUT", r"\bhout\b|\bbois\b")):
        if re.search(pat, low):
            return m
    return ""


def _naam_uit_tekst(t: str, kw: str) -> str:
    """'Buitenschrijnwerk in PVC, reeks: Classix 70.' -> 'Classix 70' (NL 'reeks', FR 'gamme')."""
    m = re.search(kw + r"\s*:?\s*(.+?)\s*(?:,|\.$|$)", t or "")
    return _norm(m.group(1)).rstrip(".") if m else ""


def import_xlsx(*paths: str | Path, out: Path = DATA) -> dict:
    """Importeert één of meer gamma-exports en voegt ze samen (op __id):

    - 'Gammas.xlsx' (kolommen z_3_Serie, zzcSupplierName, z_2_Material, _1_Type_Txt, z_6_Vleugel): de reeksnaam;
    - 'Gamma_lijst_alternatief.xlsx' (SSC_SUP::Name en offerteteksten 'reeks: …' / 'gamme: …'): de namen zoals ze in
      offertes staan (bv. 'Duoslide 137', 'SlideS', 'Classix Bloc 118') en reeksen die in de eerste export ontbreken.

    Namen die eigenlijk een vleugel zijn ('reeks: Luna') worden niet als reeks opgenomen."""
    per_id: dict = {}
    vleugels: set[str] = set()
    for path in paths:
        for r in _rijen(path):
            d = per_id.setdefault(r.get("__id"), {"namen": [], "leverancier": "", "materiaal": "", "type": ""})
            if "z_3_Serie" in r:  # Gammas.xlsx
                if _norm(r.get("z_3_Serie")) not in ("", "None"):
                    d["namen"].insert(0, _norm(r["z_3_Serie"]))
                d["leverancier"] = _norm(r.get("zzcSupplierName")) or d["leverancier"]
                d["materiaal"] = _norm(r.get("z_2_Material")) or d["materiaal"]
                vleugels.add(_norm(r.get("z_6_Vleugel")).lower())
            if "SSC_SUP::Name" in r:  # alternatieve export met offerteteksten
                nl_t, fr_t = r.get("SSC_SSC.lng~settings1::Text"), r.get("SSC_SSC.lng~settings2::Text")
                d["namen"] += [n for n in (_naam_uit_tekst(nl_t, "reeks"), _naam_uit_tekst(fr_t, "gamme")) if n]
                d["leverancier"] = d["leverancier"] or _norm(r.get("SSC_SUP::Name"))
                d["materiaal"] = d["materiaal"] or _materiaal_uit_tekst(f"{nl_t} {fr_t}")
            d["type"] = _norm(r.get("_1_Type_Txt")) or d["type"]
    # leveranciersnamen gelijk schrijven ('WND' -> 'WnD')
    spelling = {}
    for d in per_id.values():
        spelling.setdefault(d["leverancier"].lower(), d["leverancier"])
        if any(c.islower() for c in d["leverancier"]):
            spelling[d["leverancier"].lower()] = d["leverancier"]
    items = set()
    for d in per_id.values():
        lev = spelling.get(d["leverancier"].lower(), d["leverancier"])
        if not lev or lev == "None":
            continue
        for i, naam in enumerate(dict.fromkeys(d["namen"])):
            if naam.lower() in vleugels or len(naam) < 3:
                continue
            items.add((naam, lev, d["materiaal"], d["type"], "alias" if i else "reeks"))
    data = {
        "bron": " + ".join(Path(p).name for p in paths),
        "geimporteerd": date.today().isoformat(),
        "items": [dict(serie=s, leverancier=l, materiaal=m, type=t, soort=k) for s, l, m, t, k in sorted(items)],
    }
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    load.cache_clear()
    _patronen.cache_clear()
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
    naam = _norm(serie).lower()
    kand = [i for i in load()["items"] if i["serie"].lower() == naam]
    if not kand and len(naam) >= 4:  # reeksfamilie, bv. 'Classix Blok' -> 'Classix Blok 118', 'Classix Blok 118 HVP', …
        kand = [i for i in load()["items"] if i["serie"].lower().startswith(naam + " ")]
    if not kand:
        return None

    def score(i):
        return ((leverancier or "").lower() == i["leverancier"].lower()) * 4 \
            + ((type_ or "").lower() == i["type"].lower()) * 2 + ((materiaal or "").lower() == i["materiaal"].lower())
    beste = max(kand, key=score)
    levs = {i["leverancier"] for i in kand}
    return dict(beste, dubbelzinnig=len(levs) > 1 and not leverancier, alternatieven=sorted(levs))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Gebruik: python -m subsidie.gammas <Gammas.xlsx> [Gamma_lijst_alternatief.xlsx]")
    d = import_xlsx(*sys.argv[1:])
    print(f"{len(d['items'])} gamma-regels geïmporteerd uit {d['bron']} → {DATA}")
