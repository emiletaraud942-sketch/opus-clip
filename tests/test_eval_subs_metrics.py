"""Tests pour sortclip.eval.subs_metrics (Partie 0.2/0.3, correction-sous-titres)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pytest import approx as pytest_approx
from sortclip.eval.subs_metrics import (
    word_error_rate,
    timestamp_offsets,
    offset_median_and_stddev,
    offset_trend,
    hallucination_rate,
    loop_rate,
)


def test_wer_identical_is_zero():
    words = ["bonjour", "tout", "le", "monde"]
    assert word_error_rate(words, words) == 0.0


def test_wer_one_substitution():
    ref = ["bonjour", "tout", "le", "monde"]
    cand = ["bonjour", "tous", "le", "monde"]
    assert word_error_rate(cand, ref) == 1 / 4


def test_wer_case_and_punctuation_insensitive():
    ref = ["Bonjour", "monde"]
    cand = ["bonjour,", "monde!"]
    assert word_error_rate(cand, ref) == 0.0


def test_wer_empty_reference_and_candidate():
    assert word_error_rate([], []) == 0.0


def test_wer_empty_reference_nonempty_candidate_is_one():
    assert word_error_rate(["mot"], []) == 1.0


def test_timestamp_offsets_computes_difference():
    ref = [("a", 1.0), ("b", 2.0)]
    cand = [("a", 1.25), ("b", 1.9)]
    offsets = timestamp_offsets(cand, ref)
    assert offsets[0] == pytest_approx(0.25)
    assert offsets[1] == pytest_approx(-0.1)


def test_offset_median_and_stddev_constant_offset():
    median, std = offset_median_and_stddev([0.25, 0.25, 0.25])
    assert median == 0.25
    assert std == 0.0


def test_offset_trend_constant_profile():
    # Écart quasi identique sur tout le clip -> "constant".
    data = [(0.0, 0.25), (5.0, 0.24), (10.0, 0.26), (20.0, 0.25)]
    assert offset_trend(data) == "constant"


def test_offset_trend_growing_profile():
    # L'écart grandit régulièrement avec le temps -> "croissant".
    data = [(0.0, 0.0), (10.0, 0.5), (20.0, 1.0), (30.0, 1.5)]
    assert offset_trend(data) == "croissant"


def test_offset_trend_irregular_profile():
    data = [(0.0, 0.1), (10.0, -0.4), (20.0, 0.3), (30.0, -0.2)]
    assert offset_trend(data) == "irrégulier"


def test_offset_trend_indeterminate_with_few_points():
    assert offset_trend([(0.0, 0.1), (1.0, 0.1)]) == "indéterminé (pas assez de repères)"


def test_hallucination_rate_basic():
    assert hallucination_rate(3, 30) == 0.1
    assert hallucination_rate(0, 0) == 0.0


def test_loop_rate_basic():
    assert loop_rate(2, 10) == 0.2
    assert loop_rate(0, 0) == 0.0


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
