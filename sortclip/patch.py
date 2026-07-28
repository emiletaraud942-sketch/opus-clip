"""
Patchs sur un EDL — les ajustements NE régénèrent jamais l'EDL.

Deux niveaux :
  - apply_patch(edl, ops)         : patchs bas niveau, par `id` d'événement
                                    (remove / modify / add). Ne lève jamais.
  - apply_text_adjustment(edl, s) : traduit une consigne en français (« moins
                                    de zooms », « sous-titres plus gros »…) en
                                    patchs déterministes, SANS IA. C'est le
                                    chemin rapide et gratuit ; le réalisateur
                                    LLM (director.adjust_with_text) sert de
                                    repli pour les consignes libres.

Invariant : l'utilisateur doit voir changer ce qu'il a demandé et RIEN d'autre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .edl import EDL


@dataclass
class PatchOp:
    action: str                       # "remove" | "modify" | "add"
    event_id: str | None = None
    field: str | None = None
    value: Any = None
    new: dict | None = None           # pour "add" : dict d'un événement


@dataclass
class PatchResult:
    edl: EDL
    applied: list[PatchOp] = field(default_factory=list)
    rejected: list[PatchOp] = field(default_factory=list)


def apply_patch(edl: EDL, ops: list[PatchOp]) -> PatchResult:
    """Applique une liste de patchs. Ne lève jamais : chaque op est soit
    appliquée, soit rejetée. Les événements sont référencés par `id` stable."""
    events = list(edl.events)
    by_id = {e.id: i for i, e in enumerate(events)}
    applied: list[PatchOp] = []
    rejected: list[PatchOp] = []

    for op in ops:
        try:
            if op.action == "remove":
                idx = by_id.get(op.event_id)
                if idx is None:
                    rejected.append(op); continue
                events[idx] = None
                applied.append(op)

            elif op.action == "modify":
                idx = by_id.get(op.event_id)
                if idx is None or events[idx] is None or not op.field:
                    rejected.append(op); continue
                events[idx] = events[idx].model_copy(update={op.field: op.value})
                applied.append(op)

            elif op.action == "add" and op.new:
                from .edl import (FramingEvent, EmphasisEvent,
                                  HoldOnSpeakerEvent, SpeedEvent)
                kinds = {"framing": FramingEvent, "emphasis": EmphasisEvent,
                         "hold_on_speaker": HoldOnSpeakerEvent, "speed": SpeedEvent}
                cls = kinds.get(op.new.get("op"))
                if not cls:
                    rejected.append(op); continue
                events.append(cls(**{k: v for k, v in op.new.items() if k != "op"}))
                applied.append(op)
            else:
                rejected.append(op)
        except Exception:
            rejected.append(op)

    events = [e for e in events if e is not None]
    return PatchResult(edl=edl.model_copy(update={"events": events}),
                       applied=applied, rejected=rejected)


# --------------------------------------------------------------------------
# Ajustement par texte — déterministe, sans IA, pour les consignes courantes.
# --------------------------------------------------------------------------

_COLOR_WORDS = {
    "blanc": "#FFFFFF", "jaune": "#FFEB3B", "rose": "#F43F8E",
    "cyan": "#22E5FF", "vert": "#22FF88", "orange": "#F39200",
}


def apply_text_adjustment(edl: EDL, instruction: str) -> tuple[EDL, list[str]]:
    """Traduit une consigne FR en patchs déterministes. Retourne (EDL, notes).
    Ne touche QUE ce que la consigne vise. Renvoie une note par ajustement (ou
    une note « non compris » si rien ne correspond — au caller de basculer sur
    le réalisateur LLM)."""
    s = (instruction or "").lower()
    notes: list[str] = []
    edl2 = edl

    # --- Cadrages / zooms ---
    if "moins de zoom" in s or "moins de cadrage" in s or "trop de zoom" in s:
        framings = [e for e in edl2.events if e.op == "framing"]
        if framings:
            # On garde un cadrage sur deux (le premier, le troisième…).
            keep_ids = {e.id for i, e in enumerate(sorted(framings, key=lambda x: x.t)) if i % 2 == 0}
            new_events = [e for e in edl2.events if e.op != "framing" or e.id in keep_ids]
            edl2 = edl2.model_copy(update={"events": new_events})
            notes.append(f"zooms réduits ({len(framings)} -> {len(keep_ids)})")
    if "aucun zoom" in s or "sans zoom" in s or "pas de zoom" in s:
        new_events = [e for e in edl2.events if e.op != "framing"]
        edl2 = edl2.model_copy(update={"events": new_events})
        notes.append("tous les zooms retirés")

    # --- Taille des sous-titres ---
    if "sous-titre" in s or "sous titre" in s or "texte" in s:
        if "plus gros" in s or "plus grand" in s or "plus grand" in s:
            edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(
                update={"size": min(160, edl2.captions.size + 12)})})
            notes.append(f"sous-titres agrandis ({edl2.captions.size})")
        elif "plus petit" in s or "plus discret" in s:
            edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(
                update={"size": max(20, edl2.captions.size - 12)})})
            notes.append(f"sous-titres réduits ({edl2.captions.size})")
        # Couleur des sous-titres
        for word, hexv in _COLOR_WORDS.items():
            if word in s:
                edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(
                    update={"primary": hexv})})
                notes.append(f"sous-titres en {word}")
                break

    # --- Fond ---
    if "plus flou" in s:
        edl2 = edl2.model_copy(update={"background": edl2.background.model_copy(
            update={"sigma": min(100, edl2.background.sigma + 10)})})
        notes.append("fond plus flou")
    elif "moins flou" in s or "net" in s:
        edl2 = edl2.model_copy(update={"background": edl2.background.model_copy(
            update={"sigma": max(0, edl2.background.sigma - 10)})})
        notes.append("fond moins flou")

    if not notes:
        notes.append("consigne non reconnue par l'ajustement déterministe")
    return edl2, notes
