"""
Annotation prosodique du transcript (Partie C1).

Le réalisateur (director.py) ne lit aujourd'hui que du texte brut indexé —
il perd COMMENT la phrase a été dite. Ce module annote la transcription avec
des marqueurs de prosodie avant de l'envoyer au LLM, au format de la mission :

    [12.4-12.9] (énergie +2.1σ, débit +40%) "jamais"
    [12.9-13.6] (pause 0.7s) ...
    [13.6-14.2] (rire détecté)

Deux familles de signaux, séparées volontairement :
  - Signaux dérivés du TIMING DES MOTS seuls (débit, pauses) : PURS, aucune
    dépendance audio, entièrement testables ici.
  - Signaux dérivés de l'AUDIO (énergie RMS, rires) : calculés en amont par
    extract_signals() (modal_app.py, librosa — CPU, pas de GPU) et simplement
    consommés ici sous forme de tableaux déjà calculés (aucun appel librosa
    dans ce module, pour qu'il reste testable sans cette dépendance lourde).
"""

from __future__ import annotations

import bisect


def words_with_pause_and_rate(words: list[dict], window: float = 5.0) -> list[dict]:
    """Ajoute à chaque mot `pause_before` (silence depuis le mot précédent, en
    secondes) et `rate_delta` (écart du débit local — mots/seconde dans une
    fenêtre de `window` s centrée sur le mot — au débit moyen du clip, en
    proportion : +0.4 = 40% plus rapide que la moyenne). Fonction PURE, ne
    dépend que des horodatages déjà présents dans `words`."""
    if not words:
        return []
    n = len(words)
    total_span = words[-1]["end"] - words[0]["start"]
    mean_rate = n / total_span if total_span > 0 else 0.0

    out = []
    for i, w in enumerate(words):
        pause_before = w["start"] - words[i - 1]["end"] if i > 0 else 0.0

        lo = w["start"] - window / 2
        hi = w["start"] + window / 2
        count = sum(1 for w2 in words if lo <= w2["start"] <= hi)
        local_span = min(hi, words[-1]["end"]) - max(lo, words[0]["start"])
        local_rate = count / local_span if local_span > 0 else mean_rate
        rate_delta = (local_rate - mean_rate) / mean_rate if mean_rate > 0 else 0.0

        out.append({**w, "pause_before": round(pause_before, 2), "rate_delta": round(rate_delta, 3)})
    return out


def energy_zscore_at(times: list[float], z_values: list[float], t: float) -> float | None:
    """Cherche la valeur de z-score d'énergie la plus proche de l'instant `t`
    dans un tableau précalculé (times, z_values) — PAS de calcul audio ici,
    juste une recherche. Renvoie None si les tableaux sont vides."""
    if not times or not z_values:
        return None
    idx = bisect.bisect_left(times, t)
    candidates = [i for i in (idx - 1, idx) if 0 <= i < len(times)]
    if not candidates:
        return None
    best = min(candidates, key=lambda i: abs(times[i] - t))
    return z_values[best]


def _is_near_any(t: float, timestamps: list[float], tolerance: float = 0.5) -> bool:
    if not timestamps:
        return False
    idx = bisect.bisect_left(timestamps, t)
    for i in (idx - 1, idx):
        if 0 <= i < len(timestamps) and abs(timestamps[i] - t) <= tolerance:
            return True
    return False


# Seuils d'affichage : en dessous, la mesure existe mais n'est pas assez
# marquante pour mériter une annotation (bruit d'affichage inutile au LLM).
PAUSE_ANNOTATION_THRESHOLD = 0.5     # secondes
ENERGY_ANNOTATION_THRESHOLD = 1.5    # écarts-types
RATE_ANNOTATION_THRESHOLD = 0.25     # 25% d'écart au débit moyen


def annotate_transcript(
    words: list[dict],
    *,
    energy_times: list[float] | None = None,
    energy_z: list[float] | None = None,
    laughter_times: list[float] | None = None,
    window: float = 5.0,
) -> str:
    """Construit la transcription annotée envoyée au réalisateur : chaque mot
    porte son index ET, quand c'est notable, ses marqueurs de prosodie
    (énergie, débit, pause, rire). Format proche de l'exemple de la mission,
    adapté pour rester lisible par le LLM et traçable par index de mot
    (l'index reste la seule référence temporelle fiable — cf. director.py)."""
    if not words:
        return ""
    timed = words_with_pause_and_rate(words, window=window)

    lines = []
    for i, w in enumerate(timed):
        markers = []
        if w["pause_before"] >= PAUSE_ANNOTATION_THRESHOLD:
            markers.append(f"pause {w['pause_before']:.1f}s")
        z = energy_zscore_at(energy_times or [], energy_z or [], w["start"])
        if z is not None and abs(z) >= ENERGY_ANNOTATION_THRESHOLD:
            markers.append(f"énergie {z:+.1f}σ")
        if abs(w["rate_delta"]) >= RATE_ANNOTATION_THRESHOLD:
            markers.append(f"débit {w['rate_delta'] * 100:+.0f}%")
        if _is_near_any(w["start"], laughter_times or []):
            markers.append("rire détecté")

        marker_str = f" ({', '.join(markers)})" if markers else ""
        lines.append(f"({i}){w['word']}{marker_str}")

    return " ".join(lines)
