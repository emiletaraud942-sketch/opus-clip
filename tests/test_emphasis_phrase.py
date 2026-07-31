"""F2 (prompt amélioration commandes) : mettre en valeur une EXPRESSION de
plusieurs mots, pas seulement un mot isolé.

Preuve du problème (avant fix) : `_find_word_index` comparait la phrase
entière ("vraiment incroyable") à CHAQUE mot individuel du transcript -> ne
matchait jamais, la consigne tombait silencieusement dans "non reconnue"."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.patch import _find_phrase_range, apply_text_adjustment
from sortclip.edl import EDL, Source, Interval, Background, Captions, Watermark, Canvas, EmphasisEvent

WORDS = [
    {"word": "c'est", "start": 0.0, "end": 0.2},
    {"word": "vraiment", "start": 0.2, "end": 0.5},
    {"word": "incroyable", "start": 0.5, "end": 1.0},
    {"word": "et", "start": 1.0, "end": 1.1},
    {"word": "vraiment", "start": 1.1, "end": 1.4},
    {"word": "incroyable", "start": 1.4, "end": 1.9},
]


def _edl():
    return EDL(
        source=Source(path="x.mp4", duration=5.0, width=1080, height=1920),
        keeps=[Interval(start=0.0, end=1.9)],
        background=Background(), captions=Captions(), watermark=Watermark(), canvas=Canvas(),
    )


def test_find_phrase_range_first_occurrence():
    assert _find_phrase_range(WORDS, "vraiment incroyable") == (1, 2)


def test_find_phrase_range_second_occurrence():
    assert _find_phrase_range(WORDS, "vraiment incroyable", occurrence=1) == (4, 5)


def test_find_phrase_range_no_match():
    assert _find_phrase_range(WORDS, "absent du tout") is None


def test_apply_text_adjustment_emphasizes_whole_phrase():
    edl2, notes = apply_text_adjustment(_edl(), 'mets en valeur "vraiment incroyable"', words=WORDS)
    emphases = sorted(e.word_index for e in edl2.events if e.op == "emphasis")
    assert emphases == [1, 2]
    assert any("vraiment incroyable" in n for n in notes)


def test_remove_emphasis_on_phrase_without_ordinal_removes_all_occurrences():
    edl = _edl().model_copy(update={"events": [
        EmphasisEvent(t=0.2, word_index=1, style="pop"),
        EmphasisEvent(t=0.5, word_index=2, style="pop"),
        EmphasisEvent(t=1.1, word_index=4, style="pop"),
        EmphasisEvent(t=1.4, word_index=5, style="pop"),
    ]})
    edl2, notes = apply_text_adjustment(edl, 'retire la mise en valeur sur "vraiment incroyable"', words=WORDS)
    assert [e for e in edl2.events if e.op == "emphasis"] == []


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
