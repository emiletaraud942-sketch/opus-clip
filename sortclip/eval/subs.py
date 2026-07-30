"""
Chargement du jeu de test de sous-titrage (Partie 0.1 du chantier
« correction-sous-titres ») depuis `evals/subs/`.

Format d'un clip (un dossier par clip) :
    evals/subs/<clip_id>/
        meta.json        # {"category": "...", "note": "..."}
        reference.json   # {"words": [{"word": "...", "start": s, "end": e}, ...]}
                          # (au moins 20 mots-repères, horodatages vérifiés à la main)
        candidate.json    # optionnel : {"words": [...]} produit en rejouant le
                          # pipeline réel — nécessite l'API AssemblyAI + une
                          # vraie source vidéo, indisponible dans cet environnement.

Voir evals/subs/README.md pour l'état réel de ce jeu (infrastructure prête,
les 12 clips réels demandés par la mission n'ont pas pu être constitués en
autonomie — mêmes raisons que le jeu golden de la mission précédente).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SUBS_ROOT = Path("evals") / "subs"


@dataclass
class SubsClip:
    clip_id: str
    category: str
    reference_words: list[dict]
    candidate_words: list[dict] | None


def list_subs_clips() -> list[SubsClip]:
    """Renvoie une liste vide (jamais une erreur) si `evals/subs/` n'existe
    pas ou est vide — un jeu manquant est une information à afficher, pas un
    crash (même logique que list_golden_clips)."""
    if not SUBS_ROOT.is_dir():
        return []

    clips: list[SubsClip] = []
    for clip_dir in sorted(p for p in SUBS_ROOT.iterdir() if p.is_dir()):
        meta_path = clip_dir / "meta.json"
        ref_path = clip_dir / "reference.json"
        if not meta_path.exists() or not ref_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
            reference = json.loads(ref_path.read_text())
        except Exception:
            continue
        candidate = None
        cand_path = clip_dir / "candidate.json"
        if cand_path.exists():
            try:
                candidate = json.loads(cand_path.read_text()).get("words")
            except Exception:
                candidate = None
        clips.append(SubsClip(
            clip_id=clip_dir.name,
            category=meta.get("category", "inconnu"),
            reference_words=reference.get("words", []),
            candidate_words=candidate,
        ))
    return clips
