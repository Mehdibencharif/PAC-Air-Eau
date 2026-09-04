"""
Sélectionne et classe les modèles du catalogue selon le besoin en ECS calculé
(modules.needs) et des préférences pondérées (efficacité, prix, bruit).

Le score n'est PAS une vérité absolue — c'est une aide à la décision. Les
modèles dont il manque des données (COP, puissance...) sont marqués
"donnees_incompletes" et rétrogradés plutôt qu'exclus, pour ne pas cacher un
bon modèle juste parce que sa fiche est incomplète.
"""

import pandas as pd
from modules.needs import BesoinECS


def filtrer_et_scorer(
    df: pd.DataFrame,
    besoin: BesoinECS,
    temp_hivernale_design_C: float = -20.0,
    marge_puissance: float = 1.15,
    poids_efficacite: float = 0.5,
    poids_prix: float = 0.3,
    poids_bruit: float = 0.2,
) -> pd.DataFrame:
    df = df.copy()
    puissance_min = besoin.puissance_recommandee_kw * marge_puissance

    def compatible_climat(temp_min):
        if pd.isna(temp_min):
            return None  # inconnu
        return temp_min <= temp_hivernale_design_C

    def compatible_puissance(p):
        if pd.isna(p):
            return None
        return p >= puissance_min

    df["compatible_climat"] = df["temp_min_C"].apply(compatible_climat)
    df["compatible_puissance"] = df["puissance_kw"].apply(compatible_puissance)
    df["donnees_incompletes"] = df[["puissance_kw", "cop", "temp_min_C"]].isna().any(axis=1)

    # Normalisation simple pour le score (0-1), en ignorant les NaN
    def norm(col, inverse=False):
        s = df[col].astype(float)
        if s.dropna().empty:
            return pd.Series([0.5] * len(df), index=df.index)
        mn, mx = s.min(), s.max()
        if mx == mn:
            return pd.Series([1.0] * len(df), index=df.index)
        n = (s - mn) / (mx - mn)
        return 1 - n if inverse else n

    score_cop = norm("cop")
    score_prix = norm("prix_estime_cad", inverse=True)
    score_bruit = norm("niveau_sonore_dB", inverse=True)

    df["score"] = (
        poids_efficacite * score_cop.fillna(0.3)
        + poids_prix * score_prix.fillna(0.3)
        + poids_bruit * score_bruit.fillna(0.3)
    )

    # Pénalité si non compatible climat/puissance (mais pas exclusion totale
    # si l'info est juste manquante -> None reste neutre)
    def penalite(row):
        p = 1.0
        if row["compatible_climat"] is False:
            p -= 0.4
        if row["compatible_puissance"] is False:
            p -= 0.4
        if row["donnees_incompletes"]:
            p -= 0.1
        return max(p, 0.05)

    df["score_final"] = df["score"] * df.apply(penalite, axis=1)

    df["puissance_min_requise_kw"] = round(puissance_min, 2)

    return df.sort_values("score_final", ascending=False)
