"""Tests G1 (intégrité sémantique du clip) — sortclip.semantic.

Ne dépend d'aucune API ni de modal_app.py (module pur). Vérifie que
extend_to_sentence_boundaries ÉTEND correctement vers la frontière de phrase
la plus proche, sans jamais rétrécir ni planter.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.semantic import (
    core_word,
    looks_like_sentence_start,
    looks_like_sentence_end,
    extend_to_sentence_boundaries,
)


def _words(tokens):
    """Construit une liste de mots factices avec des horodatages croissants
    à partir d'une liste de tokens texte (avec ponctuation éventuelle)."""
    out = []
    t = 0.0
    for tok in tokens:
        out.append({"word": tok, "start": t, "end": t + 0.4})
        t += 0.5
    return out


def test_core_word_strips_punctuation():
    assert core_word("Anthropic,") == "anthropic"
    assert core_word("« Bonjour") == "bonjour"
    assert core_word("qu'est-ce") == "qu'est-ce"


def test_sentence_start_detection():
    assert looks_like_sentence_start("Bonjour") is True
    assert looks_like_sentence_start("bonjour") is False
    assert looks_like_sentence_start("") is False


def test_sentence_end_detection():
    assert looks_like_sentence_end("fini.") is True
    assert looks_like_sentence_end("vraiment ?") is True
    assert looks_like_sentence_end("mais") is False


def test_extends_start_away_from_dangling_conjunction():
    # "Bonjour tous. Donc je disais que c'était bien." -- si le clip commence
    # sur "que" (subordonnée orpheline), il doit reculer jusqu'à "Donc".
    words = _words(["Bonjour", "tous.", "Donc", "je", "disais", "que", "c'était", "bien."])
    s, e = extend_to_sentence_boundaries(5, 7, words)   # 5 = index du mot "que"
    assert s == 2   # recule jusqu'à "Donc" (frontière de phrase la plus proche)
    assert words[s]["word"] == "Donc"


def test_extends_end_away_from_dangling_conjunction():
    # Le clip finit sur "parce" (conjonction en suspens) -> doit avancer
    # jusqu'à la ponctuation finale suivante.
    words = _words(["Bonjour.", "C'était", "top", "parce", "que", "c'était", "drôle."])
    s, e = extend_to_sentence_boundaries(1, 3, words)   # finit sur "parce" (index 3)
    assert e == 6
    assert words[e]["word"] == "drôle."


def test_does_not_shrink_a_good_boundary():
    # Début déjà sur majuscule, fin déjà sur ponctuation finale -> inchangé.
    words = _words(["Bonjour.", "Ça", "va", "bien."])
    s, e = extend_to_sentence_boundaries(0, 3, words)
    assert (s, e) == (0, 3)


def test_never_raises_on_out_of_range_indices():
    words = _words(["Un", "seul", "mot."])
    # Indices hors bornes : la fonction doit renvoyer tel quel, jamais lever.
    assert extend_to_sentence_boundaries(-1, 10, words) == (-1, 10)
    assert extend_to_sentence_boundaries(0, 0, words)[0] in (0,)


def test_never_raises_on_empty_words():
    assert extend_to_sentence_boundaries(0, 0, []) == (0, 0)


def test_max_shift_bounds_the_search():
    # Une longue liste de mots minuscules sans ponctuation : la recherche ne
    # doit pas remonter indéfiniment, elle s'arrête à max_shift.
    words = _words(["mot"] * 30)
    s, e = extend_to_sentence_boundaries(20, 25, words, max_shift=5)
    assert s >= 15   # n'est jamais allé plus loin que max_shift en arrière
