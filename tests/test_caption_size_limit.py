"""B1 (prompt amélioration commandes) : prévenir l'utilisateur quand la taille
des sous-titres est déjà au maximum/minimum plutôt que de prétendre l'avoir
changée.

Preuve du problème (avant fix) : demander "plus gros" avec size=160 (déjà le
plafond) posait quand même la note "sous-titres agrandis (160)" -- une
confirmation trompeuse puisque rien n'avait bougé."""

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


def _edl(size):
    return EDL(
        source=Source(path="x.mp4", duration=10.0, width=1080, height=1920),
        keeps=[Interval(start=0.0, end=10.0)],
        background=Background(), captions=Captions(size=size),
        watermark=Watermark(), canvas=Canvas(),
    )


def test_bigger_at_max_size_reports_limit_reached_without_lying():
    edl2, notes = apply_text_adjustment(_edl(160), "sous-titres plus gros")
    assert edl2.captions.size == 160
    assert any("déjà à la taille maximum" in n for n in notes)
    assert not any("agrandis" in n for n in notes)


def test_smaller_at_min_size_reports_limit_reached_without_lying():
    edl2, notes = apply_text_adjustment(_edl(20), "sous-titres plus petit")
    assert edl2.captions.size == 20
    assert any("déjà à la taille minimum" in n for n in notes)
    assert not any("réduits" in n for n in notes)


def test_bigger_below_max_still_grows_normally():
    edl2, notes = apply_text_adjustment(_edl(64), "sous-titres plus gros")
    assert edl2.captions.size == 76
    assert any("agrandis (76)" in n for n in notes)


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
