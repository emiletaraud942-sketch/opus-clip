"""
SortClip — moteur de montage vertical piloté par EDL.

Format pivot unique (JSON déclaratif) partagé par le mode automatique, le mode
texte et l'interface d'édition. Voir CLAUDE_EDL.md pour les invariants.

Fondations posées :
  - edl.py       : le schéma EDL (vocabulaire d'opérations fermé)
  - presets.py   : styles nommés (point de départ sans IA)
  - build.py     : nettoyage transcript + construction d'un EDL depuis un preset
  - captions.py  : génération des sous-titres ASS
  - compile.py   : EDL -> FFmpeg (le SEUL module qui connaît FFmpeg)

À venir : validate.py, patch.py, store.py, director.py (LLM), UI.
"""

from .edl import (
    EDL, Interval, Canvas, Source, Background, Captions, Watermark,
    FramingEvent, EmphasisEvent, HoldOnSpeakerEvent, SpeedEvent, TextOverlayEvent,
    Framing, Transition, FRAMING_ZOOM, new_id,
)
from .presets import PRESETS, get_preset
from .build import build_edl, clean_words
from .captions import map_words_to_output, build_ass
from .compile import build_filter_complex, build_command, render, as_shell, foreground_size
from .validate import validate, Issue
from .store import EDLStore
from .patch import PatchOp, PatchResult, apply_patch, apply_text_adjustment
from .semantic import extend_to_sentence_boundaries, looks_like_sentence_start, looks_like_sentence_end
from . import director

__all__ = [
    "validate", "Issue", "EDLStore",
    "PatchOp", "PatchResult", "apply_patch", "apply_text_adjustment", "director",
    "extend_to_sentence_boundaries", "looks_like_sentence_start", "looks_like_sentence_end",
    "EDL", "Interval", "Canvas", "Source", "Background", "Captions", "Watermark",
    "FramingEvent", "EmphasisEvent", "HoldOnSpeakerEvent", "SpeedEvent", "TextOverlayEvent",
    "Framing", "Transition", "FRAMING_ZOOM", "new_id",
    "PRESETS", "get_preset",
    "build_edl", "clean_words",
    "map_words_to_output", "build_ass",
    "build_filter_complex", "build_command", "render", "as_shell", "foreground_size",
]
