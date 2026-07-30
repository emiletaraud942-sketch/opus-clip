"""
C3/C4 (chantier « correction-sous-titres ») — filtre les artefacts
d'hallucination et les boucles dans un transcript mot-à-mot, APRÈS
transcription, quel que soit le moteur ASR utilisé (backend-agnostique,
pure logique sur `words` : {word, start, end, ...}).

Honnêteté sur C3 : la méthodologie demandée (« fais tourner le moteur sur 30
minutes de silence et de musique pure extraites de tes SOURCES ») suppose un
accès à de vraies vidéos de production pour observer ce qu'AssemblyAI produit
réellement sur du silence/de la musique — je n'ai ni ce corpus ni d'appel API
réel dans cet environnement (même limite que le jeu de 40 clips annotés).
KNOWN_HALLUCINATION_PHRASES ci-dessous est donc un point de départ DOCUMENTÉ
(artefacts connus et publiés dans les corpus de sous-titres français, pas
vérifiés spécifiquement contre AssemblyAI) — PAS une liste construite depuis
les données réelles de SortClip comme demandé. À reconstituer en production
dès qu'un vrai historique de clips est disponible (voir _log_removal ci-dessous,
pensé pour ça).
"""

from __future__ import annotations

import re

# Point de départ documenté, PAS vérifié contre AssemblyAI (voir docstring du
# module). Artefacts connus des corpus de sous-titres français amateurs —
# formules de fin de vidéo et mentions de communautés de sous-titrage.
# {{À_COMPLÉTER : à reconstruire depuis les vraies sorties AssemblyAI de
# SortClip sur du silence/musique réels, dès que ce corpus existe.}}
KNOWN_HALLUCINATION_PHRASES = [
    "sous-titres réalisés par la communauté d'amara.org",
    "sous-titres par la communauté d'amara.org",
    "abonnez-vous à la chaîne",
    "n'oubliez pas de vous abonner",
    "merci d'avoir regardé cette vidéo",
    "merci d'avoir regardé",
    "à bientôt pour une nouvelle vidéo",
]

# Un mot isolé qui dure plus longtemps que ça est suspect (voix qui traîne
# sur un artefact plutôt qu'un vrai mot prononcé) — C4, "durée incohérente
# avec le nombre de mots". {{À_COMPLÉTER : seuil pas validé sur de vrais cas.}}
MAX_PLAUSIBLE_WORD_SECONDS = 4.0

# Un même mot (normalisé) répété plus de ce nombre de fois D'AFFILÉE est une
# boucle probable, pas une vraie répétition volontaire (rare en français
# parlé au-delà de 2-3 fois consécutives). {{À_COMPLÉTER : pas validé.}}
MAX_CONSECUTIVE_REPEATS = 3


def _normalize(text: str) -> str:
    return re.sub(r"[^\wÀ-ÿ\s]", "", text or "").strip().lower()


def filter_hallucination_phrases(words: list[dict],
                                 phrases: list[str] | None = None) -> tuple[list[dict], int]:
    """Retire les mots qui composent une des phrases connues de la liste
    noire (comparaison sur une fenêtre glissante de mots normalisés, pour
    tolérer une ponctuation/majuscule différente). Retourne (mots filtrés,
    nombre de mots retirés) — le compte sert à journaliser chaque
    suppression (C3 : "en journalisant chaque suppression")."""
    phrases = phrases if phrases is not None else KNOWN_HALLUCINATION_PHRASES
    if not words or not phrases:
        return list(words), 0

    normalized_phrases = [(_normalize(p), len(p.split())) for p in phrases]
    to_remove = [False] * len(words)
    norm_words = [_normalize(w.get("word", "")) for w in words]

    for phrase_norm, n_tokens in normalized_phrases:
        if n_tokens <= 0:
            continue
        for i in range(len(words) - n_tokens + 1):
            if to_remove[i]:
                continue
            window = " ".join(norm_words[i:i + n_tokens])
            if window == phrase_norm:
                for j in range(i, i + n_tokens):
                    to_remove[j] = True

    filtered = [w for w, rm in zip(words, to_remove) if not rm]
    removed = sum(to_remove)
    return filtered, removed


def filter_anomalous_word_duration(words: list[dict],
                                   max_seconds: float = MAX_PLAUSIBLE_WORD_SECONDS) -> tuple[list[dict], int]:
    """C4 : retire les mots dont la durée individuelle dépasse un seuil
    invraisemblable pour un mot réellement prononcé — signe fréquent d'une
    hallucination qui "traîne" sur un artefact plutôt qu'un vrai mot."""
    filtered, removed = [], 0
    for w in words:
        dur = float(w.get("end", 0)) - float(w.get("start", 0))
        if dur > max_seconds:
            removed += 1
            continue
        filtered.append(w)
    return filtered, removed


def filter_consecutive_repeats(words: list[dict],
                               max_repeats: int = MAX_CONSECUTIVE_REPEATS) -> tuple[list[dict], int]:
    """C4 : « boucles » — un même mot (normalisé) répété plus de `max_repeats`
    fois D'AFFILÉE est tronqué à `max_repeats` occurrences (garde les
    premières, retire l'excédent) plutôt que retiré en bloc — au cas où la
    répétition serait volontaire (« non, non, non »), on garde un nombre
    raisonnable d'occurrences plutôt que zéro."""
    if not words:
        return [], 0
    filtered: list[dict] = []
    removed = 0
    run_word = None
    run_count = 0
    for w in words:
        norm = _normalize(w.get("word", ""))
        if norm and norm == run_word:
            run_count += 1
        else:
            run_word = norm
            run_count = 1
        if run_count <= max_repeats:
            filtered.append(w)
        else:
            removed += 1
    return filtered, removed


def clean_hallucinations(words: list[dict], *,
                         phrases: list[str] | None = None,
                         max_word_seconds: float = MAX_PLAUSIBLE_WORD_SECONDS,
                         max_repeats: int = MAX_CONSECUTIVE_REPEATS) -> tuple[list[dict], dict]:
    """Applique les trois filtres C3/C4 dans l'ordre (phrases connues ->
    durée invraisemblable -> boucles), et renvoie un décompte détaillé pour
    journalisation (C3 : "journalisant chaque suppression"). Ne lève jamais."""
    words, n_phrases = filter_hallucination_phrases(words, phrases=phrases)
    words, n_duration = filter_anomalous_word_duration(words, max_seconds=max_word_seconds)
    words, n_repeats = filter_consecutive_repeats(words, max_repeats=max_repeats)
    return words, {
        "removed_phrases": n_phrases,
        "removed_duration_anomaly": n_duration,
        "removed_loop_repeats": n_repeats,
        "removed_total": n_phrases + n_duration + n_repeats,
    }
