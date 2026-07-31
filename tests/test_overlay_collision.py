"""E1 (prompt amélioration commandes) : le texte incrusté "en bas" ne doit pas
recouvrir les sous-titres actifs.

Preuve du problème (avant fix) : overlay "bottom" dessine à h*0.80
(sortclip.compile) alors que captions.y=0.78 par défaut place les
sous-titres à peu près à la même hauteur (margin_v = (1-y)*h) -> les deux
textes se superposaient systématiquement sans aucune détection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.patch import apply_text_adjustment
from sortclip.edl import EDL, Source, Interval, Background, Captions, Watermark, Canvas, TextOverlayEvent


def _edl(captions_enabled=True, y=0.78, events=()):
    return EDL(
        source=Source(path="x.mp4", duration=10.0, width=1080, height=1920),
        keeps=[Interval(start=0.0, end=10.0)], events=list(events),
        background=Background(), captions=Captions(enabled=captions_enabled, y=y),
        watermark=Watermark(), canvas=Canvas(),
    )


def test_overlay_bottom_repositioned_when_captions_collide():
    edl2, notes = apply_text_adjustment(_edl(), 'ajoute le titre "promo" en bas')
    overlay = next(e for e in edl2.events if e.op == "text_overlay")
    assert overlay.position == "center"
    assert any("repositionné" in n for n in notes)


def test_overlay_bottom_kept_when_captions_disabled():
    edl2, notes = apply_text_adjustment(_edl(captions_enabled=False), 'ajoute le titre "promo" en bas')
    overlay = next(e for e in edl2.events if e.op == "text_overlay")
    assert overlay.position == "bottom"


def test_overlay_top_never_repositioned():
    edl2, notes = apply_text_adjustment(_edl(), 'ajoute le titre "promo" en haut')
    overlay = next(e for e in edl2.events if e.op == "text_overlay")
    assert overlay.position == "top"


# --- B2 : symétrique — descendre les sous-titres après coup ---------------

def test_moving_captions_down_repositions_existing_bottom_overlay():
    edl = _edl(y=0.55, events=[TextOverlayEvent(t=0.0, text="promo", position="bottom")])
    edl2, notes = apply_text_adjustment(edl, "descends les sous-titres")
    overlay = next(e for e in edl2.events if e.op == "text_overlay")
    assert overlay.position == "center"
    assert any("repositionné au centre" in n for n in notes)


def test_moving_captions_down_leaves_top_overlay_alone():
    edl = _edl(y=0.55, events=[TextOverlayEvent(t=0.0, text="promo", position="top")])
    edl2, notes = apply_text_adjustment(edl, "descends les sous-titres")
    overlay = next(e for e in edl2.events if e.op == "text_overlay")
    assert overlay.position == "top"


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
