"""
Test des fondations EDL — SANS IA. N'exécute pas ffmpeg par défaut (il vérifie
seulement le graphe généré) ; tente un rendu réel en bonus si ffmpeg est là.

Lancer : python tests/test_edl_compile.py
(Nécessite pydantic — fourni par fastapi dans l'image Modal.)
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal). "
          "Syntaxe vérifiable via `python -m py_compile sortclip/*.py`.")
    sys.exit(0)

from sortclip import (  # noqa: E402
    build_edl, map_words_to_output, build_ass, build_filter_complex,
    FramingEvent,
)

# Transcript factice (temps SOURCE) : 2 blocs séparés par un silence, avec un
# « euh » à retirer.
WORDS = [
    {"word": "Le", "start": 0.50, "end": 0.70},
    {"word": "vrai", "start": 0.72, "end": 1.05},
    {"word": "euh", "start": 1.07, "end": 1.30},
    {"word": "problème", "start": 1.35, "end": 1.95},
    {"word": "c'est", "start": 1.98, "end": 2.20},
    {"word": "la", "start": 2.22, "end": 2.35},
    {"word": "marge", "start": 2.38, "end": 2.90},
    # --- silence ~1,4 s ---
    {"word": "Personne", "start": 4.30, "end": 4.85},
    {"word": "n'en", "start": 4.88, "end": 5.10},
    {"word": "parle", "start": 5.12, "end": 5.55},
    {"word": "jamais", "start": 5.58, "end": 6.10},
]


def main() -> None:
    ok = 0

    # 1. Construction depuis un preset, sans IA.
    edl, cleaned = build_edl(
        source_path="/tmp/does_not_matter.mp4",
        source_duration=40.0,
        words=WORDS,
        preset_name="podcast_dynamique",
        watermark=True,
        source_width=1920, source_height=1080,   # évite un appel ffprobe
    )
    assert all(w["word"] != "euh" for w in cleaned), "le « euh » aurait dû être retiré"
    assert len(edl.keeps) >= 2, edl.keeps
    assert abs(edl.out_duration - sum(k.dur for k in edl.keeps)) < 1e-6
    print(f"  1. build_edl      : {len(edl.keeps)} intervalles, "
          f"{edl.out_duration:.2f}s gardées"); ok += 1

    # 2. Conversion sortie -> source.
    assert abs(edl.out_to_src(0.0) - edl.keeps[0].start) < 1e-6
    print("  2. out_to_src     : sortie -> source correct"); ok += 1

    # 3. Cadrages : un FramingEvent scinde la timeline de sortie.
    edl = edl.model_copy(update={"events": [FramingEvent(t=1.0, value="tight")]})
    spans = edl.framing_spans()
    assert spans[0][0] == 0.0 and abs(spans[-1][1] - edl.out_duration) < 1e-6
    print(f"  3. framing_spans  : {len(spans)} segments {[s[2] for s in spans]}"); ok += 1

    # 4. Remappage des mots en temps de SORTIE.
    words_out = map_words_to_output(cleaned, edl)
    assert words_out and all(w["end"] <= edl.out_duration + 1e-6 for w in words_out)
    print(f"  4. map_words      : {len(words_out)} mots remappés en temps sortie"); ok += 1

    # 5. Sous-titres ASS.
    work = Path("/tmp/sortclip_edl_test"); work.mkdir(exist_ok=True)
    ass_path = str(work / "clip.ass")
    build_ass(words_out, edl.captions, edl.canvas, ass_path)
    ass = Path(ass_path).read_text(encoding="utf-8")
    assert "[V4+ Styles]" in ass and "Dialogue:" in ass
    print("  5. build_ass      : fichier .ass généré"); ok += 1

    # 6. Graphe FFmpeg : cohérent, avec filigrane.
    fc = build_filter_complex(edl, ass_path)
    assert "concat" in fc, "les keeps doivent être concaténés"
    assert "overlay" in fc, "composition fond/premier plan attendue"
    assert "ass=filename=" in fc, "sous-titres attendus"
    assert "drawtext=text='Sortclip'" in fc, "filigrane attendu (watermark=True)"
    assert fc.endswith("[out]")
    print("  6. filter_complex : concat + overlay + ass + filigrane"); ok += 1

    # 7. Bonus : rendu réel si ffmpeg est disponible.
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        import subprocess
        src = work / "source.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             "testsrc2=size=1920x1080:rate=30:duration=8",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-shortest", str(src)],
            check=True, capture_output=True,
        )
        edl2, cleaned2 = build_edl(
            source_path=str(src), source_duration=8.0, words=WORDS,
            preset_name="podcast_dynamique", watermark=True,
        )
        wo = map_words_to_output(cleaned2, edl2)
        build_ass(wo, edl2.captions, edl2.canvas, ass_path)
        from sortclip import render
        r = render(edl2, str(work / "out.mp4"), ass_path=ass_path)
        assert r.returncode == 0, r.stderr[-800:]
        print(f"  7. rendu ffmpeg   : OK ({(work / 'out.mp4').stat().st_size // 1024} Ko)"); ok += 1
    else:
        print("  7. rendu ffmpeg   : ignoré (ffmpeg absent)")

    print(f"\n{ok} étapes validées.")


if __name__ == "__main__":
    main()
