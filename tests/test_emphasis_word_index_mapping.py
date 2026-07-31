"""Audit du repo (finding vérifié par exécution) : `EmphasisEvent.word_index`
doit référencer l'index dans `words_out` (temps de SORTIE, même convention
que `director.py`), pas dans `words` (temps SOURCE) — sans conversion,
l'emphase pouvait être posée sur le MAUVAIS mot dès qu'un silence était
coupé avant le mot ciblé (cas normal : c'est le cœur du produit).

Preuve du problème (avant fix) : avec un `keeps` qui saute un mot ("silence"
supprimé), `words_out[idx]` (idx = index SOURCE) pointait sur un mot décalé
par rapport à celui réellement ciblé par `_find_word_index`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.patch import apply_text_adjustment, _source_to_output_indices
from sortclip.captions import map_words_to_output
from sortclip.edl import EDL, Source, Interval, Background, Captions, Watermark, Canvas, EmphasisEvent

# Mots en temps SOURCE. "silence" (2.0-3.0) est un mot bruité tombé dans un
# silence COUPÉ (absent de `keeps`) -> absent de words_out.
WORDS = [
    {"word": "bonjour", "start": 0.0, "end": 0.5},
    {"word": "silence", "start": 2.0, "end": 3.0},   # tombe HORS des keeps
    {"word": "vraiment", "start": 4.0, "end": 4.5},
    {"word": "incroyable", "start": 4.5, "end": 5.0},
]

# keeps ne couvre PAS 2.0-3.0 -> "silence" est éliminé par map_words_to_output.
KEEPS = [Interval(start=0.0, end=0.5), Interval(start=4.0, end=5.0)]


def _edl():
    return EDL(
        source=Source(path="x.mp4", duration=10.0, width=1080, height=1920),
        keeps=KEEPS,
        background=Background(), captions=Captions(), watermark=Watermark(), canvas=Canvas(),
    )


def test_words_out_is_shorter_than_words_after_silence_removal():
    words_out = map_words_to_output(WORDS, _edl())
    assert len(words_out) == 3  # "silence" (index source 1) a disparu
    assert [w["word"] for w in words_out] == ["bonjour", "vraiment", "incroyable"]


def test_source_to_output_indices_shifts_correctly_across_removed_word():
    mapping = _source_to_output_indices(WORDS, _edl(), {0, 2, 3})
    # "bonjour" (source 0) -> sortie 0 ; "silence" (source 1) absent du mapping ;
    # "vraiment" (source 2) -> sortie 1 (pas 2, décalé par la suppression) ;
    # "incroyable" (source 3) -> sortie 2.
    assert mapping == {0: 0, 2: 1, 3: 2}


def test_emphasis_lands_on_correct_word_across_a_removed_silence():
    edl2, notes = apply_text_adjustment(_edl(), 'mets en valeur "vraiment"', words=WORDS)
    emphases = [e for e in edl2.events if e.op == "emphasis"]
    assert len(emphases) == 1
    words_out = map_words_to_output(WORDS, _edl())
    # AVANT le fix, ceci aurait pointé sur words_out[2] ("incroyable", l'index
    # SOURCE de "vraiment" utilisé à tort comme index de SORTIE) au lieu de
    # words_out[1] ("vraiment", le bon mot une fois le silence pris en compte).
    assert words_out[emphases[0].word_index]["word"] == "vraiment"


def test_remove_emphasis_matches_across_a_removed_silence():
    edl = _edl().model_copy(update={"events": [EmphasisEvent(t=4.0, word_index=1, style="pop")]})
    edl2, notes = apply_text_adjustment(edl, 'retire la mise en valeur sur "vraiment"', words=WORDS)
    assert [e for e in edl2.events if e.op == "emphasis"] == []


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
