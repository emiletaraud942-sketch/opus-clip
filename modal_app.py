"""
Pipeline de traitement vidéo Sortclip : AssemblyAI + LLM + FFmpeg.

Étapes pour une vidéo importée :
  1. Téléchargement depuis Supabase Storage (bucket "videos").
  2. Transcription avec horodatage mot par mot via l'API AssemblyAI, avec
     détection des mots-clés à mettre en emphase (Auto Highlights).
  3. Un LLM (Claude) lit la transcription complète et choisit les meilleurs
     segments à clipper : début, fin, titre accrocheur, score de viralité
     (0-100) et justification. C'est un jugement de LLM sur du texte, pas
     un modèle entraîné sur des données réelles de performance sociale —
     à considérer comme une bonne heuristique éditoriale, pas une vérité
     statistique.
  4. Chaque segment choisi est découpé (les silences trop longs sont
     retirés du montage), recadré en 9:16 et sous-titré avec FFmpeg — les
     mots-clés détectés à l'étape 2 apparaissent en couleur.
  5. Les clips sont envoyés dans le bucket Supabase "clips" et
     enregistrés dans la table "clips".

Déploiement (nécessite `pip install fastapi` en local, en plus de `modal`) :
  modal deploy modal_app.py

Secrets Modal requis (une fois, via `modal secret create sortclip-secrets`) :
  SUPABASE_SERVICE_ROLE_KEY, ASSEMBLYAI_API_KEY, ANTHROPIC_API_KEY
(L'URL Supabase et la clé anon, publiques, sont en dur dans ce fichier.)
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

# L'URL du projet et la clé anon sont PUBLIQUES par design (déjà exposées à
# tous les visiteurs via supabase-config.js). On les met en dur ici, à
# l'identique du front, plutôt que de dépendre d'un secret Modal qui s'est
# déjà retrouvé corrompu lors de copier-coller PowerShell. Seule la clé
# service_role (réellement secrète) reste dans le secret Modal.
SUPABASE_URL = "https://kxnacycqaqhmvwdkprbq.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt4bmFjeWNxYXFobXZ3ZGtwcmJxIiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3ODQzNjI2OTksImV4cCI6MjA5OTkzODY5OX0."
    "2OJnqWcvP_g4ovGm1DGSGoY7TnCeKoMYjCilExGSe7w"
)


def _validate_supabase_key(name: str, key: str, expected_role: str):
    """Décode le payload du JWT (sans vérifier la signature) pour détecter
    une clé corrompue ou du mauvais type, avec un message d'erreur clair."""
    import base64

    try:
        payload_b64 = key.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception as exc:
        raise RuntimeError(
            f"La clé '{name}' (longueur {len(key)}) est corrompue : impossible de "
            "décoder son contenu. Recrée le secret Modal avec une valeur copiée "
            "proprement depuis le dashboard Supabase."
        ) from exc

    role = payload.get("role")
    if role != expected_role:
        raise RuntimeError(
            f"La clé '{name}' a le rôle '{role}' au lieu de '{expected_role}' — "
            "les clés anon et service_role ont probablement été inversées."
        )


def get_supabase_client():
    from supabase import create_client

    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
    _validate_supabase_key("SUPABASE_SERVICE_ROLE_KEY", service_key, "service_role")
    return create_client(SUPABASE_URL, service_key)


def verify_user_token(token: str) -> str | None:
    """Vérifie un token JWT Supabase auprès de l'API Auth et renvoie l'user_id."""
    import requests

    token = token.strip()
    if not token:
        print("[verify_user_token] aucun token reçu")
        return None

    res = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": SUPABASE_ANON_KEY,
        },
        timeout=10,
    )

    print(f"[verify_user_token] Supabase a répondu {res.status_code} : {res.text[:300]}")

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
    (en secondes), le texte complet, et l'ensemble des mots-clés à mettre
    en emphase dans les sous-titres (fonctionnalité "Auto Highlights")."""
    import assemblyai as aai

    aai.settings.api_key = os.environ["ASSEMBLYAI_API_KEY"]
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(
        video_path,
        config=aai.TranscriptionConfig(language_code="fr", auto_highlights=True),
    )

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"Échec transcription AssemblyAI : {transcript.error}")

    words = [
        {"word": w.text, "start": w.start / 1000, "end": w.end / 1000}
        for w in transcript.words
    ]

    highlight_words = set()
    if transcript.auto_highlights_result:
        for result in transcript.auto_highlights_result.results:
            for token in result.text.split():
                highlight_words.add(token.strip(".,!?;:\"'").lower())

    return words, transcript.text, highlight_words


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
    if start_idx == -1 or end_idx == -1:
        raise RuntimeError("Le modèle IA n'a pas renvoyé de sélection de clips exploitable.")

    try:
        clips = json.loads(raw[start_idx:end_idx + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Réponse IA illisible pour sélectionner les clips : {exc}") from exc

    valid_clips = []
    for c in clips:
        if not all(k in c for k in ("start", "end", "title", "score")):
            continue
        if c["end"] <= c["start"]:
            continue
        valid_clips.append(c)

    if not valid_clips:
        raise RuntimeError("Aucun moment exploitable n'a été trouvé dans cette vidéo.")

    valid_clips.sort(key=lambda c: c["score"], reverse=True)
    return valid_clips[:MAX_CLIPS_PER_VIDEO]


def words_in_range(words: list, start: float, end: float) -> list:
    return [w for w in words if w["start"] >= start and w["end"] <= end]


# Silences (entre deux mots) plus longs que ce seuil sont coupés au montage.
MIN_SILENCE_GAP = 0.6
# Petite marge conservée autour de chaque coupe pour ne pas couper un mot trop court.
SILENCE_CUT_BUFFER = 0.08
# Au-delà de ce nombre de coupures, le filtre FFmpeg devient trop complexe et
# risque d'échouer : on renonce à retirer les silences pour ce clip plutôt
# que de faire planter tout le montage.
MAX_SEGMENTS_FOR_SILENCE_REMOVAL = 40


def build_keep_segments(clip_words: list, clip_start: float, clip_end: float) -> list:
    """Calcule les portions de la vidéo à garder (en excluant les silences
    trop longs) pour un clip donné. Retourne une liste de (start, end)."""
    if not clip_words:
        return [(clip_start, clip_end)]

    segments = []
    cursor = clip_start
    prev_end = clip_start

    for w in clip_words:
        gap = w["start"] - prev_end
        if gap > MIN_SILENCE_GAP:
            segment_end = prev_end + SILENCE_CUT_BUFFER
            if segment_end > cursor:
                segments.append((cursor, segment_end))
            cursor = max(segment_end, w["start"] - SILENCE_CUT_BUFFER)
        prev_end = max(prev_end, w["end"])

    if clip_end > cursor:
        segments.append((cursor, clip_end))

    segments = [(s, e) for s, e in segments if e - s > 0.05] or [(clip_start, clip_end)]

    if len(segments) > MAX_SEGMENTS_FOR_SILENCE_REMOVAL:
        return [(clip_start, clip_end)]

    return segments


def remap_time(t: float, segments: list) -> float:
    """Convertit un horodatage de la vidéo d'origine vers sa position dans
    le montage compressé (silences retirés)."""
    compressed = 0.0
    for s, e in segments:
        if t <= e:
            return compressed + max(0.0, t - s)
        compressed += e - s
    return compressed


def fmt_ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,44,&H00FFFFFF,&H00000000,&H00000000,0,3,3,0,2,20,20,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Couleur d'emphase des mots-clés (format ASS &HAABBGGRR&) : jaune vif.
HIGHLIGHT_COLOR = "&H0000FFFF&"
DEFAULT_COLOR = "&H00FFFFFF&"


def write_subtitles(words: list, highlight_words: set, segments: list, path: str):
    """Génère un fichier de sous-titres .ass, avec les horodatages remappés
    sur le montage compressé (silences retirés) et les mots-clés en couleur."""
    lines = [ASS_HEADER]
    chunk = []

    def flush():
        if not chunk:
            return
        start = remap_time(chunk[0]["start"], segments)
        end = remap_time(chunk[-1]["end"], segments)
        parts = []
        for w in chunk:
            clean = w["word"].strip(".,!?;:\"'").lower()
            if clean in highlight_words:
                parts.append(f"{{\\c{HIGHLIGHT_COLOR}}}{w['word']}{{\\c{DEFAULT_COLOR}}}")
            else:
                parts.append(w["word"])
        text = " ".join(parts)
        lines.append(f"Dialogue: 0,{fmt_ass_time(start)},{fmt_ass_time(end)},Default,,0,0,0,,{text}")

    for w in words:
        chunk.append(w)
        if len(chunk) >= 6:
            flush()
            chunk = []
    flush()

    Path(path).write_text("\n".join(lines), encoding="utf-8")


def render_clip(source_path: str, clip: dict, words: list, highlight_words: set, out_path: str):
    with tempfile.TemporaryDirectory() as tmp:
        clip_words = words_in_range(words, clip["start"], clip["end"])
        segments = build_keep_segments(clip_words, clip["start"], clip["end"])

        subs_path = os.path.join(tmp, "subs.ass")
        write_subtitles(clip_words, highlight_words, segments, subs_path)

        # Un morceau de filtre par segment gardé (coupe les silences), puis
        # on les recolle (concat) avant le recadrage et les sous-titres.
        filter_parts = []
        concat_inputs = ""
        for i, (s, e) in enumerate(segments):
            filter_parts.append(
                f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}];"
                f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]"
            )
            concat_inputs += f"[v{i}][a{i}]"

        filter_complex = ";".join(filter_parts)
        filter_complex += f";{concat_inputs}concat=n={len(segments)}:v=1:a=1[catv][cata]"
        filter_complex += (
            f";[catv]crop=ih*9/16:ih,scale=1080:1920,subtitles={subs_path}[outv]"
        )

        subprocess.run([
            "ffmpeg", "-y",
            "-i", source_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[cata]",
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
                # Vérifie la durée AVANT de télécharger, pour ne pas rapatrier
                # une vidéo de plusieurs heures inutilement. Fait ici (en
                # arrière-plan) et non dans l'endpoint HTTP, car yt-dlp peut
                # mettre de longues secondes à répondre.
                try:
                    yt_duration = get_youtube_duration(youtube_url)
                except Exception as exc:
                    raise RuntimeError(
                        "Impossible de lire ce lien YouTube (vidéo privée, "
                        "supprimée, ou accès bloqué)."
                    ) from exc

                if yt_duration > MAX_VIDEO_DURATION_SECONDS:
                    raise RuntimeError(
                        f"Vidéo trop longue ({int(yt_duration / 60)} min). "
                        f"La limite actuelle est de {MAX_VIDEO_DURATION_SECONDS // 60} minutes."
                    )

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

            words, full_text, highlight_words = transcribe(local_video)
            if not words:
                raise RuntimeError("Aucune parole détectée dans cette vidéo — impossible de générer des clips.")

            clips = select_clips_with_llm(words, full_text)

            rows = []
            for i, clip in enumerate(clips):
                try:
                    out_path = os.path.join(tmp, f"clip_{i}.mp4")
                    render_clip(local_video, clip, words, highlight_words, out_path)

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
                except Exception as clip_exc:
                    # Un clip qui échoue au montage ne doit pas faire échouer
                    # toute la vidéo : on le saute et on continue les autres.
                    print(f"[process_video] Échec du montage du clip {i}: {clip_exc}")
                    continue

            if not rows:
                raise RuntimeError("Aucun clip n'a pu être monté avec succès pour cette vidéo.")

            supabase.table("clips").insert(rows).execute()

            if not youtube_url:
                # La vidéo source a déjà été traitée : on la supprime du bucket
                # "videos" pour ne pas payer du stockage indéfiniment. Les
                # clips générés, eux, restent dans le bucket "clips".
                supabase.storage.from_(SOURCE_BUCKET).remove([source_path])

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
            # La validation de la durée est faite dans process_video (en
            # arrière-plan) : yt-dlp est trop lent pour bloquer la réponse
            # HTTP ici, et le site attendrait sans retour visuel.
            source_path = f"{user_id}/youtube_{int(time.time())}"
            process_video.spawn(user_id=user_id, source_path=source_path, youtube_url=youtube_url)
            return {"status": "processing_started", "sourcePath": source_path}

        source_path = payload["path"]
        if not source_path.startswith(f"{user_id}/"):
            return JSONResponse({"error": "Chemin invalide"}, status_code=403)

        process_video.spawn(user_id=user_id, source_path=source_path)
        return {"status": "processing_started", "sourcePath": source_path}

    return web_app
