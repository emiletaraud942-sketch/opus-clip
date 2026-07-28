"""
Presets nommés — le point de départ SANS IA.

Règle produit : ne jamais présenter un champ texte vide. On propose des styles
nommés qui chargent chacun un jeu de paramètres. Le texte (mode texte, plus
tard) ne sert qu'à AJUSTER un EDL déjà construit à partir d'un preset.

Chaque preset décrit :
  - captions   : apparence des sous-titres,
  - background : fond (flou / uni / aucun),
  - cleaning   : agressivité du nettoyage (silences / tics),
  - director   : indications pour le futur réalisateur LLM (rythme des
                 cadrages, emphase). Ignorées tant que director.py n'existe pas.
"""

from __future__ import annotations

# Chaque valeur est un dict de surcharges appliqué par-dessus les défauts EDL.
PRESETS: dict[str, dict] = {
    "podcast_dynamique": {
        "label": "Podcast dynamique",
        "description": "Rythmé, zooms fréquents sur les punchlines, sous-titres nets.",
        "captions": {"style": "plain", "size": 68, "bold": True,
                     "primary": "#FFFFFF", "words_per_line": 3, "y": 0.74},
        "background": {"mode": "blur", "sigma": 25},
        "cleaning": {"max_gap": 0.35, "remove_fillers": True},
        "director": {"framing_rate": "high", "emphasis": True, "hold_on_speaker": True},
    },
    "sobre": {
        "label": "Sobre",
        "description": "Cadrage stable, peu d'effets, sous-titres discrets.",
        "captions": {"style": "plain", "size": 56, "bold": False,
                     "primary": "#FFFFFF", "words_per_line": 5, "y": 0.80},
        "background": {"mode": "blur", "sigma": 20},
        "cleaning": {"max_gap": 0.6, "remove_fillers": False},
        "director": {"framing_rate": "low", "emphasis": False, "hold_on_speaker": True},
    },
    "rythme": {
        "label": "Rythmé",
        "description": "Coupes serrées, cadrages serrés, énergie maximale (style viral).",
        "captions": {"style": "karaoke", "size": 74, "bold": True,
                     "primary": "#FFFFFF", "highlight": "#F43F8E",
                     "words_per_line": 2, "y": 0.68},
        "background": {"mode": "blur", "sigma": 30},
        "cleaning": {"max_gap": 0.25, "remove_fillers": True},
        "director": {"framing_rate": "high", "emphasis": True, "hold_on_speaker": True},
    },
    "pedagogique": {
        "label": "Pédagogique",
        "description": "Clair et posé, sous-titres lisibles, peu de zooms.",
        "captions": {"style": "plain", "size": 60, "bold": True,
                     "primary": "#FFFFFF", "words_per_line": 5, "y": 0.82},
        "background": {"mode": "solid", "color": "#0F0F14"},
        "cleaning": {"max_gap": 0.5, "remove_fillers": True},
        "director": {"framing_rate": "low", "emphasis": True, "hold_on_speaker": False},
    },
}

# Preset par défaut si aucun n'est choisi.
DEFAULT_PRESET = "podcast_dynamique"


def get_preset(name: str | None) -> dict:
    """Retourne le preset demandé, ou le preset par défaut si inconnu/None."""
    if name and name in PRESETS:
        return PRESETS[name]
    return PRESETS[DEFAULT_PRESET]
