"""Tests C1 (prosodie) — sortclip.prosody.

Module pur : pas de librosa ici, les tableaux d'énergie/rire sont fournis
PRÉ-CALCULÉS (comme le ferait extract_signals côté modal_app.py, non testable
dans cet environnement faute de librosa/audio réel — voir le rapport final).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.prosody import (
    words_with_pause_and_rate,
    energy_zscore_at,
    annotate_transcript,
)


def _words(specs):
    """specs: liste de (mot, start, end)."""
    return [{"word": w, "start": s, "end": e} for w, s, e in specs]


def test_pause_before_first_word_is_zero():
    words = _words([("Bonjour", 0.0, 0.4), ("tous", 0.5, 0.8)])
    out = words_with_pause_and_rate(words)
    assert out[0]["pause_before"] == 0.0


def test_pause_before_detects_silence():
    words = _words([("Bonjour", 0.0, 0.4), ("tous", 3.0, 3.4)])
    out = words_with_pause_and_rate(words)
    assert out[1]["pause_before"] == 2.6


def test_rate_delta_zero_for_uniform_speech():
    # débit constant : mots régulièrement espacés -> rate_delta proche de 0
    # partout (aux bords près, où la fenêtre est tronquée).
    words = _words([(f"m{i}", i * 0.5, i * 0.5 + 0.3) for i in range(20)])
    out = words_with_pause_and_rate(words, window=5.0)
    middle = out[10]
    assert abs(middle["rate_delta"]) < 0.15


def test_rate_delta_detects_fast_burst():
    # Un mot toutes les 0.5s pendant longtemps, puis une rafale de mots
    # rapprochés (débit local très supérieur à la moyenne).
    slow = [(f"s{i}", i * 1.0, i * 1.0 + 0.3) for i in range(10)]
    burst_start = 10.0
    burst = [(f"b{i}", burst_start + i * 0.1, burst_start + i * 0.1 + 0.05) for i in range(6)]
    words = _words(slow + burst)
    out = words_with_pause_and_rate(words, window=2.0)
    burst_word = out[13]   # au milieu de la rafale
    assert burst_word["rate_delta"] > 0.3   # nettement plus rapide que la moyenne


def test_energy_zscore_at_nearest_lookup():
    times = [0.0, 1.0, 2.0, 3.0]
    z = [0.1, 2.5, -0.3, 1.8]
    assert energy_zscore_at(times, z, 1.05) == 2.5   # le plus proche de 1.0
    assert energy_zscore_at(times, z, 2.9) == 1.8


def test_energy_zscore_at_empty_arrays_returns_none():
    assert energy_zscore_at([], [], 1.0) is None


def test_annotate_transcript_marks_pause():
    words = _words([("Bonjour", 0.0, 0.4), ("silence", 2.0, 2.4)])
    out = annotate_transcript(words)
    assert "(0)Bonjour" in out
    assert "pause 1.6s" in out


def test_annotate_transcript_marks_laughter():
    words = _words([("drôle", 5.0, 5.4)])
    out = annotate_transcript(words, laughter_times=[5.1])
    assert "rire détecté" in out


def test_annotate_transcript_marks_energy_spike():
    words = _words([("jamais", 12.4, 12.9)])
    out = annotate_transcript(words, energy_times=[12.4], energy_z=[2.1])
    assert "énergie +2.1σ" in out


def test_annotate_transcript_no_markers_when_nothing_notable():
    words = _words([("Bonjour", 0.0, 0.4), ("tous", 0.5, 0.9)])
    out = annotate_transcript(words)
    assert out == "(0)Bonjour (1)tous"   # aucun marqueur : rien de notable ici


def test_annotate_transcript_empty_words():
    assert annotate_transcript([]) == ""
