"""F6 (AUDIT.md) : scinder un clip en deux — sortclip.clipops. Pure, testable
sans FFmpeg."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.edl import Interval, FramingEvent, EmphasisEvent
from sortclip.clipops import (
    split_keeps_at_output_time, split_events_at_output_time, snap_split_to_word_boundary,
)


def test_split_keeps_single_interval_in_the_middle():
    keeps = [Interval(start=10.0, end=40.0)]   # 30s de sortie
    before, after = split_keeps_at_output_time(keeps, 12.0)
    assert len(before) == 1 and len(after) == 1
    assert before[0].start == 10.0 and before[0].end == 22.0
    assert after[0].start == 22.0 and after[0].end == 40.0


def test_split_keeps_across_multiple_intervals():
    # Deux intervalles de 10s chacun (20s de sortie au total) ; coupe à 15s
    # -> tombe dans le second intervalle, 5s après son début.
    keeps = [Interval(start=0.0, end=10.0), Interval(start=20.0, end=30.0)]
    before, after = split_keeps_at_output_time(keeps, 15.0)
    assert len(before) == 2   # le premier intervalle entier + le début du second
    assert before[0].start == 0.0 and before[0].end == 10.0
    assert before[1].start == 20.0 and before[1].end == 25.0
    assert len(after) == 1
    assert after[0].start == 25.0 and after[0].end == 30.0


def test_split_keeps_boundary_exactly_between_intervals():
    keeps = [Interval(start=0.0, end=10.0), Interval(start=20.0, end=30.0)]
    before, after = split_keeps_at_output_time(keeps, 10.0)
    assert before == [Interval(start=0.0, end=10.0)]
    assert after == [Interval(start=20.0, end=30.0)]


def test_split_events_reassigns_and_shifts_second_half():
    events = [
        FramingEvent(t=2.0, value="tight"),
        FramingEvent(t=8.0, value="wide"),
        EmphasisEvent(t=9.5, word_index=4, style="pop"),
    ]
    before, after = split_events_at_output_time(events, 5.0)
    assert len(before) == 1 and before[0].t == 2.0
    assert len(after) == 2
    assert after[0].t == 3.0    # 8.0 - 5.0
    assert after[1].t == 4.5    # 9.5 - 5.0
    # word_index n'est jamais touché (référence le transcript, pas le temps).
    assert after[1].word_index == 4


def test_split_events_empty_list():
    before, after = split_events_at_output_time([], 5.0)
    assert before == [] and after == []


# --- G1 (prompt amélioration commandes) : point de scission au silence -----

WORDS_OUT = [
    {"word": "bonjour", "start": 0.0, "end": 0.5},
    {"word": "tout", "start": 0.6, "end": 0.9},
    {"word": "le", "start": 0.9, "end": 1.0},
    {"word": "monde", "start": 1.0, "end": 1.4},
]


def test_snap_split_leaves_boundary_in_a_gap_unchanged():
    # 0.55s tombe déjà dans le silence entre "bonjour" et "tout".
    assert snap_split_to_word_boundary(0.55, WORDS_OUT) == 0.55


def test_snap_split_moves_out_of_mid_word_cut():
    # Preuve du problème (avant fix) : couper à 1.2s tombait EN PLEIN dans
    # "monde" (1.0-1.4), le coupant en deux. Le point doit être décalé sur
    # un bord du mot, jamais laissé à l'intérieur.
    snapped = snap_split_to_word_boundary(1.2, WORDS_OUT)
    assert snapped in (1.0, 1.4)
    assert not any(w["start"] < snapped < w["end"] for w in WORDS_OUT)


def test_snap_split_picks_nearest_edge():
    # 1.05s est plus proche du début (1.0) que de la fin (1.4) de "monde".
    assert snap_split_to_word_boundary(1.05, WORDS_OUT) == 1.0


def test_snap_split_gives_up_beyond_max_shift():
    words = [{"word": "long", "start": 0.0, "end": 10.0}]
    assert snap_split_to_word_boundary(5.0, words, max_shift=1.0) == 5.0


def test_snap_split_no_words_returns_unchanged():
    assert snap_split_to_word_boundary(3.0, []) == 3.0


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
