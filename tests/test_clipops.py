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
from sortclip.clipops import split_keeps_at_output_time, split_events_at_output_time


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


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
