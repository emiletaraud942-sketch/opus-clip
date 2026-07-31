"""E2 (prompt amélioration commandes) : durée d'exposition configurable du
texte incrusté.

Preuve du problème (avant fix) : `TextOverlayEvent.duration` valait toujours
1e9 (tout le clip) — "affiche le titre pendant 3 secondes" n'avait aucun
effet, la consigne ne parsait aucune durée."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.patch import _extract_overlay_duration, apply_text_adjustment
from sortclip.edl import EDL, Source, Interval, Background, Captions, Watermark, Canvas


def _edl():
    return EDL(
        source=Source(path="x.mp4", duration=10.0, width=1080, height=1920),
        keeps=[Interval(start=0.0, end=10.0)],
        background=Background(), captions=Captions(enabled=False), watermark=Watermark(), canvas=Canvas(),
    )


def test_extract_duration_parses_seconds():
    assert _extract_overlay_duration("affiche pendant 3 secondes") == 3.0
    assert _extract_overlay_duration("pendant 2.5s") == 2.5
    assert _extract_overlay_duration("pendant 4,5 sec") == 4.5


def test_extract_duration_none_when_absent():
    assert _extract_overlay_duration("ajoute un titre en haut") is None


def test_overlay_uses_default_full_clip_duration_without_keyword():
    edl2, notes = apply_text_adjustment(_edl(), 'ajoute le titre "promo" en haut')
    overlay = next(e for e in edl2.events if e.op == "text_overlay")
    assert overlay.duration > 1e6  # sentinelle "tout le clip", inchangée


def test_overlay_uses_explicit_duration_when_given():
    edl2, notes = apply_text_adjustment(_edl(), 'ajoute le titre "promo" en haut pendant 3 secondes')
    overlay = next(e for e in edl2.events if e.op == "text_overlay")
    assert overlay.duration == 3.0
    assert any("3s" in n for n in notes)


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
