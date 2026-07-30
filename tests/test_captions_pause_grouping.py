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
from sortclip.captions import group_words_by_pause, build_ass
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


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
