"""A1 (prompt amélioration commandes) : le crop resserré doit suivre la
position du visage détecté au lieu de toujours centrer géométriquement.

Preuve du problème (avant fix) : `_crop_scale` ne prenait aucun paramètre de
position -> le filtre `crop` généré était toujours centré, quel que soit le
visage détecté. Ce test prouve que ce n'est plus le cas."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.compile import _crop_scale
from sortclip.edl import FramingEvent, EDL, Source, Interval, Background, Captions, Watermark, Canvas


def test_crop_scale_centered_by_default_unchanged_from_before():
    # face_x=0.5 (défaut) doit reproduire EXACTEMENT l'ancien calcul centré :
    # x = (iw-cropw)/2, ce que 'clamp(iw*0.5-cropw/2, 0, iw-cropw)' vaut aussi.
    out = _crop_scale(0.68, 1080, 1920)
    assert "x='clamp(iw*0.5000-(2*floor(iw*0.6800/2))/2,0,iw-(2*floor(iw*0.6800/2)))'" in out


def test_crop_scale_shifts_left_when_face_is_on_the_left():
    left = _crop_scale(0.68, 1080, 1920, face_x=0.2)
    centered = _crop_scale(0.68, 1080, 1920, face_x=0.5)
    assert left != centered
    assert "iw*0.2000" in left


def test_crop_scale_no_crop_at_full_zoom_ignores_face_x():
    # zoom=1.0 (cadrage "wide") : aucun crop, face_x n'a aucun effet.
    out_a = _crop_scale(1.0, 1080, 1920, face_x=0.1)
    out_b = _crop_scale(1.0, 1080, 1920, face_x=0.9)
    assert out_a == out_b
    assert "crop=" not in out_a


def test_framing_spans_carries_face_x():
    edl = EDL(
        source=Source(path="x.mp4", duration=10.0, width=1920, height=1080, face_x=0.25),
        keeps=[Interval(start=0.0, end=10.0)],
        events=[FramingEvent(t=2.0, value="tight", face_x=0.25)],
        background=Background(), captions=Captions(), watermark=Watermark(),
        canvas=Canvas(),
    )
    spans = edl.framing_spans()
    tight_span = next(sp for sp in spans if sp[2] == "tight")
    assert tight_span[3] == 0.25


def test_framing_event_default_face_x_is_centered():
    e = FramingEvent(t=0.0, value="tight")
    assert e.face_x == 0.5


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
