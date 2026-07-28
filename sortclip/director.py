"""
Réalisateur LLM — pose des ÉVÉNEMENTS sur l'EDL (cadrages, emphases).

Invariants respectés :
  - Le LLM ne touche jamais au shell : il émet des données via l'`input_schema`
    d'un outil, avec `tool_choice` FORCÉ. Vocabulaire fermé.
  - Il ne produit JAMAIS de timestamp en secondes : il référence des INDEX DE
    MOTS ; le code convertit en temps de sortie. Seule protection fiable.
  - Température 0 (quasi-déterminisme ; le vrai déterminisme vient du cache).
  - Les fonctions de parsing (events_from_tool_input / patches_from_tool_input)
    sont PURES et testables sans API.

Le résultat DOIT toujours passer par validate() avant rendu.
"""

from __future__ import annotations

import json

DIRECTOR_MODEL = "claude-sonnet-4-5"

DIRECTOR_SYSTEM = """Tu es réalisateur de clips verticaux courts. On te donne la transcription d'un clip, chaque mot indexé (i)mot. Tu places des ÉVÉNEMENTS de montage pour rythmer le clip :
- framing : un changement de cadrage (wide/medium/tight) sur un mot. Zoome (tight) sur les punchlines, les mots forts, les réactions ; reviens en wide sur les respirations. Sobriété : 1 cadrage toutes les ~4-6 secondes, jamais deux collés.
- emphasis : mettre un mot en valeur (pop/underline/scale) sur les 1-2 mots les plus percutants.

RÈGLES ABSOLUES :
- Tu ne donnes JAMAIS de temps en secondes. Uniquement des index de mots.
- Peu d'événements, bien placés, valent mieux que beaucoup. 3 à 6 cadrages pour un clip d'une minute.
- Ne place pas deux cadrages sur des mots voisins."""

DIRECTOR_USER = """CLIP ({n} mots) :
{indexed}

Place les événements de montage."""

ADJUST_SYSTEM = """Tu ajustes un montage EXISTANT décrit par ses événements. On te donne la liste des événements (avec leur id stable) et une consigne en français. Tu produis des PATCHS qui modifient l'existant — tu ne repars jamais de zéro.
- remove : retirer un événement (par id).
- modify : changer un champ d'un événement (par id) — ex. field="value", value="wide".
- add : ajouter un événement.
Ne touche QUE ce que la consigne demande. Réponds avec le moins de patchs possible."""


def build_place_events_tool() -> dict:
    return {
        "name": "place_events",
        "description": "Place les cadrages et emphases du clip, par index de mots.",
        "input_schema": {
            "type": "object",
            "properties": {
                "framings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "word_index": {"type": "integer"},
                            "value": {"type": "string", "enum": ["wide", "medium", "tight"]},
                            "transition": {"type": "string", "enum": ["cut", "punch", "smooth"]},
                        },
                        "required": ["word_index", "value"],
                    },
                },
                "emphases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "word_index": {"type": "integer"},
                            "style": {"type": "string", "enum": ["pop", "underline", "scale"]},
                        },
                        "required": ["word_index"],
                    },
                },
            },
            "required": ["framings", "emphases"],
        },
    }


def build_edit_tool() -> dict:
    return {
        "name": "edit_events",
        "description": "Ajuste le montage existant via des patchs (remove/modify/add).",
        "input_schema": {
            "type": "object",
            "properties": {
                "patches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["remove", "modify", "add"]},
                            "event_id": {"type": "string"},
                            "field": {"type": "string"},
                            "value": {},
                            "new": {"type": "object"},
                        },
                        "required": ["action"],
                    },
                },
            },
            "required": ["patches"],
        },
    }


# --------------------------------------------------------------------------
# Parsing PUR (testable sans API)
# --------------------------------------------------------------------------

def events_from_tool_input(tool_input: dict, words_out: list[dict]) -> list:
    """Convertit la sortie de l'outil (index de mots) en événements EDL, avec
    conversion index -> temps de SORTIE. Ignore silencieusement les index hors
    bornes ou les valeurs invalides (validate() nettoiera le reste)."""
    from .edl import FramingEvent, EmphasisEvent
    n = len(words_out)
    events = []
    for f in (tool_input.get("framings") or []):
        i = f.get("word_index")
        if not isinstance(i, int) or not (0 <= i < n):
            continue
        try:
            events.append(FramingEvent(
                t=float(words_out[i]["start"]),
                value=f.get("value", "medium"),
                transition=f.get("transition", "cut"),
            ))
        except Exception:
            continue
    for e in (tool_input.get("emphases") or []):
        i = e.get("word_index")
        if not isinstance(i, int) or not (0 <= i < n):
            continue
        try:
            events.append(EmphasisEvent(
                t=float(words_out[i]["start"]), word_index=i,
                style=e.get("style", "pop"),
            ))
        except Exception:
            continue
    return events


def patches_from_tool_input(tool_input: dict) -> list:
    from .patch import PatchOp
    ops = []
    for p in (tool_input.get("patches") or []):
        if p.get("action") not in ("remove", "modify", "add"):
            continue
        ops.append(PatchOp(
            action=p["action"], event_id=p.get("event_id"),
            field=p.get("field"), value=p.get("value"), new=p.get("new"),
        ))
    return ops


# --------------------------------------------------------------------------
# Appels LLM (forcés, température 0)
# --------------------------------------------------------------------------

def direct(client, words_out: list[dict], *, hint: str = "",
           model: str = DIRECTOR_MODEL) -> list:
    """Demande au LLM de placer cadrages + emphases. Retourne des événements
    EDL (à valider ensuite). Ne lève pas : renvoie [] en cas de souci."""
    if not words_out:
        return []
    try:
        indexed = " ".join(f"({i}){w['word']}" for i, w in enumerate(words_out))
        system = DIRECTOR_SYSTEM + (("\n\n" + hint) if hint else "")
        resp = client.messages.create(
            model=model, max_tokens=1500, temperature=0,
            system=system, tools=[build_place_events_tool()],
            tool_choice={"type": "tool", "name": "place_events"},
            messages=[{"role": "user",
                       "content": DIRECTOR_USER.format(n=len(words_out), indexed=indexed)}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "place_events":
                return events_from_tool_input(block.input, words_out)
    except Exception as exc:
        print(f"[director] indisponible (ignoré) : {exc}")
    return []


def adjust_with_text(client, edl, instruction: str, *, model: str = DIRECTOR_MODEL) -> list:
    """Traduit une consigne libre en patchs, en lisant les événements existants.
    Repli du chemin déterministe (patch.apply_text_adjustment). Retourne une
    liste de PatchOp (éventuellement vide)."""
    try:
        summary = []
        for e in edl.events:
            item = {"id": e.id, "op": e.op, "t": round(e.t, 2)}
            if e.op == "framing":
                item["value"] = e.value
            summary.append(item)
        resp = client.messages.create(
            model=model, max_tokens=1000, temperature=0,
            system=ADJUST_SYSTEM, tools=[build_edit_tool()],
            tool_choice={"type": "tool", "name": "edit_events"},
            messages=[{"role": "user", "content":
                       f"Événements actuels : {json.dumps(summary, ensure_ascii=False)}\n\n"
                       f"Consigne : {instruction}\n\nProduis les patchs."}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "edit_events":
                return patches_from_tool_input(block.input)
    except Exception as exc:
        print(f"[director.adjust] indisponible (ignoré) : {exc}")
    return []
