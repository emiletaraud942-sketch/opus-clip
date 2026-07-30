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


def _hex_to_ff(color: str) -> str:
    return "0x" + color.lstrip("#").upper()


def _escape_filter_path(p: str) -> str:
    """Échappe un chemin pour l'insérer dans un filtre (ass=, subtitles=)."""
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


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


def measure_loudness(edl: EDL) -> dict | None:
    """H1, passe 1 — mesure le volume réel de l'audio du clip (après
    trim/concat des `keeps`, AVANT tout traitement) via `loudnorm` en mode
    mesure seule (aucun fichier produit). Renvoie les clés attendues par la
    passe 2 (`measured_I`, `measured_TP`, `measured_LRA`, `measured_thresh`,
    `target_offset`), ou None si la mesure échoue — le rendu retombe alors sur
    un loudnorm à une passe (moins précis, mais jamais bloquant)."""
    audio_filter, out_label = _audio_keeps_filter(edl)
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
                         loudness_measurement: dict | None = None) -> str:
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
    # Passe-haut (retire les grondements de table/clim) -> légère compression
    # (resserre l'écart proche/loin du micro) -> encoche sifflantes -> loudness
    # EBU R128. Deux passes si `loudness_measurement` est fourni (mesuré par
    # measure_loudness), sinon repli une passe (moins précis, jamais bloquant).
    # Placé juste après [ca] (ne dépend que de lui) pour que le graphe se
    # termine toujours par l'étape vidéo [out] — contrat inchangé pour les
    # appelants qui inspectent la fin de la chaîne.
    audio_chain = [
        "highpass=f=80",
        "acompressor=threshold=-18dB:ratio=3:attack=5:release=50",
        f"equalizer=f={_DEESS_FREQ_HZ}:width_type=o:width=2:g={_DEESS_GAIN_DB}",
    ]
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
        parts.append(f"[fgsrc]{_crop_scale(z, fw, fh)}[fg]")
    else:
        labels = "".join(f"[fs{i}]" for i in range(len(spans)))
        parts.append(f"[fgsrc]split={len(spans)}{labels}")
        for i, (t0, t1, value) in enumerate(spans):
            z = FRAMING_ZOOM[value]
            parts.append(
                f"[fs{i}]trim=start={t0:.3f}:end={t1:.3f},setpts=PTS-STARTPTS,"
                f"{_crop_scale(z, fw, fh)}[fc{i}]"
            )
        chain = "".join(f"[fc{i}]" for i in range(len(spans)))
        parts.append(f"{chain}concat=n={len(spans)}:v=1:a=0[fg]")

    # --- 4. Fond -------------------------------------------------------------
    if edl.background.mode == "blur":
        parts.append(
            f"[bgsrc]scale={c.w}:{c.h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={c.w}:{c.h},gblur=sigma={edl.background.sigma},setsar=1[bg]"
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


def probe_source(path: str) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip().split(",")
    return int(out[0]), int(out[1])


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


def _crop_scale(zoom: float, w: int, h: int) -> str:
    """setsar=1 est obligatoire : concat compare aussi les ratios de pixel."""
    crop = (
        ""
        if zoom >= 0.999
        else f"crop=w='2*floor(iw*{zoom:.4f}/2)':h='2*floor(ih*{zoom:.4f}/2)',"
    )
    return f"{crop}scale={w}:{h}:flags=lanczos,setsar=1"


def build_command(
    edl: EDL,
    out_path: str,
    *,
    ass_path: str | None = None,
    encoder: str = "libx264",
    crf: int = 18,
    loudness_measurement: dict | None = None,
) -> list[str]:
    fc = build_filter_complex(edl, ass_path, loudness_measurement=loudness_measurement)
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
    # passes plus précis. Best-effort : si la mesure échoue (ffmpeg absent,
    # source illisible…), le rendu se fait quand même, avec un loudnorm à une
    # passe (repli géré dans build_filter_complex).
    if "loudness_measurement" not in kw:
        kw = {**kw, "loudness_measurement": measure_loudness(edl)}
    cmd = build_command(edl, out_path, **kw)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run(cmd, capture_output=True, text=True)


def as_shell(edl: EDL, out_path: str, **kw) -> str:
    return " ".join(shlex.quote(a) for a in build_command(edl, out_path, **kw))
