"""
Métriques automatiques (Partie 0.2) — fonctions PURES, sans API, sans FFmpeg,
testables en isolation. Chacune correspond à une ligne du tableau de la
mission :

    Accord de cadrage | Emphase précision/rappel | Densité d'événements |
    Taux de rejet validateur | Satisfaction d'instruction | Stabilité

Toutes acceptent des structures simples (listes de tuples / dicts) plutôt que
les classes pydantic de sortclip.edl, pour rester utilisables même sans
l'EDL complet (ex. sur des fixtures de test légères).
"""

from __future__ import annotations

import copy


def framing_agreement(
    candidate_spans: list[tuple[float, float, str]],
    reference_spans: list[tuple[float, float, str]],
    out_duration: float,
    window: float = 0.5,
) -> float:
    """Part du temps de sortie où le cadrage candidat correspond à la
    référence, mesurée par fenêtres de `window` secondes. Renvoie 1.0 si
    out_duration <= 0 (rien à comparer, on ne pénalise pas)."""
    if out_duration <= 0:
        return 1.0

    def _value_at(spans, t):
        val = "wide"
        for t0, t1, v in spans:
            if t0 <= t:
                val = v
            else:
                break
        return val

    n_windows = max(1, int(out_duration / window))
    agree = 0
    for i in range(n_windows):
        t = i * window
        if _value_at(candidate_spans, t) == _value_at(reference_spans, t):
            agree += 1
    return agree / n_windows


def emphasis_precision_recall(
    candidate_word_indices: set[int] | list[int],
    reference_word_indices: set[int] | list[int],
) -> tuple[float, float]:
    """Précision/rappel des mots mis en emphase par rapport à la référence.
    (1.0, 1.0) si les deux ensembles sont vides (rien à reprocher)."""
    cand = set(candidate_word_indices)
    ref = set(reference_word_indices)
    if not cand and not ref:
        return 1.0, 1.0
    tp = len(cand & ref)
    precision = tp / len(cand) if cand else 0.0
    recall = tp / len(ref) if ref else 0.0
    return precision, recall


def event_density_delta(
    n_candidate_events: int, n_reference_events: int, duration_seconds: float
) -> float:
    """Écart absolu à la densité de référence, en événements par minute.
    0.0 si duration_seconds <= 0 (indéfini, on ne pénalise pas)."""
    if duration_seconds <= 0:
        return 0.0
    minutes = duration_seconds / 60.0
    cand_density = n_candidate_events / minutes
    ref_density = n_reference_events / minutes
    return abs(cand_density - ref_density)


def validator_rejection_rate(n_rejected: int, n_emitted: int) -> float:
    """Part des événements émis que le validateur écarte. 0.0 si rien n'a été
    émis (pas de déchet possible)."""
    if n_emitted <= 0:
        return 0.0
    return n_rejected / n_emitted


def instruction_satisfaction(results: list[bool]) -> float:
    """Part des consignes de retouche correctement appliquées, sur un jeu de
    consignes annotées (chaque bool = succès jugé par un humain ou par un
    contrôle automatique). 1.0 si aucune consigne testée (rien à reprocher —
    à distinguer explicitement d'un 0/0 qui masquerait une régression)."""
    if not results:
        return 1.0
    return sum(1 for r in results if r) / len(results)


def _strip_ids(obj):
    """Retire récursivement les champs `id` (générés aléatoirement à chaque
    construction d'événement) avant comparaison de stabilité — sans ça, deux
    exécutions IDENTIQUES en contenu seraient jugées instables à cause du seul
    identifiant technique."""
    if isinstance(obj, dict):
        return {k: _strip_ids(v) for k, v in obj.items() if k != "id"}
    if isinstance(obj, list):
        return [_strip_ids(v) for v in obj]
    return obj


def is_stable(edl_a: dict, edl_b: dict) -> bool:
    """Deux exécutions identiques (même entrée, même prompt) doivent produire
    le même EDL, aux identifiants d'événements près. Compare en profondeur les
    deux structures (dicts JSON, ex. edl.model_dump(mode='json'))."""
    return _strip_ids(copy.deepcopy(edl_a)) == _strip_ids(copy.deepcopy(edl_b))
