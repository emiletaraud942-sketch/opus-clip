"""C3/C4 (chantier « correction-sous-titres ») — liste noire d'artefacts et
détection de boucles, indépendantes du moteur ASR."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.hallucinations import (
    filter_hallucination_phrases,
    filter_anomalous_word_duration,
    filter_consecutive_repeats,
    clean_hallucinations,
    safe_clean_hallucinations,
)


def _words(tokens):
    return [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(tokens)]


def test_filter_hallucination_phrases_removes_known_artifact():
    words = _words(["bonjour", "à", "tous", "abonnez-vous", "à", "la", "chaîne", "merci"])
    filtered, removed = filter_hallucination_phrases(words, phrases=["abonnez-vous à la chaîne"])
    assert [w["word"] for w in filtered] == ["bonjour", "à", "tous", "merci"]
    assert removed == 4


def test_filter_hallucination_phrases_case_and_punctuation_insensitive():
    words = _words(["Merci", "d'avoir", "regardé", "cette", "vidéo", "!"])
    filtered, removed = filter_hallucination_phrases(
        words, phrases=["merci d'avoir regardé cette vidéo"])
    assert removed == 5
    assert [w["word"] for w in filtered] == ["!"]


def test_filter_hallucination_phrases_no_false_positive_on_unrelated_text():
    words = _words(["merci", "beaucoup", "pour", "votre", "attention"])
    filtered, removed = filter_hallucination_phrases(words)
    assert removed == 0
    assert len(filtered) == 5


def test_filter_anomalous_word_duration_removes_long_stall():
    words = _words(["ok"])
    words[0]["end"] = words[0]["start"] + 8.0   # un seul "mot" qui dure 8s : suspect
    filtered, removed = filter_anomalous_word_duration(words, max_seconds=4.0)
    assert removed == 1
    assert filtered == []


def test_filter_anomalous_word_duration_keeps_normal_words():
    words = _words(["bonjour", "tout", "le", "monde"])
    filtered, removed = filter_anomalous_word_duration(words, max_seconds=4.0)
    assert removed == 0
    assert len(filtered) == 4


def test_filter_consecutive_repeats_truncates_loop():
    words = _words(["non"] * 6)   # boucle : "non non non non non non"
    filtered, removed = filter_consecutive_repeats(words, max_repeats=3)
    assert len(filtered) == 3
    assert removed == 3


def test_filter_consecutive_repeats_allows_intentional_short_repeat():
    words = _words(["non", "non", "vraiment", "pas"])
    filtered, removed = filter_consecutive_repeats(words, max_repeats=3)
    assert removed == 0
    assert len(filtered) == 4


def test_filter_consecutive_repeats_resets_between_different_words():
    words = _words(["oui", "oui", "non", "non", "non", "non"])
    filtered, removed = filter_consecutive_repeats(words, max_repeats=3)
    # "oui oui" (2, sous le seuil) reste ; "non"x4 est tronqué à 3.
    assert [w["word"] for w in filtered] == ["oui", "oui", "non", "non", "non"]
    assert removed == 1


def test_clean_hallucinations_combines_all_filters_and_reports_counts():
    words = _words(["bonjour"] + ["euh"] * 5 + ["merci", "beaucoup"])
    filtered, counts = clean_hallucinations(words, phrases=[], max_repeats=2)
    assert counts["removed_loop_repeats"] == 3   # "euh" x5 -> tronqué à 2
    assert counts["removed_total"] == 3
    assert [w["word"] for w in filtered] == ["bonjour", "euh", "euh", "merci", "beaucoup"]


def test_clean_hallucinations_never_raises_on_empty_input():
    filtered, counts = clean_hallucinations([])
    assert filtered == []
    assert counts["removed_total"] == 0


# --- Garde-fou (bug réel : filtre trop agressif -> vidéo vidée) ------------

def test_safe_clean_hallucinations_reverts_when_too_aggressive():
    # "boucle" x20 tronquée à 3 -> retire 17 mots sur 21, largement > 50% :
    # DOIT annuler et renvoyer les mots d'origine intacts.
    words = _words(["boucle"] * 20 + ["bonjour", "vrai"])
    filtered, counts = safe_clean_hallucinations(words, phrases=[], max_repeats=3)
    assert counts["reverted"] is True
    assert filtered == words   # transcription d'origine, intacte


def test_safe_clean_hallucinations_keeps_cleanup_when_reasonable():
    words = _words(["bonjour"] + ["euh"] * 5 + ["merci", "beaucoup", "tout", "le", "monde"])
    filtered, counts = safe_clean_hallucinations(words, phrases=[], max_repeats=2)
    assert counts["reverted"] is False
    assert len(filtered) < len(words)   # le nettoyage a bien été appliqué


def test_safe_clean_hallucinations_never_raises_on_empty_input():
    filtered, counts = safe_clean_hallucinations([])
    assert filtered == []
    assert counts["reverted"] is False


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
