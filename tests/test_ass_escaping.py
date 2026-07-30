"""Audit C4 (AUDIT.md #4) : un mot transcrit contenant `{`, `}` ou `\\` casse
le format ASS (libass interprète tout `{...}` comme un bloc de commande de
style). Vérifie que ces caractères sont neutralisés avant d'écrire un
Dialogue, dans les DEUX générateurs de sous-titres du projet."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.captions import build_ass, _sanitize_ass_text
from sortclip.edl import Captions, Canvas

# modal_app.py n'est pas importable ici (fastapi/modal non installés) : sa
# propre fonction write_subtitles() utilise la MÊME logique de neutralisation
# (_sanitize_ass_text, dupliquée localement, voir modal_app.py juste avant
# write_subtitles) — vérifiée par lecture, pas testable directement ici.


def test_sanitize_ass_text_neutralizes_braces_and_backslash():
    assert _sanitize_ass_text("avec{accolade") == "avec｛accolade"
    assert _sanitize_ass_text("avec}accolade") == "avec｝accolade"
    assert _sanitize_ass_text("anti\\slash") == "anti＼slash"
    assert _sanitize_ass_text("{\\k500}injection") == "｛＼k500｝injection"


def test_sanitize_ass_text_preserves_normal_text():
    assert _sanitize_ass_text("café") == "café"
    assert _sanitize_ass_text("emoji😀present") == "emoji😀present"
    assert _sanitize_ass_text("") == ""
    assert _sanitize_ass_text(None) == ""


def test_build_ass_neutralizes_dangerous_words_plain_style():
    words = [
        {"word": "normal", "start": 0.0, "end": 0.4},
        {"word": "avec{accolade", "start": 0.5, "end": 0.9},
        {"word": "{\\k500}injection", "start": 1.0, "end": 1.4},
    ]
    path = "/tmp/test_ass_escaping_plain.ass"
    build_ass(words, Captions(enabled=True, style="plain", words_per_line=4), Canvas(w=1080, h=1920, fps=30), path)
    content = Path(path).read_text(encoding="utf-8")
    assert "avec{accolade" not in content
    assert "{\\k500}injection" not in content
    assert "avec｛accolade" in content


def test_build_ass_neutralizes_dangerous_words_karaoke_style():
    words = [
        {"word": "avec}accolade", "start": 0.0, "end": 0.4},
        {"word": "anti\\slash", "start": 0.5, "end": 0.9},
    ]
    path = "/tmp/test_ass_escaping_karaoke.ass"
    build_ass(words, Captions(enabled=True, style="karaoke", words_per_line=4), Canvas(w=1080, h=1920, fps=30), path)
    content = Path(path).read_text(encoding="utf-8")
    assert "avec}accolade" not in content
    assert "anti\\slash" not in content
    # Les balises karaoké \k QU'ON CONSTRUIT NOUS-MÊMES doivent rester intactes.
    assert "{\\k" in content


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
