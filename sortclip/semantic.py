"""
Intégrité sémantique du clip (Partie G1).

Module PUR (aucune dépendance à modal/supabase/anthropic) : opère sur des
listes de mots {"word", "start", "end"} déjà transcrits, comme le reste du
package sortclip. Aucun appel LLM, aucun GPU — uniquement du texte déjà en
mémoire.

Contrôle : un clip qui commence au milieu d'une pensée (subordonnée
orpheline, pronom sans antécédent) ou qui se termine sur une conjonction en
suspens (« parce que », « donc », « mais »…) « sonne faux » même quand le
reste du montage est bon. Plutôt que de rejeter un tel segment, on l'ÉTEND
vers la frontière de phrase la plus proche (majuscule initiale / ponctuation
finale déjà présente dans les tokens transcrits).
"""

from __future__ import annotations

import re

_DANGLING_START_WORDS = {
    "qui", "que", "qu", "dont", "où", "et", "mais", "or", "car", "donc",
    "puis", "alors", "puisque", "comme", "parce", "lequel", "laquelle",
    "lesquels", "lesquelles", "celui", "celle", "ceux", "celles",
}
_DANGLING_END_WORDS = {
    "parce", "donc", "mais", "car", "alors", "puisque", "comme", "et", "or",
    "cependant", "néanmoins", "toutefois", "puis",
}


def core_word(word_text: str) -> str:
    """Mot sans ponctuation attachée, en minuscules (ex. "Anthropic," -> "anthropic")."""
    return re.sub(r"[^\wÀ-ÿ'-]", "", word_text or "").strip().lower()


def looks_like_sentence_start(word_text: str) -> bool:
    core = (word_text or "").strip()
    return bool(core) and core[0].isupper()


def looks_like_sentence_end(word_text: str) -> bool:
    return (word_text or "").rstrip().endswith((".", "!", "?", "…"))


def extend_to_sentence_boundaries(
    start_i: int, end_i: int, words: list[dict], max_shift: int = 15
) -> tuple[int, int]:
    """Étend (jamais ne rétrécit) une découpe [start_i, end_i] (indices dans
    `words`) vers la frontière de phrase la plus proche si le début ou la fin
    tombe sur un marqueur de phrase tronquée. Ne dépasse jamais `max_shift`
    mots dans chaque direction — au-delà, on n'insiste pas (mieux vaut un
    clip imparfait qu'un clip très allongé). Ne lève jamais."""
    n = len(words)
    if n == 0 or not (0 <= start_i < n) or not (0 <= end_i < n):
        return start_i, end_i
    s, e = start_i, end_i

    starts_badly = (
        core_word(words[s]["word"]) in _DANGLING_START_WORDS
        or not looks_like_sentence_start(words[s]["word"])
    )
    if starts_badly:
        for j in range(s, max(0, s - max_shift) - 1, -1):
            if j == 0 or looks_like_sentence_start(words[j]["word"]):
                s = j
                break

    ends_badly = (
        core_word(words[e]["word"]) in _DANGLING_END_WORDS
        or not looks_like_sentence_end(words[e]["word"])
    )
    if ends_badly:
        for j in range(e, min(n - 1, e + max_shift) + 1):
            if looks_like_sentence_end(words[j]["word"]) or j == n - 1:
                e = j
                break

    return s, e
