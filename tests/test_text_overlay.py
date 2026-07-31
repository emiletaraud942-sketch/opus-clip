"""F8.3/F5 (AUDIT.md) : texte incrusté (titre/accroche) — absent avant ce
correctif, ajouté suite à une demande utilisateur concrète ("ajoute un titre
fixe en haut" ne faisait rien, silencieusement)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.edl import EDL, Source, Canvas, Captions, Background, TextOverlayEvent
from sortclip.patch import apply_text_adjustment, _extract_overlay_text_and_position
from sortclip.compile import build_filter_complex


def _edl(events=None):
    return EDL(
        source=Source(path="x.mp4", duration=60, width=1920, height=1080),
        canvas=Canvas(w=1080, h=1920, fps=30),
        keeps=[{"start": 0, "end": 30}],
        events=events or [],
        captions=Captions(),
        background=Background(),
    )


def _is_deterministic(notes):
    return not any("non reconnue" in n for n in notes)


# --- Extraction du texte/position -------------------------------------------

def test_extract_quoted_text_is_reliable():
    text, pos = _extract_overlay_text_and_position('ajoute le titre "Épisode 12" en haut')
    assert text == "Épisode 12"
    assert pos == "top"


def test_extract_position_bottom():
    text, pos = _extract_overlay_text_and_position('écris "Abonne-toi" en bas de la vidéo')
    assert text == "Abonne-toi"
    assert pos == "bottom"


def test_extract_position_center():
    text, pos = _extract_overlay_text_and_position('mets "PAUSE" au centre')
    assert text == "PAUSE"
    assert pos == "center"


def test_extract_unquoted_best_effort():
    text, pos = _extract_overlay_text_and_position("ajoute le titre Vlog du jour en haut de la vidéo")
    assert text == "Vlog du jour"
    assert pos == "top"


def test_extract_returns_none_when_nothing_exploitable():
    text, pos = _extract_overlay_text_and_position("ajoute un titre fixe en haut de la vidéo")
    assert text is None


# --- apply_text_adjustment --------------------------------------------------

def test_apply_text_adjustment_adds_overlay_from_quoted_instruction():
    edl2, notes = apply_text_adjustment(_edl(), 'ajoute le titre "Mon podcast" en haut')
    overlays = [e for e in edl2.events if e.op == "text_overlay"]
    assert len(overlays) == 1
    assert overlays[0].text == "Mon podcast"
    assert overlays[0].position == "top"
    assert _is_deterministic(notes)


def test_apply_text_adjustment_no_overlay_added_when_text_not_extractable():
    # Le déclencheur ("titre") est présent mais aucun texte exploitable :
    # ne doit RIEN ajouter (mieux vaut basculer sur le repli LLM que
    # créer un overlay vide ou halluciner un contenu).
    edl2, notes = apply_text_adjustment(_edl(), "ajoute un titre fixe en haut de la vidéo")
    overlays = [e for e in edl2.events if e.op == "text_overlay"]
    assert len(overlays) == 0


def test_apply_text_adjustment_only_touches_events():
    edl0 = _edl()
    edl2, notes = apply_text_adjustment(edl0, 'mets "Titre" en haut')
    assert edl2.captions.model_dump() == edl0.captions.model_dump()
    assert edl2.background.model_dump() == edl0.background.model_dump()


# --- Rendu FFmpeg (construction du filtre, pas d'exécution réelle) --------

def test_build_filter_complex_includes_drawtext_for_overlay():
    overlay = TextOverlayEvent(t=0.0, text="Mon titre", position="top")
    fc = build_filter_complex(_edl(events=[overlay]))
    assert "drawtext=text='Mon titre'" in fc
    assert "enable='between(t," in fc
    assert fc.endswith("[out]")   # contrat existant, ne doit jamais casser


def test_build_filter_complex_escapes_dangerous_overlay_text():
    overlay = TextOverlayEvent(t=0.0, text="C'est : 100% sûr", position="top")
    fc = build_filter_complex(_edl(events=[overlay]))
    assert "C\\'est \\: 100\\% sûr" in fc


def test_build_filter_complex_position_expressions_differ():
    top = build_filter_complex(_edl(events=[TextOverlayEvent(t=0.0, text="A", position="top")]))
    bottom = build_filter_complex(_edl(events=[TextOverlayEvent(t=0.0, text="A", position="bottom")]))
    center = build_filter_complex(_edl(events=[TextOverlayEvent(t=0.0, text="A", position="center")]))
    assert "y=h*0.08" in top
    assert "y=h*0.80" in bottom
    assert "y=(h-th)/2" in center


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
