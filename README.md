# 💧 Sélecteur de thermopompe air-eau pour eau chaude sanitaire (Québec)

Outil d'aide à la décision en Python/Streamlit pour :
1. Estimer le besoin en eau chaude sanitaire (ECS) d'un logement
2. Extraire automatiquement les caractéristiques d'une fiche technique PDF (COP, puissance, réservoir, plage de température, bruit, réfrigérant)
3. Comparer et scorer les modèles disponibles selon le besoin réel
4. Simuler les subventions applicables au Québec (LogisVert, Rénoclimat, CAMT)

## ⚠️ Important — précision sur les subventions

Les montants exacts des programmes gouvernementaux changent souvent et varient
selon le modèle précis (n° AHRI), le fournisseur d'électricité, le revenu du
ménage, etc. **Ce que génère l'outil est une estimation indicative**, pas un
montant garanti. Toutes les règles sont dans `data/subsidies_qc.yaml`, avec :
- un champ `confidence` (`officiel` vs `estimation`)
- un `source_url` vers la page officielle à consulter
- un `last_verified` à mettre à jour quand tu revérifies

**→ Avant de lancer ce projet en usage réel, vérifie et corrige les montants
dans ce fichier YAML en consultant directement :**
- Hydro-Québec — programme LogisVert
- Transition énergétique Québec — Rénoclimat
- Québec.ca — subventions rénovation (CAMT, mazout)

## Installation

```bash
git clone <ton-repo>
cd thermopompe-eco
python -m venv venv
source venv/bin/activate  # ou venv\Scripts\activate sous Windows
pip install -r requirements.txt
streamlit run app.py
```

## Structure du projet

```
thermopompe-eco/
├── app.py                     # Application Streamlit (wizard 4 étapes)
├── modules/
│   ├── needs.py                # Calcul du besoin ECS (L/jour, kWh/jour, kW requis)
│   ├── pdf_extractor.py         # Extraction par règles (regex) des fiches PDF
│   ├── catalog.py               # Gestion du catalogue de modèles
│   ├── subsidies.py             # Moteur de règles de subventions (piloté par YAML)
│   └── recommender.py           # Scoring et classement des modèles
├── data/
│   ├── subsidies_qc.yaml        # Base de règles des subventions — À METTRE À JOUR
│   └── catalog_sample.csv       # Catalogue d'exemple (placeholders à remplacer)
└── requirements.txt
```

## Limites connues de l'extraction PDF

L'extraction est basée sur des expressions régulières cherchant des motifs
courants (FR/EN) dans le texte du PDF. Elle fonctionne bien sur des fiches
techniques bien structurées et **ne fonctionnera pas** sur :
- des PDF scannés en image (nécessiterait de l'OCR, non inclus)
- des fiches avec une mise en page en tableaux complexes où le texte extrait
  perd sa structure

Dans ces cas, les champs manquants sont clairement signalés dans l'interface
et peuvent être saisis manuellement.

## Prochaines étapes suggérées

- [ ] Vérifier et corriger les montants réels dans `subsidies_qc.yaml`
- [ ] Étoffer `catalog_sample.csv` avec de vrais modèles du marché québécois
- [ ] Ajouter un export PDF/CSV du comparatif final
- [ ] Ajouter l'OCR (ex: `pytesseract`) pour les fiches scannées
- [ ] Persister le catalogue enrichi (base SQLite plutôt que CSV en mémoire)
