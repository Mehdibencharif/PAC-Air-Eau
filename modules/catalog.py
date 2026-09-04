"""
Gestion du catalogue de modèles de thermopompes air-eau : chargement du CSV
de base, et fusion avec les fiches techniques uploadées et extraites par
pdf_extractor.
"""

import pandas as pd
from modules.pdf_extractor import SpecsExtraites

COLONNES = [
    "modele", "fabricant", "puissance_kw", "cop", "volume_reservoir_l",
    "temp_min_C", "temp_max_eau_C", "niveau_sonore_dB", "refrigerant",
    "prix_estime_cad", "source",
]


def charger_catalogue(csv_path: str) -> pd.DataFrame:
    return pd.read_csv(csv_path)


def specs_vers_ligne(specs: SpecsExtraites, nom_modele: str | None = None,
                      prix_estime: float | None = None) -> dict:
    return {
        "modele": nom_modele or specs.fichier,
        "fabricant": "extrait de la fiche PDF",
        "puissance_kw": specs.puissance_kw,
        "cop": specs.cop,
        "volume_reservoir_l": specs.volume_reservoir_l,
        "temp_min_C": specs.temp_min_C,
        "temp_max_eau_C": specs.temp_max_eau_C,
        "niveau_sonore_dB": specs.niveau_sonore_dB,
        "refrigerant": specs.refrigerant,
        "prix_estime_cad": prix_estime,
        "source": specs.fichier,
    }


def ajouter_modele(df: pd.DataFrame, ligne: dict) -> pd.DataFrame:
    nouvelle = pd.DataFrame([ligne], columns=COLONNES)
    return pd.concat([df, nouvelle], ignore_index=True)
