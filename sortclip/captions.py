"""
Génération des sous-titres ASS.

Pourquoi ASS et pas SRT : le SRT ne sait pas colorer un mot dans une ligne. Le
karaoké mot-à-mot (style "rythmé"/viral) a besoin des balises \\k d'ASS.

Rappel des deux timelines : le transcript nettoyé est en TEMPS SOURCE. Les
sous-titres, eux, doivent être en TEMPS SORTIE (après suppression des silences).
map_words_to_output() fait la conversion via les `keeps` de l'EDL.

Couleurs ASS : format &H00BBGGRR (Alpha=00, puis Bleu, Vert, Rouge — inversé).
"""

from __future__ import annotations

# H2 — DÉSACTIVÉ pour ce merge : change le rendu visuel des sous-titres en
# style "plain" pour tous les clips existants ; à valider visuellement en
# prod avant activation (repasser à True). Le code reste en place et testé.
ENABLE_FR_LINE_BREAKING = False

# Bug signalé par l'utilisateur : le regroupement par nombre de mots fixe
# (words_per_line) ignore le SILENCE entre deux mots. Si une pause tombe au
# milieu d'un groupe, tout le texte du groupe (y compris les mots d'APRÈS la
# pause, pas encore prononcés) s'affiche dès le premier mot du groupe — on
# voit des mots à l'écran pendant que personne ne parle. Un groupe ne doit
# donc jamais traverser une pause plus longue que ce seuil.
# {{À_COMPLÉTER : validé visuellement, pas mesuré}}. Resserré de 0.6s à 0.3s
# suite au retour utilisateur (« le seuil est trop large ») — 0.3s correspond
# à une pause perceptible entre deux groupes de mots sans couper une simple
# respiration à l'intérieur d'une phrase continue.
MAX_CAPTION_PAUSE_GAP = 0.3

# Filtre les mots transcrits avec une confiance trop faible (AssemblyAI donne
# un score 0-1 par mot) AVANT de les sous-titrer. Objectif utilisateur :
# « sous-titrer les paroles de la personne, pas les paroles de la chanson » —
# des paroles de musique qui fuitent dans la transcription (chant en fond)
# sont typiquement mal reconnues (score bas) par rapport à de la parole nette
# au micro. Ce n'est PAS une détection musique/parole (aucun modèle audio) :
# c'est un usage du signal de confiance qu'AssemblyAI fournit déjà, pour de
# vraies raisons statistiques (audio dégradé/chanté = confiance plus basse).
# {{À_COMPLÉTER : seuil pas validé sur de vrais clips avec musique — 0.4 est
# un point de départ prudent (n'exclut que les mots vraiment incertains).}}
MIN_CAPTION_CONFIDENCE = 0.4


def filter_low_confidence_words(words_out: list[dict],
                                min_confidence: float = MIN_CAPTION_CONFIDENCE) -> list[dict]:
    """Retire les mots dont le score de confiance (AssemblyAI, `confidence`
    0-1) est en dessous du seuil — jamais pour les mots SANS confiance connue
    (clips déjà transcrits avant l'ajout de ce champ, ou toute source qui ne
    le fournit pas) : on ne filtre que ce qu'on peut réellement juger,
    jamais par excès de prudence sur une donnée absente."""
    out = []
    for w in words_out:
        c = w.get("confidence")
        if c is not None and float(c) < min_confidence:
            continue
        out.append(w)
    return out


def group_words_by_pause(words_out: list[dict], max_words: int | None = None,
                         max_gap: float = MAX_CAPTION_PAUSE_GAP) -> list[list[dict]]:
    """Regroupe des mots (temps de SORTIE) en respectant DEUX limites : un
    nombre de mots maximum (`max_words`, None = illimité) ET une pause
    maximum tolérée ENTRE deux mots consécutifs (`max_gap`). Dès qu'une pause
    dépasse `max_gap`, le groupe est coupé — jamais de mots affichés avant
    d'être prononcés à cause d'une pause interne au groupe. Pure, testable."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    prev_end: float | None = None
    for w in words_out:
        gap_too_long = prev_end is not None and float(w["start"]) - prev_end > max_gap
        count_full = max_words is not None and len(current) >= max_words
        if current and (gap_too_long or count_full):
            groups.append(current)
            current = []
        current.append(w)
        prev_end = float(w["end"])
    if current:
        groups.append(current)
    return groups


def _hex_to_ass(color: str) -> str:
    """#RRGGBB -> &H00BBGGRR (composantes inversées, alpha opaque)."""
    h = color.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _fmt_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def map_words_to_output(words: list[dict], edl_or_keeps) -> list[dict]:
    """Remappe des mots en TEMPS SOURCE vers le TEMPS SORTIE.

    Un mot tombant entièrement dans un silence supprimé est ignoré. Un mot à
    cheval est ramené à l'intérieur de l'intervalle gardé qui le contient.
    Retourne [{word, start, end}] en secondes de la timeline de SORTIE.
    """
    keeps = getattr(edl_or_keeps, "keeps", edl_or_keeps)
    # Durée cumulée de sortie avant chaque intervalle gardé.
    acc = []
    total = 0.0
    for k in keeps:
        acc.append(total)
        total += (k.end - k.start)

    out: list[dict] = []
    for w in words:
        s = float(w["start"])
        e = float(w["end"])
        for i, k in enumerate(keeps):
            if s < k.end and e > k.start:  # le mot chevauche cet intervalle
                cs = max(s, k.start)
                ce = min(e, k.end)
                out_start = acc[i] + (cs - k.start)
                out_end = acc[i] + (ce - k.start)
                if out_end > out_start:
                    mapped = {"word": w.get("word", ""), "start": out_start, "end": out_end}
                    if "confidence" in w:
                        mapped["confidence"] = w["confidence"]
                    out.append(mapped)
                break
    return out


def _ass_header(captions, canvas) -> str:
    primary = _hex_to_ass(captions.primary)
    # En karaoké, la couleur "secondaire" est la base, la "primaire" la couleur
    # de surbrillance : \k fait passer de secondaire à primaire au fil des mots.
    if captions.style == "karaoke":
        primary = _hex_to_ass(captions.highlight)
        secondary = _hex_to_ass(captions.primary)
    else:
        secondary = primary
    bold = -1 if captions.bold else 0
    margin_v = max(10, int((1.0 - captions.y) * canvas.h))
    outline = 4 if captions.style == "karaoke" else 3
    shadow = 1
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {canvas.w}\n"
        f"PlayResY: {canvas.h}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{captions.font},{captions.size},{primary},{secondary},"
        f"&H00000000,&H80000000,{bold},0,0,0,100,100,0,0,1,{outline},{shadow},2,"
        f"40,40,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _dialogue(start: float, end: float, text: str) -> str:
    return f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},Default,,0,0,0,,{text}"


def build_ass(words_out: list[dict], captions, canvas, path: str) -> str:
    """Écrit un fichier .ass depuis des mots en TEMPS SORTIE. Retourne le chemin.

    - style "karaoke": mot-à-mot, surbrillance progressive via \\k — groupés
      par `words_per_line`, INCHANGÉ (le découpage linguistique H2 ne
      s'applique qu'au style "plain", où des lignes de plusieurs mots sont
      réellement composées ; en karaoké chaque mot s'affiche isolément, le
      problème de coupure de ligne ne se pose pas de la même façon).
    - style "plain"  : H2 — découpage linguistique français (jamais entre
      déterminant et nom, jamais après une forme élidée, "ne...pas" toujours
      ensemble, jamais un mot d'1-2 lettres isolé en fin de ligne), deux
      lignes par bloc maximum, typographie française (espaces insécables).
      DÉSACTIVÉ pour l'instant (ENABLE_FR_LINE_BREAKING = False) : repli sur
      l'ancien découpage fixe par `words_per_line`, identique à avant ce chantier.
    """
    from pathlib import Path

    words_out = filter_low_confidence_words(words_out)
    lines = [_ass_header(captions, canvas)]

    if captions.style == "karaoke" or not ENABLE_FR_LINE_BREAKING:
        wpl = captions.words_per_line
        # Coupe aussi le groupe sur une pause trop longue (voir
        # group_words_by_pause) : sans ça, un groupe de `wpl` mots qui
        # chevauche un silence affiche les mots d'après-pause à l'écran avant
        # qu'ils soient prononcés.
        groups = group_words_by_pause(words_out, max_words=wpl)
        for g in groups:
            if not g:
                continue
            start = g[0]["start"]
            end = g[-1]["end"]
            if captions.style == "karaoke":
                chunks = []
                for w in g:
                    cs = max(1, int(round((w["end"] - w["start"]) * 100)))
                    chunks.append(f"{{\\k{cs}}}{w['word']}")
                lines.append(_dialogue(start, end, " ".join(chunks)))
            else:
                lines.append(_dialogue(start, end, " ".join(w["word"] for w in g)))
    else:
        from .subtitles_fr import break_lines_fr, group_into_blocks, apply_french_typography
        # Découpe d'abord sur les pauses (mêmes règles que ci-dessus), PUIS
        # applique le découpage linguistique FR à l'intérieur de chaque
        # segment sans pause — un bloc ne doit jamais traverser un silence.
        blocks: list[list[list[dict]]] = []
        for segment in group_words_by_pause(words_out, max_words=None):
            fr_lines = break_lines_fr(segment, max_chars=22)
            blocks.extend(group_into_blocks(fr_lines, max_lines=2))
        for block in blocks:
            if not block:
                continue
            block_words = [w for line in block for w in line]
            start = block_words[0]["start"]
            end = block_words[-1]["end"]
            text = "\\N".join(
                apply_french_typography(" ".join(w["word"] for w in line))
                for line in block
            )
            lines.append(_dialogue(start, end, text))

    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path
