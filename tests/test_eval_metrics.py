"""Tests des métriques du harnais d'évaluation (sortclip.eval.metrics).

Fixtures synthétiques uniquement — ces tests vérifient que le CALCUL des
métriques est correct, pas la qualité réelle du produit (pour ça, il faudrait
le vrai jeu de 40 clips annotés, qui n'existe pas — voir evals/golden/README.md).
"""

from sortclip.eval.metrics import (
    framing_agreement,
    emphasis_precision_recall,
    event_density_delta,
    validator_rejection_rate,
    instruction_satisfaction,
    is_stable,
)


def test_framing_agreement_identical_is_1():
    spans = [(0.0, 5.0, "wide"), (5.0, 10.0, "tight")]
    assert framing_agreement(spans, spans, out_duration=10.0) == 1.0


def test_framing_agreement_totally_different_is_0():
    a = [(0.0, 10.0, "wide")]
    b = [(0.0, 10.0, "tight")]
    assert framing_agreement(a, b, out_duration=10.0) == 0.0


def test_framing_agreement_partial_overlap():
    # référence : wide 0-5, tight 5-10 ; candidat : wide sur tout (0-10)
    # accord uniquement sur la moitié [0,5) -> ~0.5
    cand = [(0.0, 10.0, "wide")]
    ref = [(0.0, 5.0, "wide"), (5.0, 10.0, "tight")]
    score = framing_agreement(cand, ref, out_duration=10.0, window=0.5)
    assert 0.45 <= score <= 0.55


def test_framing_agreement_zero_duration_does_not_penalize():
    assert framing_agreement([], [], out_duration=0.0) == 1.0


def test_emphasis_precision_recall_perfect_match():
    p, r = emphasis_precision_recall([3, 7, 12], [3, 7, 12])
    assert p == 1.0 and r == 1.0


def test_emphasis_precision_recall_partial():
    # candidat propose {3,7,99} ; référence attend {3,7,12}
    p, r = emphasis_precision_recall([3, 7, 99], [3, 7, 12])
    assert round(p, 3) == round(2 / 3, 3)
    assert round(r, 3) == round(2 / 3, 3)


def test_emphasis_precision_recall_empty_both():
    assert emphasis_precision_recall([], []) == (1.0, 1.0)


def test_emphasis_precision_recall_candidate_empty_reference_not():
    p, r = emphasis_precision_recall([], [1, 2])
    assert p == 0.0 and r == 0.0


def test_event_density_delta_matching_density_is_zero():
    # 6 événements sur 60s (candidat) vs 6 sur 60s (référence) -> 0
    assert event_density_delta(6, 6, 60.0) == 0.0


def test_event_density_delta_detects_overuse():
    # candidat : 12 evts/min, référence : 6 evts/min -> delta 6
    delta = event_density_delta(12, 6, 60.0)
    assert delta == 6.0


def test_validator_rejection_rate_no_emission_is_zero():
    assert validator_rejection_rate(0, 0) == 0.0


def test_validator_rejection_rate_half_rejected():
    assert validator_rejection_rate(5, 10) == 0.5


def test_instruction_satisfaction_all_pass():
    assert instruction_satisfaction([True, True, True]) == 1.0


def test_instruction_satisfaction_half_pass():
    assert instruction_satisfaction([True, False, True, False]) == 0.5


def test_instruction_satisfaction_empty_does_not_penalize():
    assert instruction_satisfaction([]) == 1.0


def test_is_stable_identical_content_different_ids():
    a = {"events": [{"id": "abc123", "op": "framing", "t": 1.0, "value": "tight"}]}
    b = {"events": [{"id": "xyz789", "op": "framing", "t": 1.0, "value": "tight"}]}
    assert is_stable(a, b) is True


def test_is_stable_different_content():
    a = {"events": [{"id": "abc", "op": "framing", "t": 1.0, "value": "tight"}]}
    b = {"events": [{"id": "abc", "op": "framing", "t": 1.0, "value": "wide"}]}
    assert is_stable(a, b) is False
