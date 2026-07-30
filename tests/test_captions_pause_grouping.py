"""Vérifie qu'un groupe de sous-titres ne traverse JAMAIS une pause trop
longue — sinon des mots s'affichent à l'écran avant d'être prononcés (bug
signalé par l'utilisateur après le premier test H1/H2 en prod)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

import sortclip.captions as captions_mod
from sortclip.captions import group_words_by_pause, build_ass, filter_low_confidence_words
from sortclip.edl import Captions, Canvas


def _words(pairs):
    return [{"word": f"m{i}", "start": s, "end": e} for i, (s, e) in enumerate(pairs)]


def test_group_words_by_pause_splits_on_long_gap():
    # Pause de 2s entre le mot 1 et le mot 2 -> ne doit JAMAIS être dans le
    # même groupe, même si max_words=10 le permettrait par le compte.
    words = _words([(0.0, 0.4), (0.5, 0.9), (2.9, 3.3), (3.4, 3.8)])
    groups = group_words_by_pause(words, max_words=10, max_gap=0.6)
    assert len(groups) == 2
    assert [w["word"] for w in groups[0]] == ["m0", "m1"]
    assert [w["word"] for w in groups[1]] == ["m2", "m3"]


def test_group_words_by_pause_respects_max_words_within_continuous_speech():
    words = _words([(0.0, 0.3), (0.3, 0.6), (0.6, 0.9), (0.9, 1.2), (1.2, 1.5)])
    groups = group_words_by_pause(words, max_words=3, max_gap=0.6)
    assert [len(g) for g in groups] == [3, 2]


def test_group_words_by_pause_short_gap_stays_together():
    # Micro-pause de 0.2s (respiration normale) : reste dans le même groupe.
    words = _words([(0.0, 0.4), (0.6, 1.0)])
    groups = group_words_by_pause(words, max_words=10, max_gap=0.6)
    assert len(groups) == 1


def test_group_words_by_pause_empty():
    assert group_words_by_pause([]) == []


def test_build_ass_dialogue_never_starts_before_its_first_word_and_never_spans_a_pause():
    # Reproduit le bug concret : words_per_line=4, mais une pause de 3s tombe
    # entre le 2e et le 3e mot d'un groupe de 4 -> AVANT le correctif, un seul
    # Dialogue de 4 mots aurait démarré au 1er mot et affiché les 4 mots
    # tout de suite, y compris les 2 d'après la pause.
    captions = Captions(enabled=True, words_per_line=4, style="plain")
    canvas = Canvas(w=1080, h=1920, fps=30)
    words = _words([(0.0, 0.4), (0.5, 0.9), (3.9, 4.3), (4.4, 4.8)])
    path = "/tmp/test_captions_pause.ass"
    build_ass(words, captions, canvas, path)
    content = Path(path).read_text(encoding="utf-8")
    dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogue_lines) == 2, "la pause de 3s doit couper le groupe en deux Dialogue"
    # Le second Dialogue ne doit pas démarrer avant son premier mot réel (3.9s).
    assert "0:00:03.90" in dialogue_lines[1]


def test_default_pause_threshold_is_tightened_to_0_3s():
    # Retour utilisateur : « le seuil est trop large » (0.6s). Resserré à 0.3s.
    assert captions_mod.MAX_CAPTION_PAUSE_GAP == 0.3


def test_default_threshold_now_splits_a_gap_that_used_to_pass():
    # Un trou de 0.45s passait avec l'ancien seuil (0.6s) mais doit maintenant
    # couper le groupe (nouveau seuil 0.3s).
    words = _words([(0.0, 0.4), (0.85, 1.25)])   # écart de 0.45s
    groups = group_words_by_pause(words)   # utilise le seuil par défaut du module
    assert len(groups) == 2


# --- Confiance AssemblyAI : ne pas sous-titrer les paroles de la chanson ---

def _words_with_confidence(triples):
    return [{"word": w, "start": s, "end": e, "confidence": c} for w, s, e, c in triples]


def test_filter_low_confidence_words_removes_below_threshold():
    words = _words_with_confidence([
        ("bonjour", 0.0, 0.5, 0.95),
        ("mumbled_lyric", 0.5, 1.0, 0.15),   # confiance basse -> probablement pas de la parole nette
        ("clair", 1.0, 1.5, 0.8),
    ])
    kept = filter_low_confidence_words(words, min_confidence=0.4)
    assert [w["word"] for w in kept] == ["bonjour", "clair"]


def test_filter_low_confidence_words_never_drops_words_without_confidence():
    # Rétro-compatibilité : anciens clips transcrits avant l'ajout du champ
    # `confidence` -> ne JAMAIS filtrer par excès de prudence sur une donnée absente.
    words = [{"word": "bonjour", "start": 0.0, "end": 0.5}]
    kept = filter_low_confidence_words(words, min_confidence=0.4)
    assert kept == words


def test_build_ass_excludes_low_confidence_words_from_output():
    captions = Captions(enabled=True, words_per_line=4, style="plain")
    canvas = Canvas(w=1080, h=1920, fps=30)
    words = _words_with_confidence([
        ("bonjour", 0.0, 0.5, 0.95),
        ("lyric", 0.5, 1.0, 0.1),
        ("tout", 1.0, 1.5, 0.9),
        ("va", 1.5, 2.0, 0.9),
        ("bien", 2.0, 2.5, 0.9),
    ])
    path = "/tmp/test_captions_confidence.ass"
    build_ass(words, captions, canvas, path)
    content = Path(path).read_text(encoding="utf-8")
    assert "lyric" not in content
    assert "bonjour" in content and "bien" in content


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
