# CLAUDE.md — Belisol ISDE-subsidieverwerker

Context voor Claude Code. Taal: Nederlands (gebruiker Alex, Belisol). Code en commits mogen Nederlands zijn.

## Doel
Belisol plaatst kozijnen/deuren in Nederland. Klanten vragen achteraf ISDE-subsidie aan bij RVO en hebben daarvoor
gegevens van Belisol nodig. Deze tool leest het thermische rapport van de leverancier (+ bestelling) en maakt een
klantdocument (PDF) met alle gegevens voor het RVO-formulier en indicatieve subsidiebedragen.

## Opbouw
- `streamlit_app.py` — webapp (Streamlit Cloud) met switch bovenaan: **Administratie** en **Sales**. Beide pagina's
  worden altijd getekend (de andere verborgen via CSS) zodat uploads blijven staan; sessiesleutels per module
  (`k("…")` → `adm_…`/`sal_…`); in de pagina's `return` i.p.v. `st.stop()`.
- Administratie: gebruiker uploadt **bestelling** (definitieve opmeting, vaak
  handgeschreven scan) én **Uw-rapport**; bevestigt flens; vult klantgegevens in; downloadt PDF (+ JSON).
  Bij Certix vervalt stap 2 (bestelling controleren) en is de bestelling optioneel (maat komt uit de tekening).
- Sales (`subsidie/sales.py`, `templates/sales.html`): offerte → elementen (reeks → leverancier via
  `subsidie/gammas.py` + `data/gammas.json`, maten, aantal, vulling) → bewerkbare tabel → zelfde `rules.evaluate`
  (zonder meldcodes, tarief per categorie) → PDF "Subsidie-indicatie". Gedeelde huisstijl: `templates/_stijl.html`.
- `api.py` (FastAPI) / `verwerk.py` (CLI) — zelfde verwerking voor n8n/Zapier.
- `subsidie/parser.py` — PDF → posities (afmetingen, Uw/Uf/Ug, panelen, glas, kozijnschets).
- `subsidie/rules.py` — subsidieregels, flens, meldcodes, bedragen, waarschuwingen.
- `subsidie/meldcodelijst.py` + `subsidie/data/meldcodelijst.json` — RVO-meldcodelijst Hoogrendementsglas
  (april 2026, 1925 codes). Bijwerken: `python -m subsidie.meldcodelijst <xlsx>`.
- `subsidie/templates/rapport.html` + `report.py` — klantrapport (WeasyPrint, Belisol-huisstijl, 3 pagina's).
- `subsidie/config.json` — alle instelbare regels (flens, leveranciers, meldcodes, vestigingen/KVK, panelen).
- Klantdata (PDF's, .eml) NOOIT committen (`samples/` staat in .gitignore).

## Beslissingen (door Alex bevestigd)
- Geen montagedatum (nergens). Altijd actuele tarieven uit de meldcodelijst; min 3 m², max 45 m².
- Geen keuze "situatie klant": het document toont altijd 3 indicatiebedragen in een tabel op pagina 1:
  één maatregel / meerdere maatregelen / monumentale woning, met totaal. JSON `bedrag_indicatief` = één maatregel.
- Kader "verklaring uitvoerend bedrijf" standaard uit (`config.json → rapport`).
- Flens/aanslag/aanslagprofiel/T-kader telt in NL niet mee: aftrekken van de maatvoering.
- Meldcode deuren op basis van U-waarde (Ud); glas via hoofdmeldcode van het merk.
- Leveranciers NL: Profel en Oknoplast (Certix = Oknoplast-glas).

## Sales / offertes (door Alex bevestigd)
- Belisol-offerte: per element pagina "Specificaties / Post 1A - Kozijn" met Afmetingen, Aantal, Detailomschrijving
  (reeks, glas, panelen, Up), Opmerkingen en schets. Tekst via pypdfium2 (pdfplumber is te traag op deze PDF's).
- Altijd de maatvoering uit de tekst gebruiken. Tekeningen zijn NIET op schaal: alleen bij verdelingen met panelen
  de verdeling bepalen uit schets + deelmaten (label 'p' in een glasvak = paneel).
- Sales toont GEEN meldcodes (die volgen pas na bestelling uit het thermisch rapport). Tarief per m² hangt alleen
  van de categorie af → reeks/leverancier zijn in Sales informatief (geen waarschuwingen over de leverancier).
- Gamma-lijst = Gammas.xlsx (reeksnamen) + Gamma_lijst_alternatief.xlsx (offertenamen 'reeks:'/'gamme:', bv.
  Duoslide 137 → Profel, SlideS → Schmidt); vleugelnamen (Luna, Pure …) worden niet als reeks opgenomen.
- Pagina "Indicatie vierkante meters voor subsidie" (thermisch rapport) is meestal afwezig en is gewoon 90 % van de
  oppervlakte → niet gebruiken.

## Domeinregels (zie config.json)
- Glas: Ug ≤ 0,7 triple (€111 / €222), ≤ 1,2 HR++ (€25 / €50 / monument €92).
- Deur: Ud ≤ 1,0 (€111 / €222), ≤ 1,5 (€25 / €50 / €92). Alleen samen met HR++/triple glas.
- Monumentkolom = "meerdere maatregelen in monument". Geen monumentbedrag in lijst (triple, deur hoog) →
  bedrag meerdere maatregelen getoond met voetnoot (AANNAME, nog te bevestigen).
- Certix maatvoering: in de schets van het Uw-rapport is de **zwarte** maat (dichtst bij het element) = zonder
  aanslag, **groen** = met aanslag. Altijd de zwarte totaalmaat gebruiken (breedte én hoogte), niet de tabel.
  Wordt gelezen met OCR (`subsidie/tekening.py`, Tesseract; `config.json → flens → maat_uit_tekening`);
  onleesbaar/onplausibel → tabelmaat + waarschuwing. Kader 101.331/101.333 = aanslag. Deurvleugel 103.446 = deur.
- Referentie in rapport: `C26 <vestigingscode> <klantnaam> <huisnummer>`; BNNIM = BeliNijmegen B.V., KVK 22062177.

## Open punten
- Flens per zijde: nu 4 zijden gelijk bij formaten met maten mét flens. Nodig: regels per zijde (geen aftrek
  onderkant deur met dorpel/slijtkader, koppelnaad/"afgefreesde aanslag", "frame stomp") + Profel-voorbeeld.
- Profel thermisch rapport en Oknoplast-rapport: nog geen voorbeelden → parser nog niet ondersteund.
- Meldcode KA31763 (Certix-deur, uit infomail) staat niet in de RVO-lijst → waarschuwing; laten bevestigen.
- Monumentbedrag triple/deur hoog en monument met één maatregel (HR++ mogelijk €46) laten bevestigen.
- KVK-nummers andere vestigingen (bv. Tilburg).
- Sales: deur zonder Ud: nu glas + panelen (bevestigen).

## Testen
`python verwerk.py <uw_rapport.pdf> --naam X --pdf uit.pdf` en `streamlit run streamlit_app.py`.
Referentiecase Bisheshar 2063: ramen 11,29 m² (KA30682), deur 2,15 m² Ud 0,97; totaal €1.443 / €2.886 / €2.886.
