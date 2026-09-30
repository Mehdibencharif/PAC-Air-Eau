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
elif st.session_state.step == 3:

    st.subheader("3. Besoin thermique et consommation d'eau chaude")

    st.caption(
        "Définis les principaux postes de consommation d'eau chaude du site. "
        "L'outil calcule ensuite le débit, le besoin thermique annuel "
        "et la puissance moyenne requise."
    )

    # -----------------------------------------------------------------------
    # CONSTANTES
    # -----------------------------------------------------------------------

    GAL_US_TO_L = 3.78541
    CP_EAU_KWH_KG_C = 0.001163
    BTU_PAR_KWH = 3412.14

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

        # Débits par défaut = anciens volumes (gal US/jour) ÷ (12 h × 60 min)
        st.session_state.postes_ecs = [

            {
                "poste": "Lavage et assainissement des équipements",
                "debit_gpm": 3200.0 / 720,
                "jours_an": 250,
                "temperature_C": 60.0,
                "inclure": True,
            },

            {
                "poste": "Nettoyage en place (CIP)",
                "debit_gpm": 1320.0 / 720,
                "jours_an": 250,
                "temperature_C": 60.0,
                "inclure": True,
            },

            {
                "poste": "Lavage des planchers et surfaces",
                "debit_gpm": 800.0 / 720,
                "jours_an": 250,
                "temperature_C": 60.0,
                "inclure": True,
            },

            {
                "poste": "Sanitaires et vestiaires du personnel",
                "debit_gpm": 530.0 / 720,
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

            debit_gpm = c2.number_input(
                "Débit (GPM)",
                min_value=0.0,
                value=float(poste["debit_gpm"]),
                step=0.1,
                format="%.2f",
                key=f"poste_debit_{i}",
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
                value=max(
                    float(poste.get("temperature_C", temp_chaude_defaut)),
                    float(temp_froide),
                ),
                step=1.0,
                key=f"poste_temp_{i}",
            )

            postes_modifies.append(
                {
                    "poste": nom_poste,
                    "debit_gpm": debit_gpm,
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
                "debit_gpm": 0.0,
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

    resultats = []

    total_debit_gpm = 0.0
    total_energie_kwh_an = 0.0

    for poste in st.session_state.postes_ecs:

        if not poste["inclure"]:
            continue

        debit_gpm = poste["debit_gpm"]
        jours_an = poste["jours_an"]
        temp_chaude = poste["temperature_C"]

        deltaT = max(
            temp_chaude - temp_froide,
            0
        )

        # GPM -> volume annuel (gal US)
        volume_annuel_gal = (
            debit_gpm
            * 60
            * heures_fonctionnement
            * jours_an
        )

        volume_annuel_L = (
            volume_annuel_gal * GAL_US_TO_L
        )

        # 1 litre d'eau ≈ 1 kg
        energie_kwh_an = (
            volume_annuel_L
            * CP_EAU_KWH_KG_C
            * deltaT
        )

        energie_btu_an = (
            energie_kwh_an * BTU_PAR_KWH
        )

        resultats.append(
            {
                "Poste de consommation": poste["poste"],
                "Débit (GPM)": debit_gpm,
                "Jours/an": jours_an,
                "Température (°C)": temp_chaude,
                "ΔT (°C)": deltaT,
                "Besoin thermique (kWh/an)": energie_kwh_an,
                "Besoin thermique (Btu/an)": energie_btu_an,
            }
        )

        total_debit_gpm += debit_gpm
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
                    "Débit (GPM)": "{:,.2f}",
                    "Jours/an": "{:,.0f}",
                    "Température (°C)": "{:.1f}",
                    "ΔT (°C)": "{:.1f}",
                    "Besoin thermique (kWh/an)": "{:,.0f}",
                    "Besoin thermique (Btu/an)": "{:,.0f}",
                }
            ),
            use_container_width=True,
        )

        total_btu_an = total_energie_kwh_an * BTU_PAR_KWH

        # Nombre total d'heures de fonctionnement par année
        total_heures_an = (
            max(
                [
                    p["jours_an"]
                    for p in st.session_state.postes_ecs
                    if p["inclure"]
                ],
                default=0
            )
            * heures_fonctionnement
        )

        # Puissance thermique moyenne pendant les heures d'opération
        if total_heures_an > 0:
            puissance_moyenne_kw = (
                total_energie_kwh_an / total_heures_an
            )
        else:
            puissance_moyenne_kw = 0.0

        # -------------------------------------------------------------------
        # INDICATEURS
        # -------------------------------------------------------------------

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Débit cumulé",
            f"{total_debit_gpm:,.2f} GPM"
        )

        c2.metric(
            "Besoin thermique",
            f"{total_energie_kwh_an:,.0f} kWh/an"
        )

        c3.metric(
            "Besoin thermique",
            f"{total_btu_an:,.0f} Btu/an"
        )

        c4.metric(
            "Puissance moyenne",
            f"{puissance_moyenne_kw:,.1f} kW"
        )

        # -------------------------------------------------------------------
        # SESSION STATE
        # -------------------------------------------------------------------

        st.session_state.besoin_industriel = {
            "debit_gpm": total_debit_gpm,
            "energie_kwh_an": total_energie_kwh_an,
            "energie_btu_an": total_btu_an,
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

elif st.session_state.step == 4:

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

    st.info(
        f"Énergie thermique maximale théorique fournie : "
        f"**{energie_max_kwh_an:,.0f} kWh/an**"
    )

    # -----------------------------------------------------------------------
    # COMPARAISON AU BESOIN
    # -----------------------------------------------------------------------

    besoin_kwh_an = 0.0

    if "besoin_industriel" in st.session_state:
        besoin_kwh_an = (
            st.session_state.besoin_industriel.get(
                "energie_kwh_an",
                0.0
            )
        )

    if besoin_kwh_an > 0:

        energie_couverte_kwh = min(
            energie_max_kwh_an,
            besoin_kwh_an
        )

        couverture_pct = (
            energie_couverte_kwh
            / besoin_kwh_an
            * 100
        )

        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Besoin thermique du site",
            f"{besoin_kwh_an:,.0f} kWh/an"
        )

        c2.metric(
            "Énergie couverte par la PAC",
            f"{energie_couverte_kwh:,.0f} kWh/an"
        )

        c3.metric(
            "Part du besoin couverte",
            f"{couverture_pct:,.1f} %"
        )

    else:

        energie_couverte_kwh = 0.0
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

        consommation_elec_kwh = (
            energie_couverte_kwh
            / cop_nominal
        )

    else:
        consommation_elec_kwh = 0.0

    st.metric(
        "Consommation électrique PAC",
        f"{consommation_elec_kwh:,.0f} kWh/an"
    )

    st.divider()

    # -----------------------------------------------------------------------
    # EFFET SUR LA SALLE MÉCANIQUE (REFROIDISSEMENT)
    # -----------------------------------------------------------------------

    st.markdown("### ❄️ Effet sur la salle mécanique")

    st.caption(
        "Une thermopompe air-eau intérieure prélève de la chaleur à l'air de la salle : "
        "chaleur extraite = énergie fournie × (1 − 1/COP). En hiver, le chauffage du bâtiment "
        "doit compenser cette perte (coût supplémentaire). En été, la salle est refroidie "
        "gratuitement (crédit si elle est climatisée)."
    )

    sel_prev = st.session_state.get("pac_selectionnee", {})

    fraction_extraite = max(0.0, 1 - 1 / cop_nominal)

    puissance_evap_kw = puissance_corrigee_kw * fraction_extraite
    chaleur_extraite_kwh_an = energie_couverte_kwh * fraction_extraite

    options_salle = ["non_chauffee", "electricite", "gaz_naturel"]

    mode_chauffage_salle = st.radio(
        "Chauffage qui compense la chaleur extraite en hiver",
        options=options_salle,
        index=options_salle.index(
            sel_prev.get(
                "salle_mode_chauffage",
                "gaz_naturel"
                if "gaz_naturel" in st.session_state.get("energie", [])
                else "electricite",
            )
        ),
        format_func=lambda x: {
            "non_chauffee": "Salle non chauffée (aucun apport)",
            "electricite": "Chauffage électrique",
            "gaz_naturel": "Chauffage au gaz naturel",
        }[x],
        horizontal=True,
    )

    if sel_prev.get("salle_mode_chauffage") == mode_chauffage_salle:
        rdt_salle_defaut = float(sel_prev.get("salle_rdt_chauffage_pct", 100.0))
    else:
        rdt_salle_defaut = 100.0 if mode_chauffage_salle == "electricite" else 80.0

    c1, c2, c3, c4 = st.columns(4)

    part_saison_chauffage_pct = c1.number_input(
        "Part de l'énergie PAC en saison de chauffage (%)",
        min_value=0.0,
        max_value=100.0,
        value=float(sel_prev.get("salle_part_saison_chauffage_pct", 60.0)),
        step=5.0,
        help=(
            "Fraction de l'énergie produite par la PAC pendant les périodes où "
            "la salle doit être chauffée. Au Québec, environ 55 à 65 % de l'année."
        ),
    )

    rendement_chauffage_salle_pct = c2.number_input(
        "Rendement du chauffage de compensation (%)",
        min_value=1.0,
        max_value=400.0,
        value=min(max(rdt_salle_defaut, 1.0), 400.0),
        step=1.0,
        disabled=(mode_chauffage_salle == "non_chauffee"),
    )

    salle_climatisee = c3.checkbox(
        "La salle est climatisée en été",
        value=bool(sel_prev.get("salle_climatisee", False)),
    )

    cop_clim = c4.number_input(
        "COP de la climatisation",
        min_value=0.5,
        max_value=10.0,
        value=float(sel_prev.get("salle_cop_clim", 3.0)),
        step=0.1,
        disabled=not salle_climatisee,
    )

    if mode_chauffage_salle == "non_chauffee":
        st.warning(
            "Salle non chauffée : l'air va se refroidir et le COP de la PAC va baisser. "
            "Aucun coût de compensation n'est compté ici — prévoir un apport de chaleur "
            "ou une source d'air suffisante."
        )

    chaleur_hiver_kwh_an = (
        chaleur_extraite_kwh_an
        * part_saison_chauffage_pct / 100
    )

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "Puissance extraite de la salle",
        f"{puissance_evap_kw:,.1f} kW",
        help=f"≈ {puissance_evap_kw / 3.517:,.1f} tonnes de réfrigération",
    )

    c2.metric(
        "Chaleur extraite",
        f"{chaleur_extraite_kwh_an:,.0f} kWh/an"
    )

    c3.metric(
        "Dont en saison de chauffage",
        f"{chaleur_hiver_kwh_an:,.0f} kWh/an"
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
        "energie_max_kwh_an": energie_max_kwh_an,
        "energie_couverte_kwh_an": energie_couverte_kwh,
        "couverture_pct": couverture_pct,
        "consommation_elec_kwh_an": consommation_elec_kwh,
        "puissance_evap_kw": puissance_evap_kw,
        "chaleur_extraite_kwh_an": chaleur_extraite_kwh_an,
        "salle_mode_chauffage": mode_chauffage_salle,
        "salle_rdt_chauffage_pct": rendement_chauffage_salle_pct,
        "salle_part_saison_chauffage_pct": part_saison_chauffage_pct,
        "salle_climatisee": salle_climatisee,
        "salle_cop_clim": cop_clim,
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
# ÉTAPE 5 — Analyse finale : énergie + PRI + estimation OSE
# ---------------------------------------------------------------------------

elif st.session_state.step == 5:

    st.subheader("5. Analyse énergétique, économique et estimation OSE")

    pac = st.session_state.get("pac_selectionnee", {})
    besoin = st.session_state.get("besoin_industriel", {})
    tarifs = st.session_state.get("tarifs", {})
    equipements = st.session_state.get("equipements", {})

    # -----------------------------------------------------------------------
    # DONNÉES PAC
    # -----------------------------------------------------------------------

    puissance_kw = float(pac.get("puissance_corrigee_kw", 0.0))
    nombre_unites = int(pac.get("nombre_unites", 1))
    cop = float(pac.get("cop", 3.0))

    energie_couverte_kwh = float(
        pac.get("energie_couverte_kwh_an", 0.0)
    )

    conso_pac_kwh = float(
        pac.get("consommation_elec_kwh_an", 0.0)
    )

    cout_projet = float(
        pac.get("cout_total", 0.0)
    )

    # -----------------------------------------------------------------------
    # TARIFS
    # -----------------------------------------------------------------------

    prix_elec = float(
        tarifs.get(
            "electricite",
            {}
        ).get("cout_moyen", 0.12)
    )

    prix_gaz = float(
        tarifs.get(
            "gaz_naturel",
            {}
        ).get("cout_moyen", 0.42)
    )

    # -----------------------------------------------------------------------
    # CHOIX DE LA RÉFÉRENCE
    # -----------------------------------------------------------------------

    st.markdown("### Situation de référence — mix énergétique actuel")

    st.caption(
        "Répartis le besoin thermique entre les sources d'énergie actuelles du site. "
        "Les rendements viennent de l'étape 1 et les prix de l'étape 1 ou 2 (factures). "
        "La thermopompe remplace cette répartition."
    )

    # Contenu énergétique (kWh par unité physique) — valeurs approximatives
    KWH_PAR_UNITE = {
        "electricite": 1.0,
        "gaz_naturel": 10.55,   # kWh/m³
        "mazout": 10.7,         # kWh/L
        "propane": 7.08,        # kWh/L
    }

    UNITE_PHYSIQUE = {
        "electricite": "kWh",
        "gaz_naturel": "m³",
        "mazout": "L",
        "propane": "L",
    }

    prix_reference = {
        "electricite": prix_elec,
        "gaz_naturel": prix_gaz,
        "mazout": float(tarifs.get("mazout", {}).get("cout_moyen", 0.0)),
        "propane": float(tarifs.get("propane", {}).get("cout_moyen", 0.0)),
    }

    sources_site = st.session_state.get("energie", ["electricite"])
    sources_ref = [s for s in sources_site if s in KWH_PAR_UNITE]
    sources_ignorees = [s for s in sources_site if s not in KWH_PAR_UNITE]

    if sources_ignorees:
        st.warning(
            "Sources non prises en compte dans le calcul (pas de contenu énergétique défini) : "
            + ", ".join(ENERGIE_LABELS.get(s, s) for s in sources_ignorees)
        )

    if not sources_ref:
        sources_ref = ["electricite"]

    part_defaut = 100.0 / len(sources_ref)

    parts_saisies = {}
    rendements_ref = {}

    for src in sources_ref:

        label_src = ENERGIE_LABELS.get(src, src)
        unite_src = UNITE_PHYSIQUE[src]

        c1, c2, c3 = st.columns(3)

        parts_saisies[src] = c1.number_input(
            f"Part du besoin — {label_src} (%)",
            min_value=0.0,
            max_value=100.0,
            value=float(part_defaut),
            step=5.0,
        )

        rdt_defaut = float(
            equipements.get(src, {}).get(
                "rendement_pct",
                100.0 if src == "electricite" else 80.0
            )
        )

        rendements_ref[src] = c2.number_input(
            f"Rendement — {label_src} (%)",
            min_value=1.0,
            max_value=400.0,
            value=min(max(rdt_defaut, 1.0), 400.0),
            step=1.0,
        )

        c3.metric(
            f"Prix — {label_src}",
            f"{prix_reference[src]:.4f} $/{unite_src}"
        )

        if prix_reference[src] <= 0:
            st.warning(
                f"Prix de {label_src} à 0 $ : renseigne-le à l'étape 1 ou 2, "
                "sinon le coût de référence est sous-estimé."
            )

    total_parts = sum(parts_saisies.values())

    if total_parts <= 0:
        st.error("Les parts totalisent 0 % : répartition égale utilisée pour le calcul.")
        fractions = {src: 1.0 / len(sources_ref) for src in sources_ref}
    else:
        if abs(total_parts - 100.0) > 0.01:
            st.warning(
                f"Les parts totalisent {total_parts:.0f} % au lieu de 100 % — "
                "elles sont normalisées automatiquement pour le calcul."
            )
        fractions = {src: parts_saisies[src] / total_parts for src in sources_ref}

    # -----------------------------------------------------------------------
    # ÉNERGIE DE RÉFÉRENCE
    # -----------------------------------------------------------------------

    cout_pac = conso_pac_kwh * prix_elec

    cout_reference = 0.0
    energie_evitee = 0.0
    unite_evitee = "kWh équiv."

    lignes_reference = []

    for src in sources_ref:

        energie_utile_kwh = energie_couverte_kwh * fractions[src]

        # Énergie primaire consommée par l'équipement existant
        energie_primaire_kwh = (
            energie_utile_kwh
            / (rendements_ref[src] / 100)
        )

        quantite = (
            energie_primaire_kwh
            / KWH_PAR_UNITE[src]
        )

        cout_src = (
            quantite
            * prix_reference[src]
        )

        cout_reference += cout_src
        energie_evitee += energie_primaire_kwh

        lignes_reference.append(
            {
                "Source": ENERGIE_LABELS.get(src, src),
                "Part (%)": fractions[src] * 100,
                "Besoin couvert (kWh/an)": energie_utile_kwh,
                "Rendement (%)": rendements_ref[src],
                "Énergie consommée (kWh/an)": energie_primaire_kwh,
                "Quantité": quantite,
                "Unité": f"{UNITE_PHYSIQUE[src]}/an",
                "Coût de référence ($/an)": cout_src,
            }
        )

    st.dataframe(
        pd.DataFrame(lignes_reference).style.format(
            {
                "Part (%)": "{:.1f}",
                "Besoin couvert (kWh/an)": "{:,.0f}",
                "Rendement (%)": "{:.1f}",
                "Énergie consommée (kWh/an)": "{:,.0f}",
                "Quantité": "{:,.0f}",
                "Coût de référence ($/an)": "{:,.0f}",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    # -----------------------------------------------------------------------
    # ÉCONOMIES
    # -----------------------------------------------------------------------

    # Effet sur la salle mécanique (chaleur extraite de l'air par la PAC)
    chaleur_extraite_kwh = float(pac.get("chaleur_extraite_kwh_an", 0.0))
    mode_salle = pac.get("salle_mode_chauffage", "electricite")
    part_hiver = float(pac.get("salle_part_saison_chauffage_pct", 60.0)) / 100
    rdt_salle = max(float(pac.get("salle_rdt_chauffage_pct", 100.0)) / 100, 0.01)
    salle_climatisee = bool(pac.get("salle_climatisee", False))
    cop_clim = max(float(pac.get("salle_cop_clim", 3.0)), 0.01)

    chaleur_hiver_kwh = chaleur_extraite_kwh * part_hiver
    chaleur_ete_kwh = chaleur_extraite_kwh - chaleur_hiver_kwh

    if mode_salle == "electricite":
        cout_penalite_salle = chaleur_hiver_kwh / rdt_salle * prix_elec
    elif mode_salle == "gaz_naturel":
        cout_penalite_salle = (
            chaleur_hiver_kwh
            / rdt_salle
            / KWH_PAR_UNITE["gaz_naturel"]
            * prix_gaz
        )
    else:
        cout_penalite_salle = 0.0

    if salle_climatisee:
        credit_clim_salle = chaleur_ete_kwh / cop_clim * prix_elec
    else:
        credit_clim_salle = 0.0

    # Positif = coût supplémentaire, négatif = gain
    effet_salle_net = cout_penalite_salle - credit_clim_salle

    economie_brute = (
        cout_reference
        - cout_pac
    )

    economie_annuelle = (
        economie_brute
        - effet_salle_net
    )

    if economie_annuelle > 0:
        pri_avant = (
            cout_projet
            / economie_annuelle
        )
    else:
        pri_avant = None

    # -----------------------------------------------------------------------
    # ESTIMATION OSE
    # -----------------------------------------------------------------------

    st.divider()

    st.markdown("### Estimation OSE")

    taux_ose_kw = st.number_input(
        "Taux d'aide OSE utilisé pour l'estimation ($/kW)",
        min_value=0.0,
        value=530.0,
        step=10.0,
        help=(
            "Valeur de travail à valider avec la version officielle "
            "de l'outil OSE applicable au projet."
        ),
    )

    puissance_ose_kw = st.number_input(
        "Puissance admissible OSE (kW)",
        min_value=0.0,
        value=float(puissance_kw),
        step=1.0,
    )

    appui_ose_brut = (
        puissance_ose_kw
        * taux_ose_kw
    )

    plafond_pct = st.number_input(
        "Plafond d'aide (% du coût du projet)",
        min_value=0.0,
        max_value=100.0,
        value=100.0,
        step=5.0,
    )

    plafond_aide = (
        cout_projet
        * plafond_pct / 100
    )

    appui_ose = min(
        appui_ose_brut,
        plafond_aide
    )

    cout_net = (
        cout_projet
        - appui_ose
    )

    if economie_annuelle > 0:
        pri_apres = (
            cout_net
            / economie_annuelle
        )
    else:
        pri_apres = None

    # -----------------------------------------------------------------------
    # AFFICHAGE RÉSULTATS
    # -----------------------------------------------------------------------

    st.divider()

    st.markdown("### Résultats")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Puissance PAC",
        f"{puissance_kw:,.1f} kW"
    )

    c2.metric(
        "Énergie couverte",
        f"{energie_couverte_kwh:,.0f} kWh/an"
    )

    c3.metric(
        "Consommation PAC",
        f"{conso_pac_kwh:,.0f} kWh/an"
    )

    c4.metric(
        "COP",
        f"{cop:.2f}"
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        f"Énergie évitée ({unite_evitee})",
        f"{energie_evitee:,.0f}"
    )

    c2.metric(
        "Coût de référence",
        f"{cout_reference:,.0f} $/an"
    )

    c3.metric(
        "Coût PAC",
        f"{cout_pac:,.0f} $/an"
    )

    c4.metric(
        "Économie annuelle nette",
        f"{economie_annuelle:,.0f} $/an"
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Économie avant effet salle",
        f"{economie_brute:,.0f} $/an"
    )

    c2.metric(
        "Pénalité chauffage salle",
        f"{cout_penalite_salle:,.0f} $/an"
    )

    c3.metric(
        "Crédit climatisation salle",
        f"{credit_clim_salle:,.0f} $/an"
    )

    c4.metric(
        "Effet net salle (+ = coût)",
        f"{effet_salle_net:+,.0f} $/an"
    )

    st.divider()

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Coût projet",
        f"{cout_projet:,.0f} $"
    )

    c2.metric(
        "Aide OSE estimée",
        f"{appui_ose:,.0f} $"
    )

    c3.metric(
        "PRI avant aide",
        (
            f"{pri_avant:.1f} ans"
            if pri_avant is not None
            else "Non rentable"
        )
    )

    c4.metric(
        "PRI après aide",
        (
            f"{pri_apres:.1f} ans"
            if pri_apres is not None
            else "Non rentable"
        )
    )

    # -----------------------------------------------------------------------
    # DIAGNOSTIC
    # -----------------------------------------------------------------------

    if pri_apres is None:
        st.error(
            "🔴 Le projet augmente actuellement les coûts d'exploitation."
        )

    elif pri_apres <= 5:
        st.success(
            "🟢 Projet économiquement très intéressant."
        )

    elif pri_apres <= 10:
        st.success(
            "🟢 Projet économiquement intéressant."
        )

    elif pri_apres <= 15:
        st.warning(
            "🟡 Projet à considérer selon les objectifs de décarbonation "
            "et les aides disponibles."
        )

    else:
        st.error(
            "🔴 Rentabilité économique faible avec les hypothèses actuelles."
        )

    # -----------------------------------------------------------------------
    # DIAGNOSTIC DÉTAILLÉ : COÛT PAR kWh UTILE ET COP MINIMUM
    # -----------------------------------------------------------------------

    if energie_couverte_kwh > 0:

        cout_kwh_ref = cout_reference / energie_couverte_kwh
        cout_kwh_pac = cout_pac / energie_couverte_kwh
        cout_kwh_salle = effet_salle_net / energie_couverte_kwh

        st.caption(
            f"Coût par kWh de chaleur utile — référence : {cout_kwh_ref:.4f} $/kWh · "
            f"PAC (électricité) : {cout_kwh_pac:.4f} $/kWh · "
            f"effet salle : {cout_kwh_salle:+.4f} $/kWh. "
            "Le projet est rentable seulement si la référence coûte plus cher que "
            "PAC + effet salle."
        )

        def economie_nette_pour_cop(cop_test):
            fraction = max(0.0, 1 - 1 / cop_test)
            chaleur = energie_couverte_kwh * fraction
            chaleur_h = chaleur * part_hiver
            chaleur_e = chaleur - chaleur_h

            if mode_salle == "electricite":
                pen = chaleur_h / rdt_salle * prix_elec
            elif mode_salle == "gaz_naturel":
                pen = (
                    chaleur_h / rdt_salle
                    / KWH_PAR_UNITE["gaz_naturel"] * prix_gaz
                )
            else:
                pen = 0.0

            cred = (
                chaleur_e / cop_clim * prix_elec
                if salle_climatisee else 0.0
            )

            return (
                cout_reference
                - (energie_couverte_kwh / cop_test) * prix_elec
                - (pen - cred)
            )

        cop_min = None
        cop_test = 1.5
        while cop_test <= 10.0:
            if economie_nette_pour_cop(cop_test) > 0:
                cop_min = cop_test
                break
            cop_test += 0.05

        if cop_min is None:
            st.caption(
                "Aucun COP jusqu'à 10 ne donne une économie nette positive avec "
                "ces hypothèses (prix des énergies et effet sur la salle)."
            )
        else:
            st.caption(
                f"COP minimum pour une économie nette positive : ≈ {cop_min:.2f} "
                f"(COP actuel : {cop:.2f})."
            )

    # -----------------------------------------------------------------------
    # TABLEAU SYNTHÈSE TYPE OSE
    # -----------------------------------------------------------------------

    st.markdown("### Synthèse du scénario")

    df_synthese = pd.DataFrame(
        {
            "Paramètre": [
                "Nombre d'unités",
                "Puissance admissible",
                "Besoin couvert",
                "Électricité consommée",
                "Énergie de référence évitée",
                "Économie annuelle nette",
                "Effet net sur la salle (+ = coût)",
                "Investissement",
                "Aide OSE estimée",
                "PRI avant aide",
                "PRI après aide",
            ],

            "Valeur": [
                nombre_unites,
                f"{puissance_ose_kw:,.1f} kW",
                f"{energie_couverte_kwh:,.0f} kWh/an",
                f"{conso_pac_kwh:,.0f} kWh/an",
                f"{energie_evitee:,.0f} {unite_evitee}/an",
                f"{economie_annuelle:,.0f} $/an",
                f"{effet_salle_net:+,.0f} $/an",
                f"{cout_projet:,.0f} $",
                f"{appui_ose:,.0f} $",
                (
                    f"{pri_avant:.1f} ans"
                    if pri_avant is not None
                    else "Non rentable"
                ),
                (
                    f"{pri_apres:.1f} ans"
                    if pri_apres is not None
                    else "Non rentable"
                ),
            ],
        }
    )

    st.dataframe(
        df_synthese,
        use_container_width=True,
        hide_index=True,
    )

    # -----------------------------------------------------------------------
    # NAVIGATION
    # -----------------------------------------------------------------------

    st.divider()

    if st.button(
        "← Précédent",
        key="prev_5"
    ):
        st.session_state.step = 4
        st.rerun()
