"""A2 (prompt amélioration commandes) : "plan large" ne doit pas prétendre
avoir élargi le cadrage quand il n'y avait rien à élargir.

Preuve du problème (avant fix) : un clip SANS aucun événement de cadrage (déjà
"wide" par défaut, cf. EDL.framing_spans()) recevait quand même la note
"cadrage élargi sur tout le clip" — faux, rien n'a changé."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.patch import apply_text_adjustment
from sortclip.edl import EDL, Source, Interval, Background, Captions, Watermark, Canvas, FramingEvent


def _edl(events=()):
    return EDL(
        source=Source(path="x.mp4", duration=10.0, width=1080, height=1920),
        keeps=[Interval(start=0.0, end=10.0)], events=list(events),
        background=Background(), captions=Captions(), watermark=Watermark(), canvas=Canvas(),
    )


def test_wide_on_clip_without_framing_events_is_a_noop():
    edl2, notes = apply_text_adjustment(_edl(), "plan large sur tout le clip")
    assert edl2.events == []
    assert any("déjà en plan large" in n for n in notes)
    assert not any("élargi" in n for n in notes)


def test_wide_when_already_all_wide_is_a_noop():
    edl = _edl([FramingEvent(t=1.0, value="wide"), FramingEvent(t=3.0, value="wide")])
    edl2, notes = apply_text_adjustment(edl, "plan large")
    assert any("déjà en plan large" in n for n in notes)


def test_wide_actually_changes_tight_framings():
    edl = _edl([FramingEvent(t=1.0, value="tight"), FramingEvent(t=3.0, value="wide")])
    edl2, notes = apply_text_adjustment(edl, "plan large")
    assert all(e.value == "wide" for e in edl2.events if e.op == "framing")
    assert any("cadrage élargi" in n for n in notes)


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
