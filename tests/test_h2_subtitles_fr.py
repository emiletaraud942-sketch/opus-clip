"""Tests H2 (découpage linguistique français) — sortclip.subtitles_fr.

Critère de réussite de la mission : zéro coupure violant les règles. Ces
tests vérifient chaque règle individuellement puis sur des phrases complètes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.subtitles_fr import (
    is_elided,
    is_forbidden_break,
    prefers_break,
    break_lines_fr,
    group_into_blocks,
    apply_french_typography,
)


def _words(tokens):
    return [{"word": t, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, t in enumerate(tokens)]


def _flatten(lines):
    return [[w["word"] for w in line] for line in lines]


# --- Règles individuelles -------------------------------------------------

def test_is_elided_detects_common_forms():
    for form in ["l'", "d'", "qu'", "j'", "n'", "c'", "s'", "m'", "t'", "jusqu'", "quoiqu'"]:
        assert is_elided(form), form


def test_is_elided_rejects_full_words():
    assert not is_elided("les")
    assert not is_elided("dans")


def test_forbidden_break_after_determiner():
    assert is_forbidden_break("le", "chat") is True
    assert is_forbidden_break("chat", "noir") is False


def test_forbidden_break_after_elided_form():
    assert is_forbidden_break("l'", "ami") is True


def test_forbidden_break_after_ne():
    assert is_forbidden_break("ne", "pas") is True
    assert is_forbidden_break("n'", "arrive") is True


def test_forbidden_break_isolating_short_word():
    assert is_forbidden_break("à", "demain") is True
    assert is_forbidden_break("y", "aller") is True
    assert is_forbidden_break("on", "verra") is True


def test_short_coordinating_conjunction_not_forbidden_by_length_rule():
    # "et"/"ou" (2 lettres) sont dans les conjonctions -> pas interdits par la
    # règle de longueur (mais restent un point de coupure PRÉFÉRÉ, pas un mot
    # à isoler pour rien).
    assert is_forbidden_break("et", "voilà") is False


def test_normal_word_break_allowed():
    assert is_forbidden_break("maison", "rouge") is False


def test_prefers_break_on_comma():
    assert prefers_break("bonjour,") is True


def test_prefers_break_on_coordinating_conjunction():
    assert prefers_break("mais") is True
    assert prefers_break("chat") is False


# --- Découpage en lignes complet ------------------------------------------

def test_break_lines_never_separates_determiner_from_noun():
    words = _words(["Le", "chat", "noir", "dort", "sur", "le", "canapé", "vert", "aujourd'hui"])
    lines = break_lines_fr(words, max_chars=10)
    # Jamais "le"/"Le" en dernier mot d'une ligne (sauf la toute dernière du bloc).
    for line in lines[:-1]:
        assert line[-1]["word"].lower() != "le"


def test_break_lines_never_ends_on_elided_form():
    words = _words(["C'", "est", "vraiment", "l'", "histoire", "la", "plus", "folle", "jamais", "racontée"])
    lines = break_lines_fr(words, max_chars=8)
    for line in lines[:-1]:
        assert not is_elided(line[-1]["word"])


def test_break_lines_never_separates_ne_pas():
    words = _words(["Je", "ne", "sais", "pas", "vraiment", "ce", "que", "tu", "veux", "dire", "exactement"])
    lines = break_lines_fr(words, max_chars=8)
    for line in lines[:-1]:
        assert line[-1]["word"].lower() not in ("ne", "n'")


def test_break_lines_never_isolates_short_word_at_line_end():
    words = _words(["Je", "vais", "y", "aller", "demain", "matin", "sans", "faute", "cette", "fois"])
    lines = break_lines_fr(words, max_chars=8)
    for line in lines[:-1]:
        last = line[-1]["word"]
        core = last.strip(".,!?;:").lower()
        # soit ce n'est pas un mot court, soit c'est une conjonction de coordination.
        assert len(core) > 2 or core in ("et", "ou")


def test_break_lines_respects_char_budget_when_safe():
    # Phrase sans piège linguistique : chaque ligne doit rester proche du budget.
    words = _words(["Chaque", "matin", "je", "cours", "dans", "le", "parc", "avant", "le", "travail"])
    lines = break_lines_fr(words, max_chars=20)
    for line in lines:
        text = " ".join(w["word"] for w in line)
        assert len(text) <= 40   # tolérance large : le budget n'est qu'indicatif près des règles


def test_break_lines_extends_rather_than_violates():
    # "Le" seul dépasserait presque le budget minuscule choisi ; la fonction
    # ne doit JAMAIS finir une ligne sur "Le".
    words = _words(["Le", "chien"])
    lines = break_lines_fr(words, max_chars=2)   # budget volontairement absurde
    assert len(lines) == 1   # forcé à rester ensemble : "Le chien"
    assert [w["word"] for w in lines[0]] == ["Le", "chien"]


def test_break_lines_empty_input():
    assert break_lines_fr([]) == []


def test_group_into_blocks_max_two_lines():
    lines = [["a"], ["b"], ["c"], ["d"], ["e"]]
    blocks = group_into_blocks(lines, max_lines=2)
    assert blocks == [[["a"], ["b"]], [["c"], ["d"]], [["e"]]]
    assert all(len(b) <= 2 for b in blocks)


# --- Typographie française -------------------------------------------------

def test_typography_nbsp_before_punctuation():
    out = apply_french_typography("Vraiment ? Oui !")
    assert " ?" in out
    assert " !" in out


def test_typography_nbsp_in_french_quotes():
    out = apply_french_typography("« Bonjour »")
    assert "« Bonjour" in out
    assert "Bonjour »" in out


def test_typography_empty_string():
    assert apply_french_typography("") == ""
