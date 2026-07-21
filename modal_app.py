"""
Pipeline de traitement vidéo Sortclip, exécuté sur Modal.

Ce que fait ce script pour une vidéo importée :
  1. Télécharge la vidéo depuis Supabase Storage (bucket "videos").
  2. Transcrit l'audio avec l'API Whisper d'OpenAI (horodatage mot par mot).
  3. Découpe la transcription en segments candidats et leur attribue un
     score heuristique (densité de mots-clés d'accroche, longueur, rythme).
  4. Garde les N meilleurs segments, recadre chacun en 9:16 et incruste
     les sous-titres avec ffmpeg.
  5. Envoie les clips générés dans le bucket Supabase "clips" et met à
     jour la table "clips" (statut, score, chemin du fichier).

Le score n'est PAS un modèle de viralité entraîné : c'est une heuristique
simple (mots-clés, ponctuation, débit de parole). À affiner avec de vraies
données de performance si besoin plus tard.

Déploiement :
  modal deploy modal_app.py

Secrets Modal requis (à créer une fois avec `modal secret create sortclip-secrets`) :
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENAI_API_KEY
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import modal

app = modal.App("sortclip-pipeline")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "openai==1.51.0",
        "supabase==2.7.4",
        "fastapi[standard]",
    )
)

SOURCE_BUCKET = "videos"
CLIPS_BUCKET = "clips"

MAX_CLIPS_PER_VIDEO = 5
MIN_CLIP_SECONDS = 12
MAX_CLIP_SECONDS = 75

HOOK_KEYWORDS = [
    "jamais", "incroyable", "erreur", "secret", "personne", "attend",
    "changé", "vérité", "choc", "important", "conseil", "problème",
    "peur", "gagner", "perdre", "argent", "pourquoi", "comment",
]


def get_supabase_client():
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def transcribe(video_path: str):
    """Transcrit l'audio via l'API Whisper d'OpenAI et retourne les mots
    avec horodatage (start/end en secondes)."""
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    with open(video_path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="fr",
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words = [
        {"word": w.word.strip(), "start": w.start, "end": w.end}
        for w in transcript.words
    ]
    return words


def score_text(text: str, duration: float) -> int:
    lowered = text.lower()
    keyword_hits = sum(1 for kw in HOOK_KEYWORDS if kw in lowered)
    has_question = "?" in text
    word_count = len(text.split())
    words_per_sec = word_count / duration if duration > 0 else 0

    score = 50
    score += keyword_hits * 8
    score += 10 if has_question else 0
    score += 10 if 2.0 <= words_per_sec <= 3.5 else -5
    score += 5 if 15 <= duration <= 45 else 0
    return max(1, min(99, score))


def build_candidate_clips(words: list) -> list:
    """Regroupe les mots en segments de 12-75s en coupant sur les pauses,
    puis attribue un score à chaque segment."""
    if not words:
        return []

    candidates = []
    current = [words[0]]

    for prev, w in zip(words, words[1:]):
        gap = w["start"] - prev["end"]
        current_duration = current[-1]["end"] - current[0]["start"]

        should_cut = (
            (gap > 0.6 and current_duration >= MIN_CLIP_SECONDS)
            or current_duration >= MAX_CLIP_SECONDS
        )
        if should_cut:
            candidates.append(current)
            current = []
        current.append(w)

    if current:
        candidates.append(current)

    clips = []
    for seg in candidates:
        duration = seg[-1]["end"] - seg[0]["start"]
        if duration < MIN_CLIP_SECONDS:
            continue
        text = " ".join(w["word"] for w in seg)
        clips.append({
            "start": seg[0]["start"],
            "end": seg[-1]["end"],
            "text": text,
            "score": score_text(text, duration),
            "words": seg,
        })

    clips.sort(key=lambda c: c["score"], reverse=True)
    return clips[:MAX_CLIPS_PER_VIDEO]


def write_srt(words: list, clip_start: float, path: str):
    """Génère un .srt avec des sous-titres courts (par groupes de mots),
    horodatés relativement au début du clip."""
    def fmt(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

    lines = []
    idx = 1
    chunk = []
    chunk_start = None

    def flush():
        nonlocal idx, chunk
        if not chunk:
            return
        start = chunk[0]["start"] - clip_start
        end = chunk[-1]["end"] - clip_start
        text = " ".join(w["word"] for w in chunk)
        lines.append(f"{idx}\n{fmt(max(0, start))} --> {fmt(max(0, end))}\n{text}\n")
        idx += 1
        chunk = []

    for w in words:
        chunk.append(w)
        if len(chunk) >= 6:
            flush()
    flush()

    Path(path).write_text("\n".join(lines), encoding="utf-8")


def render_clip(source_path: str, clip: dict, out_path: str):
    """Coupe le segment, recadre en 9:16 (centré), incruste les sous-titres."""
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = os.path.join(tmp, "subs.srt")
        write_srt(clip["words"], clip["start"], srt_path)

        duration = clip["end"] - clip["start"]
        vf = (
            "crop=ih*9/16:ih,scale=1080:1920,"
            f"subtitles={srt_path}:force_style='Fontsize=20,PrimaryColour=&HFFFFFF&,"
            "OutlineColour=&H000000&,BorderStyle=3,Outline=2,Alignment=2,MarginV=80'"
        )

        subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(clip["start"]),
            "-i", source_path,
            "-t", str(duration),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac",
            out_path,
        ], check=True, capture_output=True)


@app.function(image=image, secrets=[modal.Secret.from_name("sortclip-secrets")], timeout=1800)
def process_video(user_id: str, source_path: str, clip_row_id: str | None = None):
    supabase = get_supabase_client()

    with tempfile.TemporaryDirectory() as tmp:
        local_video = os.path.join(tmp, "source.mp4")
        video_bytes = supabase.storage.from_(SOURCE_BUCKET).download(source_path)
        Path(local_video).write_bytes(video_bytes)

        words = transcribe(local_video)
        clips = build_candidate_clips(words)

        results = []
        for i, clip in enumerate(clips):
            out_path = os.path.join(tmp, f"clip_{i}.mp4")
            render_clip(local_video, clip, out_path)

            storage_path = f"{user_id}/{Path(source_path).stem}_clip{i}.mp4"
            with open(out_path, "rb") as f:
                supabase.storage.from_(CLIPS_BUCKET).upload(
                    storage_path, f, {"content-type": "video/mp4", "upsert": "true"}
                )

            results.append({
                "user_id": user_id,
                "source_path": source_path,
                "output_path": storage_path,
                "title": clip["text"][:80],
                "score": clip["score"],
                "start": clip["start"],
                "end": clip["end"],
                "status": "done",
            })

        if results:
            supabase.table("clips").insert(results).execute()

        if clip_row_id:
            supabase.table("videos").update({"status": "done"}).eq("id", clip_row_id).execute()

    return {"clips_generated": len(results)}


@app.function(image=image, secrets=[modal.Secret.from_name("sortclip-secrets")])
@modal.fastapi_endpoint(method="POST")
def process(payload: dict):
    """Endpoint HTTP appelé par le site après un upload réussi.
    Body attendu : {"user_id": "...", "source_path": "..."}"""
    user_id = payload["user_id"]
    source_path = payload["source_path"]
    process_video.spawn(user_id=user_id, source_path=source_path)
    return {"status": "processing_started"}
