"""
Construction d'un EDL SANS IA : nettoyage du transcript + application d'un
preset. C'est l'étape 1-2 du CLAUDE_EDL.md — suffisante pour un produit
vendable, avant tout appel LLM.

Le LLM (director.py, plus tard) viendra ENRICHIR cet EDL avec des événements
(cadrages, emphases) ; il ne le reconstruit jamais depuis zéro.
"""

from __future__ import annotations

from .edl import EDL, Interval, Background, Captions, Watermark, Source
from .presets import get_preset

# Tics de langage retirés au nettoyage (comparés en minuscules, sans ponctuation).
FILLERS = {"euh", "heu", "heum", "hum", "hmm", "bah", "ben", "bin"}


def _norm(word: str) -> str:
    return word.strip().strip(".,!?…:;»«\"'").lower()


def clean_words(words: list[dict], remove_fillers: bool = True) -> list[dict]:
    """Retire les tics de langage. Retirer un tic AGRANDIT le silence entre ses
    voisins : le mot est réellement excisé, pas seulement masqué (cf. invariant
    du montage). La reconstruction des `keeps` s'appuie sur ce comportement."""
    if not remove_fillers:
        return list(words)
    return [w for w in words if _norm(w.get("word", "")) not in FILLERS]


def _keeps_from_words(words: list[dict], max_gap: float, buffer: float = 0.08,
                      source_duration: float | None = None) -> list[Interval]:
    """Reconstruit les intervalles à GARDER (temps source) : on regroupe les
    mots consécutifs, et dès qu'un silence dépasse `max_gap`, on ferme
    l'intervalle courant et on en ouvre un nouveau."""
    if not words:
        return []
    keeps: list[tuple[float, float]] = []
    cur_start = float(words[0]["start"])
    prev_end = float(words[0]["end"])
    for w in words[1:]:
        s, e = float(w["start"]), float(w["end"])
        if s - prev_end > max_gap:
            keeps.append((cur_start, prev_end))
            cur_start = s
        prev_end = max(prev_end, e)
    keeps.append((cur_start, prev_end))

    out: list[Interval] = []
    for s, e in keeps:
        s2 = max(0.0, s - buffer)
        e2 = e + buffer
        if source_duration:
            e2 = min(e2, source_duration)
        if e2 - s2 > 0.05:
            out.append(Interval(start=s2, end=e2))
    return out


def build_edl(
    source_path: str,
    source_duration: float,
    words: list[dict],
    preset_name: str | None = None,
    *,
    watermark: bool = False,
    source_width: int | None = None,
    source_height: int | None = None,
) -> tuple[EDL, list[dict]]:
    """Construit un EDL prêt à compiler à partir d'un transcript et d'un preset.

    Retourne (EDL, transcript_nettoyé). Le transcript nettoyé est en TEMPS
    SOURCE ; c'est captions.map_words_to_output() qui le remappe en temps de
    sortie pour les sous-titres."""
    preset = get_preset(preset_name)
    cleaning = preset.get("cleaning", {})
    max_gap = float(cleaning.get("max_gap", 0.4))
    remove_fillers = bool(cleaning.get("remove_fillers", True))

    cleaned = clean_words(words, remove_fillers=remove_fillers)
    keeps = _keeps_from_words(cleaned, max_gap=max_gap, source_duration=source_duration)
    if not keeps:
        # Garde-fou : au moins un intervalle, sinon l'EDL est invalide.
        keeps = [Interval(start=0.0, end=min(source_duration, 15.0))]

    edl = EDL(
        preset=preset.get("label"),
        source=Source(path=source_path, duration=source_duration,
                      width=source_width, height=source_height),
        keeps=keeps,
        background=Background(**preset.get("background", {})),
        captions=Captions(**preset.get("captions", {})),
        watermark=Watermark(enabled=watermark),
    )
    return edl, cleaned
