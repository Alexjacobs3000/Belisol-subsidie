"""
HTTP-service voor n8n / Zapier / Make.

Starten:   uvicorn api:app --host 0.0.0.0 --port 8000
Docs:      http://localhost:8000/docs

Endpoints
  POST /verwerk        multipart: bestand=<PDF> + optionele klantvelden
                       -> JSON (maatregelen, m², U-waarden, meldcodes, bedrag, waarschuwingen)
                       ?met_pdf=true voegt 'rapport_pdf_base64' toe
  POST /verwerk/pdf    zelfde invoer -> direct het klantrapport als application/pdf
  GET  /health
"""
import base64
import os
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Header
from fastapi.responses import Response

from subsidie.parser import parse_report
from subsidie.rules import evaluate
from subsidie.report import render_pdf

app = FastAPI(title="Belisol ISDE-subsidieverwerker", version="1.0")
API_KEY = os.getenv("SUBSIDIE_API_KEY")  # optioneel: zet een sleutel om de service af te schermen


def _check_key(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Ongeldige of ontbrekende X-API-Key")


def _run(data: bytes, klant: dict):
    try:
        report = parse_report(data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return report, evaluate(report, {k: v for k, v in klant.items() if v})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/verwerk")
async def verwerk(
    bestand: UploadFile = File(..., description="Thermisch Uw-rapport (PDF)"),
    naam: Optional[str] = Form(None), aanhef: Optional[str] = Form(None),
    straat: Optional[str] = Form(None), huisnummer: Optional[str] = Form(None),
    postcode: Optional[str] = Form(None), plaats: Optional[str] = Form(None),
    email: Optional[str] = Form(None), uitvoeringsdatum: Optional[str] = Form(None),
    scenario: Optional[str] = Form(None, description="enkel | meerdere | monument"),
    met_pdf: bool = False,
    x_api_key: Optional[str] = Header(None),
):
    _check_key(x_api_key)
    report, result = _run(await bestand.read(), locals_klant(naam, aanhef, straat, huisnummer, postcode, plaats, email, uitvoeringsdatum, scenario))
    if met_pdf:
        result["rapport_pdf_base64"] = base64.b64encode(render_pdf(result, report)).decode()
        result["rapport_bestandsnaam"] = bestandsnaam(result)
    return result


@app.post("/verwerk/pdf")
async def verwerk_pdf(
    bestand: UploadFile = File(...),
    naam: Optional[str] = Form(None), aanhef: Optional[str] = Form(None),
    straat: Optional[str] = Form(None), huisnummer: Optional[str] = Form(None),
    postcode: Optional[str] = Form(None), plaats: Optional[str] = Form(None),
    email: Optional[str] = Form(None), uitvoeringsdatum: Optional[str] = Form(None),
    scenario: Optional[str] = Form(None, description="enkel | meerdere | monument"),
    x_api_key: Optional[str] = Header(None),
):
    _check_key(x_api_key)
    report, result = _run(await bestand.read(), locals_klant(naam, aanhef, straat, huisnummer, postcode, plaats, email, uitvoeringsdatum, scenario))
    return Response(
        render_pdf(result, report), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{bestandsnaam(result)}"',
                 "X-Subsidie-Status": result["status"],
                 "X-Subsidie-Waarschuwingen": str(len(result["waarschuwingen"]))},
    )


def locals_klant(naam, aanhef, straat, huisnummer, postcode, plaats, email, uitvoeringsdatum, scenario=None):
    return dict(naam=naam, aanhef=aanhef, straat=straat, huisnummer=huisnummer,
                postcode=postcode, plaats=plaats, email=email, uitvoeringsdatum=uitvoeringsdatum, scenario=scenario)


def bestandsnaam(result: dict) -> str:
    k = result["klant"]
    naam = "".join(c for c in f"{k.get('naam') or 'klant'} {k.get('huisnummer') or ''}".strip() if c.isalnum() or c in " -_")
    return f"Subsidie-overzicht {naam}.pdf"
