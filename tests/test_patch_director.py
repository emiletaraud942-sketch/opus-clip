"""Test des patchs, de l'ajustement texte et du parsing du réalisateur — SANS IA."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip import (  # noqa: E402
    build_edl, apply_patch, apply_text_adjustment, PatchOp, FramingEvent,
)
from sortclip.director import events_from_tool_input, patches_from_tool_input  # noqa: E402

WORDS = [
    {"word": "Le", "start": 0.5, "end": 0.7}, {"word": "vrai", "start": 0.72, "end": 1.05},
    {"word": "problème", "start": 1.1, "end": 1.7}, {"word": "c'est", "start": 1.75, "end": 2.0},
    {"word": "la", "start": 2.05, "end": 2.2}, {"word": "marge", "start": 2.25, "end": 2.9},
]


def main() -> None:
    ok = 0
    edl, cleaned = build_edl(source_path="/tmp/x.mp4", source_duration=20.0, words=WORDS,
                             preset_name="podcast_dynamique", source_width=1920, source_height=1080)
    edl = edl.model_copy(update={"events": [
        FramingEvent(t=0.5, value="tight"),
        FramingEvent(t=2.0, value="wide"),
    ]})

    # 1. apply_patch : seul l'événement visé bouge, un id inexistant est rejeté.
    before = {e.id: e.model_dump() for e in edl.events}
    target = edl.events[1]
    res = apply_patch(edl, [
        PatchOp(action="remove", event_id=target.id),
        PatchOp(action="modify", event_id=edl.events[0].id, field="value", value="wide"),
        PatchOp(action="remove", event_id="inexistant"),
    ])
    assert len(res.applied) == 2 and len(res.rejected) == 1, (res.applied, res.rejected)
    assert all(e.id != target.id for e in res.edl.events)
    modified = next(e for e in res.edl.events if e.id == edl.events[0].id)
    assert modified.value == "wide"
    print(f"  1. apply_patch    : {len(res.applied)} appliqués, {len(res.rejected)} rejeté"); ok += 1

    # 2. Réalisateur : parsing PUR de la sortie d'outil (index -> événements).
    words_out = [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in cleaned]
    events = events_from_tool_input(
        {"framings": [{"word_index": 2, "value": "tight"},
                      {"word_index": 999, "value": "wide"}],   # hors bornes -> ignoré
         "emphases": [{"word_index": 5, "style": "pop"}]},
        words_out,
    )
    assert len(events) == 2, [e.op for e in events]   # 1 framing valide + 1 emphasis
    assert any(e.op == "framing" and abs(e.t - words_out[2]["start"]) < 1e-6 for e in events)
    print(f"  2. director parse : {len(events)} événements (index -> temps sortie)"); ok += 1

    # 3. Patches parsing.
    ops = patches_from_tool_input({"patches": [
        {"action": "remove", "event_id": "abc"},
        {"action": "modify", "event_id": "def", "field": "value", "value": "tight"},
        {"action": "bogus"},   # ignoré
    ]})
    assert len(ops) == 2
    print(f"  3. patches parse  : {len(ops)} patchs valides"); ok += 1

    # 4. Ajustement texte déterministe (sans IA).
    edl3, notes = apply_text_adjustment(edl, "mets moins de zooms et des sous-titres plus gros")
    n_fr = sum(1 for e in edl3.events if e.op == "framing")
    assert n_fr < 2, n_fr
    assert edl3.captions.size > edl.captions.size
    print(f"  4. texte -> patch : {notes}"); ok += 1

    edl4, notes2 = apply_text_adjustment(edl, "sous-titres en jaune")
    assert edl4.captions.primary == "#FFEB3B", edl4.captions.primary
    print(f"  5. texte couleur  : {notes2}"); ok += 1

    print(f"\n{ok} étapes validées.")


if __name__ == "__main__":
    main()
