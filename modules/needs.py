"""
Estimation du besoin en eau chaude sanitaire (ECS) et de la puissance
de thermopompe nécessaire.

Hypothèses simplifiées (à ajuster selon ton contexte réel / normes CSA-B211,
guide RNCan, etc.) :
- Consommation moyenne par personne : ~50 L/jour d'eau chaude à 55°C (valeur
  courante utilisée en dimensionnement résidentiel québécois — ajustable).
- Température d'entrée d'eau froide au Québec : ~7°C l'hiver (pire cas).
- Capacité thermique de l'eau : 4.186 kJ/(kg·K).
"""

from dataclasses import dataclass

EAU_CHALEUR_SPECIFIQUE_KJ = 4.186  # kJ/(kg.K)

PROFILS_L_PAR_JOUR = {
    "petit": 150,      # 1-2 personnes, usage économe
    "moyen": 250,       # famille de 3-4, usage standard
    "grand": 400,       # 5+ personnes ou forte consommation (bains fréquents, etc.)
}


@dataclass
class BesoinECS:
    volume_l_par_jour: float
    temp_froide_C: float
    temp_consigne_C: float
    heures_fonctionnement_jour: float

    @property
    def delta_t(self) -> float:
        return max(self.temp_consigne_C - self.temp_froide_C, 1.0)

    @property
    def energie_kwh_jour(self) -> float:
        # kJ -> kWh : / 3600
        kj = self.volume_l_par_jour * EAU_CHALEUR_SPECIFIQUE_KJ * self.delta_t
        return kj / 3600

    @property
    def puissance_recommandee_kw(self) -> float:
        """Puissance thermique nécessaire si l'appareil fonctionne
        heures_fonctionnement_jour heures par jour pour combler le besoin."""
        return self.energie_kwh_jour / max(self.heures_fonctionnement_jour, 1)


def estimer_besoin(
    nb_personnes: int | None = None,
    profil: str | None = None,
    volume_manuel_l: float | None = None,
    temp_froide_C: float = 7.0,
    temp_consigne_C: float = 55.0,
    heures_fonctionnement_jour: float = 12.0,
) -> BesoinECS:
    """Retourne un objet BesoinECS à partir de l'une des 3 méthodes :
    nombre de personnes, profil (petit/moyen/grand), ou volume manuel en L/jour.
    Priorité : volume_manuel > nb_personnes > profil.
    """
    if volume_manuel_l is not None:
        volume = volume_manuel_l
    elif nb_personnes is not None:
        volume = nb_personnes * 50  # L/jour/personne, valeur courante
    elif profil is not None:
        volume = PROFILS_L_PAR_JOUR.get(profil, PROFILS_L_PAR_JOUR["moyen"])
    else:
        volume = PROFILS_L_PAR_JOUR["moyen"]

    return BesoinECS(
        volume_l_par_jour=volume,
        temp_froide_C=temp_froide_C,
        temp_consigne_C=temp_consigne_C,
        heures_fonctionnement_jour=heures_fonctionnement_jour,
    )
