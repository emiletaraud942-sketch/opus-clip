"""7 nouvelles fonctionnalités de retouche déterministes (patch.py) —
demande utilisateur : "de vraies fonctionnalités", pas des variantes
cosmétiques. Toutes testées ici en exécutant réellement le code."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.edl import EDL, Source, Canvas, Captions, Background, FramingEvent, EmphasisEvent
from sortclip.patch import apply_text_adjustment


def _edl(events=None, captions=None, background=None, keeps=None):
    return EDL(
        source=Source(path="x.mp4", duration=60, width=1920, height=1080),
        canvas=Canvas(w=1080, h=1920, fps=30),
        keeps=keeps or [{"start": 0, "end": 30}],
        events=events or [],
        captions=captions or Captions(),
        background=background or Background(),
    )


def _is_deterministic(notes):
    return not any("non reconnue" in n for n in notes)


def _words():
    return [
        {"word": "bonjour", "start": 0.0, "end": 0.4},
        {"word": "tout", "start": 0.5, "end": 0.8},
        {"word": "le", "start": 0.9, "end": 1.0},
        {"word": "monde", "start": 1.1, "end": 1.5},
        {"word": "aujourd'hui", "start": 1.6, "end": 2.1},
    ]


# 1/2 — désactiver / réactiver les sous-titres

def test_disable_subtitles():
    edl2, notes = apply_text_adjustment(_edl(), "retire les sous-titres")
    assert edl2.captions.enabled is False
    assert _is_deterministic(notes)


def test_reenable_subtitles():
    edl0 = _edl(captions=Captions(enabled=False))
    edl2, notes = apply_text_adjustment(edl0, "remets les sous-titres")
    assert edl2.captions.enabled is True
    assert _is_deterministic(notes)


# 3 — fond en couleur unie

def test_solid_background():
    edl2, notes = apply_text_adjustment(_edl(), "mets un fond de couleur unie")
    assert edl2.background.mode == "solid"
    assert _is_deterministic(notes)


def test_solid_black_background():
    edl2, notes = apply_text_adjustment(_edl(), "fond noir en couleur unie")
    assert edl2.background.mode == "solid"
    assert edl2.background.color == "#000000"


# 4 — plein cadre sans fond

def test_no_background_mode():
    edl2, notes = apply_text_adjustment(_edl(), "plein cadre sans fond")
    assert edl2.background.mode == "none"
    assert _is_deterministic(notes)


# 5 — figer le cadrage

def test_freeze_framing_on_dominant_value():
    events = [
        FramingEvent(t=1.0, value="tight"),
        FramingEvent(t=5.0, value="tight"),
        FramingEvent(t=9.0, value="wide"),
    ]
    edl2, notes = apply_text_adjustment(_edl(events=events), "fige le cadrage")
    framings = [e for e in edl2.events if e.op == "framing"]
    assert len(framings) == 1
    assert framings[0].value == "tight"   # dominant (2 sur 3)
    assert framings[0].t == 0.0
    assert _is_deterministic(notes)


def test_freeze_framing_defaults_to_medium_without_existing_framing():
    edl2, notes = apply_text_adjustment(_edl(), "garde un seul cadrage")
    framings = [e for e in edl2.events if e.op == "framing"]
    assert len(framings) == 1
    assert framings[0].value == "medium"


# 6 — mise en valeur d'un mot précis

def test_add_emphasis_on_specific_word_quoted():
    edl2, notes = apply_text_adjustment(_edl(), 'mets en valeur "monde"', words=_words())
    emphases = [e for e in edl2.events if e.op == "emphasis"]
    assert len(emphases) == 1
    assert emphases[0].word_index == 3   # "monde" est le 4e mot (index 3)
    assert _is_deterministic(notes)


def test_add_emphasis_on_last_word_unquoted():
    edl2, notes = apply_text_adjustment(_edl(), "surligne bonjour", words=_words())
    emphases = [e for e in edl2.events if e.op == "emphasis"]
    assert len(emphases) == 1
    assert emphases[0].word_index == 0


def test_add_emphasis_without_words_falls_back_to_llm():
    # Pas de `words` fourni -> impossible de résoudre le mot -> non reconnu.
    edl2, notes = apply_text_adjustment(_edl(), 'mets en valeur "monde"')
    assert any("non reconnue" in n for n in notes)


# 7 — retirer une mise en valeur (précise ou toutes)

def test_remove_specific_emphasis():
    events = [EmphasisEvent(t=1.5, word_index=3, style="pop")]
    edl2, notes = apply_text_adjustment(_edl(events=events), 'retire la mise en valeur sur "monde"', words=_words())
    assert len([e for e in edl2.events if e.op == "emphasis"]) == 0
    assert _is_deterministic(notes)


def test_remove_all_emphases():
    events = [EmphasisEvent(t=1.5, word_index=3, style="pop"), EmphasisEvent(t=0.2, word_index=0, style="pop")]
    edl2, notes = apply_text_adjustment(_edl(events=events), "retire toutes les mises en valeur")
    assert len([e for e in edl2.events if e.op == "emphasis"]) == 0
    assert _is_deterministic(notes)


# Invariant : ces retouches ne touchent QUE ce qui est visé.

def test_freeze_framing_does_not_touch_captions_or_background():
    edl0 = _edl(events=[FramingEvent(t=1.0, value="tight")], captions=Captions(size=80, bold=True))
    edl2, _ = apply_text_adjustment(edl0, "fige le cadrage")
    assert edl2.captions.model_dump() == edl0.captions.model_dump()
    assert edl2.background.model_dump() == edl0.background.model_dump()


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
