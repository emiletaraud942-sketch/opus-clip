"""Test du validateur (ne lève jamais) et du store versionné — SANS IA."""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip import (  # noqa: E402
    build_edl, validate, EDLStore, FramingEvent, EmphasisEvent,
)

WORDS = [
    {"word": "Le", "start": 0.50, "end": 0.70},
    {"word": "vrai", "start": 0.72, "end": 1.05},
    {"word": "euh", "start": 1.07, "end": 1.30},
    {"word": "problème", "start": 1.35, "end": 1.95},
    {"word": "c'est", "start": 1.98, "end": 2.20},
    {"word": "la", "start": 2.22, "end": 2.35},
    {"word": "marge", "start": 2.38, "end": 2.90},
    {"word": "Personne", "start": 4.30, "end": 4.85},
    {"word": "n'en", "start": 4.88, "end": 5.10},
    {"word": "parle", "start": 5.12, "end": 5.55},
    {"word": "jamais", "start": 5.58, "end": 6.10},
]


def main() -> None:
    ok = 0
    edl, cleaned = build_edl(
        source_path="/tmp/x.mp4", source_duration=40.0, words=WORDS,
        preset_name="podcast_dynamique", source_width=1920, source_height=1080,
    )

    # 1. Validateur : ne lève jamais, écarte les événements bancals.
    edl = edl.model_copy(update={"events": [
        FramingEvent(t=1.5, value="tight", transition="punch"),
        FramingEvent(t=1.9, value="wide"),            # trop proche -> rejeté
        FramingEvent(t=4.0, value="wide"),
        EmphasisEvent(t=2.0, word_index=5, style="pop"),
        EmphasisEvent(t=3.0, word_index=999),         # hors transcript -> rejeté
        FramingEvent(t=900.0, value="tight"),         # hors durée -> rejeté
    ]})
    edl, issues = validate(edl, word_count=len(cleaned))
    dropped = [i for i in issues if i.level == "dropped"]
    assert len(dropped) == 3, [i.message for i in issues]
    print(f"  1. validate       : {len(dropped)} événements écartés, 0 exception")
    for i in issues:
        print(f"       - {i.message}")
    ok += 1

    # 2. Store versionné + retour arrière.
    tmp = Path("/tmp/sortclip_store_test")
    shutil.rmtree(tmp, ignore_errors=True)
    store = EDLStore(tmp)
    v1 = store.save("clip42", edl, note="montage initial")
    edl2 = edl.model_copy(update={"events": edl.events[:1]})
    v2 = store.save("clip42", edl2, note="moins de zooms")
    reverted = store.revert("clip42", v1)
    assert (v1, v2) == (1, 2), (v1, v2)
    assert len(store.history("clip42")) == 3
    assert len(reverted.events) == len(edl.events)
    print(f"  2. store          : v1, v2, revert -> "
          f"{len(store.history('clip42'))} versions, revert restaure {len(reverted.events)} events")
    ok += 1

    # 3. Rechargement fidèle (round-trip JSON).
    loaded = store.load("clip42", 1)
    assert loaded is not None and len(loaded.events) == len(edl.events)
    assert loaded.keeps[0].start == edl.keeps[0].start
    print("  3. round-trip     : sauvegarde -> rechargement identique")
    ok += 1

    print(f"\n{ok} étapes validées.")


if __name__ == "__main__":
    main()
