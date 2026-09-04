"""
Extraction par règles (regex) des caractéristiques techniques d'une
thermopompe air-eau à partir d'une fiche PDF.

Approche volontairement "low-tech" et transparente : on cherche des motifs
texte courants (FR/EN) autour des mots-clés habituels des fiches fabricants
(COP, puissance, volume de réservoir, plage de température, niveau sonore,
réfrigérant). Ça ne remplacera jamais une lecture humaine sur des fiches
mal structurées ou scannées en image — dans ce cas, `extract_text` renverra
peu ou pas de texte et il faudra OCR (non couvert ici) ou saisie manuelle.

Chaque champ retourné est accompagné de la ligne de texte source, pour que
l'utilisateur puisse vérifier rapidement d'où vient la valeur.
"""

import re
from dataclasses import dataclass, field

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None


@dataclass
class SpecsExtraites:
    fichier: str
    texte_brut: str = ""
    cop: float | None = None
    puissance_kw: float | None = None
    volume_reservoir_l: float | None = None
    temp_min_C: float | None = None
    temp_max_eau_C: float | None = None
    niveau_sonore_dB: float | None = None
    refrigerant: str | None = None
    lignes_source: dict = field(default_factory=dict)


# Chaque pattern capture un nombre (float), motif insensible à la casse.
# On garde plusieurs variantes FR/EN car les fiches sont rarement uniformes.
PATTERNS = {
    "cop": [
        r"COP\s*[:=]?\s*(\d+[.,]\d+)",
        r"coefficient\s+de\s+performance\s*[:=]?\s*(\d+[.,]\d+)",
    ],
    "puissance_kw": [
        r"puissance\s+(?:thermique\s+)?(?:nominale\s+)?[:=]?\s*(\d+[.,]?\d*)\s*kW",
        r"heating\s+capacity\s*[:=]?\s*(\d+[.,]?\d*)\s*kW",
        r"capacité\s+(?:de\s+)?chauffage\s*[:=]?\s*(\d+[.,]?\d*)\s*kW",
    ],
    "volume_reservoir_l": [
        r"(?:volume|capacité)\s+(?:du\s+)?réservoir\s*[:=]?\s*(\d+[.,]?\d*)\s*L",
        r"tank\s+(?:volume|capacity)\s*[:=]?\s*(\d+[.,]?\d*)\s*L",
        r"(\d+[.,]?\d*)\s*litres?\b",
    ],
    "temp_min_C": [
        r"temp[ée]rature\s+(?:ext[ée]rieure\s+)?min(?:imale?)?\s*(?:de\s+)?(?:fonctionnement\s+)?[:=]?\s*(-?\d+[.,]?\d*)\s*°?C",
        r"min(?:imum)?\s+operating\s+temp(?:erature)?\s*[:=]?\s*(-?\d+[.,]?\d*)\s*°?C",
    ],
    "temp_max_eau_C": [
        r"temp[ée]rature\s+(?:de\s+)?(?:l['’]eau\s+)?max(?:imale?)?\s*[:=]?\s*(\d+[.,]?\d*)\s*°?C",
        r"max(?:imum)?\s+water\s+temp(?:erature)?\s*[:=]?\s*(\d+[.,]?\d*)\s*°?C",
    ],
    "niveau_sonore_dB": [
        r"niveau\s+sonore\s*[:=]?\s*(\d+[.,]?\d*)\s*dB",
        r"sound\s+(?:level|pressure)\s*[:=]?\s*(\d+[.,]?\d*)\s*dB",
    ],
}

REFRIGERANT_PATTERN = r"\bR[- ]?(134a|410A|32|290|744|454B)\b"


def _to_float(txt: str) -> float:
    return float(txt.replace(",", "."))


def extract_text(pdf_path: str) -> str:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber n'est pas installé (pip install pdfplumber)")
    texte = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            texte.append(t)
    return "\n".join(texte)


def _trouver_ligne(texte: str, motif: str) -> str | None:
    """Retourne la ligne complète où le motif a matché, pour traçabilité."""
    for ligne in texte.splitlines():
        if re.search(motif, ligne, re.IGNORECASE):
            return ligne.strip()
    return None


def extraire_specs(pdf_path: str, nom_fichier: str | None = None) -> SpecsExtraites:
    texte = extract_text(pdf_path)
    result = SpecsExtraites(fichier=nom_fichier or pdf_path, texte_brut=texte)

    for champ, motifs in PATTERNS.items():
        for motif in motifs:
            m = re.search(motif, texte, re.IGNORECASE)
            if m:
                setattr(result, champ, _to_float(m.group(1)))
                result.lignes_source[champ] = _trouver_ligne(texte, motif)
                break

    m = re.search(REFRIGERANT_PATTERN, texte, re.IGNORECASE)
    if m:
        result.refrigerant = "R" + m.group(1)
        result.lignes_source["refrigerant"] = _trouver_ligne(texte, REFRIGERANT_PATTERN)

    return result


def champs_manquants(specs: SpecsExtraites) -> list[str]:
    """Liste des champs importants non trouvés — à saisir manuellement."""
    champs = ["cop", "puissance_kw", "volume_reservoir_l", "temp_min_C",
              "temp_max_eau_C", "niveau_sonore_dB", "refrigerant"]
    return [c for c in champs if getattr(specs, c) is None]
