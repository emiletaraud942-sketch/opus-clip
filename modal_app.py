"""
Pipeline de traitement vidéo Sortclip : AssemblyAI + LLM + FFmpeg.

Étapes pour une vidéo importée :
  1. Téléchargement depuis Supabase Storage (bucket "videos").
  2. Transcription avec horodatage mot par mot via l'API AssemblyAI.
  3. Un LLM (Claude) lit la transcription complète et choisit les meilleurs
     segments à clipper : début, fin, titre accrocheur, score de viralité
     (0-100) et justification. C'est un jugement de LLM sur du texte, pas un
     modèle entraîné sur des données réelles de performance sociale — à
     considérer comme une bonne heuristique éditoriale, pas une vérité
     statistique.
  4. Chaque segment choisi est découpé (les silences trop longs sont
     retirés du montage), mis au format vertical 9:16 avec fond flou (cadre
     complet, rien n'est coupé) et sous-titré avec FFmpeg. Tous les
     sous-titres ont une couleur uniforme.
  5. Les clips sont envoyés dans le bucket Supabase "clips" et
     enregistrés dans la table "clips".

Déploiement (nécessite `pip install fastapi` en local, en plus de `modal`) :
  modal deploy modal_app.py

Secrets Modal requis (une fois, via `modal secret create sortclip-secrets`) :
  SUPABASE_SERVICE_ROLE_KEY, ASSEMBLYAI_API_KEY, ANTHROPIC_API_KEY,
  STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET
Secrets optionnels (contournement anti-robot YouTube) :
  YOUTUBE_COOKIES (cookies.txt d'un compte connecté),
  YOUTUBE_PROXY (proxy résidentiel http://user:pass@host:port)
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
    .apt_install("ffmpeg", "git")
    .pip_install(
        "assemblyai==0.35.1",
        "anthropic==0.34.2",
        "supabase==2.7.4",
        "fastapi[standard]",
        "requests==2.32.3",
        "stripe==10.12.0",
    )
    # yt-dlp installé depuis GitHub (master) : YouTube change ses protections
    # anti-robot en permanence, une version PyPI figée casse vite.
    .pip_install("yt-dlp @ git+https://github.com/yt-dlp/yt-dlp.git")
)

SOURCE_BUCKET = "videos"
CLIPS_BUCKET = "clips"
MAX_CLIPS_PER_VIDEO = 6

# Limites d'usage par plan (voir tarifs.html). Le plan "free" est compté à
# vie (3 vidéos offertes, une fois). Les plans payants sont comptés par mois
# calendaire, tous statuts confondus (chaque tentative coûte de l'argent en
# API tierces). Tant qu'aucune facturation (Stripe) n'est branchée, le plan
# de chaque utilisateur est stocké dans la table Supabase "profiles" et mis
# à jour manuellement.
PLAN_MONTHLY_LIMITS = {
    "free": 3,     # à vie, pas par mois — voir check_quota
    "pro": 30,
    "equipe": 60,
}

# Comptes exemptés de la limite ci-dessus (phase de test uniquement) — à
# vider une fois les tests terminés pour que la limite s'applique à tous.
TEST_ACCOUNT_EMAILS = {"emiletaraud942@gmail.com"}

# Durée maximale acceptée pour une vidéo (upload ou lien YouTube), pour éviter
# qu'un compte gratuit ne lance un traitement démesurément coûteux.
MAX_VIDEO_DURATION_SECONDS = 60 * 60  # 60 minutes

ALLOWED_ORIGINS = [
    "https://opus-clip-alpha.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:5500",
]

SITE_URL = "https://opus-clip-alpha.vercel.app"

# Correspondance entre les produits Stripe (créés dans le dashboard) et les
# plans internes de Sortclip.
STRIPE_PRODUCT_TO_PLAN = {
    "prod_UxMunMagkVFAZR": "pro",
    "prod_UxMtXHRHDGQljE": "equipe",
}

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


def verify_user_token(token: str) -> tuple[str, str] | None:
    """Vérifie un token JWT Supabase auprès de l'API Auth et renvoie (user_id, email)."""
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
    data = res.json()
    return data.get("id"), data.get("email")


# User-Agent d'un vrai navigateur : sans ça, YouTube identifie plus vite
# les requêtes comme automatisées et renvoie "Sign in to confirm you're not
# a bot".
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _base_ydl_opts() -> dict:
    """Options communes à l'inspection et au téléchargement, réglées pour
    limiter la détection anti-robot de YouTube.

    Note honnête : ces réglages RÉDUISENT les blocages mais ne les
    éliminent pas. YouTube bloque de plus en plus les IP de datacenters
    (comme celles de Modal). Le contournement le plus fiable reste de
    fournir des cookies d'un compte connecté via le secret Modal
    YOUTUBE_COOKIES (voir download_youtube)."""
    return {
        "quiet": True,
        "no_warnings": True,
        "user_agent": _BROWSER_UA,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "socket_timeout": 30,
        # On tente plusieurs "clients" internes de YouTube ; certains
        # passent quand le client web par défaut est bloqué.
        "extractor_args": {"youtube": {"player_client": ["android", "web", "ios"]}},
    }


def _apply_evasion(opts: dict) -> dict:
    """Ajoute les contournements anti-détection YouTube si les secrets
    correspondants sont fournis :
      - YOUTUBE_COOKIES : cookies d'un compte connecté (format Netscape
        cookies.txt). yt-dlp s'authentifie alors comme un vrai utilisateur.
      - YOUTUBE_PROXY : proxy (idéalement résidentiel) pour ne pas sortir
        depuis une IP de datacenter Modal, que YouTube bloque massivement.
        Format : http://user:pass@host:port
    C'est la combinaison des deux qui contourne le plus fiablement le
    "Sign in to confirm you're not a bot"."""
    cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if cookies:
        # Les cookies peuvent être fournis soit en clair (format Netscape),
        # soit encodés en base64 (recommandé : une seule ligne, sûr dans un
        # fichier .env). On tente d'abord de décoder le base64.
        import base64
        try:
            decoded = base64.b64decode(cookies).decode("utf-8")
            if "\t" in decoded or "youtube" in decoded.lower():
                cookies = decoded
        except Exception:
            pass
        cookie_file = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")
        Path(cookie_file).write_text(cookies, encoding="utf-8")
        opts["cookiefile"] = cookie_file

    proxy = os.environ.get("YOUTUBE_PROXY", "").strip()
    if proxy:
        opts["proxy"] = proxy

    return opts


def _is_bot_block(error: Exception) -> bool:
    msg = str(error).lower()
    return any(s in msg for s in ("sign in to confirm", "not a bot", "captcha", "403"))


def get_youtube_duration(url: str) -> float:
    import yt_dlp

    opts = _apply_evasion(_base_ydl_opts())
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("duration") or 0


def download_youtube(url: str, out_path: str):
    import yt_dlp

    opts = _apply_evasion(_base_ydl_opts())
    opts.update({
        # Prend la MEILLEURE vidéo disponible (jusqu'à 4K si la source
        # l'offre) + le meilleur audio, au lieu de se limiter à un mp4
        # potentiellement basse résolution.
        "format": "bestvideo+bestaudio/best",
        "outtmpl": out_path,
        "merge_output_format": "mp4",
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def has_audio_stream(video_path: str) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", video_path],
        capture_output=True, text=True, check=True,
    )
    return bool(result.stdout.strip())


def get_video_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def get_user_plan(supabase, user_id: str) -> str:
    res = supabase.table("profiles").select("plan").eq("user_id", user_id).maybe_single().execute()
    if res.data and res.data.get("plan") in PLAN_MONTHLY_LIMITS:
        return res.data["plan"]
    return "free"


def check_quota(supabase, user_id: str) -> str | None:
    """Retourne un message d'erreur si le quota du plan est atteint, sinon None."""
    plan = get_user_plan(supabase, user_id)
    limit = PLAN_MONTHLY_LIMITS[plan]

    query = supabase.table("clip_jobs").select("id", count="exact").eq("user_id", user_id)
    if plan == "free":
        # Plan gratuit : quota à vie ("3 vidéos offertes"), pas remis à zéro.
        used = query.execute().count or 0
        period_label = ""
    else:
        # Plans payants : quota remis à zéro chaque mois calendaire.
        month_start = time.strftime("%Y-%m-01T00:00:00Z", time.gmtime())
        used = query.gte("created_at", month_start).execute().count or 0
        period_label = " ce mois-ci"

    if used >= limit:
        return (
            f"Tu as atteint la limite de {limit} vidéos{period_label} pour le plan "
            f"'{plan}'. Contacte-nous pour passer à un plan supérieur."
        )
    return None


def transcribe(video_path: str):
    """Transcrit l'audio avec AssemblyAI. Retourne les mots horodatés
    (en secondes) et le texte complet.

    Note : la fonctionnalité "Auto Highlights" d'AssemblyAI n'est pas
    disponible en français, donc les mots-clés à mettre en emphase sont
    déterminés par Claude (voir select_clips_with_llm) plutôt que par
    AssemblyAI."""
    import assemblyai as aai

    aai.settings.api_key = os.environ["ASSEMBLYAI_API_KEY"]
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(
        video_path,
        config=aai.TranscriptionConfig(language_code="fr"),
    )

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


# Options de personnalisation des sous-titres proposées à l'utilisateur.
# On ne fait jamais confiance à des valeurs libres venant du site : seuls
# ces presets whitelistés peuvent être choisis, pour éviter d'injecter du
# contenu arbitraire dans le fichier .ass passé à FFmpeg.
SUBTITLE_COLOR_PRESETS = {
    "blanc": "FFFFFF",
    "jaune": "FFEB3B",
    "rose": "F43F8E",
    "cyan": "22E5FF",
    "vert": "22FF88",
}
SUBTITLE_POSITION_PRESETS = {"bas": 2, "milieu": 5, "haut": 8}
SUBTITLE_SIZE_PRESETS = {"petit": 32, "moyen": 44, "grand": 58}

DEFAULT_SUBTITLE_STYLE = {
    "textColor": "blanc",
    "position": "bas",
    "size": "moyen",
}


def _hex_to_ass_color(hex_rgb: str) -> str:
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    return f"&H00{b}{g}{r}&"


def resolve_subtitle_style(raw_style: dict | None) -> dict:
    """Valide et complète le style de sous-titres choisi par l'utilisateur
    avec les valeurs par défaut, en rejetant toute valeur hors whitelist."""
    raw_style = raw_style or {}
    style = dict(DEFAULT_SUBTITLE_STYLE)
    for key in style:
        value = raw_style.get(key)
        if key == "textColor" and value in SUBTITLE_COLOR_PRESETS:
            style[key] = value
        elif key == "position" and value in SUBTITLE_POSITION_PRESETS:
            style[key] = value
        elif key == "size" and value in SUBTITLE_SIZE_PRESETS:
            style[key] = value
    return style


def build_ass_header(style: dict) -> str:
    text_color = _hex_to_ass_color(SUBTITLE_COLOR_PRESETS[style["textColor"]])
    font_size = SUBTITLE_SIZE_PRESETS[style["size"]]
    alignment = SUBTITLE_POSITION_PRESETS[style["position"]]
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},{text_color},&H00000000,&H00000000,0,3,3,0,{alignment},20,20,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_subtitles(words: list, segments: list, path: str, style: dict):
    """Génère un fichier de sous-titres .ass, avec les horodatages remappés
    sur le montage compressé (silences retirés). TOUS les mots ont la même
    couleur (celle choisie par l'utilisateur) — aucune mise en emphase par
    mot, pour un rendu uniforme."""
    lines = [build_ass_header(style)]
    chunk = []

    def flush():
        if not chunk:
            return
        start = remap_time(chunk[0]["start"], segments)
        end = remap_time(chunk[-1]["end"], segments)
        text = " ".join(w["word"] for w in chunk)
        lines.append(f"Dialogue: 0,{fmt_ass_time(start)},{fmt_ass_time(end)},Default,,0,0,0,,{text}")

    for w in words:
        chunk.append(w)
        if len(chunk) >= 6:
            flush()
            chunk = []
    flush()

    Path(path).write_text("\n".join(lines), encoding="utf-8")


# Résolutions de sortie disponibles (largeur x hauteur, format vertical 9:16).
OUTPUT_RESOLUTIONS = {
    "1080p": (1080, 1920),
    "4k": (2160, 3840),
}

# Résolution max autorisée par plan — le 4K est réservé au plan Équipe,
# quoi que le client réclame (jamais faire confiance à une entitlement
# envoyée par le navigateur).
PLAN_MAX_RESOLUTION = {"free": "1080p", "pro": "1080p", "equipe": "4k"}


def render_clip(source_path: str, clip: dict, words: list, style: dict, resolution: str, out_path: str):
    width, height = OUTPUT_RESOLUTIONS[resolution]

    with tempfile.TemporaryDirectory() as tmp:
        clip_words = words_in_range(words, clip["start"], clip["end"])
        segments = build_keep_segments(clip_words, clip["start"], clip["end"])

        subs_path = os.path.join(tmp, "subs.ass")
        write_subtitles(clip_words, segments, subs_path, style)

        # Un morceau de filtre par segment gardé (coupe les silences), puis
        # on les recolle (concat).
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
        # Cadrage vertical "cadre complet + fond flou" : au lieu de rogner le
        # centre en aveugle (ce qui coupe souvent le sujet), on affiche
        # l'image entière (aucun élément perdu), redimensionnée pour tenir
        # dans le 9:16, et on remplit le haut/bas avec une version zoomée et
        # floutée de la même image. Rendu professionnel, rien n'est coupé.
        filter_complex += (
            f";[catv]split=2[main][bg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height},gblur=sigma=25[bgb];"
            f"[main]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bgb][fg]overlay=(W-w)/2:(H-h)/2[comp];"
            f"[comp]unsharp=5:5:0.3:5:5:0.0,subtitles={subs_path}[outv]"
        )

        subprocess.run([
            "ffmpeg", "-y",
            "-i", source_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[cata]",
            # preset "slow" + CRF 18 = qualité nettement supérieure (moins
            # de compression, plus de détails) au prix d'un encodage plus long.
            "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            out_path,
        ], check=True, capture_output=True)


@app.function(image=image, secrets=[modal.Secret.from_name("sortclip-secrets")], timeout=2400)
def process_video(user_id: str, source_path: str, youtube_url: str | None = None, subtitle_style: dict | None = None):
    style = resolve_subtitle_style(subtitle_style)
    supabase = get_supabase_client()
    plan = get_user_plan(supabase, user_id)
    resolution = PLAN_MAX_RESOLUTION[plan]

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
                    if _is_bot_block(exc):
                        raise RuntimeError(
                            "YouTube a bloqué le téléchargement (protection anti-robot). "
                            "Importe plutôt le fichier vidéo directement depuis ton ordinateur."
                        ) from exc
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

            if not has_audio_stream(local_video):
                raise RuntimeError(
                    "Cette vidéo ne contient aucune piste audio exploitable — "
                    "impossible de générer des clips sans parole à transcrire."
                )

            words, full_text = transcribe(local_video)
            if not words:
                raise RuntimeError("Aucune parole détectée dans cette vidéo — impossible de générer des clips.")

            clips = select_clips_with_llm(words, full_text)

            rows = []
            for i, clip in enumerate(clips):
                try:
                    out_path = os.path.join(tmp, f"clip_{i}.mp4")
                    render_clip(local_video, clip, words, style, resolution, out_path)

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
        # Tout est enveloppé dans ce try/except : si une exception remonte
        # sans passer par ici, FastAPI renvoie une erreur 500 SANS les
        # en-têtes CORS (ils ne sont ajoutés qu'aux réponses "normales").
        # Le navigateur voit alors "Failed to fetch" / net::ERR_FAILED au
        # lieu du vrai message d'erreur — d'où ce filet de sécurité.
        try:
            auth_header = request.headers.get("authorization", "")
            token = auth_header.replace("Bearer ", "")
            auth_result = verify_user_token(token)
            if not auth_result:
                return JSONResponse({"error": "Non authentifié"}, status_code=401)
            user_id, email = auth_result

            supabase = get_supabase_client()
            if email not in TEST_ACCOUNT_EMAILS:
                quota_error = check_quota(supabase, user_id)
                if quota_error:
                    return JSONResponse({"error": quota_error}, status_code=403)

            subtitle_style = resolve_subtitle_style(payload.get("subtitleStyle"))

            youtube_url = payload.get("youtubeUrl")
            if youtube_url:
                # La validation de la durée est faite dans process_video (en
                # arrière-plan) : yt-dlp est trop lent pour bloquer la réponse
                # HTTP ici, et le site attendrait sans retour visuel.
                source_path = f"{user_id}/youtube_{int(time.time())}"
                process_video.spawn(user_id=user_id, source_path=source_path, youtube_url=youtube_url, subtitle_style=subtitle_style)
                return {"status": "processing_started", "sourcePath": source_path}

            source_path = payload["path"]
            if not source_path.startswith(f"{user_id}/"):
                return JSONResponse({"error": "Chemin invalide"}, status_code=403)

            process_video.spawn(user_id=user_id, source_path=source_path, subtitle_style=subtitle_style)
            return {"status": "processing_started", "sourcePath": source_path}
        except Exception as exc:
            print(f"[handle] Erreur non gérée : {exc}")
            return JSONResponse({"error": f"Erreur serveur : {exc}"}, status_code=500)

    return web_app


@app.function(image=image, secrets=[modal.Secret.from_name("sortclip-secrets")])
@modal.asgi_app()
def billing():
    """Endpoints de facturation Stripe.
    - POST /checkout : crée une session de paiement Stripe pour un plan.
      Header requis : Authorization: Bearer <token utilisateur Supabase>
      Body attendu : {"plan": "pro"} ou {"plan": "equipe"}
      Retourne {"url": "https://checkout.stripe.com/..."} à rediriger.
    - POST /webhook : reçu par Stripe après un paiement réussi, met à jour
      le plan de l'utilisateur dans la table "profiles"."""
    import stripe
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

    web_app = FastAPI()
    web_app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @web_app.post("/checkout")
    async def checkout(payload: dict, request: Request):
        try:
            auth_header = request.headers.get("authorization", "")
            token = auth_header.replace("Bearer ", "")
            auth_result = verify_user_token(token)
            if not auth_result:
                return JSONResponse({"error": "Non authentifié"}, status_code=401)
            user_id, email = auth_result

            plan = payload.get("plan")
            product_id = next(
                (pid for pid, p in STRIPE_PRODUCT_TO_PLAN.items() if p == plan), None
            )
            if not product_id:
                return JSONResponse({"error": "Plan invalide"}, status_code=400)

            product = stripe.Product.retrieve(product_id, expand=["default_price"])
            if not product.default_price:
                return JSONResponse(
                    {"error": f"Le produit Stripe '{product_id}' n'a pas de prix par défaut configuré."},
                    status_code=500,
                )

            session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": product.default_price.id, "quantity": 1}],
                customer_email=email,
                client_reference_id=user_id,
                metadata={"user_id": user_id, "plan": plan},
                subscription_data={"metadata": {"user_id": user_id, "plan": plan}},
                success_url=f"{SITE_URL}/tarifs.html?paiement=succes",
                cancel_url=f"{SITE_URL}/tarifs.html?paiement=annule",
            )
            return {"url": session.url}
        except Exception as exc:
            print(f"[checkout] Erreur : {exc}")
            return JSONResponse({"error": f"Erreur serveur : {exc}"}, status_code=500)

    @web_app.post("/webhook")
    async def webhook(request: Request):
        payload_bytes = await request.body()
        sig_header = request.headers.get("stripe-signature", "")
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()

        if not webhook_secret:
            print("[webhook] STRIPE_WEBHOOK_SECRET absent — configure-le après avoir créé le webhook dans Stripe.")
            return JSONResponse({"error": "Webhook non configuré côté serveur."}, status_code=500)

        try:
            event = stripe.Webhook.construct_event(payload_bytes, sig_header, webhook_secret)
        except (ValueError, stripe.error.SignatureVerificationError) as exc:
            print(f"[webhook] Signature invalide : {exc}")
            return JSONResponse({"error": "Signature invalide"}, status_code=400)

        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            user_id = session.get("client_reference_id") or session.get("metadata", {}).get("user_id")
            plan = session.get("metadata", {}).get("plan")

            if user_id and plan in PLAN_MONTHLY_LIMITS:
                supabase = get_supabase_client()
                supabase.table("profiles").upsert({"user_id": user_id, "plan": plan}).execute()
                print(f"[webhook] Plan de {user_id} mis à jour vers '{plan}'.")

        elif event["type"] in ("customer.subscription.deleted", "customer.subscription.updated"):
            subscription = event["data"]["object"]
            user_id = subscription.get("metadata", {}).get("user_id")
            status = subscription.get("status")

            if user_id and status in ("canceled", "unpaid", "incomplete_expired"):
                supabase = get_supabase_client()
                supabase.table("profiles").upsert({"user_id": user_id, "plan": "free"}).execute()
                print(f"[webhook] Abonnement de {user_id} terminé — retour au plan 'free'.")

        return {"received": True}

    return web_app
