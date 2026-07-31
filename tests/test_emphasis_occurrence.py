"""F1 (prompt amélioration commandes) : cibler la N-ième occurrence d'un mot
pour les mises en valeur, au lieu d'être bloqué sur la première.

Preuve du problème (avant fix) : `_find_word_index` renvoyait toujours le
PREMIER match, sans aucun moyen de viser une répétition plus loin dans le
clip, et « retire la mise en valeur sur X » ne pouvait retirer que la
première occurrence, même si l'emphase existante ciblait la deuxième."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.patch import _find_word_index, _extract_occurrence, apply_text_adjustment
from sortclip.edl import EDL, Source, Interval, Background, Captions, Watermark, Canvas, EmphasisEvent

WORDS = [
    {"word": "vraiment", "start": 0.0, "end": 0.3},
    {"word": "incroyable", "start": 0.3, "end": 0.8},
    {"word": "et", "start": 0.8, "end": 0.9},
    {"word": "vraiment", "start": 0.9, "end": 1.2},
    {"word": "unique", "start": 1.2, "end": 1.6},
]


def _edl():
    return EDL(
        source=Source(path="x.mp4", duration=5.0, width=1080, height=1920),
        keeps=[Interval(start=0.0, end=1.6)],
        background=Background(), captions=Captions(), watermark=Watermark(),
        canvas=Canvas(),
    )


def test_find_word_index_default_is_first_occurrence():
    assert _find_word_index(WORDS, "vraiment") == 0


def test_find_word_index_second_occurrence():
    assert _find_word_index(WORDS, "vraiment", occurrence=1) == 3


def test_find_word_index_last_occurrence():
    assert _find_word_index(WORDS, "vraiment", occurrence=-1) == 3


def test_find_word_index_out_of_range_occurrence_is_none():
    assert _find_word_index(WORDS, "vraiment", occurrence=5) is None


def test_extract_occurrence_parses_ordinal_words():
    assert _extract_occurrence("mets en valeur le deuxième vraiment") == 1
    assert _extract_occurrence("mets en valeur le dernier vraiment") == -1
    assert _extract_occurrence("mets en valeur vraiment") == 0


def test_apply_text_adjustment_targets_second_occurrence_via_ordinal():
    edl2, notes = apply_text_adjustment(
        _edl(), "mets en valeur la deuxième fois vraiment", words=WORDS
    )
    emphases = [e for e in edl2.events if e.op == "emphasis"]
    assert len(emphases) == 1
    assert emphases[0].word_index == 3  # la deuxième occurrence de "vraiment"


def test_remove_emphasis_with_ordinal_removes_only_that_occurrence():
    edl = _edl().model_copy(update={"events": [
        EmphasisEvent(t=0.0, word_index=0, style="pop"),
        EmphasisEvent(t=0.9, word_index=3, style="pop"),
    ]})
    edl2, notes = apply_text_adjustment(edl, "retire la mise en valeur sur le deuxième vraiment", words=WORDS)
    remaining = [e for e in edl2.events if e.op == "emphasis"]
    assert len(remaining) == 1 and remaining[0].word_index == 0


def test_remove_emphasis_without_ordinal_removes_all_matching_occurrences():
    edl = _edl().model_copy(update={"events": [
        EmphasisEvent(t=0.0, word_index=0, style="pop"),
        EmphasisEvent(t=0.9, word_index=3, style="pop"),
    ]})
    edl2, notes = apply_text_adjustment(edl, "retire la mise en valeur sur vraiment", words=WORDS)
    remaining = [e for e in edl2.events if e.op == "emphasis"]
    assert remaining == []


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
