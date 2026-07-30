"""Tests A1 (table de patchs déterministes) et A2 (score de motivation).

Critère de réussite A1 (mission) : sur 50 consignes réalistes, au moins 70%
traitées sans appel LLM, en moins de 200ms. Testé ci-dessous sur un
échantillon synthétique de 50 consignes (pas de vraies consignes utilisateur
réelles collectées — voir le rapport final)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.edl import EDL, Source, Canvas, Captions, Background, FramingEvent
from sortclip.patch import apply_text_adjustment


def _edl(events=None, keeps=None):
    return EDL(
        source=Source(path="x.mp4", duration=60),
        canvas=Canvas(w=1080, h=1920, fps=30),
        keeps=keeps or [{"start": 0, "end": 30}],
        events=events or [],
        captions=Captions(),
        background=Background(),
    )


def _is_deterministic(notes):
    return not any("non reconnue" in n for n in notes)


# --- A2 : motivation --------------------------------------------------------

def test_framing_event_has_default_motivation():
    e = FramingEvent(t=1.0, value="tight")
    assert e.motivation == 0.5
    assert e.raison == ""


def test_moins_de_zoom_removes_least_motivated_first():
    events = [
        FramingEvent(t=1.0, value="tight", motivation=0.9, raison="punchline"),
        FramingEvent(t=5.0, value="wide", motivation=0.2, raison="remplissage"),
        FramingEvent(t=9.0, value="tight", motivation=0.8, raison="réaction"),
        FramingEvent(t=13.0, value="wide", motivation=0.1, raison="remplissage"),
    ]
    edl2, notes = apply_text_adjustment(_edl(events, keeps=[{"start": 0, "end": 30}]), "moins de zooms")
    remaining = [e for e in edl2.events if e.op == "framing"]
    assert len(remaining) == 2
    # Les deux retirés doivent être les motivations 0.2 et 0.1 (les plus faibles).
    remaining_motivations = sorted(e.motivation for e in remaining)
    assert remaining_motivations == [0.8, 0.9]


def test_moins_de_zoom_falls_back_to_alternation_when_uninformative():
    # Toutes les motivations à 0.5 (par défaut, comme un EDL construit sans
    # réalisateur LLM) : doit reproduire l'ancien comportement (garde pair).
    events = [FramingEvent(t=float(i) + 1, value="tight") for i in range(4)]
    edl2, notes = apply_text_adjustment(_edl(events, keeps=[{"start": 0, "end": 30}]), "moins de zooms")
    remaining_ts = sorted(e.t for e in edl2.events if e.op == "framing")
    assert remaining_ts == [1.0, 3.0]   # index 0 et 2 (comme l'alternance historique)


# --- A1 : nouvelles intentions déterministes --------------------------------

def test_plan_large_sets_all_framings_wide():
    events = [FramingEvent(t=1.0, value="tight"), FramingEvent(t=5.0, value="medium")]
    edl2, notes = apply_text_adjustment(_edl(events), "mets un plan large")
    assert all(e.value == "wide" for e in edl2.events if e.op == "framing")
    assert _is_deterministic(notes)


def test_subtitle_position_up_and_down():
    edl0 = _edl()
    base_y = edl0.captions.y
    edl_up, notes_up = apply_text_adjustment(edl0, "monte les sous-titres")
    assert edl_up.captions.y < base_y
    assert _is_deterministic(notes_up)
    edl_down, notes_down = apply_text_adjustment(edl0, "descend les sous-titres")
    assert edl_down.captions.y > base_y
    assert _is_deterministic(notes_down)


def test_subtitle_bold_toggle():
    edl2, notes = apply_text_adjustment(_edl(), "mets les sous-titres en gras")
    assert edl2.captions.bold is True
    assert _is_deterministic(notes)
    edl3, notes3 = apply_text_adjustment(edl2, "sous-titres pas gras")
    assert edl3.captions.bold is False


def test_karaoke_style_toggle():
    edl2, notes = apply_text_adjustment(_edl(), "style karaoké")
    assert edl2.captions.style == "karaoke"
    assert _is_deterministic(notes)
    edl3, notes3 = apply_text_adjustment(edl2, "sous-titres classiques")
    assert edl3.captions.style == "plain"


def test_coupe_le_debut_shrinks_first_keep():
    edl0 = _edl(keeps=[{"start": 0, "end": 30}])
    edl2, notes = apply_text_adjustment(edl0, "coupe le début")
    assert edl2.keeps[0].start == 2.0
    assert edl2.out_duration == 28.0   # recalculé automatiquement (propriété)
    assert _is_deterministic(notes)


def test_coupe_la_fin_shrinks_last_keep():
    edl0 = _edl(keeps=[{"start": 0, "end": 30}])
    edl2, notes = apply_text_adjustment(edl0, "coupe la fin")
    assert edl2.keeps[-1].end == 28.0
    assert edl2.out_duration == 28.0
    assert _is_deterministic(notes)


def test_coupe_le_debut_never_makes_keep_invalid():
    # Intervalle trop court pour être rogné de 2s + marge -> ne touche à rien.
    edl0 = _edl(keeps=[{"start": 0, "end": 2.5}])
    edl2, notes = apply_text_adjustment(edl0, "coupe le début")
    assert edl2.keeps[0].start == 0.0   # inchangé, protection respectée
    assert edl2.out_duration == 2.5


def test_plus_de_zoom_is_not_deterministic():
    # Doit rester non reconnu déterministe (ajouter un zoom pertinent
    # suppose d'identifier un nouveau moment fort) -> repli LLM attendu.
    edl2, notes = apply_text_adjustment(_edl(), "mets plus de zooms")
    assert any("non reconnue" in n for n in notes)


# --- Critère de réussite A1 : >=70% sans LLM, <200ms sur 50 consignes ------

REALISTIC_INSTRUCTIONS = [
    "moins de zooms", "trop de zooms", "aucun zoom", "sans zoom", "pas de zoom",
    "resserre sur le locuteur", "recadre sur le locuteur", "mets un plan large",
    "dézoome un peu", "sous-titres plus gros", "sous-titres plus grands",
    "sous-titres plus petits", "texte plus discret", "sous-titres en jaune",
    "sous-titres en blanc", "sous-titres en rose", "sous-titres en cyan",
    "sous-titres en vert", "sous-titres en orange", "monte les sous-titres",
    "descend les sous-titres", "remonte le texte", "mets les sous-titres en gras",
    "sous-titres pas gras", "style karaoké", "mot par mot", "sous-titres classiques",
    "sans karaoké", "coupe le début", "raccourcis le début", "coupe la fin",
    "raccourcis la fin", "plus flou", "moins flou", "rends le fond net",
    "moins de cadrages", "trop de cadrages", "recule un peu la caméra",
    "sous-titres en gras et en jaune", "moins de zooms et sous-titres plus gros",
    "plan large et sous-titres en blanc", "coupe le début et la fin",
    "resserre et mets en gras", "sous-titres plus gros et en rose",
    "moins de zoom stp", "un peu moins de zooms", "vraiment moins de zoom",
    "j'aimerais moins de zooms", "peux-tu resserrer sur le visage",
    "mets du karaoké",
]


def test_a1_success_criterion_coverage_and_speed():
    assert len(REALISTIC_INSTRUCTIONS) == 50
    edl0 = _edl(events=[FramingEvent(t=1.0, value="tight"), FramingEvent(t=5.0, value="wide")])

    t0 = time.perf_counter()
    handled = 0
    for instr in REALISTIC_INSTRUCTIONS:
        _, notes = apply_text_adjustment(edl0, instr)
        if _is_deterministic(notes):
            handled += 1
    elapsed_ms = (time.perf_counter() - t0) * 1000

    rate = handled / len(REALISTIC_INSTRUCTIONS)
    print(f"\n  [A1] {handled}/{len(REALISTIC_INSTRUCTIONS)} consignes traitées sans LLM "
          f"({rate:.0%}) en {elapsed_ms:.1f}ms au total")
    assert rate >= 0.70, f"seulement {rate:.0%} traité sans LLM (objectif 70%)"
    assert elapsed_ms < 200, f"{elapsed_ms:.1f}ms pour 50 consignes (objectif < 200ms)"
