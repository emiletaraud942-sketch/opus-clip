"""B3 (prompt amélioration commandes) : recomposer les lignes de sous-titres
(mots par ligne), jusqu'ici impossible à changer après génération.

Preuve du problème (avant fix) : `Captions.words_per_line` existait déjà
(consommé par `build_ass`) mais AUCUNE consigne de `apply_text_adjustment`
ne le touchait — "sous-titres plus compacts" tombait en "non reconnue"."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.patch import apply_text_adjustment
from sortclip.edl import EDL, Source, Interval, Background, Captions, Watermark, Canvas


def _edl(wpl):
    return EDL(
        source=Source(path="x.mp4", duration=10.0, width=1080, height=1920),
        keeps=[Interval(start=0.0, end=10.0)],
        background=Background(), captions=Captions(words_per_line=wpl),
        watermark=Watermark(), canvas=Canvas(),
    )


def test_more_compact_reduces_words_per_line():
    edl2, notes = apply_text_adjustment(_edl(4), "sous-titres plus compacts")
    assert edl2.captions.words_per_line == 3
    assert any("plus courtes (3 mots/ligne)" in n for n in notes)


def test_more_airy_increases_words_per_line():
    edl2, notes = apply_text_adjustment(_edl(4), "sous-titres plus aérés")
    assert edl2.captions.words_per_line == 5
    assert any("plus aérées (5 mots/ligne)" in n for n in notes)


def test_more_compact_at_minimum_reports_limit_honestly():
    edl2, notes = apply_text_adjustment(_edl(1), "sous-titres plus compacts")
    assert edl2.captions.words_per_line == 1
    assert any("déjà au minimum" in n for n in notes)
    assert not any("plus courtes" in n for n in notes)


def test_more_airy_at_maximum_reports_limit_honestly():
    edl2, notes = apply_text_adjustment(_edl(10), "sous-titres plus aérés")
    assert edl2.captions.words_per_line == 10
    assert any("déjà au maximum" in n for n in notes)
    assert not any("plus aérées" in n for n in notes)


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
