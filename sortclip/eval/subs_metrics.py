"""
Partie 0.3 (chantier « correction-sous-titres ») — métriques de sous-titrage,
pures, sans dépendance réseau/API. Comparent une transcription CANDIDATE
(produite par le pipeline) à une transcription de RÉFÉRENCE (mots-repères
horodatés à la main, voir evals/subs/README.md).
"""

from __future__ import annotations

import re


def _norm(word: str) -> str:
    return re.sub(r"[^\wÀ-ÿ]", "", word or "").strip().lower()


def word_error_rate(candidate_words: list[str], reference_words: list[str]) -> float:
    """WER classique par distance de Levenshtein sur les mots (normalisés,
    insensible à la casse/ponctuation). 0 = identique, peut dépasser 1 si le
    candidat a beaucoup plus de mots que la référence (insertions massives,
    typiquement une hallucination)."""
    ref = [_norm(w) for w in reference_words]
    cand = [_norm(w) for w in candidate_words]
    if not ref:
        return 0.0 if not cand else 1.0
    n, m = len(ref), len(cand)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            tmp = dp[j]
            if ref[i - 1] == cand[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = tmp
    return dp[m] / n


def timestamp_offsets(candidate_marks: list[tuple[str, float]],
                      reference_marks: list[tuple[str, float]]) -> list[float]:
    """Écarts (candidat - référence, en secondes) sur des mots-repères
    appariés par POSITION (les deux listes doivent référencer les mêmes mots
    repères, dans le même ordre — c'est le rôle du jeu de référence 0.1).
    Ignore silencieusement les repères en trop d'un côté."""
    n = min(len(candidate_marks), len(reference_marks))
    return [candidate_marks[i][1] - reference_marks[i][1] for i in range(n)]


def offset_median_and_stddev(offsets: list[float]) -> tuple[float, float]:
    """Médiane et écart-type des écarts — sert à qualifier le PROFIL du
    décalage (0.2) : constant (médiane loin de 0, écart-type faible),
    croissant (nécessite de regarder l'évolution dans le temps, pas cette
    fonction seule), ou irrégulier (écart-type élevé)."""
    if not offsets:
        return 0.0, 0.0
    s = sorted(offsets)
    n = len(s)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    mean = sum(offsets) / n
    variance = sum((x - mean) ** 2 for x in offsets) / n
    return median, variance ** 0.5


def offset_trend(offsets_with_time: list[tuple[float, float]]) -> str:
    """Classe le profil de décalage (tableau 0.2 de la mission) à partir de
    (temps_dans_le_clip, écart) : "constant", "croissant", ou "irrégulier".
    Régression linéaire simple (pente) sur l'écart en fonction du temps,
    seuils documentés ci-dessous — {{À_COMPLÉTER : pas validés sur de vrais
    clips, faute du jeu de référence réel (evals/subs/README.md)}}."""
    if len(offsets_with_time) < 3:
        return "indéterminé (pas assez de repères)"
    ts = [t for t, _ in offsets_with_time]
    os_ = [o for _, o in offsets_with_time]
    n = len(ts)
    mean_t = sum(ts) / n
    mean_o = sum(os_) / n
    num = sum((t - mean_t) * (o - mean_o) for t, o in offsets_with_time)
    den = sum((t - mean_t) ** 2 for t in ts) or 1e-9
    slope = num / den   # secondes d'écart par seconde de clip
    _, std = offset_median_and_stddev(os_)
    # Pente significative sur la durée du clip -> croissant. Sinon, écart-type
    # faible -> constant. Sinon -> irrégulier.
    span = max(ts) - min(ts) or 1.0
    slope_effect = abs(slope) * span
    if slope_effect > 0.3 and slope_effect > std:
        return "croissant"
    if std < 0.08:
        return "constant"
    return "irrégulier"


def hallucination_rate(n_words_in_silent_zones: int, n_words_total: int) -> float:
    """Proportion de mots produits sur des zones classées SANS parole dans la
    référence (0.3 : "taux d'hallucination"). 0 si aucun mot au total."""
    if n_words_total <= 0:
        return 0.0
    return n_words_in_silent_zones / n_words_total


def loop_rate(n_looped_segments: int, n_segments_total: int) -> float:
    """Proportion de segments retirés comme boucles (0.3 : "taux de boucle").
    0 si aucun segment au total."""
    if n_segments_total <= 0:
        return 0.0
    return n_looped_segments / n_segments_total
