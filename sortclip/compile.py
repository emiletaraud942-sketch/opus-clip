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

import shlex
import subprocess
from pathlib import Path

from .edl import EDL, FRAMING_ZOOM


def _hex_to_ff(color: str) -> str:
    return "0x" + color.lstrip("#").upper()


def _escape_filter_path(p: str) -> str:
    """Échappe un chemin pour l'insérer dans un filtre (ass=, subtitles=)."""
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def build_filter_complex(edl: EDL, ass_path: str | None = None) -> str:
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
) -> list[str]:
    fc = build_filter_complex(edl, ass_path)
    cmd = [
        "ffmpeg", "-y",
        "-i", edl.source.path,
        "-filter_complex", fc,
        "-map", "[out]", "-map", "[ca]",
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
    cmd = build_command(edl, out_path, **kw)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run(cmd, capture_output=True, text=True)


def as_shell(edl: EDL, out_path: str, **kw) -> str:
    return " ".join(shlex.quote(a) for a in build_command(edl, out_path, **kw))
