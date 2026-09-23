"""
Belisol ISDE-subsidieverwerker — webapp (Streamlit).

De gebruiker uploadt de bestelling (definitieve opmeting) en het Uw-rapport van de leverancier,
controleert flens en klantgegevens, en downloadt het subsidie-overzicht voor de klant (PDF).

Lokaal:  streamlit run streamlit_app.py
"""
from __future__ import annotations

import hmac
import json
from pathlib import Path

import streamlit as st

from subsidie.parser import parse_report
from subsidie.rules import evaluate, nl, eur
from subsidie.report import render_pdf
from subsidie.bestelling import lees_bestelling
from subsidie import meldcodelijst as mcl

ASSETS = Path(__file__).parent / "subsidie" / "assets"

st.set_page_config(page_title="Belisol subsidie-overzicht", page_icon=str(ASSETS / "belisol_logo.png"), layout="wide")

st.markdown("""
<style>
  .block-container { padding-top: 2rem; max-width: 1200px; }
  h1, h2, h3 { color: #00346B; }
  div[data-testid="stMetricValue"] { color: #00346B; font-weight: 800; }
  .stepnr { display:inline-block; width:1.7rem; height:1.7rem; border-radius:50%; background:#00346B; color:#fff;
            text-align:center; line-height:1.7rem; font-weight:800; margin-right:.5rem; }
  .muted { color:#5E6E82; font-size:.9rem; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- toegang
def check_password() -> bool:
    """Optioneel wachtwoord via Streamlit secrets (APP_PASSWORD). Zonder secret is de app open."""
    try:
        secret = st.secrets.get("APP_PASSWORD")
    except Exception:
        secret = None
    if not secret:
        return True
    if st.session_state.get("auth_ok"):
        return True
    col, _ = st.columns([1, 2])
    with col:
        st.image(str(ASSETS / "belisol_logo.png"), width=90)
        pw = st.text_input("Wachtwoord", type="password")
        if pw:
            if hmac.compare_digest(pw, str(secret)):
                st.session_state["auth_ok"] = True
                st.rerun()
            st.error("Onjuist wachtwoord.")
    return False


if not check_password():
    st.stop()


_stapnr = 0


def step(titel: str):
    global _stapnr
    _stapnr += 1
    st.markdown(f"<h3><span class='stepnr'>{_stapnr}</span>{titel}</h3>", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def _parse(uw_bytes: bytes):
    return parse_report(uw_bytes)


@st.cache_data(show_spinner=False)
def _bestelling(b: bytes):
    return lees_bestelling(b)


# ---------------------------------------------------------------- opnieuw beginnen
st.session_state.setdefault("dossier", 0)
dossier = st.session_state["dossier"]  # onderdeel van de widget-keys: ophogen = lege uploads en formulier


def _markeer_gedownload():
    st.session_state["pdf_gedownload"] = True


def _opnieuw_beginnen():
    """Wist uploads, klantgegevens en resultaat (ook uit de cache) en begint een nieuw dossier."""
    for k in ("result", "pdf", "sig", "pdf_gedownload"):
        st.session_state.pop(k, None)
    st.session_state["dossier"] += 1
    _parse.clear()
    _bestelling.clear()


@st.dialog("Nieuw dossier starten?")
def _bevestig_opnieuw():
    if not st.session_state.get("pdf"):  # net gedownload en gewist -> hele app opnieuw tekenen (sluit dialoog)
        st.rerun()
    st.warning("Het subsidie-overzicht van dit dossier is **nog niet gedownload**. "
               "Bij opnieuw beginnen worden de geüploade bestanden en het resultaat gewist.")
    st.download_button("⬇  Eerst downloaden, dan opnieuw beginnen", data=st.session_state["pdf"],
                       file_name=f"{st.session_state['bestandsnaam']}.pdf", mime="application/pdf",
                       type="primary", width="stretch", on_click=_opnieuw_beginnen)
    a, b = st.columns(2)
    if a.button("Niet downloaden, wissen", width="stretch"):
        _opnieuw_beginnen()
        st.rerun()
    if b.button("Annuleren", width="stretch"):
        st.rerun()


def opnieuw_knop(label: str, key: str, **kw):
    """Knop 'nieuw dossier'; vraagt eerst om te downloaden als het overzicht nog niet is gedownload."""
    if st.button(label, key=key, icon=":material/restart_alt:", **kw):
        if st.session_state.get("pdf") and not st.session_state.get("pdf_gedownload"):
            _bevestig_opnieuw()
        else:
            _opnieuw_beginnen()
            st.rerun()


# ---------------------------------------------------------------- kop
c1, c2, c3 = st.columns([1, 7, 2], vertical_alignment="center")
with c1:
    st.image(str(ASSETS / "belisol_logo.png"), width=80)
with c2:
    st.title("Subsidie-overzicht ISDE")
    st.markdown("<span class='muted'>Upload het Uw-rapport van de leverancier en (behalve bij Certix) de bestelling. "
                "De app berekent de subsidiegegevens en maakt het overzicht voor de klant.</span>", unsafe_allow_html=True)
with c3:
    if st.session_state.get(f"uw_{dossier}") or st.session_state.get(f"best_{dossier}"):
        opnieuw_knop("Nieuw dossier", key="opnieuw_kop", width="stretch",
                     help="Wist de geüploade bestanden en klantgegevens en begint opnieuw.")

with st.sidebar:
    st.image(str(ASSETS / "belisol_logo.png"), width=70)
    st.markdown("**Belisol subsidieverwerker**")
    lijst = mcl.load()
    st.caption(f"Meldcodelijst: {lijst.get('bron') or 'niet geladen'}"
               + (f" ({lijst.get('aantal')} codes)" if lijst.get("aantal") else ""))
    st.caption("Ondersteund: Uw-rapport Certix (Oknoplast). Profel/Oknoplast-formaten volgen.")
    st.caption("Bestanden worden alleen tijdens deze sessie verwerkt en niet opgeslagen.")

# ---------------------------------------------------------------- 1. uploads
step("Documenten uploaden")
u1, u2 = st.columns(2)
with u1:
    f_best = st.file_uploader("Bestelling / definitieve opmeting (PDF) — niet nodig bij Certix", type=["pdf"], key=f"best_{dossier}")
with u2:
    f_uw = st.file_uploader("Uw-rapport / thermisch rapport leverancier (PDF)", type=["pdf"], key=f"uw_{dossier}")

if not f_uw:
    st.info("Upload het **Uw-rapport** (en, behalve bij Certix, de bestelling) om verder te gaan.")
    st.stop()


try:
    with st.spinner("Uw-rapport inlezen…"):
        report = _parse(f_uw.getvalue())
except ValueError as e:
    st.error(f"Het Uw-rapport kon niet gelezen worden: {e}")
    st.stop()

# Certix: de maat zonder aanslag staat in de schets van het Uw-rapport (zwarte maat) -> bestelling controleren is niet nodig
is_certix = bool(report.posities) and all(p.systeem.upper().startswith("CERTIX") for p in report.posities)

if not f_best and not is_certix:
    st.info("Dit is geen Certix-rapport: upload ook de **bestelling** om de flens te controleren.")
    st.stop()

best = _bestelling(f_best.getvalue()) if f_best else {"afbeeldingen": [], "gescand": False, "velden": {}}

# nieuwe bestanden -> oud resultaat weggooien
sig = (f_uw.name, f_uw.size, f_best.name if f_best else None, f_best.size if f_best else None)
if st.session_state.get("sig") != sig:
    st.session_state["sig"] = sig
    st.session_state.pop("result", None)
    st.session_state.pop("pdf", None)
    st.session_state.pop("pdf_gedownload", None)

rapport_flens = any(
    any(k.split()[0] in ("101.331", "101.333") or "aanslag" in k.lower() for k in p.materialen.get("KADER", []))
    for p in report.posities)

# ---------------------------------------------------------------- 2. controle bestelling (niet bij Certix)
if is_certix:
    # flens volgt het rapport; de maat zonder aanslag wordt uit de schets gelezen (zwarte maat)
    flens_bevestigd, flens_mm = None, None
    st.caption(f"Certix-rapport (order `{report.ordernummer}`, {len(report.posities)} posities): de maat zonder aanslag wordt uit de tekening gelezen (zwarte maat) — controle van de bestelling is niet nodig.")
else:
    step("Bestelling controleren")
    b1, b2 = st.columns([3, 2])
    with b1:
        if best["afbeeldingen"]:
            tabs = st.tabs([f"Pagina {i+1}" for i in range(len(best["afbeeldingen"]))])
            for t, img in zip(tabs, best["afbeeldingen"]):
                with t:
                    st.image(img, use_container_width=True)
    with b2:
        st.markdown(f"**Uw-rapport**: order `{report.ordernummer}` · referentie `{report.referentie}`")
        st.markdown(f"Systeem: **{', '.join(sorted({p.systeem for p in report.posities}))}** · {len(report.posities)} posities")
        if best["gescand"]:
            st.caption("De bestelling is een scan: controleer de gegevens hieronder visueel.")
        elif best["velden"]:
            st.caption("Herkend in de bestelling: " + ", ".join(f"{k}: {v}" for k, v in best["velden"].items()))

        st.markdown("**Flens / aanslag / T-kader**")
        st.caption("In Nederland telt de flens niet mee en wordt aan alle zijden van de maat afgetrokken. "
                   "Kijk op de bestelling of 'aanslag' (flens) is aangeduid.")
        keuze = st.radio(
            "Staat er op de bestelling een flens (aanslag) aangeduid?",
            ["Ja", "Nee"], index=0 if rapport_flens else 1, horizontal=True,
            help=f"Volgens het Uw-rapport: {'ja (kaderprofiel met aanslag)' if rapport_flens else 'nee'}.")
        flens_bevestigd = keuze == "Ja"
        if flens_bevestigd != rapport_flens:
            st.warning("Dit wijkt af van het kaderprofiel in het Uw-rapport. De keuze van de bestelling wordt gevolgd.")
        flens_mm = None
        if flens_bevestigd:
            flens_mm = st.number_input("Flensbreedte per zijde (mm)", min_value=0.0, max_value=100.0, value=0.0, step=1.0,
                                       help="Alleen nodig als het leveranciersrapport de maten mét flens geeft.") or None

# ---------------------------------------------------------------- 3. klantgegevens
step("Klantgegevens")
with st.form(f"klant_{dossier}"):
    k1, k2, k3 = st.columns(3)
    naam = k1.text_input("Naam klant", value=best["velden"].get("naam") or report.klantnaam or "")
    aanhef = k2.text_input("Aanhef", value=f"Geachte heer/mevrouw {report.klantnaam or ''}".strip())
    email = k3.text_input("E-mail (optioneel)")
    k4, k5, k6, k7 = st.columns([3, 1, 1.3, 2])
    straat = k4.text_input("Straat")
    huisnr = k5.text_input("Huisnr.", value=report.huisnummer or "")
    postcode = k6.text_input("Postcode")
    plaats = k7.text_input("Plaats")
    ok = st.form_submit_button("Subsidie-overzicht maken", type="primary", use_container_width=True)

if not ok and "result" not in st.session_state:
    st.stop()

klant = {
    "naam": naam or None, "aanhef": aanhef or None, "email": email or None, "straat": straat or None,
    "huisnummer": huisnr or None, "postcode": postcode or None, "plaats": plaats or None,
    "flens_bevestigd": flens_bevestigd, "flens_mm": flens_mm,
}
if ok:
    with st.spinner("Berekenen en rapport opmaken…"):
        result = evaluate(report, klant)
        pdf = render_pdf(result, report)
    st.session_state["result"], st.session_state["pdf"] = result, pdf
    st.session_state["pdf_gedownload"] = False
result, pdf = st.session_state["result"], st.session_state["pdf"]

# ---------------------------------------------------------------- 4. resultaat
step("Resultaat")
rb = result["totaal"]["richtbedragen"]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Geïsoleerd oppervlak", f"{nl(result['totaal']['m2'])} m²")
m2.metric("Eén maatregel", eur(rb["enkel"]) if rb["enkel"] is not None else "–")
m3.metric("Meerdere maatregelen", eur(rb["meerdere"]) if rb["meerdere"] is not None else "–")
m4.metric("Monumentale woning", eur(rb["monument"]) if rb["monument"] is not None else "–")

if result["status"] == "ok" and not result["waarschuwingen"]:
    st.success("Alles in orde — het overzicht kan naar de klant.")
else:
    st.warning("Controleer de volgende punten voordat je het overzicht verstuurt:")
    for w in result["waarschuwingen"]:
        st.markdown(f"- {w}")

st.markdown("**Maatregelen voor het RVO-formulier**")
st.dataframe(
    [{"Maatregel": m["label"], "Netto m²": nl(m["m2"]), f"U-waarde": f"{m['u_label']} {nl(m['u_waarde'])}",
      "Meldcode": m["meldcode"] or "Overige", "Posities": ", ".join(map(str, m["posities"])),
      "Eén maatregel": eur(m["bedragen"]["enkel"]["totaal"]) if m["bedragen"]["enkel"]["totaal"] is not None else "–",
      "Meerdere": eur(m["bedragen"]["meerdere"]["totaal"]) if m["bedragen"]["meerdere"]["totaal"] is not None else "–"}
     for m in result["maatregelen"]],
    hide_index=True, use_container_width=True)

with st.expander("Detail per positie"):
    st.dataframe(
        [{"Pos.": p["positie"], "Omschrijving": p["omschrijving"], "Type": p["type"],
          "B × H netto": f"{nl(p['flens']['netto_breedte_mm'], 0)} × {nl(p['flens']['netto_hoogte_mm'], 0)}",
          "Maat uit": p["flens"].get("maat_bron", "tabel"),
          "m²": nl(p["oppervlakte_m2"]), "Ug": nl(p["ug"]), "Uw": nl(p["uw"]),
          "Flens": p["flens"]["toegepast"], "Maatregel": p["categorie"] or "niet subsidiabel"}
         for p in result["posities"]], hide_index=True, use_container_width=True)

bestandsnaam = f"Subsidie-overzicht {result['klant']['naam'] or 'klant'} {result['klant']['huisnummer'] or ''}".strip()
st.session_state["bestandsnaam"] = bestandsnaam
d1, d2 = st.columns([2, 1])
d1.download_button("⬇  Download subsidie-overzicht (PDF)", data=pdf, file_name=f"{bestandsnaam}.pdf",
                   mime="application/pdf", type="primary", use_container_width=True, on_click=_markeer_gedownload)
d2.download_button("Download gegevens (JSON)", data=json.dumps(result, ensure_ascii=False, indent=2),
                   file_name=f"{bestandsnaam}.json", mime="application/json", use_container_width=True)

st.divider()
n1, n2 = st.columns([2, 1], vertical_alignment="center")
if st.session_state.get("pdf_gedownload"):
    n1.success("Subsidie-overzicht gedownload. Je kunt een nieuw dossier starten.")
else:
    n1.caption("Klaar met deze klant? Download eerst het overzicht en start dan een nieuw dossier.")
with n2:
    opnieuw_knop("Nieuw dossier starten", key="opnieuw_onder", width="stretch")
