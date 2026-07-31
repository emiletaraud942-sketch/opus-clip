"""
Opérations sur un clip ENTIER plutôt que sur son EDL déclaratif seul :
scission en deux clips indépendants (F6 de l'audit, AUDIT.md). Pures,
testables sans FFmpeg/API — le module qui orchestre le rendu (modal_app.py)
consomme ces fonctions.
"""

from __future__ import annotations

from .edl import Interval, Event


def split_keeps_at_output_time(keeps: list[Interval], t_split: float) -> tuple[list[Interval], list[Interval]]:
    """Coupe une liste de `keeps` (temps SOURCE) à l'instant `t_split` de la
    timeline de SORTIE — l'intervalle qui contient ce point est lui-même
    coupé en deux. Renvoie (keeps_avant, keeps_après). Une liste vide d'un
    côté signifie un point de coupure invalide (tout au début ou à la fin)."""
    acc = 0.0
    before: list[Interval] = []
    after: list[Interval] = []
    for k in keeps:
        dur = k.end - k.start
        if acc + dur <= t_split:
            before.append(k)
        elif acc >= t_split:
            after.append(k)
        else:
            local = t_split - acc
            src_cut = k.start + local
            before.append(Interval(start=k.start, end=src_cut))
            after.append(Interval(start=src_cut, end=k.end))
        acc += dur
    return before, after


def split_events_at_output_time(events: list[Event], t_split: float) -> tuple[list, list]:
    """Répartit les événements (temps de SORTIE) de part et d'autre du point
    de coupure. Ceux de la seconde moitié ont leur `t` RECALÉ à zéro (nouvelle
    origine du second clip)."""
    before = [e for e in events if e.t < t_split]
    after = [e.model_copy(update={"t": e.t - t_split}) for e in events if e.t >= t_split]
    return before, after


def snap_split_to_word_boundary(t_split: float, words_out: list[dict],
                                 max_shift: float = 1.0) -> float:
    """G1 (prompt amélioration commandes) : ajuste `t_split` (temps de
    SORTIE) pour tomber dans un silence entre deux mots plutôt qu'au milieu
    d'un mot — sans ça, « scinder » pouvait couper un mot en deux, avec la
    moitié de son audio/sous-titre de chaque côté.

    Ne décale JAMAIS de plus de `max_shift` secondes (au-delà, on considère
    qu'aucun silence exploitable n'est proche et on renvoie `t_split` tel
    quel plutôt que de déplacer arbitrairement le point demandé par
    l'utilisateur). Ne gère qu'un seul locuteur / des mots non chevauchants ;
    des `words_out` désordonnés ou se chevauchant ne sont pas garantis."""
    if not words_out:
        return t_split
    for w in words_out:
        start, end = float(w["start"]), float(w["end"])
        if start <= t_split < end:
            # t_split tombe DANS un mot -> décale vers le bord le plus proche,
            # dans la limite de max_shift.
            to_start = t_split - start
            to_end = end - t_split
            if to_start <= to_end and to_start <= max_shift:
                return start
            if to_end <= max_shift:
                return end
            return t_split  # ni l'un ni l'autre bord n'est assez proche
    return t_split
