"""
Compilateur EDL -> FFmpeg.

C'est le SEUL module du projet autorisé à connaître FFmpeg. Ni le LLM, ni le
validateur, ni le store n'ont le droit de produire une chaîne de commande.

Stratégie de recadrage : on découpe la timeline de sortie en segments à
cadrage constant et on applique un crop FIXE à chacun, puis on concatène.
On évite ainsi les expressions FFmpeg variables dans le temps, fragiles et
illisibles. Le graphe est plus gros, mais il est généré par du code.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

from .edl import EDL, FRAMING_ZOOM

# H1 — ACTIVÉ pour test manuel en prod sur Modal (ffmpeg réel). Seuils non
# encore validés à l'oreille sur de vraies voix : à écouter et ajuster
# (LOUDNORM_TARGET_I, _DEESS_FREQ_HZ, _DEESS_GAIN_DB ci-dessous) si besoin.
# Repasser à False si le résultat ne convient pas — aucun risque, c'est
# réversible en un mot (voir rapport du chantier refonte-ia).
ENABLE_AUDIO_CHAIN_H1 = True

# H1 — chaîne audio. Coût nul (aucun GPU, du FFmpeg pur), effet perçu fort : le
# format court se consomme au volume maximum, dans le bruit. Cible EBU R128.
# {{À_COMPLÉTER: valider à l'oreille sur TikTok/Reels/Shorts — le format court
# est souvent masterisé plus fort que -14 LUFS ; départ raisonnable en attendant.}}
LOUDNORM_TARGET_I = -14.0     # LUFS intégré
LOUDNORM_TARGET_TP = -1.0     # crête vraie, dBTP
LOUDNORM_TARGET_LRA = 11.0    # plage dynamique cible

# Approximation d'atténuation des sifflantes par une encoche statique (pas un
# vrai de-esser dynamique — ffmpeg n'en fournit pas nativement).
# {{À_COMPLÉTER : valider fréquence/gain à l'oreille sur des voix réelles ;
# 7500 Hz / -3 dB est un point de départ conservateur, pas mesuré.}}
_DEESS_FREQ_HZ = 7500
_DEESS_GAIN_DB = -3

# H1 (suite) — musique de fond vs parole. On n'a qu'UNE seule piste audio déjà
# mixée : on ne peut pas séparer musique et voix quand elles jouent EN MÊME
# TEMPS sans un modèle de séparation de sources (type Demucs — nécessite
# torch, pas installé dans cet environnement, coût GPU par clip à évaluer).
# {{À_COMPLÉTER : si une vraie séparation de sources est voulue, c'est un
# chantier à part avec son propre coût — non fait ici, documenté comme tel.}}
#
# Ce qu'on SAIT en revanche, avec certitude, sans aucun modèle : QUAND la
# parole a lieu (les timestamps mot-à-mot d'AssemblyAI, déjà dans le
# pipeline). Deux corrections déterministes en découlent :
#   1. mesurer le volume UNIQUEMENT sur les passages parlés (pas sur les
#      silences ni les passages où seule la musique joue) -> une cible de
#      normalisation qui reflète la voix, pas une moyenne diluée par la
#      musique. C'est la cause la plus probable du "la normalisation ne
#      marche pas bien" : mesurer sur le mix entier tire la cible vers le
#      niveau de la musique quand elle est présente une bonne partie du clip.
#   2. atténuer légèrement le niveau global pendant les passages SANS parole
#      (musique seule / silence) pour éviter que la musique ressorte plus
#      fort entre deux phrases qu'elle ne le fait pendant que quelqu'un parle.
# Marge de sécurité (pas de coupure nette pile sur le mot) : on élargit
# chaque intervalle de parole avant de fusionner les trous proches, pour ne
# jamais couper une respiration ou une consonne finale.
SPEECH_PAD_SECONDS = 0.15
SPEECH_MERGE_GAP_SECONDS = 0.35
# Sous ce seuil, un "trou" entre deux passages parlés est trop court pour
# qu'une baisse de volume s'entende autrement que comme un artefact de pompage.
MIN_DUCK_GAP_SECONDS = 0.5
# {{À_COMPLÉTER : validé à l'oreille}}. Renforcé de -5dB à -8dB suite au
# retour utilisateur (« je veux entendre clairement la personne qui parle » —
# demande une différenciation plus marquée entre parole et musique seule).
DUCK_NON_SPEECH_DB = -8.0

# Ce qu'on ne peut PAS faire sans séparation de sources : baisser SEULEMENT la
# musique pendant que la voix continue, en laissant la voix intacte — les deux
# sont sur la MÊME piste mixée, toute baisse de volume pendant la parole
# baisserait aussi la voix elle-même. Le vrai "ducking" radio (musique baissée
# sous la voix) suppose deux pistes séparées ; on n'en a qu'une. Approximation
# honnête, sans séparation de sources : booster la bande de fréquences où vit
# l'intelligibilité de la parole (présence des consonnes, ~2-3kHz) PENDANT les
# passages parlés seulement. Ça n'éteint pas la musique, mais ça rend la voix
# relativement plus nette dessus — un vrai levier FFmpeg déterministe, pas une
# fausse promesse de séparation qu'on ne peut pas tenir ici.
# {{À_COMPLÉTER : fréquence/gain pas validés à l'oreille sur de vrais clips.}}
VOCAL_PRESENCE_HZ = 2800
VOCAL_PRESENCE_BOOST_DB = 4.0


def speech_intervals_from_words(words_out: list[dict],
                                pad: float = SPEECH_PAD_SECONDS,
                                merge_gap: float = SPEECH_MERGE_GAP_SECONDS) -> list[tuple[float, float]]:
    """Convertit des mots (temps de SORTIE, comme `words_out` ailleurs dans le
    projet) en intervalles [début, fin] de présence de parole, fusionnés
    quand deux mots sont proches (silence court dans une phrase) et élargis
    de `pad` secondes de chaque côté (marge de sécurité, jamais coupé pile
    sur un phonème). Pure, ne dépend d'aucun signal audio ni modèle."""
    words = sorted(
        (w for w in words_out if w.get("word", "").strip()),
        key=lambda w: w["start"],
    )
    if not words:
        return []
    intervals: list[list[float]] = []
    for w in words:
        start = max(0.0, float(w["start"]) - pad)
        end = float(w["end"]) + pad
        if intervals and start <= intervals[-1][1] + merge_gap:
            intervals[-1][1] = max(intervals[-1][1], end)
        else:
            intervals.append([start, end])
    return [(s, e) for s, e in intervals]


def non_speech_gaps(intervals: list[tuple[float, float]], out_duration: float,
                    min_gap: float = MIN_DUCK_GAP_SECONDS) -> list[tuple[float, float]]:
    """Complémentaire des intervalles de parole dans [0, out_duration] :
    les passages où on est sûr qu'il n'y a QUE de la musique (ou du silence),
    jamais de la voix. Ignore les trous trop courts (< min_gap) pour ne pas
    produire un pompage audible sur une simple respiration entre deux mots."""
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in sorted(intervals):
        if start - cursor >= min_gap:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if out_duration - cursor >= min_gap:
        gaps.append((cursor, out_duration))
    return gaps


def _hex_to_ff(color: str) -> str:
    return "0x" + color.lstrip("#").upper()


def _escape_filter_path(p: str) -> str:
    """Échappe un chemin pour l'insérer dans un filtre (ass=, subtitles=)."""
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _escape_drawtext(text: str) -> str:
    """Échappe un texte pour la valeur `text=` d'un filtre drawtext — `:`
    sépare les options du filtre, `%` déclenche l'expansion de motifs
    drawtext (date, compteur...), `'`/`\\` cassent le guillemetage Python."""
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "\\%")
    )


def _audio_keeps_filter(edl: EDL, audio_label: str = "ka") -> tuple[str, str]:
    """Reconstruit UNIQUEMENT le sous-graphe audio (trim des `keeps` +
    concaténation), sans toucher à la vidéo. Utilisé deux fois : une fois pour
    mesurer le volume (passe 1 de H1, mesure seule, aucun rendu), une fois
    intégré au graphe complet de production. Les deux graphes audio doivent
    être IDENTIQUES pour que la mesure soit valable pour le rendu réel."""
    n = len(edl.keeps)
    parts: list[str] = []
    for i, k in enumerate(edl.keeps):
        parts.append(
            f"[0:a]atrim=start={k.start:.3f}:end={k.end:.3f},"
            f"asetpts=PTS-STARTPTS[{audio_label}{i}]"
        )
    out_label = f"{audio_label}_out"
    if n == 1:
        parts.append(f"[{audio_label}0]anull[{out_label}]")
    else:
        chain = "".join(f"[{audio_label}{i}]" for i in range(n))
        parts.append(f"{chain}concat=n={n}:v=0:a=1[{out_label}]")
    return ";".join(parts), out_label


def _speech_only_filter(base_label: str, intervals: list[tuple[float, float]],
                        audio_label: str = "casp") -> tuple[str, str]:
    """Sous-graphe qui ne garde, dans un flux déjà en TEMPS DE SORTIE
    (`base_label`), que les intervalles de parole — pour mesurer le volume
    SANS le diluer par la musique seule ou le silence. Retourne (filtre,
    label) ; si aucun intervalle, retourne un passthrough (anull)."""
    if not intervals:
        return f"[{base_label}]anull[{audio_label}_out]", f"{audio_label}_out"
    parts: list[str] = []
    for i, (s, e) in enumerate(intervals):
        parts.append(
            f"[{base_label}]atrim=start={s:.3f}:end={e:.3f},"
            f"asetpts=PTS-STARTPTS[{audio_label}{i}]"
        )
    out_label = f"{audio_label}_out"
    if len(intervals) == 1:
        parts.append(f"[{audio_label}0]anull[{out_label}]")
    else:
        chain = "".join(f"[{audio_label}{i}]" for i in range(len(intervals)))
        parts.append(f"{chain}concat=n={len(intervals)}:v=0:a=1[{out_label}]")
    return ";".join(parts), out_label


def measure_loudness(edl: EDL, words_out: list[dict] | None = None) -> dict | None:
    """H1, passe 1 — mesure le volume réel de l'audio du clip (après
    trim/concat des `keeps`, AVANT tout traitement) via `loudnorm` en mode
    mesure seule (aucun fichier produit). Renvoie les clés attendues par la
    passe 2 (`measured_I`, `measured_TP`, `measured_LRA`, `measured_thresh`,
    `target_offset`), ou None si la mesure échoue — le rendu retombe alors sur
    un loudnorm à une passe (moins précis, mais jamais bloquant).

    Si `words_out` est fourni (mots en temps de SORTIE, même base que le reste
    du projet), la mesure porte UNIQUEMENT sur les passages parlés — pas sur
    les silences ni les passages où seule la musique de fond joue. Sans ça, la
    cible de normalisation est tirée vers le niveau de la musique dès qu'elle
    occupe une part significative du clip, ce qui produit une voix qui sonne
    tantôt trop forte, tantôt trop faible selon le clip."""
    audio_filter, out_label = _audio_keeps_filter(edl)
    if words_out:
        intervals = speech_intervals_from_words(words_out)
        if intervals:
            speech_filter, out_label = _speech_only_filter(out_label, intervals)
            audio_filter = f"{audio_filter};{speech_filter}"
    loudnorm = (
        f"highpass=f=80,acompressor=threshold=-18dB:ratio=3:attack=5:release=50,"
        f"loudnorm=I={LOUDNORM_TARGET_I}:TP={LOUDNORM_TARGET_TP}:"
        f"LRA={LOUDNORM_TARGET_LRA}:print_format=json"
    )
    full_filter = f"{audio_filter};[{out_label}]{loudnorm}[measured]"
    cmd = [
        "ffmpeg", "-y", "-i", edl.source.path,
        "-filter_complex", full_filter,
        "-map", "[measured]", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    # loudnorm imprime un bloc JSON sur stderr, entre la dernière accolade
    # ouvrante et sa fermante — on prend le DERNIER bloc { ... } du flux.
    matches = re.findall(r"\{[^{}]*\}", result.stderr, flags=re.DOTALL)
    if not matches:
        return None
    try:
        stats = json.loads(matches[-1])
        return {
            "measured_I": float(stats["input_i"]),
            "measured_TP": float(stats["input_tp"]),
            "measured_LRA": float(stats["input_lra"]),
            "measured_thresh": float(stats["input_thresh"]),
            "target_offset": float(stats.get("target_offset", 0.0)),
        }
    except Exception:
        return None


def build_filter_complex(edl: EDL, ass_path: str | None = None,
                         loudness_measurement: dict | None = None,
                         words_out: list[dict] | None = None) -> str:
    c = edl.canvas
    fw, fh = foreground_size(edl)
    parts: list[str] = []

    # --- 1. Découpe des `keeps` (temps SOURCE) puis concaténation ------------
    n = len(edl.keeps)
    for i, k in enumerate(edl.keeps):
        parts.append(
            f"[0:v]trim=start={k.start:.3f}:end={k.end:.3f},"
            f"setpts=PTS-STARTPTS[kv{i}]"
        )
        parts.append(
            f"[0:a]atrim=start={k.start:.3f}:end={k.end:.3f},"
            f"asetpts=PTS-STARTPTS[ka{i}]"
        )
    if n == 1:
        parts.append("[kv0]null[cv]")
        parts.append("[ka0]anull[ca]")
    else:
        chain = "".join(f"[kv{i}][ka{i}]" for i in range(n))
        parts.append(f"{chain}concat=n={n}:v=1:a=1[cv][ca]")

    # --- 1bis. H1 : chaîne audio (isolée de la vidéo, coût FFmpeg pur) -------
    # DÉSACTIVÉ (ENABLE_AUDIO_CHAIN_H1 = False) : non validé sur média réel.
    # Quand désactivé, [ca] est simplement recopié vers [caf] (identité) pour
    # que build_command puisse toujours mapper "[caf]" sans condition.
    if ENABLE_AUDIO_CHAIN_H1:
        # Passe-haut (retire les grondements de table/clim) -> légère
        # compression (resserre l'écart proche/loin du micro) -> encoche
        # sifflantes -> loudness EBU R128. Deux passes si `loudness_measurement`
        # est fourni (mesuré par measure_loudness), sinon repli une passe.
        # Placé juste après [ca] (ne dépend que de lui) pour que le graphe se
        # termine toujours par l'étape vidéo [out] — contrat inchangé pour les
        # appelants qui inspectent la fin de la chaîne.
        audio_chain = [
            "highpass=f=80",
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=50",
            f"equalizer=f={_DEESS_FREQ_HZ}:width_type=o:width=2:g={_DEESS_GAIN_DB}",
        ]
        # Atténue les passages SANS parole (musique seule / silence), pour que
        # la musique de fond ne ressorte pas plus fort qu'entre les phrases
        # qu'elle ne le fait pendant que quelqu'un parle. Basé sur les mêmes
        # timestamps mot-à-mot que les sous-titres — aucun modèle audio requis,
        # aucun risque de couper la voix par erreur (pad de sécurité inclus).
        if words_out:
            intervals = speech_intervals_from_words(words_out)
            gaps = non_speech_gaps(intervals, edl.out_duration)
            for gap_start, gap_end in gaps:
                audio_chain.append(
                    f"volume={DUCK_NON_SPEECH_DB}dB:enable='between(t,{gap_start:.3f},{gap_end:.3f})'"
                )
            # Boost de présence vocale PENDANT les passages parlés (voir
            # commentaire plus haut : on ne peut pas isoler la musique sur une
            # piste déjà mixée, donc on rend la voix relativement plus nette
            # au lieu d'essayer d'éteindre la musique sous elle).
            for speech_start, speech_end in intervals:
                audio_chain.append(
                    f"equalizer=f={VOCAL_PRESENCE_HZ}:width_type=o:width=1.5:"
                    f"g={VOCAL_PRESENCE_BOOST_DB}:enable='between(t,{speech_start:.3f},{speech_end:.3f})'"
                )
        if loudness_measurement:
            m = loudness_measurement
            audio_chain.append(
                f"loudnorm=I={LOUDNORM_TARGET_I}:TP={LOUDNORM_TARGET_TP}:LRA={LOUDNORM_TARGET_LRA}:"
                f"measured_I={m['measured_I']}:measured_TP={m['measured_TP']}:"
                f"measured_LRA={m['measured_LRA']}:measured_thresh={m['measured_thresh']}:"
                f"offset={m['target_offset']}:linear=true:print_format=summary"
            )
        else:
            audio_chain.append(
                f"loudnorm=I={LOUDNORM_TARGET_I}:TP={LOUDNORM_TARGET_TP}:LRA={LOUDNORM_TARGET_LRA}"
            )
        parts.append("[ca]" + ",".join(audio_chain) + "[caf]")
    else:
        parts.append("[ca]anull[caf]")

    # --- 2. Séparation fond / premier plan -----------------------------------
    needs_bg = edl.background.mode in ("blur", "solid")
    if needs_bg:
        parts.append("[cv]split=2[bgsrc][fgsrc]")
    else:
        parts.append("[cv]null[fgsrc]")

    # --- 3. Premier plan : un crop fixe par segment de cadrage ---------------
    spans = edl.framing_spans()
    if len(spans) == 1:
        z = FRAMING_ZOOM[spans[0][2]]
        parts.append(f"[fgsrc]{_crop_scale(z, fw, fh, spans[0][3])}[fg]")
    else:
        labels = "".join(f"[fs{i}]" for i in range(len(spans)))
        parts.append(f"[fgsrc]split={len(spans)}{labels}")
        for i, (t0, t1, value, face_x) in enumerate(spans):
            z = FRAMING_ZOOM[value]
            parts.append(
                f"[fs{i}]trim=start={t0:.3f}:end={t1:.3f},setpts=PTS-STARTPTS,"
                f"{_crop_scale(z, fw, fh, face_x)}[fc{i}]"
            )
        chain = "".join(f"[fc{i}]" for i in range(len(spans)))
        parts.append(f"{chain}concat=n={len(spans)}:v=1:a=0[fg]")

    # --- 4. Fond -------------------------------------------------------------
    if edl.background.mode == "blur":
        # D1 (prompt amélioration commandes) : `sigma` est appliqué APRÈS le
        # scale vers la résolution du canevas -> un même sigma fixe (25) rend
        # un flou moitié moins fort en 4K (3840x2160, cf. OUTPUT_RESOLUTIONS)
        # qu'en 1080p, alors que l'utilisateur choisit "flou léger/fort" sans
        # savoir quelle résolution de sortie sera utilisée. On met le sigma à
        # l'échelle du canevas réel, calibré sur la référence 1920px de haut
        # sur laquelle la valeur par défaut (25) a été choisie.
        _REFERENCE_CANVAS_H = 1920
        effective_sigma = edl.background.sigma * (c.h / _REFERENCE_CANVAS_H)
        parts.append(
            f"[bgsrc]scale={c.w}:{c.h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={c.w}:{c.h},gblur=sigma={effective_sigma:.2f},setsar=1[bg]"
        )
    elif edl.background.mode == "solid":
        col = _hex_to_ff(edl.background.color)
        parts.append(f"[bgsrc]drawbox=x=0:y=0:w=iw:h=ih:color={col}:t=fill,"
                     f"scale={c.w}:{c.h},setsar=1[bg]")

    # --- 5. Composition ------------------------------------------------------
    if needs_bg:
        parts.append("[bg][fg]overlay=(W-w)/2:(H-h)/2[comp]")
    else:
        parts.append(
            f"[fg]pad={c.w}:{c.h}:(ow-iw)/2:(oh-ih)/2:color=black[comp]"
        )

    # --- 6. Netteté + sous-titres + filigrane (dernière étape) ---------------
    # unsharp compense la perte de détail du recadrage vertical.
    chain = ["unsharp=5:5:0.3:5:5:0.0"]
    if edl.captions.enabled and ass_path:
        chain.append(f"ass=filename='{_escape_filter_path(ass_path)}'")
    # Texte incrusté (titre/accroche, F8.3) — un drawtext par événement,
    # limité dans le temps via enable='between(...)'. Ajouté AVANT le
    # filigrane pour que le filigrane reste toujours visible au-dessus.
    for e in sorted((e for e in edl.events if e.op == "text_overlay"), key=lambda e: e.t):
        t0 = e.t
        t1 = min(e.t + e.duration, edl.out_duration)
        if t1 <= t0:
            continue
        y_expr = {"top": "h*0.08", "center": "(h-th)/2", "bottom": "h*0.80"}[e.position]
        chain.append(
            f"drawtext=text='{_escape_drawtext(e.text)}':"
            f"fontcolor={_hex_to_ff(e.color)}:fontsize={e.size}:"
            f"box=1:boxcolor=black@0.35:boxborderw=12:x=(w-tw)/2:y={y_expr}:"
            f"enable='between(t,{t0:.3f},{t1:.3f})'"
        )
    if edl.watermark.enabled:
        fs = max(20, c.w // 22)
        op = edl.watermark.opacity
        txt = edl.watermark.text.replace("'", "\\'")
        chain.append(
            f"drawtext=text='{txt}':fontcolor=white@{op}:fontsize={fs}:"
            f"box=1:boxcolor=black@0.25:boxborderw=10:x=w-tw-30:y=h-th-40"
        )
    if chain:
        parts.append("[comp]" + ",".join(chain) + "[out]")
    else:
        parts.append("[comp]null[out]")

    return ";".join(parts)


def _rotation_degrees(stream: dict) -> int:
    """Angle de rotation déclaré par la vidéo (métadonnée, PAS un calcul sur
    les pixels) : les vidéos filmées au téléphone en portrait sont souvent
    ENCODÉES en paysage avec un tag de rotation à 90/270° pour être affichées
    correctement — ffprobe ne renvoie par défaut que les dimensions du flux
    encodé, PAS les dimensions réellement affichées. Ignorer cette métadonnée
    fait calculer un cadrage sur le mauvais ratio d'aspect -> image étirée
    (bug réel signalé : "l'image est totalement déformée"). Cherche dans les
    deux emplacements possibles (tag `rotate` classique, ou `side_data_list`
    avec `rotation`, format moderne) ; renvoie 0 si absent."""
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(float(tags["rotate"])) % 360
        except (TypeError, ValueError):
            pass
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            try:
                return int(float(side_data["rotation"])) % 360
            except (TypeError, ValueError):
                continue
    return 0


def probe_source(path: str) -> tuple[int, int]:
    """Dimensions RÉELLEMENT AFFICHÉES de la vidéo (largeur, hauteur) — pas
    forcément les dimensions du flux encodé : si une rotation de 90/270° est
    déclarée (cas fréquent des vidéos filmées au téléphone en portrait,
    encodées en paysage), largeur et hauteur sont inversées pour refléter ce
    que le lecteur affiche réellement. Sans ça, foreground_size()/le
    recadrage calculent sur le mauvais ratio d'aspect -> image déformée.

    Bug réel rencontré : `-select_streams v:0` prend le PREMIER flux vidéo
    sans distinction — une pochette/vignette embarquée (mp3 avec cover art,
    certaines vidéos de téléphone avec une piste miniature avant la piste
    principale) peut arriver en premier et ne pas exposer width/height de la
    même façon, faisant planter l'ancien code avec un `KeyError('width')`
    sans aucun contexte. On récupère TOUS les flux vidéo et on prend le
    premier qui n'est pas une pochette (disposition attached_pic) ; erreur
    explicite si aucun n'en a."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries",
         "stream=width,height:stream_tags=rotate:stream_side_data=rotation:stream_disposition=attached_pic",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    stream = next(
        (s for s in streams if not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    if stream is None or "width" not in stream or "height" not in stream:
        raise RuntimeError(
            f"aucun flux vidéo exploitable (dimensions) trouvé dans {path} "
            f"({len(streams)} flux vidéo détecté(s), tous une pochette ou "
            "sans dimensions rapportées par ffprobe)"
        )
    width, height = int(stream["width"]), int(stream["height"])
    if _rotation_degrees(stream) in (90, 270):
        width, height = height, width
    return width, height


def _even(x: float) -> int:
    return max(2, int(x) // 2 * 2)


def foreground_size(edl: EDL) -> tuple[int, int]:
    """Taille du premier plan, IDENTIQUE pour tous les segments de cadrage.

    Sans ça, `scale=W:-2` arrondit différemment selon le zoom et `concat`
    refuse le graphe : « Input link parameters do not match ». Bug classique,
    invisible tant qu'on n'a qu'un seul cadrage.
    """
    sw, sh = edl.source.width, edl.source.height
    if not sw or not sh:
        sw, sh = probe_source(edl.source.path)
    c = edl.canvas
    w, h = c.w, _even(c.w * sh / sw)
    if h > c.h:
        h, w = c.h, _even(c.h * sw / sh)
    return w, h


def _crop_scale(zoom: float, w: int, h: int, face_x: float = 0.5) -> str:
    """setsar=1 est obligatoire : concat compare aussi les ratios de pixel.

    A1 (AUDIT.md) : `face_x` (0=bord gauche, 0.5=centre, 1=bord droit) décale
    le crop horizontalement pour garder le visage détecté dans le cadre au
    lieu de toujours centrer géométriquement. `face_x=0.5` reproduit
    EXACTEMENT l'ancien calcul par défaut de `crop` (clamp garantit qu'on ne
    sort jamais de l'image), donc aucun changement de comportement pour les
    EDL existants ou les cadrages sans visage détecté.
    """
    if zoom >= 0.999:
        return f"scale={w}:{h}:flags=lanczos,setsar=1"
    cropw = f"2*floor(iw*{zoom:.4f}/2)"
    croph = f"2*floor(ih*{zoom:.4f}/2)"
    x_expr = f"clamp(iw*{face_x:.4f}-({cropw})/2,0,iw-({cropw}))"
    crop = f"crop=w='{cropw}':h='{croph}':x='{x_expr}':y='(ih-({croph}))/2',"
    return f"{crop}scale={w}:{h}:flags=lanczos,setsar=1"


def build_command(
    edl: EDL,
    out_path: str,
    *,
    ass_path: str | None = None,
    encoder: str = "libx264",
    crf: int = 18,
    loudness_measurement: dict | None = None,
    words_out: list[dict] | None = None,
) -> list[str]:
    fc = build_filter_complex(edl, ass_path, loudness_measurement=loudness_measurement,
                              words_out=words_out)
    cmd = [
        "ffmpeg", "-y",
        "-i", edl.source.path,
        "-filter_complex", fc,
        "-map", "[out]", "-map", "[caf]",
        "-r", str(edl.canvas.fps),
        "-c:v", encoder,
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]
    if encoder == "libx264":
        cmd += ["-crf", str(crf), "-preset", "slow"]
    else:  # h264_nvenc et consorts n'acceptent pas -crf
        cmd += ["-b:v", "6M"]
    cmd.append(out_path)
    return cmd


def render(edl: EDL, out_path: str, **kw) -> subprocess.CompletedProcess:
    # H1 : mesure du volume AVANT rendu (passe 1), pour un loudnorm à deux
    # passes plus précis. DÉSACTIVÉ tant que ENABLE_AUDIO_CHAIN_H1 est False
    # (la mesure ne servirait à rien : build_filter_complex ignore la chaîne
    # audio dans ce cas) — évite une passe ffmpeg supplémentaire pour rien.
    if ENABLE_AUDIO_CHAIN_H1 and "loudness_measurement" not in kw:
        kw = {**kw, "loudness_measurement": measure_loudness(edl, words_out=kw.get("words_out"))}
    cmd = build_command(edl, out_path, **kw)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run(cmd, capture_output=True, text=True)


def as_shell(edl: EDL, out_path: str, **kw) -> str:
    return " ".join(shlex.quote(a) for a in build_command(edl, out_path, **kw))
