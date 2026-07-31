"""D1 (prompt amélioration commandes) : le flou de fond doit être proportionnel
à la résolution de sortie, pas un sigma ffmpeg absolu.

Preuve du problème (avant fix) : `gblur=sigma=25` était appliqué tel quel
après le scale vers le canevas -> en 4K (2160x3840, deux fois plus haut que
la référence 1080p/1920), le même sigma produit un flou visuellement deux
fois plus faible relativement à l'image."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.edl import EDL, Source, Canvas, Captions, Background, Interval


def _edl(canvas_h):
    canvas_w = 1080 if canvas_h == 1920 else 2160
    return EDL(
        source=Source(path="x.mp4", duration=60, width=1920, height=1080),
        canvas=Canvas(w=canvas_w, h=canvas_h, fps=30),
        keeps=[Interval(start=0.0, end=5.0)],
        events=[], captions=Captions(enabled=False), background=Background(mode="blur", sigma=25),
    )


def _sigma(fc: str) -> float:
    m = re.search(r"gblur=sigma=([\d.]+)", fc)
    assert m, fc
    return float(m.group(1))


def test_sigma_scales_proportionally_with_canvas_height():
    from sortclip.compile import build_filter_complex
    fc_1080p = build_filter_complex(_edl(1920))
    fc_4k = build_filter_complex(_edl(3840))
    sigma_1080p = _sigma(fc_1080p)
    sigma_4k = _sigma(fc_4k)
    assert sigma_1080p == 25.0  # référence : comportement inchangé à 1920px
    assert sigma_4k == 50.0     # deux fois plus haut -> sigma doublé


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
