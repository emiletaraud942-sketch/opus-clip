"""Audit du repo (findings, tous vérifiés par lecture/exécution) : les
événements `emphasis` n'étaient JAMAIS rendus. `compile.py` ne les mentionne
nulle part, et `build_ass` ignorait totalement l'EDL — "mets en valeur X"
était acceptée, validée, stockée comme un succès, mais la vidéo rendue était
strictement identique à avant.

Preuve du problème (avant fix) : `grep -c "emphasis" sortclip/compile.py`
retournait 0, et `build_ass()` n'avait aucun paramètre pour recevoir les
événements d'emphase."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.captions import build_ass
from sortclip.edl import Captions, Canvas


def _words():
    return [
        {"word": "bonjour", "start": 0.0, "end": 0.5},
        {"word": "vraiment", "start": 0.5, "end": 1.0},
        {"word": "incroyable", "start": 1.0, "end": 1.6},
    ]


def test_emphasized_word_gets_ass_override(tmp_path):
    path = str(tmp_path / "out.ass")
    build_ass(_words(), Captions(style="plain"), Canvas(), path, emphasis_indices={1: "pop"})
    content = Path(path).read_text(encoding="utf-8")
    assert r"{\b1\fscx130\fscy130}vraiment{\r}" in content
    assert r"{\b1\fscx130\fscy130}bonjour" not in content


def test_no_emphasis_produces_plain_text_unchanged():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/out.ass"
        build_ass(_words(), Captions(style="plain"), Canvas(), path)
        content = Path(path).read_text(encoding="utf-8")
        assert "\\b1" not in content
        assert "bonjour vraiment incroyable" in content


def test_emphasis_survives_karaoke_style(tmp_path):
    path = str(tmp_path / "out.ass")
    build_ass(_words(), Captions(style="karaoke"), Canvas(), path, emphasis_indices={2: "underline"})
    content = Path(path).read_text(encoding="utf-8")
    assert r"{\u1}incroyable{\r}" in content


def test_emphasis_index_refers_to_original_words_out_before_confidence_filter():
    # Le mot d'index 1 a une confiance trop basse (filtré) -> il ne doit PAS
    # décaler l'emphase posée sur l'index 2 (qui référence le words_out
    # D'ORIGINE, avant filtrage, même convention que director.py).
    words = [
        {"word": "bonjour", "start": 0.0, "end": 0.5, "confidence": 0.9},
        {"word": "hum", "start": 0.5, "end": 0.6, "confidence": 0.1},
        {"word": "incroyable", "start": 0.6, "end": 1.2, "confidence": 0.9},
    ]
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/out.ass"
        build_ass(words, Captions(style="plain"), Canvas(), path, emphasis_indices={2: "pop"})
        content = Path(path).read_text(encoding="utf-8")
        assert r"{\b1\fscx130\fscy130}incroyable{\r}" in content


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
