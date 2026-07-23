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

# Limite du plan gratuit tant qu'aucun système de facturation n'est branché
# (voir tarifs.html : "3 vidéos offertes"). Ce nombre est comptabilisé à vie,
# tous statuts confondus (chaque tentative coûte de l'argent en API tierces).
FREE_TIER_VIDEO_LIMIT = 3

# Durée maximale acceptée pour une vidéo (upload ou lien YouTube), pour éviter
# qu'un compte gratuit ne lance un traitement démesurément coûteux.
MAX_VIDEO_DURATION_SECONDS = 60 * 60  # 60 minutes

ALLOWED_ORIGINS = [
    "https://opus-clip-alpha.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:5500",
]


def get_supabase_client():
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def _check_header_safe(name: str, value: str):
    """Vérifie qu'une valeur peut servir de header HTTP (latin-1 uniquement)
    et lève une erreur précise (sans exposer la valeur complète) sinon."""
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        bad_char = value[exc.start:exc.end]
        raise RuntimeError(
            f"Le secret Modal '{name}' (longueur {len(value)}) contient un "
            f"caractère invalide {bad_char!r} à la position {exc.start}. "
            "Recrée ce secret avec une valeur propre (sans espace, accent, "
            "retour à la ligne, ou texte collé en trop)."
        ) from exc


def verify_user_token(token: str) -> str | None:
    """Vérifie un token JWT Supabase auprès de l'API Auth et renvoie l'user_id."""
    import requests

    token = token.strip()
    anon_key = os.environ["SUPABASE_ANON_KEY"].strip()
    supabase_url = os.environ["SUPABASE_URL"].strip()

    _check_header_safe("token (envoyé par le site)", token)
    _check_header_safe("SUPABASE_ANON_KEY", anon_key)

    res = requests.get(
        f"{supabase_url}/auth/v1/user",
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": anon_key,
        },
        timeout=10,
    )

    if res.status_code != 200:
        return None
    return res.json().get("id")


def get_youtube_duration(url: str) -> float:
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("duration") or 0


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


def get_video_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def count_user_videos(supabase, user_id: str) -> int:
    res = supabase.table("clip_jobs").select("id", count="exact").eq("user_id", user_id).execute()
    return res.count or 0


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

            duration = get_video_duration(local_video)
            if duration > MAX_VIDEO_DURATION_SECONDS:
                raise RuntimeError(
                    f"Vidéo trop longue ({int(duration / 60)} min). "
                    f"La limite actuelle est de {MAX_VIDEO_DURATION_SECONDS // 60} minutes."
                )

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
@modal.asgi_app()
def process():
    """Endpoint HTTP appelé par le site après un upload réussi, ou pour un lien YouTube.
    Header requis : Authorization: Bearer <token utilisateur Supabase>
    Body attendu : {"path": "user_id/xxx.mp4"} pour un fichier importé,
    ou {"youtubeUrl": "https://youtube.com/..."} pour un lien."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    web_app = FastAPI()
    web_app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @web_app.post("/")
    async def handle(payload: dict, request: Request):
        auth_header = request.headers.get("authorization", "")
        token = auth_header.replace("Bearer ", "")
        user_id = verify_user_token(token)
        if not user_id:
            return JSONResponse({"error": "Non authentifié"}, status_code=401)

        supabase = get_supabase_client()
        videos_used = count_user_videos(supabase, user_id)
        if videos_used >= FREE_TIER_VIDEO_LIMIT:
            return JSONResponse({
                "error": f"Tu as atteint la limite de {FREE_TIER_VIDEO_LIMIT} vidéos gratuites. "
                         "Contacte-nous pour passer à un plan supérieur."
            }, status_code=403)

        youtube_url = payload.get("youtubeUrl")
        if youtube_url:
            try:
                duration = get_youtube_duration(youtube_url)
            except Exception:
                return JSONResponse({"error": "Impossible de lire ce lien YouTube."}, status_code=400)

            if duration > MAX_VIDEO_DURATION_SECONDS:
                return JSONResponse({
                    "error": f"Cette vidéo est trop longue ({int(duration / 60)} min). "
                             f"La limite actuelle est de {MAX_VIDEO_DURATION_SECONDS // 60} minutes."
                }, status_code=400)

            source_path = f"{user_id}/youtube_{int(time.time())}"
            process_video.spawn(user_id=user_id, source_path=source_path, youtube_url=youtube_url)
            return {"status": "processing_started", "sourcePath": source_path}

        source_path = payload["path"]
        if not source_path.startswith(f"{user_id}/"):
            return JSONResponse({"error": "Chemin invalide"}, status_code=403)

        process_video.spawn(user_id=user_id, source_path=source_path)
        return {"status": "processing_started", "sourcePath": source_path}

    return web_app
