"""
Chargement du jeu de référence (Partie 0.1) depuis `evals/golden/`.

Format d'un clip golden (un dossier par clip) :
    evals/golden/<clip_id>/
        meta.json        # {"genre": "...", "difficult": bool, "source_note": "..."}
        reference.json   # EDL de référence annoté à la main (voir README.md)
        candidate.json   # optionnel : sortie régénérée à évaluer contre la référence

Voir evals/golden/README.md pour l'état réel de ce jeu (provisoire, pas les
40 clips réels demandés par la mission).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

GOLDEN_ROOT = Path("evals")


@dataclass
class GoldenClip:
    clip_id: str
    genre: str
    difficult: bool
    reference: dict
    candidate: dict | None


def list_golden_clips(set_name: str = "golden") -> list[GoldenClip]:
    """Liste les clips golden disponibles pour le jeu `set_name`. Renvoie une
    liste vide (jamais une erreur) si le dossier n'existe pas ou est vide —
    un jeu manquant est une information à afficher, pas un crash."""
    root = GOLDEN_ROOT / set_name
    if not root.is_dir():
        return []

    clips: list[GoldenClip] = []
    for clip_dir in sorted(p for p in root.iterdir() if p.is_dir()):
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
                candidate = json.loads(cand_path.read_text())
            except Exception:
                candidate = None
        clips.append(GoldenClip(
            clip_id=clip_dir.name,
            genre=meta.get("genre", "inconnu"),
            difficult=bool(meta.get("difficult", False)),
            reference=reference,
            candidate=candidate,
        ))
    return clips
