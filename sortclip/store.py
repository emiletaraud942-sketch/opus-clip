"""
Store EDL versionné — le vrai déterminisme du produit.

Un EDL généré une fois n'est JAMAIS régénéré : on le stocke, et on ne rappelle
le LLM que pour les ajustements (patchs). Le déterminisme ne repose pas sur la
température du modèle (température 0 = quasi-déterminisme, pas garantie) mais sur
ce cache.

Implémentation simple : un dossier par clip, un fichier JSON par version.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .edl import EDL


class EDLStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, clip_id: str) -> Path:
        d = self.root / clip_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _versions(self, clip_id: str) -> list[int]:
        return sorted(int(p.stem[1:]) for p in self._dir(clip_id).glob("v*.json"))

    def save(self, clip_id: str, edl: EDL, note: str = "") -> int:
        """Enregistre un nouvel état et retourne son numéro de version (1, 2, …)."""
        versions = self._versions(clip_id)
        n = (versions[-1] if versions else 0) + 1
        payload = {
            "version": n,
            "note": note,
            "created_at": time.time(),
            "edl": edl.model_dump(mode="json"),
        }
        (self._dir(clip_id) / f"v{n}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return n

    def load(self, clip_id: str, version: int | None = None) -> EDL | None:
        versions = self._versions(clip_id)
        if not versions:
            return None
        v = version if version is not None else versions[-1]
        path = self._dir(clip_id) / f"v{v}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return EDL.model_validate(data["edl"])

    def latest(self, clip_id: str) -> EDL | None:
        return self.load(clip_id)

    def history(self, clip_id: str) -> list[dict]:
        out = []
        for v in self._versions(clip_id):
            d = json.loads((self._dir(clip_id) / f"v{v}.json").read_text(encoding="utf-8"))
            out.append({"version": d["version"], "note": d.get("note", ""),
                        "created_at": d.get("created_at")})
        return out

    def revert(self, clip_id: str, version: int) -> EDL:
        """Revenir à une version : crée une NOUVELLE version copiant l'ancienne
        (l'historique n'est jamais réécrit) et retourne son EDL."""
        edl = self.load(clip_id, version)
        if edl is None:
            raise ValueError(f"version {version} introuvable pour « {clip_id} »")
        self.save(clip_id, edl, note=f"revert vers v{version}")
        return edl
