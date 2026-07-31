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
