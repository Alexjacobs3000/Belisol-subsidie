# Belisol ISDE-subsidieverwerker

Leest het thermische **Uw-rapport** van de leverancier, past de ISDE-regels uit de
subsidiebrochure (FEB26) toe en levert:

1. **JSON** met per maatregel de m², U-waarde, meldcode, tarief en indicatief bedrag, plus
   platte `mail_velden` voor e-mailtemplates en een lijst `waarschuwingen`;
2. een **klantrapport (PDF, 3 pagina's)** in Belisol-huisstijl met alle gegevens voor het
   RVO-formulier, richtbedragen, een overzicht per element (met kozijnschets) en een checklist.
   Een verklaring van het uitvoerend bedrijf (deur/paneel met hoge isolatiewaarde) kan aangezet
   worden via `config.json → rapport.verklaring_uitvoerend_bedrijf_tonen` (standaard uit).

## Structuur

```
api.py                  HTTP-service (FastAPI) voor n8n / Zapier / Make
verwerk.py              commandline-gebruik
subsidie/config.json    grenswaarden, flens, leveranciers (merk per systeem), handmatige meldcodes, vestigingen (KVK)
subsidie/data/meldcodelijst.json   RVO-meldcodelijst Hoogrendementsglas (geïmporteerd uit Excel)
subsidie/meldcodelijst.py          import + opzoeken van meldcodes en bedragen
subsidie/parser.py      PDF → posities (afmetingen, Uw/Uf/Ug, panelen, glas, tekening)
subsidie/rules.py       subsidieregels → maatregelen, bedragen, waarschuwingen
subsidie/report.py      HTML-template → PDF (WeasyPrint)
subsidie/templates/     rapport.html (ontwerp klantrapport)
samples/                voorbeeld Bisheshar 2063 (invoer, JSON en PDF)
```

## Webapp (Streamlit)

`streamlit_app.py` is de webversie voor collega's:

1. upload de **bestelling** (definitieve opmeting) én het **Uw-rapport** van de leverancier;
2. controleer de bestelling (pagina's worden getoond) en bevestig of er een flens/aanslag is.
   **Bij Certix wordt deze stap overgeslagen** (en is de bestelling niet nodig): de maat zonder
   aanslag wordt uit de tekening in het Uw-rapport gelezen;
3. vul de klantgegevens aan;
4. bekijk de uitkomst en waarschuwingen en **download het subsidie-overzicht (PDF)** (en optioneel de JSON).

Lokaal: `streamlit run streamlit_app.py`

### Publiceren op Streamlit Community Cloud

1. Zet deze map in een GitHub-repo (liefst **privé**).
2. Ga naar share.streamlit.io → *Create app* → kies de repo, branch `main`, bestand `streamlit_app.py`.
3. `requirements.txt` (Python) en `packages.txt` (systeembibliotheken voor de PDF-opmaak) worden automatisch geïnstalleerd.
4. Zet onder *Settings → Secrets* een wachtwoord:
   ```toml
   APP_PASSWORD = "kies-een-sterk-wachtwoord"
   ```
   Zonder dit secret is de app open voor iedereen met de link.

Klantdocumenten (map `samples/`) staan in `.gitignore` en gaan niet mee naar GitHub.

## Lokaal draaien (commandline)

```bash
pip install -r requirements.txt
python verwerk.py samples/2616563371_2_uw_value.pdf --naam Bisheshar \
    --straat Saltshof --plaats Wijchen \
    --json uit.json --pdf subsidie.pdf
```

## Als service (voor n8n / Zapier)

```bash
docker build -t belisol-subsidie .
docker run -p 8000:8000 -e SUBSIDIE_API_KEY=kies-een-sleutel belisol-subsidie
```

n8n Cloud en Zapier kunnen zelf geen Python-pakketten installeren; zet de container daarom
op een server (bv. Azure Container Apps, een VPS of naast een self-hosted n8n).

| Endpoint | Invoer (multipart/form-data) | Uitvoer |
|---|---|---|
| `POST /verwerk` | `bestand` (PDF) + optioneel `naam`, `aanhef`, `straat`, `huisnummer`, `postcode`, `plaats`, `email` | JSON. Met `?met_pdf=true` ook `rapport_pdf_base64` |
| `POST /verwerk/pdf` | idem | het klantrapport als PDF-bestand |
| `GET /health` | – | `{"status":"ok"}` |

Header `X-API-Key` is verplicht als `SUBSIDIE_API_KEY` is ingesteld.

### Voorbeeldflow in n8n

1. **Trigger**: bv. Outlook/IMAP-trigger op de mailbox waar de leveranciersrapporten binnenkomen
   (filter op bijlage `*_uw_value.pdf`), of een trigger vanuit Connect.
2. **Klantgegevens ophalen** (naam, adres, e-mail) — de referentie in het rapport
   (`C26 BNNIM Bischeshar 2063`) levert vestigingscode, naam en huisnummer; zoek hiermee het dossier op.
3. **HTTP Request** → `POST {url}/verwerk?met_pdf=true`, body *Form-Data*:
   `bestand` = *n8n Binary File* (de bijlage), overige velden uit stap 2.
4. **IF** `status == "ok"` en `waarschuwingen` leeg → automatisch door; anders → taak/mail naar
   de administratie ter controle.
5. **Convert to File** (`rapport_pdf_base64` → binair) en **Send Email** naar de klant met de
   PDF als bijlage; gebruik `mail_velden` in de mailtekst.

## Wat de regels doen

* **Raam of deur**: deur als het vleugelartikel in `deur_herkenning.vleugel_artikels` staat
  (nu `103.446`), een trefwoord als "deur" voorkomt, of — als laatste redmiddel, met
  waarschuwing — hoog/smal element met vleugel.
* **Glas**: hoogste Ug van het element ≤ 0,7 → triple glas; ≤ 1,2 → HR++; anders niet subsidiabel.
* **Deur**: Uw (Ud) ≤ 1,0 → hoog tarief; ≤ 1,5 → laag. Alleen samen met HR++/triple glas.
* **Flens (aanslag / T-kader)**: telt in Nederland niet mee en gaat aan alle zijden van de maat af.
  Per systeem staat in `config.json → flens` welke kaderprofielen een flens hebben, hoe breed die is
  en of het leveranciersrapport de maten al zonder flens geeft. **Certix**: in de schets van het
  Uw-rapport staan de maat zonder aanslag (zwart, het dichtst bij het element) en mét aanslag (groen).
  De zwarte totaalmaat wordt met OCR (Tesseract) gelezen en altijd gebruikt, ongeacht de tabelmaat.
  Alleen een waarde die niet groter is dan de tabelmaat en hoogstens 100 mm kleiner wordt aanvaard;
  anders wordt de tabelmaat gebruikt met een waarschuwing. Voor een formaat dat de maat mét flens
  geeft, wordt (B − 2·flens) × (H − 2·flens) gerekend. Flens gevonden maar breedte onbekend → waarschuwing.
* **Meldcode** (uit de RVO-meldcodelijst): het merk per systeem staat in `config.json → leveranciers`
  (Certix → Oknoplast-glas; systeemnamen met "Profel", "P8000" … → Profel).
  - Glas: de **hoofdmeldcode** van dat merk per categorie (Oknoplast triple = KA30682, Profel triple = KA30687).
    Komt de glassamenstelling exact overeen met een product, dan staat de specifieke code ook in de JSON
    (`glas_meldcodes_specifiek`, bv. 4/16/4/16/4 → KA29140, Ug 0,6).
  - Deur: merk + serie + Ud (bv. Profel P8000: Ud ≤ 1,0 → KA27765, 1,0–1,2 → KA27764, 1,2–1,5 → KA27763).
  - Niets gevonden → handmatige regels in `config.json → meldcodes`; daarna waarschuwing met mogelijke codes.
  - Code uit een handmatige regel die niet in de RVO-lijst staat → waarschuwing + `controle_nodig`.
* **Richtbedragen**: per maatregel en totaal voor drie situaties, met bedragen uit de meldcodelijst:
  één maatregel, meerdere maatregelen (verdubbeling) en monumentale woning. Staat er geen apart
  monumentbedrag in de lijst (triple glas, deur hoog), dan wordt het bedrag bij meerdere maatregelen
  getoond (met voetnoot). Het klantrapport toont altijd alle drie in één tabel; `bedrag_indicatief` in de JSON = één maatregel.
* **Oppervlakte**: netto maat per positie (na eventuele flensaftrek) × aantal stuks (kozijn + glas).
  Panelen tellen mee in het kozijn (`panelen.modus = meetellen_bij_kozijn`); op `apart` zetten
  maakt er een aparte maatregel "isolerend paneel" van.
* **Bedrag**: m² per maatregel afgerond (≥ 0,50 omhoog) × tarief uit de actuele meldcodelijst.
  Minimum 3 m², maximum 45 m². Er wordt geen montagedatum gebruikt.

## Nieuwe meldcodelijst van RVO

```bash
python -m subsidie.meldcodelijst "Meldcodelijst Hoogrendementsglas - <maand> <jaar>.xlsx"
```

## Nieuwe leverancier toevoegen

Stuur een voorbeeldrapport; er komt dan een extra parse-functie bij in `SUPPORTED_FORMATS`
(`subsidie/parser.py`). De regels en het klantrapport blijven hetzelfde.
