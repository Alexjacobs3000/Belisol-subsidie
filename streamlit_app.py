"""
Belisol ISDE-subsidieverwerker — webapp (Streamlit), met twee modules (switch bovenaan):

- Administratie: de gebruiker uploadt het Uw-rapport van de leverancier (en, behalve bij Certix, de bestelling),
  controleert flens en klantgegevens, en downloadt het subsidie-overzicht voor de klant (PDF).
  Bij Certix vervalt de controle van de bestelling: de maat zonder aanslag komt uit de tekening in het Uw-rapport.
- Sales: de verkoper uploadt een offerte; de app herkent de elementen (reeks → leverancier, maten, vulling),
  de verkoper controleert ze en downloadt een subsidie-indicatie voor de klant (PDF).

Lokaal:  streamlit run streamlit_app.py
"""
from __future__ import annotations

import hmac
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from subsidie.parser import parse_report
from subsidie.rules import evaluate, nl, eur, load_config
from subsidie.report import render_pdf
from subsidie.bestelling import lees_bestelling
from subsidie import meldcodelijst as mcl
from subsidie import gammas, sales

ASSETS = Path(__file__).parent / "subsidie" / "assets"

st.set_page_config(page_title="Belisol subsidie-overzicht", page_icon=str(ASSETS / "belisol_logo.png"), layout="wide")

st.markdown("""
<style>
  .block-container { padding-top: 3.5rem; max-width: 1200px; }
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


S = st.session_state

# ---------------------------------------------------------------- module (switch bovenaan)
MODULES = {"Administratie": "adm", "Sales": "sal"}
S.setdefault("modus", "Administratie")
if S.get("modus") is None:  # segmented control kan uitgevinkt worden -> vorige keuze houden
    S["modus"] = S.get("modus_vorig", "Administratie")
st.segmented_control("Module", list(MODULES), key="modus", label_visibility="collapsed", width="stretch",
                     help="Administratie: subsidie-overzicht na bestelling (technische documenten). "
                          "Sales: subsidie-indicatie op basis van een offerte.")
MODUS = S["modus"] or S.get("modus_vorig", "Administratie")
S["modus_vorig"] = MODUS
M = MODULES[MODUS]  # zichtbare module
P = M               # module die op dit moment getekend wordt (beide pagina's worden getekend, zie onderaan)


def k(naam: str, mod: str | None = None) -> str:
    """Sleutel in de sessie per module, zodat wisselen van module niets wist."""
    return f"{mod or P}_{naam}"


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


@st.cache_data(show_spinner=False)
def _offerte(b: bytes):
    return sales.lees_offerte(b)


# ---------------------------------------------------------------- opnieuw beginnen
for _m in MODULES.values():
    S.setdefault(k("dossier", _m), 0)  # onderdeel van de widget-keys: ophogen = lege uploads en formulier
DOC = {"adm": "subsidie-overzicht", "sal": "subsidie-indicatie"}


def _markeer_gedownload(mod: str):
    S[k("pdf_gedownload", mod)] = True


def _opnieuw_beginnen(mod: str):
    """Wist uploads, invoer en resultaat van een module (ook uit de cache) en begint een nieuw dossier."""
    for n in ("result", "pdf", "sig", "pdf_gedownload", "elementen", "invoer_sig"):
        S.pop(k(n, mod), None)
    S[k("dossier", mod)] += 1
    if mod == "adm":
        _parse.clear()
        _bestelling.clear()
    else:
        _offerte.clear()


@st.dialog("Nieuw dossier starten?")
def _bevestig_opnieuw(mod: str):
    if not S.get(k("pdf", mod)):  # net gedownload en gewist -> hele app opnieuw tekenen (sluit dialoog)
        st.rerun()
    st.warning(f"De {DOC[mod]} van dit dossier is **nog niet gedownload**. "
               "Bij opnieuw beginnen worden de geüploade bestanden en het resultaat gewist.")
    st.download_button("⬇  Eerst downloaden, dan opnieuw beginnen", data=S[k("pdf", mod)],
                       file_name=f"{S[k('bestandsnaam', mod)]}.pdf", mime="application/pdf",
                       type="primary", width="stretch", on_click=_opnieuw_beginnen, args=(mod,))
    a, b = st.columns(2)
    if a.button("Niet downloaden, wissen", width="stretch"):
        _opnieuw_beginnen(mod)
        st.rerun()
    if b.button("Annuleren", width="stretch"):
        st.rerun()


def opnieuw_knop(label: str, key: str, mod: str, **kw):
    """Knop 'nieuw dossier'; vraagt eerst om te downloaden als het document nog niet is gedownload."""
    if st.button(label, key=key, icon=":material/restart_alt:", **kw):
        if S.get(k("pdf", mod)) and not S.get(k("pdf_gedownload", mod)):
            _bevestig_opnieuw(mod)
        else:
            _opnieuw_beginnen(mod)
            st.rerun()


# ---------------------------------------------------------------- kop
c1, c2, c3 = st.columns([1, 7, 2], vertical_alignment="center")
with c1:
    st.image(str(ASSETS / "belisol_logo.png"), width=80)
with c2:
    if M == "adm":
        st.title("Subsidie-overzicht ISDE")
        st.markdown("<span class='muted'>Administratie — upload het Uw-rapport van de leverancier en (behalve bij Certix) "
                    "de bestelling. De app berekent de subsidiegegevens en maakt het overzicht voor de klant.</span>",
                    unsafe_allow_html=True)
    else:
        st.title("Subsidie-indicatie ISDE")
        st.markdown("<span class='muted'>Sales — upload de offerte. De app herkent de elementen en berekent welke "
                    "subsidie de klant kan verwachten.</span>", unsafe_allow_html=True)
with c3:
    d = S[k("dossier", M)]
    bezig = (S.get(f"uw_{d}") or S.get(f"best_{d}")) if M == "adm" else (S.get(f"offerte_{d}") or S.get(f"handmatig_{d}"))
    if bezig:
        opnieuw_knop("Nieuw dossier", key=f"{M}_opnieuw_kop", mod=M, width="stretch",
                     help="Wist de geüploade bestanden en ingevulde gegevens en begint opnieuw.")

with st.sidebar:
    st.image(str(ASSETS / "belisol_logo.png"), width=70)
    st.markdown("**Belisol subsidieverwerker**")
    lijst = mcl.load()
    st.caption(f"Meldcodelijst: {lijst.get('bron') or 'niet geladen'}"
               + (f" ({lijst.get('aantal')} codes)" if lijst.get("aantal") else ""))
    if M == "adm":
        st.caption("Ondersteund: Uw-rapport Certix (Oknoplast). Profel/Oknoplast-formaten volgen.")
    else:
        g = gammas.load()
        st.caption(f"Gamma's: {len(gammas.series())} reeksen van {len(gammas.leveranciers())} leveranciers"
                   + (f" ({g.get('bron')})" if g.get("bron") else ""))
    st.caption("Bestanden worden alleen tijdens deze sessie verwerkt en niet opgeslagen.")


# ================================================================ ADMINISTRATIE
def pagina_administratie():
    # ---------------------------------------------------------------- 1. uploads
    step("Documenten uploaden")
    u1, u2 = st.columns(2)
    with u1:
        f_best = st.file_uploader("Bestelling / definitieve opmeting (PDF) — niet nodig bij Certix", type=["pdf"], key=f"best_{dossier}")
    with u2:
        f_uw = st.file_uploader("Uw-rapport / thermisch rapport leverancier (PDF)", type=["pdf"], key=f"uw_{dossier}")

    if not f_uw:
        st.info("Upload het **Uw-rapport** (en, behalve bij Certix, de bestelling) om verder te gaan.")
        return


    try:
        with st.spinner("Uw-rapport inlezen…"):
            report = _parse(f_uw.getvalue())
    except ValueError as e:
        st.error(f"Het Uw-rapport kon niet gelezen worden: {e}")
        return

    # Certix: de maat zonder aanslag staat in de schets van het Uw-rapport (zwarte maat) -> bestelling controleren is niet nodig
    is_certix = bool(report.posities) and all(p.systeem.upper().startswith("CERTIX") for p in report.posities)

    if not f_best and not is_certix:
        st.info("Dit is geen Certix-rapport: upload ook de **bestelling** om de flens te controleren.")
        return

    best = _bestelling(f_best.getvalue()) if f_best else {"afbeeldingen": [], "gescand": False, "velden": {}}

    # nieuwe bestanden -> oud resultaat weggooien
    sig = (f_uw.name, f_uw.size, f_best.name if f_best else None, f_best.size if f_best else None)
    if S.get(k("sig")) != sig:
        S[k("sig")] = sig
        S.pop(k("result"), None)
        S.pop(k("pdf"), None)
        S.pop(k("pdf_gedownload"), None)

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

    if not ok and k("result") not in S:
        return

    klant = {
        "naam": naam or None, "aanhef": aanhef or None, "email": email or None, "straat": straat or None,
        "huisnummer": huisnr or None, "postcode": postcode or None, "plaats": plaats or None,
        "flens_bevestigd": flens_bevestigd, "flens_mm": flens_mm,
    }
    if ok:
        with st.spinner("Berekenen en rapport opmaken…"):
            result = evaluate(report, klant)
            pdf = render_pdf(result, report)
        S[k("result")], S[k("pdf")] = result, pdf
        S[k("pdf_gedownload")] = False
    result, pdf = S[k("result")], S[k("pdf")]

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
    S[k("bestandsnaam")] = bestandsnaam
    d1, d2 = st.columns([2, 1])
    d1.download_button("⬇  Download subsidie-overzicht (PDF)", data=pdf, file_name=f"{bestandsnaam}.pdf",
                       mime="application/pdf", type="primary", use_container_width=True, on_click=_markeer_gedownload, args=("adm",))
    d2.download_button("Download gegevens (JSON)", data=json.dumps(result, ensure_ascii=False, indent=2),
                       file_name=f"{bestandsnaam}.json", mime="application/json", use_container_width=True)

    st.divider()
    n1, n2 = st.columns([2, 1], vertical_alignment="center")
    if S.get(k("pdf_gedownload")):
        n1.success("Subsidie-overzicht gedownload. Je kunt een nieuw dossier starten.")
    else:
        n1.caption("Klaar met deze klant? Download eerst het overzicht en start dan een nieuw dossier.")
    with n2:
        opnieuw_knop("Nieuw dossier starten", key="adm_opnieuw_onder", mod="adm", width="stretch")


# ================================================================ SALES
VUL_LABEL = {c: v[0] for c, v in sales.VULLINGEN.items()}
VUL_CODE = {v: c for c, v in VUL_LABEL.items()}
KOLOMMEN = ["Omschrijving", "Reeks", "Leverancier", "Type", "Vulling", "Breedte (mm)", "Hoogte (mm)", "Aantal", "Opmerking"]


def _naar_rijen(elementen: list[sales.Element]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Omschrijving": e.omschrijving, "Reeks": e.serie or None, "Leverancier": e.leverancier or None, "Type": e.type,
        "Breedte (mm)": e.breedte_mm, "Hoogte (mm)": e.hoogte_mm, "Aantal": e.aantal,
        "Vulling": VUL_LABEL.get(e.vulling), "Opmerking": e.opmerking} for e in elementen], columns=KOLOMMEN)


def _naar_elementen(df: pd.DataFrame) -> list[sales.Element]:
    def val(x):
        return None if x is None or (isinstance(x, float) and pd.isna(x)) or x == "" else x
    out = []
    for _, r in df.iterrows():
        serie, lev, type_ = val(r["Reeks"]) or "", val(r["Leverancier"]) or "", val(r["Type"]) or "Raam"
        if serie and not lev:  # leverancier automatisch uit de gamma-lijst
            lev = (gammas.kies(serie, type_) or {}).get("leverancier", "")
        b, h = val(r["Breedte (mm)"]), val(r["Hoogte (mm)"])
        if not any([serie, lev, b, h, val(r["Omschrijving"])]):
            continue  # lege rij
        out.append(sales.Element(
            nr=len(out) + 1, omschrijving=val(r["Omschrijving"]) or "", serie=serie, leverancier=lev, type=type_,
            breedte_mm=float(b) if b else None, hoogte_mm=float(h) if h else None,
            aantal=int(val(r["Aantal"]) or 1), vulling=VUL_CODE.get(val(r["Vulling"]), "geen"),
            opmerking=val(r["Opmerking"]) or ""))
    return out


def pagina_sales():
    cfg = load_config()

    # ---------------------------------------------------------------- 1. offerte
    step("Offerte uploaden")
    u1, u2 = st.columns([3, 1], vertical_alignment="bottom")
    with u1:
        f_off = st.file_uploader("Offerte (PDF)", type=["pdf"], key=f"offerte_{dossier}")
    with u2:
        handmatig = st.toggle("Handmatig invullen", key=f"handmatig_{dossier}", help="Zonder offerte: vul de elementen zelf in.")

    if not f_off and not handmatig:
        st.info("Upload de **offerte**, of kies **handmatig invullen** om de elementen zelf in te voeren.")
        return

    gelezen = {"elementen": [], "meldingen": [], "offertenummer": None, "series_gevonden": [], "gescand": False}
    if f_off:
        with st.spinner("Offerte lezen…"):
            gelezen = _offerte(f_off.getvalue())
    sig = (f_off.name, f_off.size) if f_off else ("handmatig",)
    if S.get(k("sig")) != sig:  # nieuwe offerte -> tabel en resultaat opnieuw
        S[k("sig")] = sig
        S[k("elementen")] = gelezen["elementen"] or [sales.Element(nr=1)]
        for n in ("result", "pdf", "pdf_gedownload", "invoer_sig"):
            S.pop(k(n), None)
    for mld in gelezen["meldingen"]:
        st.warning(mld)
    if f_off:
        herkend = len(gelezen["elementen"])
        st.caption(f"{herkend} element{'en' if herkend != 1 else ''} herkend"
                   + (f" · offerte {gelezen['offertenummer']}" if gelezen["offertenummer"] else "")
                   + (f" · reeksen: {', '.join(gelezen['series_gevonden'])}" if gelezen["series_gevonden"] else ""))

    # ---------------------------------------------------------------- 2. elementen
    step("Elementen controleren")
    st.caption("Controleer reeks, maten, aantal en vulling. Rijen toevoegen of verwijderen kan onderaan/links in de tabel. "
               "Is de leverancier leeg, dan wordt die uit de reeks afgeleid.")
    twijfel = [e for e in S[k("elementen")] if e.opmerking]
    if twijfel:
        st.warning("Controleer:\n" + "\n".join(f"- **{e.nr}. {e.omschrijving or e.serie}** — {e.opmerking}" for e in twijfel))
    df = st.data_editor(
        _naar_rijen(S[k("elementen")]), key=f"editor_{dossier}_{hash(sig)}", num_rows="dynamic", hide_index=True,
        width="stretch",
        column_config={
            "Omschrijving": st.column_config.TextColumn(width="small"),
            "Reeks": st.column_config.SelectboxColumn(options=gammas.series(), width="small"),
            "Leverancier": st.column_config.SelectboxColumn(options=gammas.leveranciers()),
            "Type": st.column_config.SelectboxColumn(options=sales.TYPES, default="Raam", required=True, width="small"),
            "Breedte (mm)": st.column_config.NumberColumn("B (mm)", min_value=0, max_value=10000, step=1, format="%d"),
            "Hoogte (mm)": st.column_config.NumberColumn("H (mm)", min_value=0, max_value=10000, step=1, format="%d"),
            "Aantal": st.column_config.NumberColumn(width="small", min_value=1, max_value=999, step=1, default=1, format="%d"),
            "Vulling": st.column_config.SelectboxColumn(options=list(VUL_LABEL.values()), required=True,
                                                        default=VUL_LABEL["hr_plus_plus_glas"]),
            "Opmerking": st.column_config.TextColumn(disabled=True, width="medium"),
        })
    elementen = _naar_elementen(df)

    # live indicatie, zodat de verkoper meteen ziet wat een wijziging doet
    vest_codes = [c for c in cfg["vestigingen"] if not c.startswith("_")]
    voorlopig = sales.bereken(elementen, {}, vest_codes[0] if vest_codes else None, cfg=cfg) if elementen else None
    if voorlopig:
        rb = voorlopig["totaal"]["richtbedragen"]
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("In aanmerking", f"{nl(voorlopig['totaal']['m2'])} m²")
        v2.metric("Eén maatregel", eur(rb["enkel"]) if rb["enkel"] is not None else "–")
        v3.metric("Meerdere maatregelen", eur(rb["meerdere"]) if rb["meerdere"] is not None else "–")
        v4.metric("Monumentale woning", eur(rb["monument"]) if rb["monument"] is not None else "–")

    # ---------------------------------------------------------------- 3. klant
    step("Klantgegevens")
    with st.form(f"sales_klant_{dossier}"):
        k1, k2, k3 = st.columns(3)
        naam = k1.text_input("Naam klant")
        aanhef = k2.text_input("Aanhef", placeholder="Geachte heer/mevrouw …")
        offertenr = k3.text_input("Offertenummer", value=gelezen.get("offertenummer") or "")
        k4, k5, k6, k7 = st.columns([3, 1, 1.3, 2])
        straat = k4.text_input("Straat")
        huisnr = k5.text_input("Huisnr.")
        postcode = k6.text_input("Postcode")
        plaats = k7.text_input("Plaats")
        vest = st.selectbox("Vestiging", vest_codes,
                            format_func=lambda c: cfg["vestigingen"][c].get("weergavenaam") or cfg["vestigingen"][c].get("naam") or c)
        ok = st.form_submit_button("Subsidie-indicatie maken", type="primary", width="stretch",
                                   disabled=not elementen)

    invoer_sig = hash(json.dumps([e.to_dict() for e in elementen], sort_keys=True, default=str))
    if ok:
        klant = {"naam": naam or None, "aanhef": aanhef or None, "straat": straat or None, "huisnummer": huisnr or None,
                 "postcode": postcode or None, "plaats": plaats or None}
        with st.spinner("Berekenen en document opmaken…"):
            result = sales.bereken(elementen, klant, vest, offertenr or None, cfg=cfg)
            S[k("result")], S[k("pdf")] = result, sales.render_pdf(result)
        S[k("pdf_gedownload")] = False
        S[k("invoer_sig")] = invoer_sig
    if k("result") not in S:
        return
    result, pdf = S[k("result")], S[k("pdf")]

    # ---------------------------------------------------------------- 4. resultaat
    step("Resultaat")
    gewijzigd = S.get(k("invoer_sig")) != invoer_sig
    if gewijzigd:
        st.warning("De elementen zijn gewijzigd na het maken van de indicatie. Klik opnieuw op **Subsidie-indicatie maken**.")
    if result["waarschuwingen"]:
        st.warning("Let op:\n" + "\n".join(f"- {w}" for w in result["waarschuwingen"]))
    elif not gewijzigd:
        st.success("De subsidie-indicatie is klaar voor de klant.")
    st.dataframe(
        [{"Maatregel": m["label"], "m²": m["m2_voor_bedrag"],
          "Eén maatregel": eur(m["bedragen"]["enkel"]["totaal"]) if m["bedragen"]["enkel"]["totaal"] is not None else "–",
          "Meerdere": eur(m["bedragen"]["meerdere"]["totaal"]) if m["bedragen"]["meerdere"]["totaal"] is not None else "–",
          "Monument": eur(m["bedragen"]["monument"]["totaal"]) if m["bedragen"]["monument"]["totaal"] is not None else "–"}
         for m in result["maatregelen"]], hide_index=True, width="stretch")

    bestandsnaam = f"Subsidie-indicatie {result['klant']['naam'] or 'klant'}".strip()
    S[k("bestandsnaam")] = bestandsnaam
    st.download_button("⬇  Download subsidie-indicatie (PDF)", data=pdf, file_name=f"{bestandsnaam}.pdf",
                       mime="application/pdf", type="primary", width="stretch", on_click=_markeer_gedownload, args=("sal",),
                       disabled=gewijzigd)

    st.divider()
    n1, n2 = st.columns([2, 1], vertical_alignment="center")
    if S.get(k("pdf_gedownload")):
        n1.success("Subsidie-indicatie gedownload. Je kunt een nieuw dossier starten.")
    else:
        n1.caption("Klaar met deze klant? Download eerst de indicatie en start dan een nieuw dossier.")
    with n2:
        opnieuw_knop("Nieuw dossier starten", key="sal_opnieuw_onder", mod="sal", width="stretch")


# Beide pagina's worden altijd getekend (de andere verborgen), zodat uploads en invoer blijven staan bij het
# wisselen van module: Streamlit wist een widget die niet getekend wordt.
st.markdown("<style>" + "".join(f".st-key-pagina_{m} {{display:none}}" for m in MODULES.values() if m != M)
            + "</style>", unsafe_allow_html=True)
for P, _pagina in (("adm", pagina_administratie), ("sal", pagina_sales)):
    dossier = S[k("dossier")]
    _stapnr = 0
    with st.container(key=f"pagina_{P}"):
        _pagina()
