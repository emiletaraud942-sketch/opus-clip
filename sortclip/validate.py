"""
Validateur EDL — le « videur » qui ne lève JAMAIS.

Invariant : un rendu qui plante à cause d'un événement bancal est une
régression. `validate()` retourne toujours `(EDL, list[Issue])`.

Ordre de traitement : corriger > supprimer > signaler. Ici on supprime les
événements irrécupérables (hors durée, hors transcript, cadrages trop
rapprochés) plutôt que de les laisser casser le compilateur.
"""

from __future__ import annotations

from dataclasses import dataclass

from .edl import EDL

# Deux cadrages plus rapprochés que ça produisent un clignotement : on écarte
# le second.
MIN_FRAMING_GAP = 0.5


@dataclass
class Issue:
    level: str            # "dropped" | "corrected" | "flagged"
    message: str
    event_id: str | None = None


def validate(edl: EDL, word_count: int) -> tuple[EDL, list[Issue]]:
    """Nettoie les événements de l'EDL. Ne lève jamais : retourne un EDL
    rendu-sûr + la liste des problèmes rencontrés."""
    issues: list[Issue] = []
    dur = edl.out_duration
    kept = []
    last_framing_t: float | None = None

    for e in sorted(edl.events, key=lambda x: x.t):
        # 1. Hors de la durée de sortie.
        if e.t > dur + 1e-6:
            issues.append(Issue("dropped",
                f"événement à {e.t:.2f}s hors de la durée ({dur:.2f}s)", e.id))
            continue

        # 2. Emphasis sur un mot qui n'existe pas dans le transcript nettoyé.
        if e.op == "emphasis" and e.word_index >= word_count:
            issues.append(Issue("dropped",
                f"emphasis sur le mot #{e.word_index} inexistant "
                f"(transcript : {word_count} mots)", e.id))
            continue

        # 3. Cadrage trop proche du précédent cadrage retenu.
        if e.op == "framing":
            if (last_framing_t is not None and e.t > 0
                    and e.t - last_framing_t < MIN_FRAMING_GAP):
                issues.append(Issue("dropped",
                    f"cadrage à {e.t:.2f}s trop proche du précédent "
                    f"(< {MIN_FRAMING_GAP}s)", e.id))
                continue
            last_framing_t = e.t

        kept.append(e)

    return edl.model_copy(update={"events": kept}), issues
