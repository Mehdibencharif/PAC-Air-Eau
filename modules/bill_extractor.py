"""
Extraction du prix réel de l'énergie ($/kWh ou $/m³) à partir d'une facture
Hydro-Québec (électricité) ou Énergir (gaz naturel).

Supporte 3 cas :
- PDF avec texte natif (téléchargé du site du fournisseur)         -> pdfplumber
- PDF scanné en image (peu ou pas de texte extractible)            -> OCR (pdf2image + pytesseract)
- Photo/scan de la facture papier (.jpg/.jpeg/.png)                -> OCR (pytesseract)

Comme pour pdf_extractor.py, c'est une approche par règles (regex) sur des
motifs de texte courants FR. L'OCR n'est jamais fiable à 100% sur une photo
de facture (angle, éclairage, froissement...) — les valeurs détectées
doivent toujours être présentées comme éditables à l'utilisateur, jamais
comme un résultat garanti.

Dépendances additionnelles à ajouter à requirements.txt :
    pytesseract
    Pillow
    pdf2image

Et, pour l'OCR, des paquets système (à ajouter dans packages.txt si déploiement
sur Streamlit Community Cloud) :
    tesseract-ocr
    tesseract-ocr-fra
    poppler-utils      (requis par pdf2image pour rasteriser les PDF)

Sans ces paquets système, l'OCR échoue silencieusement (les fonctions
retournent un texte vide) et l'utilisateur devra saisir les valeurs
manuellement — c'est un comportement volontairement dégradé plutôt qu'un
crash.
"""

import os
import re
from dataclasses import dataclass, field

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None

try:
    import pytesseract
    from PIL import Image
except ImportError:  # pragma: no cover
    pytesseract = None
    Image = None

try:
    from pdf2image import convert_from_path
except ImportError:  # pragma: no cover
    convert_from_path = None


@dataclass
class PrixEnergie:
    fichier: str
    type_source: str                  # "electricite" | "gaz_naturel" | "autre"
    montant_total: float | None
    consommation: float | None        # kWh si électricité, m³ si gaz naturel
    methode: str                      # "texte_pdf" | "ocr_pdf_scanne" | "ocr_image"
    texte_brut: str = ""
    lignes_source: dict = field(default_factory=dict)

    @property
    def prix_unitaire(self):
        if self.consommation and self.consommation > 0 and self.montant_total is not None:
            return self.montant_total / self.consommation
        return None


# ---------------------------------------------------------------------------
# Motifs de reconnaissance (FR) — à enrichir au fil des vraies factures testées
# ---------------------------------------------------------------------------
HQ_PATTERNS = {
    "consommation": [
        r"consommation\s+totale\D{0,30}?([\d\s]+[.,]?\d*)\s*kWh",
        r"([\d\s]+[.,]?\d*)\s*kWh\s+factur[ée]s?",
        r"nombre\s+de\s+kWh\D{0,10}([\d\s]+[.,]?\d*)",
        r"consommation\D{0,30}?([\d\s]+[.,]?\d*)\s*kWh",
    ],
    "montant_total": [
        r"montant\s+de\s+(?:votre\s+)?facture\D{0,20}([\d\s]+[.,]\d{2})\s*\$",
        r"total\s+à\s+payer\D{0,20}([\d\s]+[.,]\d{2})\s*\$",
        r"montant\s+total\D{0,20}([\d\s]+[.,]\d{2})\s*\$",
        r"montant\s+d[uû]\D{0,20}([\d\s]+[.,]\d{2})\s*\$",
    ],
}

ENERGIR_PATTERNS = {
    "consommation": [
        r"consommation\D{0,30}?([\d\s]+[.,]?\d*)\s*m\s*3",
        r"consommation\D{0,30}?([\d\s]+[.,]?\d*)\s*m³",
        r"volume\s+factur[ée]\D{0,10}([\d\s]+[.,]?\d*)\s*m³",
    ],
    "montant_total": [
        r"montant\s+total\D{0,20}([\d\s]+[.,]\d{2})\s*\$",
        r"total\s+à\s+payer\D{0,20}([\d\s]+[.,]\d{2})\s*\$",
        r"montant\s+d[uû]\D{0,20}([\d\s]+[.,]\d{2})\s*\$",
    ],
}


def _to_float(s: str):
    if not s:
        return None
    s = s.strip().replace("\xa0", " ").replace(" ", "")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _ligne_contenant(texte: str, pos: int) -> str:
    debut = texte.rfind("\n", 0, pos) + 1
    fin = texte.find("\n", pos)
    if fin == -1:
        fin = len(texte)
    return texte[debut:fin].strip()


def _chercher(texte: str, patterns: list):
    for pat in patterns:
        m = re.search(pat, texte, re.IGNORECASE)
        if m:
            valeur = _to_float(m.group(1))
            if valeur is not None:
                return valeur, _ligne_contenant(texte, m.start())
    return None, None


def _detecter_fournisseur(texte: str) -> str:
    t = texte.lower().replace("é", "e").replace("è", "e")
    if "hydro-quebec" in t.replace(" ", "") or "hydro quebec" in t or "hydroquebec" in t.replace(" ", "").replace("-", ""):
        return "electricite"
    if "energir" in t or "gaz metro" in t:
        return "gaz_naturel"
    return "autre"


def _texte_depuis_pdf(chemin: str) -> str:
    if pdfplumber is None:
        return ""
    morceaux = []
    with pdfplumber.open(chemin) as pdf:
        for page in pdf.pages:
            morceaux.append(page.extract_text() or "")
    return "\n".join(morceaux)


def _ocr_depuis_pdf(chemin: str) -> str:
    """PDF scanné (image) sans texte natif — rasterise puis OCR chaque page."""
    if convert_from_path is None or pytesseract is None:
        return ""
    try:
        pages = convert_from_path(chemin, dpi=300)
    except Exception:
        return ""
    return "\n".join(pytesseract.image_to_string(p, lang="fra+eng") for p in pages)


def _ocr_depuis_image(chemin: str) -> str:
    if pytesseract is None or Image is None:
        return ""
    try:
        img = Image.open(chemin)
    except Exception:
        return ""
    return pytesseract.image_to_string(img, lang="fra+eng")


def extraire_prix_facture(chemin: str, nom_fichier: str = "") -> PrixEnergie:
    ext = os.path.splitext(chemin)[1].lower()

    if ext == ".pdf":
        texte = _texte_depuis_pdf(chemin)
        methode = "texte_pdf"
        if len(texte.strip()) < 50:
            # Probablement un PDF scanné (juste une image encapsulée) : bascule en OCR
            texte = _ocr_depuis_pdf(chemin)
            methode = "ocr_pdf_scanne"
    elif ext in (".jpg", ".jpeg", ".png"):
        texte = _ocr_depuis_image(chemin)
        methode = "ocr_image"
    else:
        raise ValueError(f"Format non supporté : {ext}")

    type_source = _detecter_fournisseur(texte)
    lignes_source = {}

    if type_source == "electricite":
        conso, ligne_c = _chercher(texte, HQ_PATTERNS["consommation"])
        montant, ligne_m = _chercher(texte, HQ_PATTERNS["montant_total"])
    elif type_source == "gaz_naturel":
        conso, ligne_c = _chercher(texte, ENERGIR_PATTERNS["consommation"])
        montant, ligne_m = _chercher(texte, ENERGIR_PATTERNS["montant_total"])
    else:
        # Fournisseur non identifié : on tente quand même les deux jeux de motifs
        conso, ligne_c = _chercher(texte, HQ_PATTERNS["consommation"] + ENERGIR_PATTERNS["consommation"])
        montant, ligne_m = _chercher(texte, HQ_PATTERNS["montant_total"])

    if ligne_c:
        lignes_source["consommation"] = ligne_c
    if ligne_m:
        lignes_source["montant_total"] = ligne_m

    return PrixEnergie(
        fichier=nom_fichier or chemin,
        type_source=type_source,
        montant_total=montant,
        consommation=conso,
        methode=methode,
        texte_brut=texte,
        lignes_source=lignes_source,
    )
