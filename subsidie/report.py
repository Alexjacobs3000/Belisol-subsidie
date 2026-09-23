"""Genereert het subsidie-overzicht voor de klant (HTML → PDF)."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .parser import Report
from .rules import nl, eur

HERE = Path(__file__).parent
MAANDEN = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
           "augustus", "september", "oktober", "november", "december"]
KORT = {
    "triple_glas": "Triple glas", "hr_plus_plus_glas": "HR++ glas",
    "deur_hoog": "Isolerende deur", "deur_laag": "Isolerende deur",
    "paneel_hoog": "Isolerend paneel", "paneel_laag": "Isolerend paneel",
}


def datum_nl(iso: Optional[str]) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {MAANDEN[d.month - 1]} {d.year}"


def eur0(x) -> str:
    if x is None:
        return "–"
    return "€ " + nl(x, 0) if float(x).is_integer() else eur(x)


def eurr(x) -> str:
    """Afgerond op hele euro's, voor totalen."""
    return "–" if x is None else "€ " + nl(round(x), 0)


def render_html(result: dict, report: Report) -> str:
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
    tpl = env.get_template("rapport.html")
    drawings = {p.positie: p.tekening_png_b64 for p in report.posities}
    posities = [dict(p, tekening=drawings.get(p["positie"])) for p in result["posities"]]
    codes = [m["code"] for m in result["maatregelen"]]
    return tpl.render(
        **{k: v for k, v in result.items() if k != "posities"},
        posities=posities,
        assets=(HERE / "assets").as_uri(),
        vandaag=datum_nl(date.today().isoformat()),
        heeft_deur=any(c.startswith("deur") for c in codes),
        heeft_triple="triple_glas" in codes,
        verklaring=[m for m in result["maatregelen"] if m["verklaring_bouwbedrijf_nodig"]],
        korte_label=KORT,
        nl=nl, eur=eur, eur0=eur0, eurr=eurr, datum_nl=datum_nl,
        rb=result["totaal"]["richtbedragen"],
    )


def render_pdf(result: dict, report: Report) -> bytes:
    from weasyprint import HTML
    html = render_html(result, report)
    return HTML(string=html, base_url=str(HERE)).write_pdf()
