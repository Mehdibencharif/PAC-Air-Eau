import os
import tempfile

import pandas as pd
import streamlit as st

from modules.needs import estimer_besoin, PROFILS_L_PAR_JOUR
from modules.pdf_extractor import extraire_specs, champs_manquants
from modules.catalog import charger_catalogue, specs_vers_ligne, ajouter_modele
from modules.subsidies import ContexteSubvention, simuler, total_estime
from modules.recommender import filtrer_et_scorer

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CATALOG_CSV = os.path.join(DATA_DIR, "catalog_sample.csv")
SUBSIDIES_YAML = os.path.join(DATA_DIR, "subsidies_qc.yaml")

st.set_page_config(page_title="Sélecteur thermopompe air-eau ECS (Québec)", layout="wide")

if "step" not in st.session_state:
    st.session_state.step = 1
if "catalogue" not in st.session_state:
    st.session_state.catalogue = charger_catalogue(CATALOG_CSV)

st.title("💧 Sélecteur de thermopompe air-eau pour eau chaude sanitaire")
st.caption("Outil d'aide à la décision — Québec. Les montants de subvention affichés sont des estimations à valider auprès des programmes officiels.")

steps = ["1. Énergie actuelle", "2. Besoin en ECS", "3. Fiches techniques", "4. Résultats"]
st.progress((st.session_state.step - 1) / 3)
st.write(" → ".join(f"**{s}**" if i + 1 == st.session_state.step else s for i, s in enumerate(steps)))
st.divider()

# ---------------------------------------------------------------------------
# ÉTAPE 1 — Énergie actuelle
# ---------------------------------------------------------------------------
if st.session_state.step == 1:
    st.subheader("1. Quelle énergie utilises-tu actuellement pour l'eau chaude ?")
    energie = st.radio(
        "Source d'énergie à remplacer",
        options=["electricite", "gaz_naturel", "mazout", "propane", "bois", "autre"],
        format_func=lambda x: {
            "electricite": "Électricité",
            "gaz_naturel": "Gaz naturel",
            "mazout": "Mazout",
            "propane": "Propane",
            "bois": "Bois",
            "autre": "Autre / je ne sais pas",
        }[x],
        index=0,
    )
    col1, col2 = st.columns(2)
    revenu_sous_median = col1.checkbox("Revenu du ménage ≤ revenu médian provincial (pertinent si mazout)")
    combine_mesures = col2.checkbox("Je prévois aussi d'autres travaux d'efficacité énergétique en même temps")

    st.session_state.energie = energie
    st.session_state.revenu_sous_median = revenu_sous_median
    st.session_state.combine_mesures = combine_mesures

    if st.button("Suivant →", type="primary"):
        st.session_state.step = 2
        st.rerun()

# ---------------------------------------------------------------------------
# ÉTAPE 2 — Besoin en ECS
# ---------------------------------------------------------------------------
elif st.session_state.step == 2:
    st.subheader("2. Quel est ton besoin en eau chaude sanitaire ?")
    methode = st.radio(
        "Méthode d'estimation",
        options=["personnes", "profil", "manuel"],
        format_func=lambda x: {
            "personnes": "Nombre de personnes dans le logement",
            "profil": "Profil de consommation (petit / moyen / grand)",
            "manuel": "Je connais mon volume quotidien en litres",
        }[x],
        horizontal=True,
    )

    nb_personnes = profil = volume_manuel = None
    if methode == "personnes":
        nb_personnes = st.slider("Nombre de personnes", 1, 10, 4)
    elif methode == "profil":
        profil = st.selectbox("Profil", options=list(PROFILS_L_PAR_JOUR.keys()),
                               format_func=lambda x: f"{x.capitalize()} (~{PROFILS_L_PAR_JOUR[x]} L/jour)")
    else:
        volume_manuel = st.number_input("Volume d'eau chaude (L/jour)", min_value=20, value=250)

    col1, col2, col3 = st.columns(3)
    temp_froide = col1.number_input("Température eau froide entrante (°C)", value=7.0)
    temp_consigne = col2.number_input("Température de consigne souhaitée (°C)", value=55.0)
    heures_fonctionnement = col3.number_input("Heures de fonctionnement visées / jour", value=12.0)
    temp_design_hiver = st.number_input(
        "Température extérieure de design hiver (°C) — pire cas de ta région",
        value=-20.0, help="Ex: -20°C pour Sherbrooke en pire cas. Ajuste selon ta localisation exacte.",
    )

    besoin = estimer_besoin(
        nb_personnes=nb_personnes, profil=profil, volume_manuel_l=volume_manuel,
        temp_froide_C=temp_froide, temp_consigne_C=temp_consigne,
        heures_fonctionnement_jour=heures_fonctionnement,
    )

    st.info(
        f"**Volume estimé :** {besoin.volume_l_par_jour:.0f} L/jour · "
        f"**Énergie requise :** {besoin.energie_kwh_jour:.1f} kWh/jour · "
        f"**Puissance thermique recommandée :** {besoin.puissance_recommandee_kw:.2f} kW"
    )

    st.session_state.besoin = besoin
    st.session_state.temp_design_hiver = temp_design_hiver

    c1, c2 = st.columns(2)
    if c1.button("← Précédent"):
        st.session_state.step = 1
        st.rerun()
    if c2.button("Suivant →", type="primary"):
        st.session_state.step = 3
        st.rerun()

# ---------------------------------------------------------------------------
# ÉTAPE 3 — Fiches techniques
# ---------------------------------------------------------------------------
elif st.session_state.step == 3:
    st.subheader("3. Ajoute des fiches techniques (PDF) — optionnel")
    st.caption(
        "L'extraction se fait par reconnaissance de motifs texte (COP, puissance, "
        "réservoir, plage de température, bruit, réfrigérant). Si une fiche est "
        "scannée en image ou mal structurée, certains champs resteront vides — "
        "tu pourras les compléter manuellement."
    )

    fichiers = st.file_uploader("Fiches techniques PDF", type=["pdf"], accept_multiple_files=True)

    if fichiers:
        for f in fichiers:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(f.read())
                tmp_path = tmp.name

            try:
                specs = extraire_specs(tmp_path, nom_fichier=f.name)
            except Exception as e:
                st.error(f"Erreur d'extraction pour {f.name} : {e}")
                continue

            with st.expander(f"📄 {f.name}", expanded=True):
                col1, col2, col3, col4 = st.columns(4)
                nom_modele = col1.text_input(f"Nom du modèle", value=f.name.replace(".pdf", ""), key=f"nom_{f.name}")
                puissance = col2.number_input("Puissance (kW)", value=specs.puissance_kw or 0.0, key=f"p_{f.name}")
                cop = col3.number_input("COP", value=specs.cop or 0.0, key=f"cop_{f.name}")
                prix = col4.number_input("Prix estimé (CAD)", value=0.0, key=f"prix_{f.name}")

                manquants = champs_manquants(specs)
                if manquants:
                    st.warning(f"Champs non détectés automatiquement, à vérifier/compléter : {', '.join(manquants)}")

                if specs.lignes_source:
                    with st.popover("Voir les lignes sources détectées"):
                        for champ, ligne in specs.lignes_source.items():
                            st.write(f"**{champ}** : `{ligne}`")

                if st.button(f"Ajouter au catalogue de comparaison", key=f"add_{f.name}"):
                    specs.puissance_kw = puissance or specs.puissance_kw
                    specs.cop = cop or specs.cop
                    ligne = specs_vers_ligne(specs, nom_modele=nom_modele, prix_estime=prix or None)
                    st.session_state.catalogue = ajouter_modele(st.session_state.catalogue, ligne)
                    st.success(f"{nom_modele} ajouté au catalogue.")

    st.divider()
    st.write("**Catalogue actuel (exemples + fiches ajoutées) :**")
    st.dataframe(st.session_state.catalogue, use_container_width=True)
    st.caption("Les lignes 'Exemple A/B/C' sont des placeholders — supprime-les ou ignore-les dans le comparatif si tu n'as que tes propres fiches.")

    c1, c2 = st.columns(2)
    if c1.button("← Précédent"):
        st.session_state.step = 2
        st.rerun()
    if c2.button("Voir les résultats →", type="primary"):
        st.session_state.step = 4
        st.rerun()

# ---------------------------------------------------------------------------
# ÉTAPE 4 — Résultats : recommandation + subventions
# ---------------------------------------------------------------------------
elif st.session_state.step == 4:
    st.subheader("4. Recommandation et estimation des subventions")

    besoin = st.session_state.besoin
    temp_design = st.session_state.temp_design_hiver

    tab1, tab2 = st.tabs(["🏆 Meilleurs choix", "💰 Subventions estimées"])

    with tab1:
        exclure_placeholders = st.checkbox("Exclure les modèles 'Exemple' (placeholders)", value=True)
        df = st.session_state.catalogue.copy()
        if exclure_placeholders:
            df = df[~df["modele"].str.contains("Exemple", na=False)]

        if df.empty:
            st.warning("Aucun modèle réel dans le catalogue. Ajoute des fiches techniques à l'étape 3, ou décoche la case ci-dessus pour voir les placeholders.")
        else:
            resultats = filtrer_et_scorer(df, besoin, temp_hivernale_design_C=temp_design)
            st.write(f"Puissance minimale requise (avec marge de sécurité) : **{resultats['puissance_min_requise_kw'].iloc[0]:.2f} kW**")

            for _, row in resultats.iterrows():
                with st.container(border=True):
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.markdown(f"### {row['modele']}")
                        st.write(f"Fabricant : {row.get('fabricant', 'n/d')}")
                        st.write(
                            f"Puissance : {row['puissance_kw']} kW · COP : {row['cop']} · "
                            f"Réservoir : {row['volume_reservoir_l']} L · "
                            f"Temp. min : {row['temp_min_C']} °C · Bruit : {row['niveau_sonore_dB']} dB"
                        )
                        if row["compatible_climat"] is False:
                            st.error("⚠️ Ne semble pas couvrir ta température de design hivernale.")
                        if row["compatible_puissance"] is False:
                            st.error("⚠️ Puissance possiblement insuffisante pour ton besoin.")
                        if row["donnees_incompletes"]:
                            st.info("ℹ️ Données incomplètes — score à interpréter avec prudence.")
                    with c2:
                        st.metric("Score", f"{row['score_final']:.2f}")
                        if pd.notna(row.get("prix_estime_cad")):
                            st.write(f"~{row['prix_estime_cad']:.0f} $ CAD")

    with tab2:
        ctx = ContexteSubvention(
            energie_actuelle=st.session_state.energie,
            type_appareil="dhw_heat_pump",
            revenu_sous_median=st.session_state.revenu_sous_median,
            combine_plusieurs_mesures=st.session_state.combine_mesures,
        )
        resultats_sub = simuler(SUBSIDIES_YAML, ctx)

        if not resultats_sub:
            st.write("Aucun programme applicable trouvé pour ce contexte dans la base de règles actuelle.")
        else:
            lo, hi = total_estime(resultats_sub)
            st.metric("Estimation totale cumulée (grossière)", f"{lo:,.0f} $ – {hi:,.0f} $ CAD")
            st.caption("⚠️ Estimation indicative seulement — basée sur des fourchettes approximatives, pas les barèmes officiels exacts. Vérifie chaque montant via les liens ci-dessous avant de budgéter ton projet.")

            for r in resultats_sub:
                badge = {"admissible_probable": "🟢", "a_verifier": "🟡", "non_applicable": "🔴"}[r.statut]
                with st.container(border=True):
                    st.markdown(f"{badge} **{r.nom}** — {r.administrateur}")
                    if r.montant_min is not None or r.montant_max is not None:
                        st.write(f"Montant estimé : {r.montant_min or 0:,.0f} $ – {r.montant_max or 0:,.0f} $ CAD")
                    st.write(r.note)
                    if r.conditions_a_confirmer:
                        with st.popover("Conditions à confirmer"):
                            for cdt in r.conditions_a_confirmer:
                                st.write(f"- {cdt}")
                    st.write(f"[Source officielle]({r.source_url}) · Confiance des données : {r.confidence} ")

    st.divider()
    if st.button("← Précédent"):
        st.session_state.step = 3
        st.rerun()
