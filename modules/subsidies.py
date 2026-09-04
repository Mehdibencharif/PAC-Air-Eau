"""
Moteur de simulation de subventions, piloté par data/subsidies_qc.yaml.

Ce module ne "sait" rien par lui-même — toute la connaissance des programmes
est dans le YAML pour que tu puisses la mettre à jour sans toucher au code.

Sortie : une liste de SubventionEstimee, chacune avec un statut
("admissible_probable", "a_verifier", "non_applicable") plutôt qu'un simple
montant, parce que l'admissibilité réelle dépend souvent de vérifications
qu'un outil ne peut pas garantir (liste officielle de modèles, revenu du
ménage, etc.)
"""

from dataclasses import dataclass
import yaml


@dataclass
class ContexteSubvention:
    energie_actuelle: str          # ex: "mazout", "electricite", ...
    type_appareil: str = "air_water_heat_pump"  # ou "dhw_heat_pump"
    revenu_sous_median: bool = False
    combine_plusieurs_mesures: bool = False
    fait_audit_renoclimat_avant_travaux: bool = False


@dataclass
class SubventionEstimee:
    id: str
    nom: str
    administrateur: str
    statut: str                     # "admissible_probable" | "a_verifier" | "non_applicable"
    montant_min: float | None
    montant_max: float | None
    confidence: str
    source_url: str
    note: str
    conditions_a_confirmer: list


def charger_programmes(yaml_path: str) -> dict:
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _applicable(programme: dict, ctx: ContexteSubvention) -> bool:
    applies_to = programme.get("applies_to", [])
    if ctx.type_appareil not in applies_to and "any" not in applies_to:
        return False
    replaces = programme.get("replaces_energy", ["any"])
    if "any" not in replaces and ctx.energie_actuelle not in replaces:
        return False
    return True


def _statut(programme: dict, ctx: ContexteSubvention) -> str:
    pid = programme["id"]
    if pid == "camt_mazout":
        if ctx.energie_actuelle != "mazout":
            return "non_applicable"
        return "admissible_probable" if ctx.revenu_sous_median else "a_verifier"
    if pid == "renoclimat_enveloppe":
        return "admissible_probable" if ctx.fait_audit_renoclimat_avant_travaux else "a_verifier"
    if pid == "bonification_multi_mesures":
        return "admissible_probable" if ctx.combine_plusieurs_mesures else "non_applicable"
    return "a_verifier"  # par défaut : dépend de vérifications qu'on ne peut pas garantir


def simuler(yaml_path: str, ctx: ContexteSubvention) -> list[SubventionEstimee]:
    data = charger_programmes(yaml_path)
    resultats = []

    for programme in data.get("programs", []):
        if not _applicable(programme, ctx):
            continue

        statut = _statut(programme, ctx)
        calc = programme.get("calc", {})

        montant_min = montant_max = None
        if calc.get("type") == "manual_check":
            rng = calc.get("typical_range_cad")
            if rng:
                montant_min, montant_max = rng
        elif calc.get("type") == "fixed_capped":
            montant_max = calc.get("cap_cad")
            montant_min = 0
        elif calc.get("type") == "percent_bonus":
            montant_min = montant_max = None  # calculé à part, en % d'un autre montant

        if statut == "non_applicable":
            continue

        resultats.append(SubventionEstimee(
            id=programme["id"],
            nom=programme["name"],
            administrateur=programme.get("administrator", ""),
            statut=statut,
            montant_min=montant_min,
            montant_max=montant_max,
            confidence=programme.get("confidence", "estimation"),
            source_url=programme.get("source_url", ""),
            note=programme.get("note", ""),
            conditions_a_confirmer=programme.get("requires", []),
        ))

    return resultats


def total_estime(resultats: list) -> tuple[float, float]:
    """Retourne (min, max) en sommant les programmes cumulables listés comme
    admissible_probable ou a_verifier. C'est une ESTIMATION grossière —
    ne pas l'afficher comme un montant garanti."""
    lo = sum(r.montant_min or 0 for r in resultats)
    hi = sum(r.montant_max or 0 for r in resultats)
    return lo, hi
