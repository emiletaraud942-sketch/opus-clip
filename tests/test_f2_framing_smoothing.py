"""Tests F2 (lissage du recadrage) — sortclip.framing_smoothing.

Module pur, testé sur des positions BRUTES synthétiques (aucune vidéo/vision
réelle nécessaire — voir le rapport final pour ce qui manque encore pour
brancher ça sur une vraie détection de visage/locuteur : F1, bloqué)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.framing_smoothing import RawPosition, smooth_framing_path


def test_empty_input_returns_empty():
    assert smooth_framing_path([]) == []


def test_hysteresis_ignores_brief_speaker_blip():
    raw = [
        RawPosition(0.0, 0.2, "A"),
        RawPosition(0.2, 0.2, "A"),
        RawPosition(0.4, 0.8, "B"),   # blip, très court
        RawPosition(0.6, 0.2, "A"),   # revient avant hysteresis_seconds (0.5)
        RawPosition(1.0, 0.2, "A"),
    ]
    decisions = smooth_framing_path(raw, hysteresis_seconds=0.5)
    assert all(d.speaker_id == "A" for d in decisions), "le blip ne doit jamais être adopté"


def test_sustained_change_is_adopted_after_hysteresis():
    raw = [
        RawPosition(0.0, 0.2, "A"),
        RawPosition(0.6, 0.8, "B"),
        RawPosition(0.9, 0.8, "B"),
        RawPosition(1.2, 0.8, "B"),   # stable depuis 0.6s -> adopté ici
    ]
    decisions = smooth_framing_path(raw, hysteresis_seconds=0.5, lockout_seconds=1.2)
    assert decisions[-1].speaker_id == "B"
    assert decisions[1].speaker_id == "A"   # pas encore adopté à t=0.6
    assert decisions[2].speaker_id == "A"   # ni à t=0.9 (0.3s de stabilité < 0.5)


def test_lockout_prevents_immediate_second_switch():
    raw = [
        RawPosition(0.0, 0.2, "A"),
        RawPosition(0.6, 0.8, "B"),
        RawPosition(1.2, 0.8, "B"),   # switch A->B adopté à t=1.2
        RawPosition(1.3, 0.2, "A"),   # début pending retour vers A
        RawPosition(1.9, 0.2, "A"),   # stable 0.6s, MAIS verrouillage (0.7s < 1.2s) -> refusé
        RawPosition(2.5, 0.2, "A"),   # verrouillage levé (1.3s >= 1.2s) -> adopté
    ]
    decisions = smooth_framing_path(raw, hysteresis_seconds=0.5, lockout_seconds=1.2)
    by_t = {d.t: d.speaker_id for d in decisions}
    assert by_t[1.2] == "B"
    assert by_t[1.9] == "B", "le verrouillage doit encore empêcher le retour à A ici"
    assert by_t[2.5] == "A", "le verrouillage est levé, le retour doit être adopté"


def test_voice_off_keeps_last_stable_framing_never_guesses():
    # speaker_id=None (voix off / hors champ) : ne doit JAMAIS faire changer
    # le locuteur retenu ni chercher un visage au hasard.
    raw = [
        RawPosition(0.0, 0.3, "A"),
        RawPosition(0.5, 0.3, "A"),
        RawPosition(1.0, 0.9, None),   # voix off : position brute très différente
        RawPosition(1.5, 0.3, "A"),
    ]
    decisions = smooth_framing_path(raw)
    assert decisions[2].speaker_id == "A"   # locuteur retenu inchangé
    assert abs(decisions[2].x - decisions[1].x) < 0.05   # position quasi inchangée


def test_cut_vs_pan_classification_without_damping():
    # alpha=1.0 : pas d'amortissement, la distance mesurée = le saut brut.
    raw = [
        RawPosition(0.0, 0.1, "A"),
        RawPosition(0.5, 0.15, "A"),    # petit déplacement -> panoramique
        RawPosition(1.0, 0.70, "A"),    # grand déplacement -> coupe franche
    ]
    decisions = smooth_framing_path(raw, smoothing_alpha=1.0, cut_distance_threshold=0.35)
    assert decisions[1].kind == "pan"
    assert decisions[2].kind == "cut"


def test_smoothing_dampens_raw_jitter():
    # Oscillation forte du même locuteur : le lissage doit réduire l'écart
    # d'une étape à l'autre par rapport au saut brut.
    raw = [RawPosition(i * 0.2, 0.1 if i % 2 == 0 else 0.9, "A") for i in range(10)]
    decisions = smooth_framing_path(raw, smoothing_alpha=0.3)
    raw_jump = 0.8   # |0.9 - 0.1|
    smoothed_jumps = [abs(decisions[i].x - decisions[i - 1].x) for i in range(1, len(decisions))]
    assert all(j < raw_jump for j in smoothed_jumps), "le lissage doit amortir chaque saut brut"


def test_never_raises_on_single_point():
    decisions = smooth_framing_path([RawPosition(0.0, 0.5, "A")])
    assert len(decisions) == 1
    assert decisions[0].kind == "hold"
