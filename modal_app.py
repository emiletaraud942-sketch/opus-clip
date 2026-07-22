"""
Pipeline de traitement vidéo Sortclip : AssemblyAI + LLM + FFmpeg.

Étapes pour une vidéo importée :
  1. Téléchargement depuis Supabase Storage (bucket "videos").
  2. Transcription avec horodatage mot par mot via l'API AssemblyAI.
  3. Un LLM (Claude) lit la transcription complète et choisit les meilleurs
     segments à clipper : début, fin, titre accrocheur, score de viralité
     (0-100) et justification. C'est un jugement de LLM sur du texte, pas
     un modèle entraîné sur des données réelles de performance sociale —
     à considérer comme une bonne heuristique éditoriale, pas une vérité
     statistique.
  4. Chaque segment choisi est découpé, recadré en 9:16 et sous-titré
     avec FFmpeg.
  5. Les clips sont envoyés dans le bucket Supabase "clips" et
     enregistrés dans la table "clips".

Déploiement (nécessite `pip install fastapi` en local, en plus de `modal`) :
  modal deploy modal_app.py

Secrets Modal requis (une fois, via `modal secret create sortclip-secrets`) :
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY,
  ASSEMBLYAI_API_KEY, ANTHROPIC_API_KEY
"""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import modal
from fastapi import Request
from fastapi.responses import JSONResponse

app = modal.App("sortclip-pipeline")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "assemblyai==0.35.1",
        "anthropic==0.34.2",
        "supabase==2.7.4",
        "fastapi[standard]",
        "requests==2.32.3",
        "yt-dlp==2024.10.7",
    )
)

SOURCE_BUCKET = "videos"
CLIPS_BUCKET = "clips"
MAX_CLIPS_PER_VIDEO = 6


def get_supabase_client():
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def verify_user_token(token: str) -> str | None:
    """Vérifie un token JWT Supabase auprès de l'API Auth et renvoie l'user_id."""
    import requests

    res = requests.get(
        f"{os.environ['SUPABASE_URL']}/auth/v1/user",
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": os.environ["SUPABASE_ANON_KEY"],
        },
        timeout=10,
    )
    if res.status_code != 200:
        return None
    return res.json().get("id")


def download_youtube(url: str, out_path: str):
    import yt_dlp

    opts = {
        "format": "mp4/bestvideo+bestaudio",
        "outtmpl": out_path,
        "merge_output_format": "mp4",
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def transcribe(video_path: str):
    """Transcrit l'audio avec AssemblyAI. Retourne les mots horodatés
    (en secondes) et le texte complet."""
    import assemblyai as aai

    aai.settings.api_key = os.environ["ASSEMBLYAI_API_KEY"]
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(video_path, config=aai.TranscriptionConfig(language_code="fr"))

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"Échec transcription AssemblyAI : {transcript.error}")

    words = [
        {"word": w.text, "start": w.start / 1000, "end": w.end / 1000}
        for w in transcript.words
    ]
    return words, transcript.text


def select_clips_with_llm(words: list, full_text: str) -> list:
    """Demande à Claude de choisir les meilleurs segments à clipper."""
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # On donne au modèle la transcription avec horodatage approximatif
    # par phrase pour qu'il puisse choisir des bornes start/end précises.
    timestamped_transcript = "\n".join(
        f"[{w['start']:.1f}s] {w['word']}" for w in words
    )

    prompt = f"""Voici la transcription horodatée (en secondes) d'une vidéo (podcast, stream ou webinar).

{timestamped_transcript}

Choisis jusqu'à {MAX_CLIPS_PER_VIDEO} extraits qui feraient de bons clips courts pour TikTok/Reels/Shorts :
moments à forte accroche, anecdotes, punchlines, révélations, conseils actionnables.
Chaque extrait doit durer entre 15 et 75 secondes.

Réponds UNIQUEMENT avec un tableau JSON, sans texte autour, de cette forme :
[
  {{"start": 12.4, "end": 45.2, "title": "titre accrocheur court", "score": 87, "reason": "pourquoi ce moment est fort"}}
]"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Robustesse si le modèle entoure quand même le JSON de texte/markdown.
    start_idx = raw.find("[")
    end_idx = raw.rfind("]")
    clips = json.loads(raw[start_idx:end_idx + 1])

    clips.sort(key=lambda c: c["score"], reverse=True)
    return clips[:MAX_CLIPS_PER_VIDEO]


def words_in_range(words: list, start: float, end: float) -> list:
    return [w for w in words if w["start"] >= start and w["end"] <= end]


def write_srt(words: list, clip_start: float, path: str):
    def fmt(t):
        h, rem = divmod(max(0, t), 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")

    lines = []
    idx = 1
    chunk = []

    def flush():
        nonlocal idx, chunk
        if not chunk:
            return
        start = chunk[0]["start"] - clip_start
        end = chunk[-1]["end"] - clip_start
        text = " ".join(w["word"] for w in chunk)
        lines.append(f"{idx}\n{fmt(start)} --> {fmt(end)}\n{text}\n")
        idx += 1
        chunk = []

    for w in words:
        chunk.append(w)
        if len(chunk) >= 6:
            flush()
    flush()

    Path(path).write_text("\n".join(lines), encoding="utf-8")


def render_clip(source_path: str, clip: dict, words: list, out_path: str):
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = os.path.join(tmp, "subs.srt")
        clip_words = words_in_range(words, clip["start"], clip["end"])
        write_srt(clip_words, clip["start"], srt_path)

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


@app.function(image=image, secrets=[modal.Secret.from_name("sortclip-secrets")], timeout=2400)
def process_video(user_id: str, source_path: str, youtube_url: str | None = None):
    supabase = get_supabase_client()
    supabase.table("clip_jobs").insert({
        "user_id": user_id, "source_path": source_path, "status": "processing",
    }).execute()

    try:
        with tempfile.TemporaryDirectory() as tmp:
            local_video = os.path.join(tmp, "source.mp4")

            if youtube_url:
                download_youtube(youtube_url, local_video)
            else:
                video_bytes = supabase.storage.from_(SOURCE_BUCKET).download(source_path)
                Path(local_video).write_bytes(video_bytes)

            words, full_text = transcribe(local_video)
            clips = select_clips_with_llm(words, full_text)

            rows = []
            for i, clip in enumerate(clips):
                out_path = os.path.join(tmp, f"clip_{i}.mp4")
                render_clip(local_video, clip, words, out_path)

                storage_path = f"{user_id}/{Path(source_path).stem}_clip{i}.mp4"
                with open(out_path, "rb") as f:
                    supabase.storage.from_(CLIPS_BUCKET).upload(
                        storage_path, f, {"content-type": "video/mp4", "upsert": "true"}
                    )

                rows.append({
                    "user_id": user_id,
                    "source_path": source_path,
                    "storage_path": storage_path,
                    "title": clip["title"],
                    "score": clip["score"],
                    "reason": clip.get("reason", ""),
                    "start_time": clip["start"],
                    "end_time": clip["end"],
                })

            if rows:
                supabase.table("clips").insert(rows).execute()

        supabase.table("clip_jobs").update({"status": "done"}).eq("source_path", source_path).eq("user_id", user_id).execute()
    except Exception as exc:
        supabase.table("clip_jobs").update({"status": "error", "error": str(exc)}).eq("source_path", source_path).eq("user_id", user_id).execute()
        raise


@app.function(image=image, secrets=[modal.Secret.from_name("sortclip-secrets")])
@modal.fastapi_endpoint(method="POST")
def process(payload: dict, request: Request):
    """Endpoint HTTP appelé par le site après un upload réussi, ou pour un lien YouTube.
    Header requis : Authorization: Bearer <token utilisateur Supabase>
    Body attendu : {"path": "user_id/xxx.mp4"} pour un fichier importé,
    ou {"youtubeUrl": "https://youtube.com/..."} pour un lien."""
    auth_header = request.headers.get("authorization", "")
    token = auth_header.replace("Bearer ", "")
    user_id = verify_user_token(token)
    if not user_id:
        return JSONResponse({"error": "Non authentifié"}, status_code=401)

    youtube_url = payload.get("youtubeUrl")
    if youtube_url:
        source_path = f"{user_id}/youtube_{int(time.time())}"
        process_video.spawn(user_id=user_id, source_path=source_path, youtube_url=youtube_url)
        return {"status": "processing_started", "sourcePath": source_path}

    source_path = payload["path"]
    if not source_path.startswith(f"{user_id}/"):
        return JSONResponse({"error": "Chemin invalide"}, status_code=403)

    process_video.spawn(user_id=user_id, source_path=source_path)
    return {"status": "processing_started", "sourcePath": source_path}
