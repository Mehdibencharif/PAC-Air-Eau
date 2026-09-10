import os
import tempfile

import pandas as pd
import streamlit as st

from modules.needs import estimer_besoin, PROFILS_L_PAR_JOUR
from modules.pdf_extractor import extraire_specs, champs_manquants
from modules.bill_extractor import extraire_prix_facture
from modules.catalog import charger_catalogue, specs_vers_ligne, ajouter_modele
from modules.subsidies import ContexteSubvention, simuler, total_estime
from modules.recommender import filtrer_et_scorer

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CATALOG_CSV = os.path.join(DATA_DIR, "catalog_sample.csv")
SUBSIDIES_YAML = os.path.join(DATA_DIR, "subsidies_qc.yaml")

ENERGIE_LABELS = {
    "electricite": "Électricité",
    "gaz_naturel": "Gaz naturel",
    "mazout": "Mazout",
    "propane": "Propane",
    "bois": "Bois",
    "autre": "Autre / je ne sais pas",
}

TYPES_EQUIPEMENT = {
    "electricite": ["Chauffe-eau électrique", "Thermopompe existante", "Chaudière électrique",
                    "Plinthes / convecteurs", "Autre"],
    "gaz_naturel": ["Chaudière au gaz (standard)", "Chaudière au gaz (condensation)",
                    "Chauffe-eau au gaz", "Autre"],
    "mazout": ["Chaudière au mazout", "Fournaise au mazout", "Autre"],
    "propane": ["Chaudière au propane", "Chauffe-eau au propane", "Autre"],
    "bois": ["Chaudière à biomasse", "Autre"],
    "autre": ["Autre / à préciser"],
}

# Rendements typiques (%) préremplis à titre indicatif — à corriger si la valeur
# réelle de l'équipement (plaque signalétique, rapport d'efficacité) est connue.
RENDEMENT_TYPIQUE = {
    "Chaudière au gaz (standard)": 80.0,
    "Chaudière au gaz (condensation)": 95.0,
    "Chauffe-eau au gaz": 80.0,
    "Chaudière au mazout": 82.0,
    "Fournaise au mazout": 80.0,
    "Chauffe-eau électrique": 98.0,
    "Thermopompe existante": 250.0,  # COP typique ~2.5 exprimé en "rendement" équivalent
    "Chaudière électrique": 99.0,
    "Plinthes / convecteurs": 100.0,
    "Chaudière au propane": 82.0,
    "Chauffe-eau au propane": 78.0,
    "Chaudière à biomasse": 70.0,
}

UNITE_CONSO = {
    "electricite": "kWh/an",
    "gaz_naturel": "m³/an",
    "mazout": "L/an",
    "propane": "L/an",
    "bois": "cordes/an",
    "autre": "unité/an",
}

st.set_page_config(page_title="Sélecteur thermopompe air-eau ECS (Québec)", layout="wide")

if "step" not in st.session_state:
    st.session_state.step = 1
if "catalogue" not in st.session_state:
    st.session_state.catalogue = charger_catalogue(CATALOG_CSV)

st.title("💧 Sélecteur de thermopompe air-eau pour eau chaude sanitaire")
st.caption("Outil d'aide à la décision — Québec. Les montants de subvention affichés sont des estimations à valider auprès des programmes officiels.")

steps = ["1. Bâtiment & énergie", "2. Prix de l'énergie", "3. Besoin en ECS", "4. Fiches techniques", "5. Résultats"]
st.progress((st.session_state.step - 1) / 4)
st.write(" → ".join(f"**{s}**" if i + 1 == st.session_state.step else s for i, s in enumerate(steps)))
st.divider()

# ---------------------------------------------------------------------------
# ÉTAPE 1 — IDENTIFICATION DU SITE ET SITUATION DE RÉFÉRENCE
# ---------------------------------------------------------------------------

if st.session_state.step == 1:

    st.subheader("1. Identification du bâtiment et situation énergétique actuelle")

    # -----------------------------------------------------------------------
    # IDENTIFICATION
    # -----------------------------------------------------------------------

    st.markdown("### 🏢 Identification du bâtiment")

    nom_batiment = st.text_input(
        "Nom du bâtiment / projet",
        value=st.session_state.get("nom_batiment", "Bâtiment 1"),
        placeholder="Ex. Usine Victoriaville - Salle mécanique 1",
    )

    st.divider()

    # -----------------------------------------------------------------------
    # MARCHÉ
    # -----------------------------------------------------------------------

    st.markdown("### Marché")

    type_batiment = st.radio(
        "Sélectionner le secteur",
        options=[
            "commercial_institutionnel",
            "industriel",
            "agricole",
        ],
        format_func=lambda x: {
            "commercial_institutionnel": "Commercial & institutionnel",
            "industriel": "Industriel",
            "agricole": "Agricole",
        }[x],
        horizontal=True,
        label_visibility="collapsed",
    )

    st.divider()

    # -----------------------------------------------------------------------
    # NATURE DU PROJET
    # -----------------------------------------------------------------------

    st.markdown("### Nature du projet")

    nature_projet = st.radio(
        "Nature",
        options=[
            "batiment_existant",
            "nouveau_batiment",
            "agrandissement",
            "renovation_majeure",
        ],
        format_func=lambda x: {
            "batiment_existant": "Bâtiment existant",
            "nouveau_batiment": "Nouveau bâtiment",
            "agrandissement": "Agrandissement",
            "renovation_majeure": "Rénovation majeure",
        }[x],
        horizontal=True,
        label_visibility="collapsed",
    )

    st.divider()

    # -----------------------------------------------------------------------
    # SOURCE D'ÉNERGIE ACTUELLE
    # -----------------------------------------------------------------------

    st.markdown("### 🔥⚡ Source d'énergie actuelle")

    energie = st.multiselect(
        "Source(s) d'énergie utilisée(s) actuellement",
        options=[
            "electricite",
            "gaz_naturel",
            "propane",
            "mazout",
            "autre",
        ],
        format_func=lambda x: {
            "electricite": "Électricité",
            "gaz_naturel": "Gaz naturel",
            "propane": "Propane",
            "mazout": "Mazout",
            "autre": "Autre",
        }[x],
        default=st.session_state.get("energie", ["electricite"]),
    )

    if not energie:
        st.warning("Sélectionne au moins une source d'énergie.")

    if len(energie) > 1:
        st.info("Site multisource détecté. Les consommations seront analysées séparément.")

    st.divider()

    st.divider()

# -----------------------------------------------------------------------
# TARIFICATION
# -----------------------------------------------------------------------

st.markdown("### 💲 Tarification énergétique")

if "tarifs" not in st.session_state:
    st.session_state.tarifs = {}

for src in energie:

    label_source = {
        "electricite": "Électricité",
        "gaz_naturel": "Gaz naturel",
        "propane": "Propane",
        "mazout": "Mazout",
        "autre": "Autre",
    }.get(src, src)

    with st.expander(f"Tarification — {label_source}", expanded=True):

        tarif_existant = st.session_state.tarifs.get(src, {})

        # ---------------------------------------------------------------
        # ÉLECTRICITÉ
        # ---------------------------------------------------------------
        if src == "electricite":

            options_tarif = [
                "G",
                "M",
                "LG",
                "DP",
                "DM",
                "Personnalisé",
                "Je ne sais pas",
            ]

            tarif_enregistre = tarif_existant.get("tarif", "G")

            # Si le tarif enregistré n'est pas dans la liste,
            # on sélectionne automatiquement "Personnalisé"
            if tarif_enregistre not in options_tarif:
                tarif_selection = "Personnalisé"
            else:
                tarif_selection = tarif_enregistre

            c1, c2 = st.columns(2)

            tarif_selection = c1.selectbox(
                "Tarif électrique",
                options=options_tarif,
                index=options_tarif.index(tarif_selection),
                key=f"tarif_select_{src}",
            )

            cout_moyen = c2.number_input(
                "Coût moyen réel ($/kWh)",
                min_value=0.0,
                value=float(tarif_existant.get("cout_moyen", 0.110)),
                format="%.4f",
                key=f"cout_tarif_{src}",
            )

            if tarif_selection == "Personnalisé":
                tarif = st.text_input(
                    "Nom du tarif personnalisé",
                    value=(
                        tarif_enregistre
                        if tarif_enregistre not in options_tarif
                        else ""
                    ),
                    placeholder="Ex. Tarif expérimental, contrat spécial...",
                    key=f"tarif_perso_{src}",
                )
            else:
                tarif = tarif_selection

        # ---------------------------------------------------------------
        # GAZ NATUREL
        # ---------------------------------------------------------------
        elif src == "gaz_naturel":

            options_tarif = [
                "Tarif D1",
                "Tarif D3",
                "Tarif D4",
                "Tarif D5",
                "Contrat particulier",
                "Personnalisé",
                "Je ne sais pas",
            ]

            tarif_enregistre = tarif_existant.get("tarif", "Tarif D1")

            if tarif_enregistre not in options_tarif:
                tarif_selection = "Personnalisé"
            else:
                tarif_selection = tarif_enregistre

            c1, c2 = st.columns(2)

            tarif_selection = c1.selectbox(
                "Tarif gaz naturel",
                options=options_tarif,
                index=options_tarif.index(tarif_selection),
                key=f"tarif_select_{src}",
            )

            cout_moyen = c2.number_input(
                "Coût moyen réel ($/m³)",
                min_value=0.0,
                value=float(tarif_existant.get("cout_moyen", 0.420)),
                format="%.4f",
                key=f"cout_tarif_{src}",
            )

            if tarif_selection == "Personnalisé":
                tarif = st.text_input(
                    "Nom du tarif personnalisé",
                    value=(
                        tarif_enregistre
                        if tarif_enregistre not in options_tarif
                        else ""
                    ),
                    placeholder="Ex. Tarif industriel spécial...",
                    key=f"tarif_perso_{src}",
                )
            else:
                tarif = tarif_selection

        # ---------------------------------------------------------------
        # PROPANE / MAZOUT
        # ---------------------------------------------------------------
        elif src in ["propane", "mazout"]:

            c1, c2 = st.columns(2)

            tarif = c1.text_input(
                "Tarif / fournisseur",
                value=tarif_existant.get("tarif", ""),
                placeholder="Ex. Contrat fournisseur",
                key=f"tarif_{src}",
            )

            cout_moyen = c2.number_input(
                "Coût moyen réel ($/L)",
                min_value=0.0,
                value=float(tarif_existant.get("cout_moyen", 0.0)),
                format="%.4f",
                key=f"cout_tarif_{src}",
            )

        # ---------------------------------------------------------------
        # AUTRE
        # ---------------------------------------------------------------
        else:

            c1, c2 = st.columns(2)

            tarif = c1.text_input(
                "Tarif / description",
                value=tarif_existant.get("tarif", ""),
                placeholder="Décrire le tarif",
                key=f"tarif_{src}",
            )

            cout_moyen = c2.number_input(
                "Coût moyen réel",
                min_value=0.0,
                value=float(tarif_existant.get("cout_moyen", 0.0)),
                format="%.4f",
                key=f"cout_tarif_{src}",
            )

        # ---------------------------------------------------------------
        # ENREGISTREMENT
        # ---------------------------------------------------------------
        st.session_state.tarifs[src] = {
            "tarif": tarif,
            "cout_moyen": cout_moyen,
        }

    # -----------------------------------------------------------------------
    # ÉQUIPEMENT EXISTANT
    # -----------------------------------------------------------------------

    st.markdown("### ⚙️ Équipement existant")

    if "equipements" not in st.session_state:
        st.session_state.equipements = {}

    for src in energie:

        label_source = {
            "electricite": "Électricité",
            "gaz_naturel": "Gaz naturel",
            "propane": "Propane",
            "mazout": "Mazout",
            "autre": "Autre",
        }[src]

        with st.expander(
            f"Équipement associé — {label_source}",
            expanded=True,
        ):

            existant = st.session_state.equipements.get(src, {})

            if src == "electricite":
                options_eq = [
                    "Chauffe-eau électrique",
                    "Chaudière électrique",
                    "Éléments électriques",
                    "Thermopompe existante",
                    "Autre",
                ]

            elif src == "gaz_naturel":
                options_eq = [
                    "Chauffe-eau gaz naturel",
                    "Chaudière gaz naturel",
                    "Chaudière vapeur",
                    "Brûleur direct",
                    "Autre",
                ]

            elif src == "propane":
                options_eq = [
                    "Chauffe-eau propane",
                    "Chaudière propane",
                    "Brûleur propane",
                    "Autre",
                ]

            else:
                options_eq = ["Chaudière", "Chauffe-eau", "Autre"]

            c1, c2 = st.columns(2)

            type_eq = c1.selectbox(
                "Type d'équipement",
                options=options_eq,
                index=(
                    options_eq.index(existant["type"])
                    if existant.get("type") in options_eq
                    else 0
                ),
                key=f"eqtype_{src}",
            )

            modele = c2.text_input(
                "Modèle / description",
                value=existant.get("modele", ""),
                key=f"eqmodele_{src}",
            )

            c3, c4, c5 = st.columns(3)

            rendement = c3.number_input(
                "Rendement actuel (%)",
                min_value=1.0,
                max_value=300.0,
                value=float(existant.get("rendement_pct", 80.0)),
                key=f"rdt_{src}",
            )

            unite = {
                "electricite": "kWh/an",
                "gaz_naturel": "m³/an",
                "propane": "L/an",
                "mazout": "L/an",
                "autre": "unité/an",
            }[src]

            consommation = c4.number_input(
                f"Consommation annuelle ({unite})",
                min_value=0.0,
                value=float(
                    existant.get("consommation_annuelle", 0.0)
                ),
                key=f"conso_{src}",
            )

            unite_prix = {
                "electricite": "$/kWh",
                "gaz_naturel": "$/m³",
                "propane": "$/L",
                "mazout": "$/L",
                "autre": "$/unité",
            }[src]

            prix = c5.number_input(
                f"Coût moyen ({unite_prix})",
                min_value=0.0,
                value=float(existant.get("prix_unitaire", 0.0)),
                format="%.4f",
                key=f"prix_{src}",
            )

            st.session_state.equipements[src] = {
                "type": type_eq,
                "modele": modele,
                "rendement_pct": rendement,
                "consommation_annuelle": consommation,
                "unite": unite,
                "prix_unitaire": prix,
                "unite_prix": unite_prix,
            }

    # -----------------------------------------------------------------------
    # ENREGISTREMENT SESSION
    # -----------------------------------------------------------------------

    st.session_state.nom_batiment = nom_batiment
    st.session_state.type_batiment = type_batiment
    st.session_state.nature_projet = nature_projet
    st.session_state.energie = energie

    st.divider()

    if st.button(
        "Suivant →",
        type="primary",
        disabled=not energie,
    ):
        st.session_state.step = 2
        st.rerun()

# ---------------------------------------------------------------------------
# ÉTAPE 2 — Prix de l'énergie (à partir de tes factures)
# ---------------------------------------------------------------------------
elif st.session_state.step == 2:
    st.subheader("2. Dépose tes factures pour calculer le prix réel de l'énergie")
    st.caption(
        "PDF téléchargé du site du fournisseur ou photo/scan de la facture papier — les deux sont "
        "acceptés. L'outil tente de détecter Hydro-Québec (kWh) ou Énergir (m³), d'en extraire la "
        "consommation et le montant facturé, puis calcule un $/kWh ou $/m³. Les photos passent par "
        "une reconnaissance de texte (OCR) qui peut se tromper — vérifie toujours les valeurs avant "
        "d'enregistrer. Cette étape est optionnelle : tu peux aussi passer et saisir un prix manuellement."
    )

    if "prix_energie" not in st.session_state:
        st.session_state.prix_energie = {}  # ex: {"electricite": {...}, "gaz_naturel": {...}}

    factures = st.file_uploader(
        "Factures (PDF ou photo JPG/PNG)",
        type=["pdf", "jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="upload_factures",
    )

    if factures:
        for f in factures:
            suffix = os.path.splitext(f.name)[1].lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(f.read())
                tmp_path = tmp.name

            try:
                prix = extraire_prix_facture(tmp_path, nom_fichier=f.name)
            except Exception as e:
                st.error(f"Erreur d'extraction pour {f.name} : {e}")
                continue

            with st.expander(f"🧾 {f.name} — méthode de lecture : {prix.methode}", expanded=True):
                options_src = ["electricite", "gaz_naturel", "propane", "mazout", "autre"]
                col1, col2, col3 = st.columns(3)
                source_detectee = col1.selectbox(
                    "Source d'énergie de cette facture",
                    options=options_src,
                    index=options_src.index(prix.type_source) if prix.type_source in options_src else 4,
                    key=f"src_{f.name}",
                )
                unite = "kWh" if source_detectee == "electricite" else "m³" if source_detectee == "gaz_naturel" else "unité"
                consommation = col2.number_input(
                    f"Consommation ({unite})", value=prix.consommation or 0.0, key=f"conso_{f.name}",
                )
                montant = col3.number_input("Montant facturé ($)", value=prix.montant_total or 0.0, key=f"mnt_{f.name}")

                if consommation > 0:
                    prix_unitaire = montant / consommation
                    st.success(f"💲 Prix calculé : **{prix_unitaire:.4f} $/{unite}**")
                else:
                    st.warning("Renseigne une consommation > 0 pour calculer le prix unitaire.")
                    prix_unitaire = None

                manquants = []
                if prix.consommation is None:
                    manquants.append("consommation")
                if prix.montant_total is None:
                    manquants.append("montant total")
                if manquants:
                    st.warning(f"Non détecté automatiquement, à vérifier/compléter : {', '.join(manquants)}")
                if prix.methode in ("ocr_image", "ocr_pdf_scanne"):
                    st.caption("⚠️ Valeurs lues par OCR sur image — plus sujettes à erreur qu'un PDF texte, vérifie-les bien.")

                if prix.lignes_source:
                    with st.popover("Voir les lignes sources détectées"):
                        for champ, ligne in prix.lignes_source.items():
                            st.write(f"**{champ}** : `{ligne}`")

                if st.button("Enregistrer ce prix", key=f"save_{f.name}"):
                    st.session_state.prix_energie[source_detectee] = {
                        "prix_unitaire": prix_unitaire,
                        "unite": unite,
                        "montant_total": montant,
                        "consommation": consommation,
                        "fichier_source": f.name,
                    }
                    st.success(f"Prix enregistré pour {source_detectee} : {prix_unitaire:.4f} $/{unite}" if prix_unitaire else "Prix enregistré (consommation manquante — à corriger).")

    if st.session_state.prix_energie:
        st.divider()
        st.write("**Prix enregistrés pour ce site :**")
        for src, d in st.session_state.prix_energie.items():
            if d["prix_unitaire"] is not None:
                st.write(f"- **{src}** : {d['prix_unitaire']:.4f} $/{d['unite']}  _(facture : {d['fichier_source']})_")
    else:
        st.info("Aucun prix enregistré pour l'instant.")

    c1, c2 = st.columns(2)
    if c1.button("← Précédent", key="prev_2"):
        st.session_state.step = 1
        st.rerun()
    if c2.button("Suivant →", type="primary", key="next_2"):
        st.session_state.step = 3
        st.rerun()

# ---------------------------------------------------------------------------
# ÉTAPE 3 — Besoin en ECS
# ---------------------------------------------------------------------------
elif st.session_state.step == 3:
    st.subheader("3. Quel est ton besoin en eau chaude sanitaire ?")
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
    if c1.button("← Précédent", key="prev_3"):
        st.session_state.step = 2
        st.rerun()
    if c2.button("Suivant →", type="primary", key="next_3"):
        st.session_state.step = 4
        st.rerun()

# ---------------------------------------------------------------------------
# ÉTAPE 4 — Fiches techniques
# ---------------------------------------------------------------------------
elif st.session_state.step == 4:
    st.subheader("4. Ajoute des fiches techniques (PDF) — optionnel")
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
                nom_modele = col1.text_input("Nom du modèle", value=f.name.replace(".pdf", ""), key=f"nom_{f.name}")
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

                if st.button("Ajouter au catalogue de comparaison", key=f"add_{f.name}"):
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
    if c1.button("← Précédent", key="prev_4"):
        st.session_state.step = 3
        st.rerun()
    if c2.button("Voir les résultats →", type="primary", key="next_4"):
        st.session_state.step = 5
        st.rerun()

# ---------------------------------------------------------------------------
# ÉTAPE 5 — Résultats : recommandation + subventions
# ---------------------------------------------------------------------------
elif st.session_state.step == 5:
    st.subheader("5. Recommandation et estimation des subventions")

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
            energie_actuelle=st.session_state.energie,          # <-- liste (mix), plus une seule chaîne
            type_appareil="dhw_heat_pump",
            type_batiment=st.session_state.type_batiment,
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
    if st.button("← Précédent", key="prev_5"):
        st.session_state.step = 4
        st.rerun()
