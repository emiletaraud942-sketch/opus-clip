"""D2 (prompt amélioration commandes) : fond en couleur unie assorti à la
couleur dominante réelle de la vidéo, au lieu d'une palette fixe manuelle.

Preuve du problème (avant fix) : "fond en couleur unie" ne pouvait choisir
qu'une couleur de `_COLOR_WORDS` ou noir — aucun moyen de l'assortir au
contenu réel de la vidéo, même quand cette information était disponible."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.patch import apply_text_adjustment
from sortclip.edl import EDL, Source, Interval, Background, Captions, Watermark, Canvas


def _edl(dominant_color=None):
    return EDL(
        source=Source(path="x.mp4", duration=10.0, width=1080, height=1920, dominant_color=dominant_color),
        keeps=[Interval(start=0.0, end=10.0)],
        background=Background(), captions=Captions(), watermark=Watermark(), canvas=Canvas(),
    )


def test_matches_video_dominant_color_when_available():
    edl2, notes = apply_text_adjustment(_edl(dominant_color="#3355AA"), "fond assorti à la vidéo")
    assert edl2.background.mode == "solid"
    assert edl2.background.color == "#3355AA"
    assert any("#3355AA" in n for n in notes)


def test_reports_unavailable_when_not_computed():
    edl2, notes = apply_text_adjustment(_edl(dominant_color=None), "fond couleur dominante")
    assert edl2.background.mode == "blur"  # inchangé
    assert any("indisponible" in n for n in notes)


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
