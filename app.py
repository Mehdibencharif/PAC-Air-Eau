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
    "electricite": ["Chauffe-eau électrique", "Chaudière électrique", "Éléments électriques",
                    "Thermopompe existante", "Autre"],
    "gaz_naturel": ["Chauffe-eau gaz naturel", "Chaudière gaz naturel (standard)",
                    "Chaudière gaz naturel (condensation)", "Chaudière vapeur", "Brûleur direct", "Autre"],
    "mazout": ["Chaudière au mazout", "Fournaise au mazout", "Autre"],
    "propane": ["Chauffe-eau propane", "Chaudière propane", "Brûleur propane", "Autre"],
    "bois": ["Chaudière à biomasse", "Autre"],
    "autre": ["Chaudière", "Chauffe-eau", "Autre"],
}

# Rendements typiques (%) préremplis à titre indicatif — à corriger si la valeur
# réelle de l'équipement (plaque signalétique, rapport d'efficacité) est connue.
RENDEMENT_TYPIQUE = {
    "Chaudière gaz naturel (standard)": 80.0,
    "Chaudière gaz naturel (condensation)": 95.0,
    "Chauffe-eau gaz naturel": 80.0,
    "Chaudière vapeur": 75.0,
    "Brûleur direct": 85.0,
    "Chaudière au mazout": 82.0,
    "Fournaise au mazout": 80.0,
    "Chauffe-eau électrique": 98.0,
    "Chaudière électrique": 99.0,
    "Éléments électriques": 100.0,
    "Thermopompe existante": 250.0,  # COP typique ~2.5 exprimé en "rendement" équivalent
    "Chauffe-eau propane": 78.0,
    "Chaudière propane": 82.0,
    "Brûleur propane": 83.0,
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
        "Secteur",
        options=["commercial_institutionnel", "industriel", "agricole"],
        format_func=lambda x: {
            "commercial_institutionnel": "Commercial & institutionnel",
            "industriel": "Industriel",
            "agricole": "Agricole",
        }[x],
        horizontal=True,
        label_visibility="collapsed",
    )
    st.caption(
        "Le dimensionnement ECS (formule L/jour/personne) reste une base indicative pensée à l'origine "
        "pour du résidentiel — à ajuster selon les usages réels du site (procédé, cheptel, occupants, "
        "horaires de production, etc.). Les programmes de subvention doivent aussi être revus : "
        "LogisVert/Rénoclimat/CAMT (déjà codés dans subsidies_qc.yaml) sont des programmes **résidentiels** "
        "et ne s'appliqueront probablement à aucun de ces trois secteurs — il faudra les remplacer par les "
        "bons programmes (ex: Transition énergétique Québec — volet affaires, Écoperformance Hydro-Québec/"
        "Énergir pour commercial-industriel, programmes agricoles du MAPAQ, etc.)."
    )

    st.divider()

    # -----------------------------------------------------------------------
    # NATURE DU PROJET
    # -----------------------------------------------------------------------
    st.markdown("### Nature du projet")

    nature_projet = st.radio(
        "Nature",
        options=["batiment_existant", "nouveau_batiment", "agrandissement", "renovation_majeure"],
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
        "Source(s) d'énergie utilisée(s) actuellement — sélectionne-en plusieurs si le site est multisource",
        options=list(ENERGIE_LABELS.keys()),
        format_func=lambda x: ENERGIE_LABELS[x],
        default=st.session_state.get("energie", ["electricite"]),
    )

    if not energie:
        st.warning("Sélectionne au moins une source d'énergie.")

    if len(energie) > 1:
        st.info("Site multisource détecté. Les consommations seront analysées séparément.")

    st.divider()

    # -----------------------------------------------------------------------
    # ÉQUIPEMENT ACTUEL, RENDEMENT ET CONSOMMATION ANNUELLE
    # -----------------------------------------------------------------------
    st.markdown("### ⚙️ Équipement actuel, rendement et consommation")
    st.caption(
        "Ces valeurs serviront de référence pour comparer la thermopompe air-eau au système existant "
        "(économies d'énergie, de coûts et de GES à l'étape des résultats)."
    )

    if "equipements" not in st.session_state:
        st.session_state.equipements = {}

    for src in energie:
        with st.expander(f"Équipement actuel — {ENERGIE_LABELS[src]}", expanded=True):
            options_eq = TYPES_EQUIPEMENT.get(src, ["Autre"])
            existant = st.session_state.equipements.get(src, {})

            c1, c2 = st.columns(2)
            type_eq = c1.selectbox(
                "Type d'équipement", options=options_eq,
                index=options_eq.index(existant["type"]) if existant.get("type") in options_eq else 0,
                key=f"eqtype_{src}",
            )
            modele = c2.text_input(
                "Modèle / description (optionnel)", value=existant.get("modele", ""), key=f"eqmodele_{src}",
            )

            rendement_defaut = RENDEMENT_TYPIQUE.get(type_eq, 80.0)
            c3, c4 = st.columns(2)
            rendement = c3.number_input(
                "Rendement de l'équipement existant (%)",
                min_value=1.0, max_value=300.0,
                value=existant.get("rendement_pct", rendement_defaut),
                key=f"rdt_{src}",
                help="Valeur typique préremplie selon le type sélectionné — ajuste selon la plaque "
                     "signalétique ou un rapport d'efficacité réel si tu l'as. Pour une thermopompe "
                     "existante, entre l'équivalent COP×100 (ex: COP 2.5 → 250%).",
            )

            unite = UNITE_CONSO.get(src, "unité/an")
            valeur_defaut_conso = existant.get("consommation_annuelle", 0.0)
            if not valeur_defaut_conso and "prix_energie" in st.session_state and src in st.session_state.prix_energie:
                conso_facture = st.session_state.prix_energie[src].get("consommation") or 0.0
                valeur_defaut_conso = conso_facture * 12  # approximation si la facture déposée est mensuelle

            conso_annuelle = c4.number_input(
                f"Consommation annuelle ({unite})",
                min_value=0.0, value=float(valeur_defaut_conso), key=f"conso_an_{src}",
                help="Préremplie ×12 si une facture a déjà été déposée à l'étape suivante — corrige avec "
                     "ta consommation annuelle réelle si tu l'as (relevé annuel, sommaire de compte, etc.).",
            )

            st.session_state.equipements[src] = {
                "type": type_eq,
                "modele": modele,
                "rendement_pct": rendement,
                "consommation_annuelle": conso_annuelle,
                "unite": unite,
            }

    st.divider()

    # -----------------------------------------------------------------------
    # TARIFICATION
    # -----------------------------------------------------------------------
    st.markdown("### 💲 Tarification énergétique")
    st.caption(
        "Sers-toi de ta facture pour remplir le coût moyen réel. Si tu déposes une facture à l'étape "
        "suivante, le prix qui en sera calculé automatiquement prendra le dessus sur la valeur saisie ici."
    )

    if "tarifs" not in st.session_state:
        st.session_state.tarifs = {}

    for src in energie:
        label_source = ENERGIE_LABELS.get(src, src)

        with st.expander(f"Tarification — {label_source}", expanded=True):
            tarif_existant = st.session_state.tarifs.get(src, {})

            # ---------------------------------------------------------------
            # ÉLECTRICITÉ
            # ---------------------------------------------------------------
            if src == "electricite":
                options_tarif = ["G", "M", "LG", "DP", "DM", "Personnalisé", "Je ne sais pas"]
                tarif_enregistre = tarif_existant.get("tarif", "G")
                tarif_selection = tarif_enregistre if tarif_enregistre in options_tarif else "Personnalisé"

                c1, c2 = st.columns(2)
                tarif_selection = c1.selectbox(
                    "Tarif électrique", options=options_tarif,
                    index=options_tarif.index(tarif_selection), key=f"tarif_select_{src}",
                )
                cout_moyen = c2.number_input(
                    "Coût moyen réel ($/kWh)", min_value=0.0,
                    value=float(tarif_existant.get("cout_moyen", 0.110)), format="%.4f", key=f"cout_tarif_{src}",
                )

                if tarif_selection == "Personnalisé":
                    tarif = st.text_input(
                        "Nom du tarif personnalisé",
                        value=tarif_enregistre if tarif_enregistre not in options_tarif else "",
                        placeholder="Ex. Tarif expérimental, contrat spécial...", key=f"tarif_perso_{src}",
                    )
                else:
                    tarif = tarif_selection

            # ---------------------------------------------------------------
            # GAZ NATUREL
            # ---------------------------------------------------------------
            elif src == "gaz_naturel":
                options_tarif = ["Tarif D1", "Tarif D3", "Tarif D4", "Tarif D5", "Contrat particulier",
                                  "Personnalisé", "Je ne sais pas"]
                tarif_enregistre = tarif_existant.get("tarif", "Tarif D1")
                tarif_selection = tarif_enregistre if tarif_enregistre in options_tarif else "Personnalisé"

                c1, c2 = st.columns(2)
                tarif_selection = c1.selectbox(
                    "Tarif gaz naturel", options=options_tarif,
                    index=options_tarif.index(tarif_selection), key=f"tarif_select_{src}",
                )
                cout_moyen = c2.number_input(
                    "Coût moyen réel ($/m³)", min_value=0.0,
                    value=float(tarif_existant.get("cout_moyen", 0.420)), format="%.4f", key=f"cout_tarif_{src}",
                )

                if tarif_selection == "Personnalisé":
                    tarif = st.text_input(
                        "Nom du tarif personnalisé",
                        value=tarif_enregistre if tarif_enregistre not in options_tarif else "",
                        placeholder="Ex. Tarif industriel spécial...", key=f"tarif_perso_{src}",
                    )
                else:
                    tarif = tarif_selection

            # ---------------------------------------------------------------
            # PROPANE / MAZOUT
            # ---------------------------------------------------------------
            elif src in ("propane", "mazout"):
                c1, c2 = st.columns(2)
                tarif = c1.text_input(
                    "Tarif / fournisseur", value=tarif_existant.get("tarif", ""),
                    placeholder="Ex. Contrat fournisseur", key=f"tarif_{src}",
                )
                cout_moyen = c2.number_input(
                    "Coût moyen réel ($/L)", min_value=0.0,
                    value=float(tarif_existant.get("cout_moyen", 0.0)), format="%.4f", key=f"cout_tarif_{src}",
                )

            # ---------------------------------------------------------------
            # AUTRE
            # ---------------------------------------------------------------
            else:
                c1, c2 = st.columns(2)
                tarif = c1.text_input(
                    "Tarif / description", value=tarif_existant.get("tarif", ""),
                    placeholder="Décrire le tarif", key=f"tarif_{src}",
                )
                cout_moyen = c2.number_input(
                    "Coût moyen réel", min_value=0.0,
                    value=float(tarif_existant.get("cout_moyen", 0.0)), format="%.4f", key=f"cout_tarif_{src}",
                )

            st.session_state.tarifs[src] = {"tarif": tarif, "cout_moyen": cout_moyen}

    st.divider()

    col1, col2 = st.columns(2)
    revenu_sous_median = col1.checkbox("Revenu du ménage ≤ revenu médian provincial (pertinent si mazout)")
    combine_mesures = col2.checkbox("Je prévois aussi d'autres travaux d'efficacité énergétique en même temps")

    st.session_state.nom_batiment = nom_batiment
    st.session_state.nature_projet = nature_projet
    st.session_state.energie = energie
    st.session_state.type_batiment = type_batiment
    st.session_state.revenu_sous_median = revenu_sous_median
    st.session_state.combine_mesures = combine_mesures

    if st.button("Suivant →", type="primary", disabled=not energie):
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
                    # Synchronise avec la tarification manuelle de l'étape 1 :
                    # le prix calculé depuis la facture devient la valeur de référence.
                    if prix_unitaire is not None and "tarifs" in st.session_state:
                        st.session_state.tarifs.setdefault(source_detectee, {})
                        st.session_state.tarifs[source_detectee]["cout_moyen"] = prix_unitaire

                    st.success(
                        f"Prix enregistré pour {source_detectee} : {prix_unitaire:.4f} $/{unite}"
                        if prix_unitaire else "Prix enregistré (consommation manquante — à corriger)."
                    )

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
# ÉTAPE 3 — Besoin thermique / consommation d'eau chaude
# ---------------------------------------------------------------------------

if st.session_state.step == 3:

    st.subheader("3. Besoin thermique et consommation d'eau chaude")

    st.caption(
        "Définis les principaux postes de consommation d'eau chaude du site. "
        "L'outil calcule ensuite le volume annuel, le besoin thermique annuel "
        "et la puissance moyenne requise."
    )

    # -----------------------------------------------------------------------
    # PARAMÈTRES GÉNÉRAUX
    # -----------------------------------------------------------------------

    st.markdown("### 🌡️ Paramètres généraux")

    c1, c2, c3 = st.columns(3)

    temp_froide = c1.number_input(
        "Température de l'eau froide entrante (°C)",
        value=10.0,
        step=1.0,
    )

    temp_chaude_defaut = c2.number_input(
        "Température d'eau chaude par défaut (°C)",
        value=60.0,
        step=1.0,
    )

    heures_fonctionnement = c3.number_input(
        "Heures d'opération par jour",
        min_value=1.0,
        max_value=24.0,
        value=12.0,
        step=1.0,
    )

    st.divider()

    # -----------------------------------------------------------------------
    # POSTES DE CONSOMMATION
    # -----------------------------------------------------------------------

    st.markdown("### 🚿 Postes de consommation")

    if "postes_ecs" not in st.session_state:

        st.session_state.postes_ecs = [

            {
                "poste": "Lavage et assainissement des équipements",
                "volume_gal_jour": 3200.0,
                "jours_an": 250,
                "temperature_C": 60.0,
                "inclure": True,
            },

            {
                "poste": "Nettoyage en place (CIP)",
                "volume_gal_jour": 1320.0,
                "jours_an": 250,
                "temperature_C": 60.0,
                "inclure": True,
            },

            {
                "poste": "Lavage des planchers et surfaces",
                "volume_gal_jour": 800.0,
                "jours_an": 250,
                "temperature_C": 60.0,
                "inclure": True,
            },

            {
                "poste": "Sanitaires et vestiaires du personnel",
                "volume_gal_jour": 530.0,
                "jours_an": 300,
                "temperature_C": 60.0,
                "inclure": True,
            },
        ]

    # -----------------------------------------------------------------------
    # TABLEAU D'ÉDITION DES POSTES
    # -----------------------------------------------------------------------

    postes_modifies = []

    for i, poste in enumerate(st.session_state.postes_ecs):

        with st.expander(
            f"{'✅' if poste.get('inclure', True) else '⬜'} {poste['poste']}",
            expanded=True
        ):

            c0, c1, c2, c3 = st.columns([0.5, 2.5, 1.5, 1.5])

            inclure = c0.checkbox(
                "Inclure",
                value=poste.get("inclure", True),
                key=f"inclure_poste_{i}",
                label_visibility="collapsed",
            )

            nom_poste = c1.text_input(
                "Poste de consommation",
                value=poste["poste"],
                key=f"poste_nom_{i}",
            )

            volume_gal_jour = c2.number_input(
                "Volume (gal US/jour)",
                min_value=0.0,
                value=float(poste["volume_gal_jour"]),
                step=10.0,
                key=f"poste_volume_{i}",
            )

            jours_an = c3.number_input(
                "Jours d'opération / an",
                min_value=0,
                max_value=365,
                value=int(poste["jours_an"]),
                step=1,
                key=f"poste_jours_{i}",
            )

            temperature_C = st.number_input(
                "Température requise pour ce poste (°C)",
                min_value=float(temp_froide),
                max_value=100.0,
                value=float(poste.get("temperature_C", temp_chaude_defaut)),
                step=1.0,
                key=f"poste_temp_{i}",
            )

            postes_modifies.append(
                {
                    "poste": nom_poste,
                    "volume_gal_jour": volume_gal_jour,
                    "jours_an": jours_an,
                    "temperature_C": temperature_C,
                    "inclure": inclure,
                }
            )

    st.session_state.postes_ecs = postes_modifies

    # -----------------------------------------------------------------------
    # AJOUT D'UN POSTE PERSONNALISÉ
    # -----------------------------------------------------------------------

    if st.button("➕ Ajouter un poste de consommation"):

        st.session_state.postes_ecs.append(
            {
                "poste": "Nouveau poste",
                "volume_gal_jour": 0.0,
                "jours_an": 250,
                "temperature_C": temp_chaude_defaut,
                "inclure": True,
            }
        )

        st.rerun()

    st.divider()

    # -----------------------------------------------------------------------
    # CALCUL DES BESOINS
    # -----------------------------------------------------------------------

    GAL_US_TO_L = 3.78541
    CP_EAU_KWH_KG_C = 0.001163

    resultats = []

    total_volume_jour = 0.0
    total_volume_annuel = 0.0
    total_energie_kwh_an = 0.0

    for poste in st.session_state.postes_ecs:

        if not poste["inclure"]:
            continue

        volume_jour_gal = poste["volume_gal_jour"]
        jours_an = poste["jours_an"]
        temp_chaude = poste["temperature_C"]

        volume_annuel_gal = volume_jour_gal * jours_an

        volume_annuel_L = (
            volume_annuel_gal * GAL_US_TO_L
        )

        deltaT = max(
            temp_chaude - temp_froide,
            0
        )

        # 1 litre d'eau ≈ 1 kg
        energie_kwh_an = (
            volume_annuel_L
            * CP_EAU_KWH_KG_C
            * deltaT
        )

        energie_mwh_an = energie_kwh_an / 1000

        energie_mmbtu_an = (
            energie_kwh_an * 0.003412
        )

        resultats.append(
            {
                "Poste de consommation": poste["poste"],
                "Volume (gal US/jour)": volume_jour_gal,
                "Jours/an": jours_an,
                "Volume annuel (gal US)": volume_annuel_gal,
                "Température (°C)": temp_chaude,
                "ΔT (°C)": deltaT,
                "Besoin thermique (MMBtu/an)": energie_mmbtu_an,
                "Besoin thermique (MWh/an)": energie_mwh_an,
            }
        )

        total_volume_jour += volume_jour_gal
        total_volume_annuel += volume_annuel_gal
        total_energie_kwh_an += energie_kwh_an

    # -----------------------------------------------------------------------
    # AFFICHAGE DU TABLEAU
    # -----------------------------------------------------------------------

    if resultats:

        df_besoins = pd.DataFrame(resultats)

        st.markdown("### 📊 Résumé des besoins")

        st.dataframe(
            df_besoins.style.format(
                {
                    "Volume (gal US/jour)": "{:,.0f}",
                    "Jours/an": "{:,.0f}",
                    "Volume annuel (gal US)": "{:,.0f}",
                    "Température (°C)": "{:.1f}",
                    "ΔT (°C)": "{:.1f}",
                    "Besoin thermique (MMBtu/an)": "{:,.1f}",
                    "Besoin thermique (MWh/an)": "{:,.1f}",
                }
            ),
            use_container_width=True,
        )

        total_mwh_an = (
            total_energie_kwh_an / 1000
        )

        total_mmbtu_an = (
            total_energie_kwh_an * 0.003412
        )

        # Puissance moyenne pendant les heures d'opération
        total_heures_an = (
            max(
                [p["jours_an"]
                 for p in st.session_state.postes_ecs
                 if p["inclure"]],
                default=0
            )
            * heures_fonctionnement
        )

        if total_heures_an > 0:
            puissance_moyenne_kw = (
                total_energie_kwh_an
                / total_heures_an
            )
        else:
            puissance_moyenne_kw = 0.0

        # -------------------------------------------------------------------
        # INDICATEURS
        # -------------------------------------------------------------------

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Volume total",
            f"{total_volume_jour:,.0f} gal US/j"
        )

        c2.metric(
            "Volume annuel",
            f"{total_volume_annuel:,.0f} gal US/an"
        )

        c3.metric(
            "Besoin thermique",
            f"{total_mwh_an:,.1f} MWh/an"
        )

        c4.metric(
            "Puissance moyenne",
            f"{puissance_moyenne_kw:,.1f} kW"
        )

        st.caption(
            f"Équivalent énergétique : "
            f"{total_mmbtu_an:,.1f} MMBtu/an"
        )

        # -------------------------------------------------------------------
        # SESSION STATE
        # -------------------------------------------------------------------

        st.session_state.besoin_industriel = {
            "volume_gal_jour": total_volume_jour,
            "volume_annuel_gal": total_volume_annuel,
            "energie_kwh_an": total_energie_kwh_an,
            "energie_mwh_an": total_mwh_an,
            "energie_mmbtu_an": total_mmbtu_an,
            "puissance_moyenne_kw": puissance_moyenne_kw,
            "temp_froide_C": temp_froide,
            "heures_fonctionnement_jour": heures_fonctionnement,
            "postes": resultats,
        }

    else:

        st.warning(
            "Aucun poste de consommation n'est sélectionné."
        )

    # -----------------------------------------------------------------------
    # NAVIGATION
    # -----------------------------------------------------------------------

    st.divider()

    c1, c2 = st.columns(2)

    if c1.button(
        "← Précédent",
        key="prev_3"
    ):
        st.session_state.step = 2
        st.rerun()

    if c2.button(
        "Suivant →",
        type="primary",
        key="next_3",
        disabled=not resultats,
    ):
        st.session_state.step = 4
        st.rerun()

# ---------------------------------------------------------------------------
# ÉTAPE 4 — Sélection et conditions d'opération de la thermopompe
# ---------------------------------------------------------------------------

if st.session_state.step == 4:

    st.subheader("4. Sélection et conditions d'opération de la thermopompe")

    st.caption(
        "Définis les caractéristiques de la thermopompe et les conditions réelles "
        "d'installation. Ces données serviront à estimer la capacité réellement "
        "disponible, l'énergie couverte et la rentabilité du projet."
    )

    # -----------------------------------------------------------------------
    # FICHE TECHNIQUE
    # -----------------------------------------------------------------------

    st.markdown("### 📄 Fiche technique")

    fichiers = st.file_uploader(
        "Ajouter une fiche technique PDF — optionnel",
        type=["pdf"],
        accept_multiple_files=False,
        key="fiche_pac",
    )

    specs = None

    if fichiers:

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".pdf"
        ) as tmp:

            tmp.write(fichiers.getvalue())
            tmp_path = tmp.name

        try:
            specs = extraire_specs(
                tmp_path,
                nom_fichier=fichiers.name
            )

        except Exception as e:
            st.warning(
                f"L'extraction automatique n'a pas fonctionné : {e}. "
                "Tu peux entrer les valeurs manuellement."
            )

    st.divider()

    # -----------------------------------------------------------------------
    # CARACTÉRISTIQUES PRINCIPALES
    # -----------------------------------------------------------------------

    st.markdown("### ⚙️ Caractéristiques de la thermopompe")

    c1, c2, c3, c4 = st.columns(4)

    nom_modele = c1.text_input(
        "Modèle",
        value=(
            fichiers.name.replace(".pdf", "")
            if fichiers
            else st.session_state.get("pac_nom", "")
        ),
    )

    nombre_unites = c2.number_input(
        "Nombre d'unités",
        min_value=1,
        value=int(
            st.session_state.get("pac_nombre_unites", 1)
        ),
        step=1,
    )

    puissance_nominale_kw = c3.number_input(
        "Puissance thermique nominale (kW)",
        min_value=0.0,
        value=float(
            specs.puissance_kw
            if specs and specs.puissance_kw
            else st.session_state.get(
                "pac_puissance_nominale_kw",
                0.0
            )
        ),
        step=1.0,
    )

    cop_nominal = c4.number_input(
        "COP nominal",
        min_value=0.1,
        value=float(
            specs.cop
            if specs and specs.cop
            else st.session_state.get(
                "pac_cop_nominal",
                3.0
            )
        ),
        step=0.1,
    )

    st.divider()

    # -----------------------------------------------------------------------
    # CONDITIONS DE TEMPÉRATURE
    # -----------------------------------------------------------------------

    st.markdown("### 🌡️ Conditions réelles d'opération")

    c1, c2, c3, c4 = st.columns(4)

    temp_air_reference = c1.number_input(
        "Température d'air de référence fabricant (°C)",
        value=float(
            st.session_state.get(
                "temp_air_reference",
                20.0
            )
        ),
        help=(
            "Température utilisée dans la fiche technique pour "
            "annoncer la puissance nominale."
        ),
    )

    temp_salle = c2.number_input(
        "Température réelle de la salle mécanique (°C)",
        value=float(
            st.session_state.get(
                "temp_salle",
                20.0
            )
        ),
    )

    temp_eau_entree = c3.number_input(
        "Température eau entrée PAC (°C)",
        value=float(
            st.session_state.get(
                "temp_eau_entree",
                45.0
            )
        ),
    )

    temp_eau_sortie = c4.number_input(
        "Température eau sortie PAC (°C)",
        value=float(
            st.session_state.get(
                "temp_eau_sortie",
                60.0
            )
        ),
    )

    st.divider()

    # -----------------------------------------------------------------------
    # DISPONIBILITÉ
    # -----------------------------------------------------------------------

    st.markdown("### ⏱️ Disponibilité annuelle")

    c1, c2, c3 = st.columns(3)

    heures_theoriques = c1.number_input(
        "Heures théoriques disponibles / an",
        min_value=0,
        max_value=8760,
        value=8760,
        step=100,
    )

    disponibilite_pct = c2.number_input(
        "Disponibilité réelle (%)",
        min_value=0.0,
        max_value=100.0,
        value=float(
            st.session_state.get(
                "disponibilite_pct",
                90.0
            )
        ),
        step=1.0,
        help=(
            "Permet de tenir compte des arrêts, entretien "
            "et indisponibilités."
        ),
    )

    heures_disponibles = (
        heures_theoriques
        * disponibilite_pct
        / 100
    )

    c3.metric(
        "Heures disponibles",
        f"{heures_disponibles:,.0f} h/an"
    )

    st.divider()

    # -----------------------------------------------------------------------
    # CORRECTION DE CAPACITÉ
    # -----------------------------------------------------------------------

    st.markdown("### 📉 Capacité corrigée aux conditions réelles")

    # Première approche simple :
    # coefficient modifiable tant qu'on n'a pas de courbe fabricant

    ecart_temp_air = (
        temp_salle - temp_air_reference
    )

    coefficient_correction = st.number_input(
        "Facteur de correction de capacité",
        min_value=0.1,
        max_value=1.5,
        value=float(
            st.session_state.get(
                "facteur_correction_capacite",
                1.0
            )
        ),
        step=0.01,
        help=(
            "1,00 = puissance nominale. "
            "À ajuster selon les tables de performance fabricant. "
            "À terme, ce facteur pourra être calculé automatiquement."
        ),
    )

    puissance_corrigee_kw = (
        puissance_nominale_kw
        * coefficient_correction
        * nombre_unites
    )

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "Puissance nominale totale",
        f"{puissance_nominale_kw * nombre_unites:,.1f} kW"
    )

    c2.metric(
        "Puissance corrigée",
        f"{puissance_corrigee_kw:,.1f} kW"
    )

    c3.metric(
        "Écart température air",
        f"{ecart_temp_air:+.1f} °C"
    )

    # -----------------------------------------------------------------------
    # ÉNERGIE ANNUELLE MAXIMALE FOURNISSABLE
    # -----------------------------------------------------------------------

    energie_max_kwh_an = (
        puissance_corrigee_kw
        * heures_disponibles
    )

    energie_max_mwh_an = (
        energie_max_kwh_an / 1000
    )

    st.info(
        f"Énergie thermique maximale théorique fournie : "
        f"**{energie_max_mwh_an:,.1f} MWh/an**"
    )

    # -----------------------------------------------------------------------
    # COMPARAISON AU BESOIN
    # -----------------------------------------------------------------------

    besoin_mwh_an = 0.0

    if "besoin_industriel" in st.session_state:
        besoin_mwh_an = (
            st.session_state.besoin_industriel.get(
                "energie_mwh_an",
                0.0
            )
        )

    if besoin_mwh_an > 0:

        energie_couverte_mwh = min(
            energie_max_mwh_an,
            besoin_mwh_an
        )

        couverture_pct = (
            energie_couverte_mwh
            / besoin_mwh_an
            * 100
        )

        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Besoin thermique du site",
            f"{besoin_mwh_an:,.1f} MWh/an"
        )

        c2.metric(
            "Énergie couverte par la PAC",
            f"{energie_couverte_mwh:,.1f} MWh/an"
        )

        c3.metric(
            "Part du besoin couverte",
            f"{couverture_pct:,.1f} %"
        )

    else:

        energie_couverte_mwh = 0.0
        couverture_pct = 0.0

        st.warning(
            "Aucun besoin thermique disponible depuis l'étape 3."
        )

    st.divider()

    # -----------------------------------------------------------------------
    # CONSOMMATION ÉLECTRIQUE
    # -----------------------------------------------------------------------

    st.markdown("### ⚡ Consommation électrique estimée")

    if cop_nominal > 0:

        consommation_elec_mwh = (
            energie_couverte_mwh
            / cop_nominal
        )

    else:
        consommation_elec_mwh = 0.0

    st.metric(
        "Consommation électrique PAC",
        f"{consommation_elec_mwh:,.1f} MWh/an"
    )

    # -----------------------------------------------------------------------
    # COÛT PROJET
    # -----------------------------------------------------------------------

    st.divider()

    st.markdown("### 💰 Coût du projet")

    c1, c2 = st.columns(2)

    cout_unitaire = c1.number_input(
        "Coût estimé par unité ($)",
        min_value=0.0,
        value=float(
            st.session_state.get(
                "pac_cout_unitaire",
                0.0
            )
        ),
        step=1000.0,
    )

    cout_total = (
        cout_unitaire
        * nombre_unites
    )

    c2.metric(
        "Investissement total",
        f"{cout_total:,.0f} $"
    )

    # -----------------------------------------------------------------------
    # SAUVEGARDE
    # -----------------------------------------------------------------------

    st.session_state.pac_selectionnee = {
        "modele": nom_modele,
        "nombre_unites": nombre_unites,
        "puissance_nominale_kw": puissance_nominale_kw,
        "puissance_corrigee_kw": puissance_corrigee_kw,
        "cop": cop_nominal,
        "temp_air_reference_C": temp_air_reference,
        "temp_salle_C": temp_salle,
        "temp_eau_entree_C": temp_eau_entree,
        "temp_eau_sortie_C": temp_eau_sortie,
        "disponibilite_pct": disponibilite_pct,
        "heures_disponibles_an": heures_disponibles,
        "facteur_correction_capacite": coefficient_correction,
        "energie_max_mwh_an": energie_max_mwh_an,
        "energie_couverte_mwh_an": energie_couverte_mwh,
        "couverture_pct": couverture_pct,
        "consommation_elec_mwh_an": consommation_elec_mwh,
        "cout_unitaire": cout_unitaire,
        "cout_total": cout_total,
    }

    # -----------------------------------------------------------------------
    # NAVIGATION
    # -----------------------------------------------------------------------

    st.divider()

    c1, c2 = st.columns(2)

    if c1.button(
        "← Précédent",
        key="prev_4"
    ):
        st.session_state.step = 3
        st.rerun()

    if c2.button(
        "Suivant →",
        type="primary",
        key="next_4"
    ):
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
