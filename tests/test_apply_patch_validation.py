"""Audit du repo (finding vérifié par exécution) : `apply_patch` (action
"modify") utilisait `model_copy(update=...)`, qui NE REVALIDE RIEN — un
patch venant du repli LLM (schéma d'outil sans contrainte de type sur
`value`) pouvait écrire une valeur hors du vocabulaire fermé d'un champ
(ex: FramingEvent.value = "ULTRA_MEGA_ZOOM" au lieu de wide/medium/tight),
accepté silencieusement, puis faire planter `build_filter_complex` (KeyError)
bien après avoir rapporté un succès.

Preuve du problème (avant fix) :
    op = PatchOp(action="modify", event_id=fe.id, field="value", value="ULTRA_MEGA_ZOOM")
    apply_patch(edl, [op])          # accepté, event.value == "ULTRA_MEGA_ZOOM"
    build_filter_complex(res.edl)   # KeyError: 'ULTRA_MEGA_ZOOM'
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.patch import apply_patch, PatchOp
from sortclip.edl import EDL, Source, Interval, Background, Captions, Watermark, Canvas, FramingEvent
from sortclip.compile import build_filter_complex


def _edl():
    return EDL(
        source=Source(path="x.mp4", duration=10.0, width=1080, height=1920),
        keeps=[Interval(start=0.0, end=5.0)],
        events=[FramingEvent(t=1.0, value="tight")],
        background=Background(), captions=Captions(enabled=False), watermark=Watermark(), canvas=Canvas(),
    )


def test_modify_with_invalid_enum_value_is_rejected_not_applied():
    edl = _edl()
    fe = edl.events[0]
    op = PatchOp(action="modify", event_id=fe.id, field="value", value="ULTRA_MEGA_ZOOM")
    res = apply_patch(edl, [op])
    assert res.rejected == [op]
    assert res.applied == []
    assert res.edl.events[0].value == "tight"  # inchangé


def test_modify_result_never_crashes_the_renderer():
    edl = _edl()
    fe = edl.events[0]
    op = PatchOp(action="modify", event_id=fe.id, field="value", value="ULTRA_MEGA_ZOOM")
    res = apply_patch(edl, [op])
    build_filter_complex(res.edl)  # ne doit plus jamais lever


def test_modify_with_valid_value_still_works():
    edl = _edl()
    fe = edl.events[0]
    op = PatchOp(action="modify", event_id=fe.id, field="value", value="wide")
    res = apply_patch(edl, [op])
    assert res.applied == [op]
    assert res.edl.events[0].value == "wide"


def test_modify_with_wrong_type_is_rejected():
    edl = _edl()
    fe = edl.events[0]
    op = PatchOp(action="modify", event_id=fe.id, field="t", value="pas un nombre")
    res = apply_patch(edl, [op])
    assert res.rejected == [op]
    assert res.edl.events[0].t == 1.0


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
