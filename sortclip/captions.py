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
                    out.append({"word": w.get("word", ""), "start": out_start, "end": out_end})
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

    - style "plain"  : couleur uniforme, mots groupés par `words_per_line`.
    - style "karaoke": mot-à-mot, surbrillance progressive via \\k.
    """
    from pathlib import Path

    lines = [_ass_header(captions, canvas)]
    wpl = captions.words_per_line
    groups = [words_out[i:i + wpl] for i in range(0, len(words_out), wpl)]

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
            text = " ".join(chunks)
        else:
            text = " ".join(w["word"] for w in g)
        lines.append(_dialogue(start, end, text))

    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path
