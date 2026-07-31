"""C1 (prompt amélioration commandes) : « coupe le début/la fin » ne doit plus
couper un mot en deux.

Preuve du problème (avant fix) : le trim retirait une durée FIXE (2s) sans
jamais regarder où tombaient les mots -> un mot à cheval sur le point de
coupe voyait son audio haché en sortie."""

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

# Un mot chevauche exactement l'instant où tomberait le trim de 2s (start=0 -> 2.0).
WORDS = [
    {"word": "bonjour", "start": 0.0, "end": 1.0},
    {"word": "monde", "start": 1.5, "end": 2.6},  # à cheval sur t=2.0
    {"word": "unique", "start": 3.0, "end": 3.8},
]


def _edl():
    return EDL(
        source=Source(path="x.mp4", duration=10.0, width=1080, height=1920),
        keeps=[Interval(start=0.0, end=10.0)],
        background=Background(), captions=Captions(), watermark=Watermark(),
        canvas=Canvas(),
    )


def test_trim_start_snaps_out_of_mid_word_cut():
    edl2, notes = apply_text_adjustment(_edl(), "coupe le début", words=WORDS)
    new_start = edl2.keeps[0].start
    assert not any(w["start"] < new_start < w["end"] for w in WORDS)
    assert new_start in (1.5, 2.6)


def test_trim_start_without_words_keeps_old_fixed_behavior():
    edl2, notes = apply_text_adjustment(_edl(), "coupe le début", words=None)
    assert edl2.keeps[0].start == 2.0


def test_trim_end_snaps_out_of_mid_word_cut():
    edl = _edl().model_copy(update={"keeps": [Interval(start=0.0, end=4.6)]})
    # coupe la fin -> end candidat = 4.6 - 2.0 = 2.6, tombe dans "monde" (1.5-2.6 exclut 2.6 lui-même)
    words = [
        {"word": "bonjour", "start": 0.0, "end": 1.0},
        {"word": "monde", "start": 1.5, "end": 3.0},
        {"word": "unique", "start": 3.5, "end": 4.4},
    ]
    edl2, notes = apply_text_adjustment(edl, "coupe la fin", words=words)
    new_end = edl2.keeps[-1].end
    assert not any(w["start"] < new_end < w["end"] for w in words)


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
