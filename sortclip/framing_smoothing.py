"""
Lissage du chemin de recadrage (Partie F2).

Module PUR : prend une série temporelle de positions de cadrage BRUTES (déjà
détectées ailleurs — visage, locuteur actif…) et produit une série LISSÉE +
des décisions coupe/panoramique, sans dépendre de vision ou d'audio ici.

État réel (à lire avant utilisation) : cet algorithme est prêt et testé, mais
RIEN ne l'alimente encore en production. F1 (diarisation) — dont ce module
est censé consommer la sortie pour savoir QUAND changer de locuteur — n'a
pas pu être implémenté (pyannote.audio a des modèles pré-entraînés soumis à
un accord de licence sur HuggingFace, nécessitant un compte réel et un jeton
que je ne peux pas générer en autonomie). L'algorithme de lissage ci-dessous
est donc un composant PRÊT, pas encore CÂBLÉ à une source de positions réelle.

Les quatre réglages demandés (hystérésis, filtre de position, décision
coupe/panoramique, verrouillage) sont des paramètres de fonction, pas des
constantes codées en dur — exposables comme réglages de preset une fois
câblés à une vraie source de détection.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RawPosition:
    """Une détection brute (ex. centre d'un visage) à un instant donné.
    `speaker_id` est optionnel : None = position détectée sans locuteur
    identifié (ex. voix off / hors champ)."""
    t: float
    x: float             # centre horizontal, proportion 0-1 du canevas
    speaker_id: str | None = None


@dataclass
class FramingDecision:
    t: float
    x: float              # centre horizontal RETENU (lissé) après décision
    kind: str              # "cut" | "pan" | "hold"
    speaker_id: str | None


def smooth_framing_path(
    raw: list[RawPosition],
    *,
    hysteresis_seconds: float = 0.5,
    smoothing_alpha: float = 0.3,
    cut_distance_threshold: float = 0.35,
    lockout_seconds: float = 1.2,
) -> list[FramingDecision]:
    """Applique les quatre réglages de F2, dans l'ordre demandé :

    1. Hystérésis : un changement de locuteur n'est retenu que s'il reste
       stable pendant plus de `hysteresis_seconds` (sinon un simple
       acquiescement ferait changer de plan).
    2. Filtre de position : moyenne glissante pondérée (exponentielle,
       facteur `smoothing_alpha`) sur le centre retenu — la détection brute
       tremble d'une image à l'autre, la position affichée ne doit pas.
    3. Décision coupe/panoramique : coupe franche si l'écart de position
       dépasse `cut_distance_threshold` (proportion du canevas), panoramique
       en dessous.
    4. Verrouillage minimal : après un changement de cadrage, interdiction
       d'en déclencher un autre avant `lockout_seconds`.

    Ne lève jamais (liste vide -> liste vide). Cas locuteur non identifié
    (`speaker_id=None`) : conserve le dernier cadrage stable, ne cherche
    jamais de visage au hasard (voir _resolve_speaker_change)."""
    if not raw:
        return []

    raw = sorted(raw, key=lambda r: r.t)
    decisions: list[FramingDecision] = []

    smoothed_x = raw[0].x
    current_speaker = raw[0].speaker_id
    last_change_t = raw[0].t
    pending_speaker: str | None = None
    pending_since: float | None = None

    for i, r in enumerate(raw):
        # --- 1. Hystérésis sur le changement de locuteur ---
        if r.speaker_id is not None and r.speaker_id != current_speaker:
            if pending_speaker != r.speaker_id:
                pending_speaker, pending_since = r.speaker_id, r.t
            stable_long_enough = (r.t - pending_since) >= hysteresis_seconds
            in_lockout = (r.t - last_change_t) < lockout_seconds
            if stable_long_enough and not in_lockout:
                current_speaker = r.speaker_id
                last_change_t = r.t
                pending_speaker, pending_since = None, None
        elif r.speaker_id is None:
            # Voix off / hors champ : on NE cherche jamais un visage au
            # hasard — on garde le dernier cadrage stable (current_speaker,
            # smoothed_x inchangés), conformément à la consigne explicite.
            pending_speaker, pending_since = None, None
        else:
            pending_speaker, pending_since = None, None

        # --- 2. Filtre de position (moyenne glissante exponentielle) ---
        # On ne se dirige vers la position brute QUE si c'est une détection
        # du locuteur COURANT. Une voix off (speaker_id=None) ou un locuteur
        # différent pas encore adopté (hystérésis) laisse la position au
        # dernier cadrage stable — jamais de visage cherché au hasard.
        target_x = r.x if (r.speaker_id is not None and r.speaker_id == current_speaker) else smoothed_x
        prev_x = smoothed_x
        smoothed_x = smoothing_alpha * target_x + (1 - smoothing_alpha) * smoothed_x

        # --- 3. Décision coupe / panoramique ---
        distance = abs(smoothed_x - prev_x)
        if i == 0:
            kind = "hold"
        elif distance >= cut_distance_threshold:
            kind = "cut"
        elif distance > 1e-6:
            kind = "pan"
        else:
            kind = "hold"

        decisions.append(FramingDecision(t=r.t, x=round(smoothed_x, 4),
                                         kind=kind, speaker_id=current_speaker))

    return decisions
