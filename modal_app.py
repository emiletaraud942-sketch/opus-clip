"""
Pipeline de traitement vidéo Sortclip : AssemblyAI + LLM + FFmpeg.

Étapes pour une vidéo importée :
  1. Téléchargement depuis Supabase Storage (bucket "videos").
  2. Transcription avec horodatage mot par mot via l'API AssemblyAI.
  3. Des signaux objectifs sont extraits (CPU, sans abonnement) : pics
     d'énergie audio, rires probables, changements de plan, présence de
     visage. Ils guident et bonifient le scoring.
  3bis. Un LLM (Claude) lit la transcription indexée (+ les signaux) et
     choisit les meilleurs segments en INDEX DE MOTS, notés sur une rubrique
     à cinq critères ; le code convertit en horodatages et calcule un score
     pondéré. C'est une heuristique éditoriale, pas une vérité statistique.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import modal
from fastapi import Request
from fastapi.responses import JSONResponse

import pricing_config as pricing

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
        # Signaux objectifs (CPU, aucun abonnement) : énergie/rires audio,
        # changements de plan, présence de visage. Servent à guider et à
        # bonifier le scoring des clips (cf. bloc [SIGNAUX] du prompt B).
        "librosa==0.10.2",
        "numpy==1.26.4",
        "scenedetect==0.6.4",
        "opencv-python-headless==4.10.0.84",
    )
    # yt-dlp installé depuis GitHub (master) : YouTube change ses protections
    # anti-robot en permanence, une version PyPI figée casse vite.
    .pip_install("yt-dlp @ git+https://github.com/yt-dlp/yt-dlp.git")
    # Le module de config économique doit voyager avec l'app sur Modal.
    .add_local_python_source("pricing_config")
    # Le moteur EDL (schéma + compilateur FFmpeg + sous-titres) voyage aussi.
    .add_local_python_source("sortclip")
)

SOURCE_BUCKET = "videos"
CLIPS_BUCKET = "clips"
MAX_CLIPS_PER_VIDEO = 6

# Réalisateur LLM (cadrages/emphases auto sur chaque clip via le moteur EDL).
# DÉSACTIVÉ par défaut : c'est un appel LLM supplémentaire par clip (~1-2 c/clip).
# Passe à True pour activer les zooms automatiques sur les punchlines.
ENABLE_AUTO_DIRECTOR = False

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
    # Produit d'abonnement récurrent 9,99 €/mois pour le plan Starter.
    "prod_UxgpL4tLubRJHB": "starter",
}

# Pack crédits : ACHAT UNIQUE (mode payment, PAS un abonnement). On stocke le
# product_id ; le checkout lira son prix par défaut (Price « one-time » 7,90 €).
STRIPE_CREDIT_PACK_PRODUCT_ID = "prod_Uxgt60DpLToZJw"

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


def _apply_evasion(opts: dict, allow_proxy: bool = False) -> dict:
    """Ajoute les contournements anti-détection YouTube si les secrets
    correspondants sont fournis :
      - YOUTUBE_COOKIES : cookies d'un compte connecté (format Netscape
        cookies.txt). yt-dlp s'authentifie alors comme un vrai utilisateur.
        Appliqué à TOUS (aucun coût à l'usage).
      - YOUTUBE_PROXY : proxy résidentiel pour ne pas sortir depuis une IP de
        datacenter Modal, que YouTube bloque massivement. Format :
        http://user:pass@host:port. RÉSERVÉ au plan Équipe (`allow_proxy`),
        car c'est une ressource facturée à la bande passante — on ne la
        consomme que pour les abonnés qui la paient.
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

    if allow_proxy:
        proxy = os.environ.get("YOUTUBE_PROXY", "").strip()
        if proxy:
            opts["proxy"] = proxy

    return opts


def _is_bot_block(error: Exception) -> bool:
    msg = str(error).lower()
    return any(s in msg for s in ("sign in to confirm", "not a bot", "captcha", "403"))


def get_youtube_duration(url: str, allow_proxy: bool = False) -> float:
    import yt_dlp

    opts = _apply_evasion(_base_ydl_opts(), allow_proxy=allow_proxy)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("duration") or 0


def download_youtube(url: str, out_path: str, allow_proxy: bool = False):
    import yt_dlp

    opts = _apply_evasion(_base_ydl_opts(), allow_proxy=allow_proxy)
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
    if res.data and res.data.get("plan") in pricing.PLANS:
        return res.data["plan"]
    return "free"


# =====================================================================
# Quota en MINUTES DE SOURCE (Phase 1)
# Le décompte se fait en minutes analysées, pas en nombre de vidéos.
# Ordre de consommation : crédits (expiration la plus proche) d'abord,
# puis quota d'abonnement. Réservation-débit AVANT traitement, avec
# remboursement intégral en cas d'échec.
# =====================================================================

def _now():
    return datetime.now(timezone.utc)


def _period_start_str() -> str:
    return _now().strftime("%Y-%m-01")


def _reset_period_if_needed(supabase, user_id: str) -> float:
    """Remet le compteur d'abonnement à zéro si on a changé de mois.
    Retourne les minutes déjà consommées sur la période courante."""
    res = supabase.table("profiles").select("minutes_used,quota_period_start").eq("user_id", user_id).maybe_single().execute()
    row = res.data or {}
    period = _period_start_str()
    if str(row.get("quota_period_start")) != period:
        supabase.table("profiles").upsert(
            {"user_id": user_id, "minutes_used": 0, "quota_period_start": period}
        ).execute()
        return 0.0
    return float(row.get("minutes_used") or 0)


def _subscription_remaining(supabase, user_id: str, plan: str) -> float:
    used = _reset_period_if_needed(supabase, user_id)
    return max(0.0, pricing.plan_minutes(plan) - used)


def _active_packs(supabase, user_id: str) -> list:
    """Lots de crédits non expirés avec du solde, du plus proche à expirer
    au plus lointain (on consomme les périssables d'abord)."""
    now_iso = _now().isoformat()
    res = (
        supabase.table("credit_packs").select("*")
        .eq("user_id", user_id).gt("minutes_remaining", 0)
        .gte("expires_at", now_iso).order("expires_at").execute()
    )
    return res.data or []


def available_minutes(supabase, user_id: str, plan: str) -> float:
    credit = sum(float(p["minutes_remaining"]) for p in _active_packs(supabase, user_id))
    return _subscription_remaining(supabase, user_id, plan) + credit


def enforce_source_caps(plan: str, seconds: float):
    """Refuse une source trop longue. Plafond dur 4 h, puis plafond du plan."""
    if seconds > pricing.GLOBAL_MAX_SOURCE_SECONDS:
        raise RuntimeError(
            "Cette source dépasse 4 h : elle ne peut être traitée sur aucun plan. "
            "Découpe-la avant de l'envoyer."
        )
    cap = pricing.plan_max_video_seconds(plan)
    if seconds > cap:
        raise RuntimeError(
            f"Cette vidéo dure {int(seconds / 60)} min, au-delà de la limite de "
            f"{cap // 60} min de ton plan « {plan} ». Passe à un plan supérieur "
            "ou découpe la vidéo."
        )


def count_active_jobs(supabase, user_id: str) -> int:
    return (
        supabase.table("clip_jobs").select("id", count="exact")
        .eq("user_id", user_id).eq("status", "processing").execute().count or 0
    )


def reserve_minutes(supabase, user_id: str, plan: str, source_id: str, minutes: float) -> list:
    """Réserve `minutes` : crédits (expiration proche) d'abord, puis abonnement.
    Retourne les lignes usage_log réservées (pour commit ou remboursement)."""
    remaining = float(minutes)
    reservations = []
    for pack in _active_packs(supabase, user_id):
        if remaining <= 1e-9:
            break
        avail = float(pack["minutes_remaining"])
        take = min(remaining, avail)
        supabase.table("credit_packs").update(
            {"minutes_remaining": avail - take}
        ).eq("id", pack["id"]).execute()
        r = supabase.table("usage_log").insert({
            "user_id": user_id, "source_id": source_id, "minutes_debited": take,
            "debited_from": "credit_pack", "credit_pack_id": pack["id"], "status": "reserved",
        }).execute()
        reservations.append(r.data[0])
        remaining -= take
    if remaining > 1e-9:
        used = _reset_period_if_needed(supabase, user_id)
        supabase.table("profiles").upsert({
            "user_id": user_id, "minutes_used": used + remaining,
            "quota_period_start": _period_start_str(),
        }).execute()
        r = supabase.table("usage_log").insert({
            "user_id": user_id, "source_id": source_id, "minutes_debited": remaining,
            "debited_from": "subscription", "status": "reserved",
        }).execute()
        reservations.append(r.data[0])
    return reservations


def commit_reservations(supabase, reservations: list, source_seconds: float | None = None):
    for r in reservations:
        upd = {"status": "committed"}
        if source_seconds is not None:
            upd["source_duration_s"] = source_seconds
        supabase.table("usage_log").update(upd).eq("id", r["id"]).execute()


def refund_reservations(supabase, user_id: str, reservations: list):
    """Restitue intégralement les minutes réservées (échec du traitement)."""
    for r in reservations:
        if r.get("status") == "refunded":
            continue
        m = float(r["minutes_debited"])
        if r["debited_from"] == "credit_pack" and r.get("credit_pack_id"):
            pack = supabase.table("credit_packs").select("minutes_remaining").eq("id", r["credit_pack_id"]).maybe_single().execute()
            if pack.data:
                supabase.table("credit_packs").update(
                    {"minutes_remaining": float(pack.data["minutes_remaining"]) + m}
                ).eq("id", r["credit_pack_id"]).execute()
        else:
            used = _reset_period_if_needed(supabase, user_id)
            supabase.table("profiles").upsert({
                "user_id": user_id, "minutes_used": max(0.0, used - m),
                "quota_period_start": _period_start_str(),
            }).execute()
        supabase.table("usage_log").update({"status": "refunded"}).eq("id", r["id"]).execute()


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


# Bornes de durée d'un clip (en secondes). Un clip hors de ces bornes est
# soit corrigé (fin ramenée), soit rejeté.
MIN_CLIP_SECONDS = 15
MAX_CLIP_SECONDS = 90
# On vise environ un clip par tranche de cette durée de vidéo source.
TARGET_SECONDS_PER_CLIP = 75


def _target_clip_count(video_duration: float) -> int:
    """Nombre de clips visé selon la longueur de la vidéo (au moins 1)."""
    return max(1, min(MAX_CLIPS_PER_VIDEO, round(video_duration / TARGET_SECONDS_PER_CLIP)))


def _snap_to_words(start: float, end: float, words: list) -> tuple[float, float] | None:
    """Aligne les bornes proposées sur de vrais mots (début du 1er mot inclus,
    fin du dernier mot inclus) et vérifie que la durée est raisonnable.
    Retourne None si le segment est inexploitable."""
    inside = [w for w in words if w["end"] > start and w["start"] < end]
    if not inside:
        return None
    real_start = inside[0]["start"]
    real_end = inside[-1]["end"]
    # Si trop long, on ramène la fin pour tenir dans MAX_CLIP_SECONDS.
    if real_end - real_start > MAX_CLIP_SECONDS:
        real_end = real_start + MAX_CLIP_SECONDS
    if real_end - real_start < MIN_CLIP_SECONDS:
        return None
    return real_start, real_end


def _heuristic_clips(words: list, needed: int) -> list:
    """Solution de secours : découpe la transcription en `needed` segments
    consécutifs d'environ TARGET_SECONDS_PER_CLIP, alignés sur les mots.
    Utilisé quand l'IA renvoie trop peu de clips valides."""
    if not words:
        return []
    total = words[-1]["end"] - words[0]["start"]
    chunk = max(MIN_CLIP_SECONDS, min(MAX_CLIP_SECONDS, total / max(1, needed)))
    clips = []
    t = words[0]["start"]
    while t < words[-1]["end"] and len(clips) < needed:
        snapped = _snap_to_words(t, t + chunk, words)
        if snapped:
            s, e = snapped
            clips.append({
                "start": s, "end": e,
                "title": "Extrait", "score": 60, "reason": "Segment automatique",
            })
            t = e
        else:
            t += chunk
    return clips


# Pondération des cinq critères de la rubrique (cf. SPEC-MOTEUR-CLIPPING §6 /
# PROMPTS-CLAUDE prompt B). Le hook est le critère le plus déterminant.
SCORE_WEIGHTS = {
    "hook_strength": 0.30,
    "context_autonomy": 0.20,
    "narrative_completeness": 0.20,
    "emotional_intensity": 0.15,
    "engagement_trigger": 0.15,
}

# Rubrique de notation condensée depuis PROMPTS-CLAUDE (prompt B). Les règles
# anti-dérive sont conservées : découpe en INDEX DE MOTS (jamais en secondes),
# calibrage médiane ~45, séparation contexte/contenu, raisonnement avant notes,
# rejet possible.
CLIP_SCORING_SYSTEM = """Tu es directeur de création spécialisé en vidéo courte verticale (TikTok, Reels, Shorts). Dans une vidéo longue, tu reconnais les vingt secondes qui vont fonctionner et celles qui n'iront nulle part.

Un clip n'est pas un extrait : c'est une œuvre autonome qui se trouve avoir été découpée dans une vidéo longue. Le spectateur n'a jamais vu la source, ignore le contexte, et décide en 1,5 seconde s'il continue ou s'il scrolle. Juge toujours depuis cette position.

On te donne une transcription où CHAQUE MOT est indexé sous la forme (index)mot. Tu choisis les meilleurs moments et, pour chacun, tu proposes une découpe EN INDEX DE MOTS (jamais en secondes — le code convertit).

RUBRIQUE — cinq critères notés 0-100, ancrages contraignants :
1. hook_strength : force des 3 premières secondes de TA découpe. 0-20 remplissage/silence/"alors euh" ; 41-60 l'intérêt n'arrive qu'après 4-5s ; 81-100 les 3 premières secondes se suffisent (question, affirmation clivante, réaction, curiosité instantanée).
2. context_autonomy : se comprend sans la source. 0-20 pronom orphelin/"ce truc-là"/"comme je disais"/renvoi visuel absent ; 61-80 tout ce qu'il faut est dans le clip.
3. narrative_completeness : arc complet, chute incluse. 0-20 fragment ou chute hors du clip ; 61-80 mise en place→tension→chute bien proportionné ; 81-100 s'arrête pile sur le point fort.
4. emotional_intensity : pic émotionnel. 0-20 plat/informatif ; 81-100 éclat de rire, sidération, cri, silence gêné, ressenti en moins de 2s.
5. engagement_trigger : raison concrète de commenter/partager/sauvegarder. 0-20 rien à retenir ; 61-80 avis discutable ou phrase citable.

CALIBRAGE CONTRAIGNANT : la majorité des segments ne font pas de bons clips. Un candidat correct sans plus se note ~45 sur chaque critère. Au-delà de 75 tu affirmes qu'il est meilleur que 9 clips sur 10 — justifie-le. Noter généreusement rend le produit inutile.

DÉCOUPE : le vrai hook est souvent 3-6s après le début brut du moment. Ne commence jamais sur "alors","donc","euh","bah","en fait","du coup","voilà". Coupe immédiatement après le dernier mot fort, jamais sur une conjonction ou une transition.

ORDRE : remplis "reasoning" AVANT les notes. Observe d'abord, note ensuite."""


def extract_signals(video_path: str) -> dict:
    """Extrait des signaux objectifs de la vidéo (CPU, sans abonnement) :
    - pics d'énergie audio (moments forts) et rires probables ;
    - changements de plan (montage dynamique) ;
    - présence de visage à l'écran.
    Renvoie des listes d'horodatages (secondes). Ne fait jamais échouer le
    pipeline : en cas d'erreur, renvoie des listes vides."""
    signals = {
        "energy_peaks": [], "laughter": [], "shot_changes": [], "face_ratio": None,
    }

    # --- Audio : énergie + rires probables ---
    try:
        import numpy as np
        import librosa
        y, sr = librosa.load(video_path, sr=16000, mono=True)
        if len(y):
            hop = 512
            rms = librosa.feature.rms(y=y, hop_length=hop)[0]
            times = librosa.frames_to_time(range(len(rms)), sr=sr, hop_length=hop)
            if rms.size:
                thr = float(np.mean(rms) + 1.5 * np.std(rms))
                peaks = [round(float(t), 1) for t, v in zip(times, rms) if v > thr]
                # On dédoublonne les pics rapprochés (< 2 s).
                signals["energy_peaks"] = _dedupe_times(peaks, 2.0)
            # Rires probables : forte énergie + centroïde spectral élevé (rires
            # = bruit large bande aigu). Heuristique, pas une détection exacte.
            cent = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop)[0]
            if rms.size and cent.size:
                e_thr = float(np.mean(rms) + 1.0 * np.std(rms))
                c_thr = float(np.mean(cent) + 1.0 * np.std(cent))
                laughs = [round(float(t), 1) for t, e, c in zip(times, rms, cent)
                          if e > e_thr and c > c_thr]
                signals["laughter"] = _dedupe_times(laughs, 3.0)
    except Exception as exc:
        print(f"[extract_signals] audio ignoré: {exc}")

    # --- Changements de plan ---
    try:
        from scenedetect import detect, ContentDetector
        scenes = detect(video_path, ContentDetector())
        signals["shot_changes"] = [round(s.get_seconds(), 1) for s, _ in scenes]
    except Exception as exc:
        print(f"[extract_signals] scènes ignorées: {exc}")

    # --- Présence de visage (échantillonnage d'images) ---
    try:
        import cv2
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(cascade_path)
        cap = cv2.VideoCapture(video_path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        # On échantillonne ~1 image toutes les 2 s, plafonné à 60 échantillons.
        step = max(1, int(fps * 2))
        sampled = 0
        with_face = 0
        idx = 0
        while sampled < 60:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if len(detector.detectMultiScale(gray, 1.1, 5)):
                with_face += 1
            sampled += 1
            idx += step
            if total and idx >= total:
                break
        cap.release()
        if sampled:
            signals["face_ratio"] = round(with_face / sampled, 2)
    except Exception as exc:
        print(f"[extract_signals] visages ignorés: {exc}")

    return signals


def _dedupe_times(times: list, min_gap: float) -> list:
    """Garde un horodatage tous les `min_gap` secondes maximum."""
    out = []
    last = -1e9
    for t in sorted(times):
        if t - last >= min_gap:
            out.append(t)
            last = t
    return out


def _signals_in_range(signals: dict, start: float, end: float) -> dict:
    """Compte les signaux tombant dans [start, end] pour un clip donné."""
    def count(key):
        return sum(1 for t in signals.get(key, []) if start <= t <= end)
    return {
        "energy_peaks": count("energy_peaks"),
        "laughter": count("laughter"),
        "shot_changes": count("shot_changes"),
    }


def _indexed_transcript(words: list) -> str:
    """Transcription avec chaque mot préfixé de son index : (0)mot (1)mot ..."""
    return " ".join(f"({i}){w['word']}" for i, w in enumerate(words))


def _final_score(scores: dict) -> int:
    """Score final pondéré (0-100) à partir des cinq critères."""
    total = sum(SCORE_WEIGHTS[k] * float(scores.get(k, 45)) for k in SCORE_WEIGHTS)
    return int(round(total))


def _snap_indices_to_words(start_i: int, end_i: int, words: list) -> tuple[float, float] | None:
    """Convertit une découpe en index de mots vers des horodatages, en
    validant les bornes et la durée (protection anti-hallucination : le modèle
    ne produit jamais de secondes, seulement des index)."""
    n = len(words)
    if not (0 <= start_i < end_i < n):
        return None
    real_start = words[start_i]["start"]
    real_end = words[end_i]["end"]
    if real_end - real_start > MAX_CLIP_SECONDS:
        # On ramène la fin sur le dernier mot tenant dans MAX_CLIP_SECONDS.
        cutoff = real_start + MAX_CLIP_SECONDS
        for j in range(end_i, start_i, -1):
            if words[j]["end"] <= cutoff:
                real_end = words[j]["end"]
                break
    if real_end - real_start < MIN_CLIP_SECONDS:
        return None
    return real_start, real_end


def _signals_summary(signals: dict | None) -> str:
    """Résumé textuel des signaux objectifs, injecté dans le prompt de
    sélection pour orienter Claude vers les moments à forte charge."""
    if not signals:
        return ""
    peaks = signals.get("energy_peaks", [])
    laughs = signals.get("laughter", [])
    face = signals.get("face_ratio")
    if not (peaks or laughs or face is not None):
        return ""
    lines = ["[SIGNAUX OBJECTIFS] — mesures automatiques, à utiliser comme preuves :"]
    if laughs:
        lines.append(f"- Rires probables (secondes) : {', '.join(str(t) for t in laughs[:40])}")
    if peaks:
        lines.append(f"- Pics d'énergie audio (secondes) : {', '.join(str(t) for t in peaks[:40])}")
    if face is not None:
        lines.append(f"- Visage visible sur {int(face * 100)}% des images échantillonnées")
    lines.append(
        "Privilégie les moments qui contiennent un rire ou un pic d'énergie : "
        "ce sont des preuves objectives de charge émotionnelle. Un moment plat, "
        "sans aucun signal, doit être noté sévèrement sur l'intensité émotionnelle."
    )
    return "\n".join(lines) + "\n\n"


# Bonus déterministe ajouté au score final quand un clip contient des signaux
# objectifs (rire = preuve la plus forte). Plafonné pour rester dans 0-100.
SIGNAL_BONUS_LAUGHTER = 6
SIGNAL_BONUS_ENERGY = 3
SIGNAL_BONUS_MAX = 15


def _record_usage(usage_sink, model, response):
    """Ajoute les tokens facturés d'un appel au journal de coût (si fourni)."""
    if usage_sink is None:
        return
    try:
        usage_sink.append({
            "model": model,
            "input": getattr(response.usage, "input_tokens", 0) or 0,
            "output": getattr(response.usage, "output_tokens", 0) or 0,
        })
    except Exception:
        pass


def select_clips_with_llm(words: list, full_text: str, signals: dict | None = None, usage_sink: list | None = None, extra_guidance: str = "") -> list:
    """Demande à Claude d'évaluer les meilleurs moments selon la rubrique à
    cinq critères (cf. PROMPTS-CLAUDE prompt B), en découpe par INDEX DE MOTS.
    Les signaux objectifs (rires, énergie, plans) guident la sélection et
    bonifient le score. Convertit les index en horodatages réels, calcule un
    score pondéré, et complète avec des segments automatiques si trop peu."""
    from anthropic import Anthropic

    video_duration = words[-1]["end"] - words[0]["start"] if words else 0
    target = _target_clip_count(video_duration)

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    n = len(words)
    user_message = f"""VIDÉO SOURCE — transcription indexée ({n} mots) :

{_indexed_transcript(words)}

{_signals_summary(signals)}{extra_guidance}Sélectionne les {target} MEILLEURS moments (au maximum {MAX_CLIPS_PER_VIDEO}) qui feraient de bons clips autonomes. Il est valide d'en proposer moins si le reste est faible, mais vise {target}.

Pour chaque clip, la découpe est en INDEX DE MOTS (start_word_index / end_word_index) pris dans la transcription ci-dessus. La durée réelle du clip doit tenir entre {MIN_CLIP_SECONDS} et {MAX_CLIP_SECONDS} secondes.

Réponds UNIQUEMENT avec un tableau JSON, sans texte autour, de cette forme exacte :
[
  {{
    "reasoning": "ce que tu observes (hook, autonomie, arc, émotion, engagement) — AVANT les notes",
    "scores": {{"hook_strength": 60, "context_autonomy": 55, "narrative_completeness": 50, "emotional_intensity": 45, "engagement_trigger": 40}},
    "start_word_index": 12,
    "end_word_index": 88,
    "title": "titre accrocheur court",
    "truncated_payoff": false
  }}
]"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        system=[{
            "type": "text",
            "text": CLIP_SCORING_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_message}],
    )
    _record_usage(usage_sink, "claude-sonnet-4-5", response)

    raw = response.content[0].text.strip()
    start_idx = raw.find("[")
    end_idx = raw.rfind("]")

    llm_clips = []
    if start_idx != -1 and end_idx != -1:
        try:
            llm_clips = json.loads(raw[start_idx:end_idx + 1])
        except json.JSONDecodeError:
            llm_clips = []

    valid_clips = []
    for c in llm_clips:
        if not isinstance(c, dict):
            continue
        try:
            si = int(c["start_word_index"])
            ei = int(c["end_word_index"])
        except (KeyError, TypeError, ValueError):
            continue
        snapped = _snap_indices_to_words(si, ei, words)
        if not snapped:
            continue
        s, e = snapped
        scores = c.get("scores") if isinstance(c.get("scores"), dict) else {}
        # Flag truncated_payoff : la chute tombe hors du clip → on plafonne
        # la complétude narrative (cf. validations obligatoires du prompt B).
        if c.get("truncated_payoff"):
            scores["narrative_completeness"] = min(
                float(scores.get("narrative_completeness", 25)), 25
            )
        final = _final_score(scores) if scores else 60
        # Bonus déterministe : un clip qui contient des rires / pics d'énergie
        # est objectivement plus fort. Plafonné, score borné à 100.
        if signals:
            counts = _signals_in_range(signals, s, e)
            bonus = min(
                SIGNAL_BONUS_MAX,
                SIGNAL_BONUS_LAUGHTER * min(counts["laughter"], 2)
                + SIGNAL_BONUS_ENERGY * min(counts["energy_peaks"], 3),
            )
            final = min(100, final + bonus)
        valid_clips.append({
            "start": s, "end": e,
            "title": c.get("title", "Extrait"),
            "score": final,
            "reason": c.get("reasoning", ""),
        })

    # Si l'IA n'a pas fourni assez de clips exploitables, on complète avec
    # des segments automatiques pour ne jamais renvoyer un résultat vide/1s.
    if len(valid_clips) < target:
        existing = [(c["start"], c["end"]) for c in valid_clips]
        for hc in _heuristic_clips(words, target):
            overlaps = any(hc["start"] < e and hc["end"] > s for s, e in existing)
            if not overlaps:
                valid_clips.append(hc)
                existing.append((hc["start"], hc["end"]))
            if len(valid_clips) >= target:
                break

    if not valid_clips:
        raise RuntimeError("Aucun moment exploitable n'a été trouvé dans cette vidéo.")

    valid_clips.sort(key=lambda c: c["score"], reverse=True)
    return valid_clips[:MAX_CLIPS_PER_VIDEO]


def words_in_range(words: list, start: float, end: float) -> list:
    return [w for w in words if w["start"] >= start and w["end"] <= end]


# Prompt D (cf. PROMPTS-CLAUDE) : légende + hashtags TikTok. Modèle Haiku,
# peu coûteux — utilise la clé Anthropic existante, aucun nouvel abonnement.
TIKTOK_COPY_SYSTEM = """Tu écris la légende TikTok d'un clip déjà monté. Tu n'écris que ce qui l'accompagne.

La légende a un seul travail : donner une raison de commenter. Ce n'est PAS un résumé — le spectateur va voir la vidéo.

Ce qui fonctionne : une question directe qui appelle un avis ; une affirmation légèrement discutable ; une phrase qui crée un manque ; une accroche sur la personne à qui on enverrait ça.
À éviter : résumer le clip, "vous allez adorer", "regardez jusqu'à la fin", les emojis en rafale, le ton publicitaire, les majuscules criardes.

Règles : français naturel, parlé, tutoiement. Maximum 150 caractères, l'essentiel dans les 40 premiers. Un emoji maximum, seulement s'il ajoute quelque chose."""


def generate_tiktok_copy(clip_transcript: str, usage_sink: list | None = None) -> dict:
    """Génère une légende TikTok + hashtags pour un clip (prompt D). Renvoie
    {"caption": str, "hashtags": [str]}. Ne fait jamais échouer le montage :
    en cas d'erreur, renvoie des valeurs vides."""
    from anthropic import Anthropic

    clip_transcript = (clip_transcript or "").strip()
    if not clip_transcript:
        return {"caption": "", "hashtags": []}

    try:
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=[{
                "type": "text",
                "text": TIKTOK_COPY_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": (
                f"CLIP : {clip_transcript}\n\n"
                "Écris la légende. Réponds UNIQUEMENT avec un objet JSON de la forme "
                '{"caption": "...", "hashtags": ["motclé1", "motclé2", "motclé3"]} '
                "(3 à 5 hashtags sans le #)."
            )}],
        )
        _record_usage(usage_sink, "claude-haiku-4-5-20251001", response)
        raw = response.content[0].text.strip()
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e == -1:
            return {"caption": "", "hashtags": []}
        data = json.loads(raw[s:e + 1])
        caption = str(data.get("caption", ""))[:150]
        hashtags = [str(h).lstrip("#").strip() for h in data.get("hashtags", [])][:5]
        return {"caption": caption, "hashtags": [h for h in hashtags if h]}
    except Exception as exc:
        print(f"[generate_tiktok_copy] échec (ignoré): {exc}")
        return {"caption": "", "hashtags": []}


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
    "bold": False,     # activé uniquement par un preset (non réglable librement)
    "punchy": False,   # sous-titres mot par mot animés (preset viral)
}

# Presets « profil créateur » : appliqués À LA DEMANDE sur une génération
# (case à cocher côté site). Ils biaisent la SÉLECTION (moments à forte
# réaction, hooks courts) et le RENDU des sous-titres (gros, centrés, gras,
# animés mot par mot). C'est le creator_preset de la spec, en version simple.
CREATOR_PRESETS = {
    "viral": {
        "style": {"size": "grand", "position": "milieu", "bold": True, "punchy": True},
        "selection_guidance": (
            "PROFIL VIRAL demandé : privilégie AU MAXIMUM les moments à forte réaction "
            "— éclats de rire, punchlines, montée d'énergie, réactions vives, "
            "retournements de situation. Écarte les passages explicatifs, calmes ou "
            "informatifs. Choisis des hooks TRÈS courts : le clip doit accrocher dès la "
            "première seconde, quitte à entrer en plein milieu de l'action. Coupe pile "
            "sur le mot fort, jamais après. Préfère des clips courts et percutants."
        ),
    },
}


def _hex_to_ass_color(hex_rgb: str) -> str:
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    return f"&H00{b}{g}{r}&"


def resolve_subtitle_style(raw_style: dict | None, preset: str | None = None) -> dict:
    """Valide et complète le style de sous-titres choisi par l'utilisateur
    avec les valeurs par défaut, en rejetant toute valeur hors whitelist. Si un
    preset créateur est fourni, ses réglages sont appliqués PAR-DESSUS (bold,
    punchy, etc. ne sont réglables que via un preset, pas librement)."""
    raw_style = raw_style or {}
    style = dict(DEFAULT_SUBTITLE_STYLE)
    for key in ("textColor", "position", "size"):
        value = raw_style.get(key)
        if key == "textColor" and value in SUBTITLE_COLOR_PRESETS:
            style[key] = value
        elif key == "position" and value in SUBTITLE_POSITION_PRESETS:
            style[key] = value
        elif key == "size" and value in SUBTITLE_SIZE_PRESETS:
            style[key] = value
    if preset and preset in CREATOR_PRESETS:
        style.update(CREATOR_PRESETS[preset]["style"])
    return style


def build_ass_header(style: dict) -> str:
    text_color = _hex_to_ass_color(SUBTITLE_COLOR_PRESETS[style["textColor"]])
    font_size = SUBTITLE_SIZE_PRESETS[style["size"]]
    alignment = SUBTITLE_POSITION_PRESETS[style["position"]]
    bold = 1 if style.get("bold") else 0
    # En mode punchy : contour plus épais + ombre portée pour un rendu qui
    # ressort bien sur n'importe quel fond (style « clip viral »).
    outline = 5 if style.get("punchy") else 3
    shadow = 2 if style.get("punchy") else 0
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},{text_color},&H00000000,&H80000000,{bold},1,{outline},{shadow},{alignment},20,20,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_subtitles(words: list, segments: list, path: str, style: dict):
    """Génère un fichier de sous-titres .ass, avec les horodatages remappés
    sur le montage compressé (silences retirés). Couleur uniforme (celle
    choisie), aucune emphase par mot. En mode « punchy » (preset viral), on
    affiche 1-2 mots à la fois avec un léger fondu d'apparition — le rendu
    « mot par mot animé » des clips viraux."""
    punchy = bool(style.get("punchy"))
    group_size = 2 if punchy else 6
    fade = r"{\fad(80,0)}" if punchy else ""

    lines = [build_ass_header(style)]
    chunk = []

    def flush():
        if not chunk:
            return
        start = remap_time(chunk[0]["start"], segments)
        end = remap_time(chunk[-1]["end"], segments)
        text = " ".join(w["word"] for w in chunk)
        lines.append(f"Dialogue: 0,{fmt_ass_time(start)},{fmt_ass_time(end)},Default,,0,0,0,,{fade}{text}")

    for w in words:
        chunk.append(w)
        if len(chunk) >= group_size:
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


# Correspondance style utilisateur -> paramètres de sous-titres EDL.
_EDL_SIZE = {"petit": 44, "moyen": 60, "grand": 76}
_EDL_Y = {"bas": 0.80, "milieu": 0.50, "haut": 0.16}


def render_clip_edl(source_path: str, clip: dict, words: list, style: dict,
                    resolution: str, out_path: str, source_duration: float,
                    src_wh: tuple[int, int] | None, watermark: bool, tmp: str):
    """Rendu d'un clip via le MOTEUR EDL (sortclip). Construit un EDL déclaratif
    pour le segment, en applique le preset (viral -> « rythme », sinon
    « podcast_dynamique ») et les réglages de sous-titres choisis par l'utilisateur,
    génère les sous-titres ASS puis compile vers FFmpeg. Lève en cas d'échec
    (l'appelant retombe alors sur render_clip)."""
    from sortclip import build_edl, map_words_to_output, build_ass, render as edl_render, validate
    from sortclip.edl import Canvas

    clip_words = words_in_range(words, clip["start"], clip["end"])
    if not clip_words:
        raise RuntimeError("clip sans mots — repli sur le rendu classique")

    punchy = bool(style.get("punchy"))
    preset_name = "rythme" if punchy else "podcast_dynamique"
    sw, sh = (src_wh or (None, None))

    edl, cleaned = build_edl(
        source_path=source_path, source_duration=source_duration,
        words=clip_words, preset_name=preset_name, watermark=watermark,
        source_width=sw, source_height=sh,
    )

    width, height = OUTPUT_RESOLUTIONS[resolution]
    cap = edl.captions.model_copy(update={
        "primary": "#" + SUBTITLE_COLOR_PRESETS[style["textColor"]],
        "size": _EDL_SIZE[style["size"]],
        "y": _EDL_Y[style["position"]],
        "style": "karaoke" if punchy else "plain",
        "bold": bool(style.get("bold")),
        "words_per_line": 2 if punchy else 4,
    })
    edl = edl.model_copy(update={"canvas": Canvas(w=width, h=height, fps=30), "captions": cap})

    # Mots en temps de SORTIE : servent au réalisateur (index de mots) ET aux
    # sous-titres.
    words_out = map_words_to_output(cleaned, edl)

    # Réalisateur LLM (optionnel) : pose cadrages + emphases sur les moments
    # forts. Désactivé par défaut (coût par clip) ; ne fait jamais échouer.
    if ENABLE_AUTO_DIRECTOR:
        try:
            from anthropic import Anthropic
            from sortclip import director
            client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            events = director.direct(client, words_out)
            if events:
                edl = edl.model_copy(update={"events": events})
        except Exception as exc:
            print(f"[render_clip_edl] réalisateur ignoré : {exc}")

    # Le « videur » : écarte tout événement bancal AVANT le rendu (sans lever).
    edl, _issues = validate(edl, word_count=len(words_out))

    ass_path = os.path.join(tmp, f"{Path(out_path).stem}.ass")
    build_ass(words_out, edl.captions, edl.canvas, ass_path)

    result = edl_render(edl, out_path, ass_path=ass_path)
    if result.returncode != 0:
        raise RuntimeError(f"rendu EDL échoué : {result.stderr[-500:]}")

    # On renvoie l'EDL final (validé) + les mots nettoyés (temps source) : ils
    # sont persistés avec le clip pour permettre l'ajustement ultérieur.
    return edl, cleaned


def render_clip(source_path: str, clip: dict, words: list, style: dict, resolution: str, out_path: str, watermark: bool = False):
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
            f"[comp]unsharp=5:5:0.3:5:5:0.0,subtitles={subs_path}"
        )
        # Filigrane « Sortclip » pour le plan gratuit : texte semi-transparent
        # en bas à droite, taille proportionnelle à la largeur.
        if watermark:
            fontsize = max(24, width // 22)
            filter_complex += (
                f",drawtext=text='Sortclip':fontcolor=white@0.55:fontsize={fontsize}:"
                f"box=1:boxcolor=black@0.25:boxborderw=10:"
                f"x=w-tw-30:y=h-th-40"
            )
        filter_complex += "[outv]"

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
def process_video(user_id: str, source_path: str, youtube_url: str | None = None, subtitle_style: dict | None = None, skip_billing: bool = False, preset: str | None = None):
    style = resolve_subtitle_style(subtitle_style, preset=preset)
    supabase = get_supabase_client()
    plan = get_user_plan(supabase, user_id)
    resolution = PLAN_MAX_RESOLUTION[plan]
    watermark = pricing.PLANS.get(plan, pricing.PLANS["free"])["watermark"]
    reservations = []
    # Le proxy résidentiel (contournement fiable des blocages YouTube) est un
    # avantage du plan Équipe : ressource facturée à la bande passante, on ne
    # l'active que pour eux.
    allow_proxy = plan == "equipe"

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
                    yt_duration = get_youtube_duration(youtube_url, allow_proxy=allow_proxy)
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

                # Contrôle du plafond AVANT de télécharger (évite de rapatrier
                # une vidéo de plusieurs heures pour rien).
                if not skip_billing:
                    enforce_source_caps(plan, yt_duration)

                download_youtube(youtube_url, local_video, allow_proxy=allow_proxy)
            else:
                video_bytes = supabase.storage.from_(SOURCE_BUCKET).download(source_path)
                Path(local_video).write_bytes(video_bytes)

            # Durée réelle et autoritaire de la source téléchargée.
            duration = get_video_duration(local_video)
            if not skip_billing:
                enforce_source_caps(plan, duration)

            # Dimensions source (une seule sonde) pour le moteur EDL : évite
            # que le compilateur ne relance ffprobe à chaque clip.
            try:
                from sortclip.compile import probe_source
                src_wh = probe_source(local_video)
            except Exception as exc:
                print(f"[process_video] sonde dimensions échouée (non bloquant) : {exc}")
                src_wh = None

            if not has_audio_stream(local_video):
                raise RuntimeError(
                    "Cette vidéo ne contient aucune piste audio exploitable — "
                    "impossible de générer des clips sans parole à transcrire."
                )

            # Réservation-débit AVANT le premier poste de coût (transcription).
            # Contrôle autoritaire du solde ici (notamment pour YouTube, sans
            # portée UX synchrone). En cas d'échec ultérieur, refund intégral.
            if not skip_billing:
                need_minutes = duration / 60.0
                if need_minutes > available_minutes(supabase, user_id, plan) + 1e-6:
                    raise RuntimeError(
                        f"Solde de minutes insuffisant : cette vidéo demande "
                        f"{need_minutes:.0f} min. Recharge avec le pack crédits "
                        "(60 min, 7,90 €) ou passe à un plan supérieur."
                    )
                reservations = reserve_minutes(supabase, user_id, plan, source_path, need_minutes)

            words, full_text = transcribe(local_video)
            if not words:
                raise RuntimeError("Aucune parole détectée dans cette vidéo — impossible de générer des clips.")

            # Signaux objectifs (CPU) : rires, énergie, plans, visage.
            # N'échoue jamais le pipeline (renvoie des listes vides sinon).
            signals = extract_signals(local_video)
            usage_sink = []   # journal des tokens LLM pour la télémétrie de coût
            preset_guidance = ""
            if preset and preset in CREATOR_PRESETS:
                preset_guidance = CREATOR_PRESETS[preset]["selection_guidance"] + "\n\n"
            clips = select_clips_with_llm(
                words, full_text, signals=signals, usage_sink=usage_sink,
                extra_guidance=preset_guidance,
            )

            rows = []
            for i, clip in enumerate(clips):
                try:
                    out_path = os.path.join(tmp, f"clip_{i}.mp4")
                    # Rendu via le moteur EDL ; repli sur l'ancien rendu si
                    # quoi que ce soit échoue, pour ne jamais perdre un clip.
                    clip_edl = None
                    clip_edl_words = None
                    try:
                        clip_edl, clip_edl_words = render_clip_edl(
                            local_video, clip, words, style, resolution, out_path,
                            source_duration=duration, src_wh=src_wh,
                            watermark=watermark, tmp=tmp,
                        )
                    except Exception as edl_exc:
                        print(f"[process_video] moteur EDL indisponible pour le clip {i}, repli : {edl_exc}")
                        render_clip(local_video, clip, words, style, resolution, out_path, watermark=watermark)

                    storage_path = f"{user_id}/{Path(source_path).stem}_clip{i}.mp4"
                    with open(out_path, "rb") as f:
                        supabase.storage.from_(CLIPS_BUCKET).upload(
                            storage_path, f, {"content-type": "video/mp4", "upsert": "true"}
                        )

                    # Légende + hashtags TikTok (prompt D, Haiku). Optionnel :
                    # si la clé est absente ou l'appel échoue, on renvoie vide.
                    clip_text = " ".join(
                        w["word"] for w in words_in_range(words, clip["start"], clip["end"])
                    )
                    copy = generate_tiktok_copy(clip_text, usage_sink=usage_sink)

                    rows.append({
                        "user_id": user_id,
                        "source_path": source_path,
                        "storage_path": storage_path,
                        "title": clip["title"],
                        "score": clip["score"],
                        "reason": clip.get("reason", ""),
                        "start_time": clip["start"],
                        "end_time": clip["end"],
                        "caption": copy["caption"],
                        "hashtags": copy["hashtags"],
                        # EDL persisté pour l'ajustement ultérieur (null si le
                        # clip est passé par le rendu de repli).
                        "edl": clip_edl.model_dump(mode="json") if clip_edl else None,
                        "edl_words": clip_edl_words,
                        "edl_resolution": resolution,
                    })
                except Exception as clip_exc:
                    # Un clip qui échoue au montage ne doit pas faire échouer
                    # toute la vidéo : on le saute et on continue les autres.
                    print(f"[process_video] Échec du montage du clip {i}: {clip_exc}")
                    continue

            if not rows:
                raise RuntimeError("Aucun clip n'a pu être monté avec succès pour cette vidéo.")

            _optional_cols = ("caption", "hashtags", "edl", "edl_words", "edl_resolution")
            try:
                supabase.table("clips").insert(rows).execute()
            except Exception as insert_exc:
                # Rétro-compatibilité : si des colonnes optionnelles (caption,
                # hashtags, edl…) n'existent pas encore (migration non appliquée),
                # on réessaie sans elles plutôt que de perdre tout le traitement.
                if any(c in str(insert_exc) for c in _optional_cols):
                    print(f"[process_video] colonnes optionnelles absentes, insertion sans: {insert_exc}")
                    stripped = [
                        {k: v for k, v in r.items() if k not in _optional_cols}
                        for r in rows
                    ]
                    supabase.table("clips").insert(stripped).execute()
                else:
                    raise

            # NOTE : on NE supprime PLUS la vidéo source après traitement — elle
            # est nécessaire pour ré-ajuster les clips (endpoint /adjust). Un
            # nettoyage périodique des sources anciennes pourra être ajouté.

        # Succès : on valide définitivement les minutes réservées.
        if reservations:
            commit_reservations(supabase, reservations, source_seconds=duration)

        # Télémétrie de coût RÉEL (Phase 3) : sert à valider ou invalider
        # COST_PER_SOURCE_MINUTE_EUR. N'échoue jamais le traitement.
        try:
            cost_llm = sum(
                pricing.llm_cost_eur(u["model"], u["input"], u["output"])
                for u in usage_sink
            )
            cost_transcription = pricing.transcription_cost_eur(duration)
            cost_total = cost_llm + cost_transcription
            supabase.table("processing_costs").insert({
                "user_id": user_id,
                "source_id": source_path,
                "plan": plan,
                "source_duration_s": duration,
                "cost_llm_eur": round(cost_llm, 5),
                "cost_transcription_eur": round(cost_transcription, 5),
                "cost_total_eur": round(cost_total, 5),
            }).execute()
        except Exception as tel_exc:
            print(f"[process_video] télémétrie de coût ignorée : {tel_exc}")

        supabase.table("clip_jobs").update({"status": "done"}).eq("source_path", source_path).eq("user_id", user_id).execute()
    except Exception as exc:
        # Échec, quelle qu'en soit la cause : remboursement INTÉGRAL des
        # minutes réservées. Un utilisateur ne perd jamais de minutes sur un bug.
        if reservations:
            try:
                refund_reservations(supabase, user_id, reservations)
            except Exception as refund_exc:
                print(f"[process_video] Échec du remboursement (à traiter manuellement): {refund_exc}")
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
            skip_billing = email in TEST_ACCOUNT_EMAILS
            plan = get_user_plan(supabase, user_id)

            # Garde-fou : traitements simultanés limités par plan.
            if not skip_billing and count_active_jobs(supabase, user_id) >= pricing.plan_concurrency(plan):
                return JSONResponse({
                    "error": (
                        f"Tu as déjà {pricing.plan_concurrency(plan)} traitement(s) en "
                        f"cours (maximum du plan « {plan} »). Attends qu'ils se terminent."
                    ),
                    "code": "too_many_concurrent",
                }, status_code=429)

            # Portée UX synchrone (upload) : le front envoie la durée lue via la
            # balise <video>. On refuse AVANT tout traitement si la vidéo dépasse
            # le plafond ou si le solde de minutes est insuffisant. Le contrôle
            # AUTORITAIRE (ffprobe/yt-dlp) est refait côté traitement.
            hint = payload.get("sourceDurationSeconds")
            if hint and not skip_billing:
                try:
                    seconds = float(hint)
                except (TypeError, ValueError):
                    seconds = 0
                if seconds > 0:
                    try:
                        enforce_source_caps(plan, seconds)
                    except RuntimeError as cap_err:
                        return JSONResponse(
                            {"error": str(cap_err), "code": "video_too_long"},
                            status_code=403,
                        )
                    need = seconds / 60.0
                    avail = available_minutes(supabase, user_id, plan)
                    if need > avail + 1e-6:
                        return JSONResponse({
                            "error": (
                                f"Il te reste {avail:.0f} min et cette vidéo en demande "
                                f"{need:.0f}. Recharge avec le pack crédits (60 min, 7,90 €) "
                                "ou passe à un plan supérieur."
                            ),
                            "code": "insufficient_minutes",
                            "proposePack": True,
                            "availableMinutes": round(avail, 1),
                            "neededMinutes": round(need, 1),
                        }, status_code=403)

            subtitle_style = resolve_subtitle_style(payload.get("subtitleStyle"))
            # Preset créateur optionnel, activé par génération (case à cocher).
            # Whitelisté : on n'accepte que des presets connus.
            preset = payload.get("preset")
            if preset not in CREATOR_PRESETS:
                preset = None

            youtube_url = payload.get("youtubeUrl")
            if youtube_url:
                # Pour YouTube, la durée n'est pas connue côté client : le
                # contrôle (plafond + solde) est fait dans process_video avant
                # tout traitement coûteux.
                source_path = f"{user_id}/youtube_{int(time.time())}"
                process_video.spawn(user_id=user_id, source_path=source_path, youtube_url=youtube_url, subtitle_style=subtitle_style, skip_billing=skip_billing, preset=preset)
                return {"status": "processing_started", "sourcePath": source_path}

            source_path = payload["path"]
            if not source_path.startswith(f"{user_id}/"):
                return JSONResponse({"error": "Chemin invalide"}, status_code=403)

            process_video.spawn(user_id=user_id, source_path=source_path, subtitle_style=subtitle_style, skip_billing=skip_billing, preset=preset)
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

            # --- Achat unique du pack crédits (mode payment) ---
            if plan == "credit_pack" or payload.get("creditPack"):
                if STRIPE_CREDIT_PACK_PRODUCT_ID.endswith("A_CREER"):
                    return JSONResponse(
                        {"error": "Le pack crédits n'est pas encore configuré côté Stripe."},
                        status_code=500,
                    )
                pack_product = stripe.Product.retrieve(
                    STRIPE_CREDIT_PACK_PRODUCT_ID, expand=["default_price"]
                )
                if not pack_product.default_price:
                    return JSONResponse(
                        {"error": "Le produit pack crédits n'a pas de prix par défaut configuré."},
                        status_code=500,
                    )
                session = stripe.checkout.Session.create(
                    mode="payment",
                    line_items=[{"price": pack_product.default_price.id, "quantity": 1}],
                    customer_email=email,
                    client_reference_id=user_id,
                    metadata={"user_id": user_id, "kind": "credit_pack"},
                    # On propage l'user_id sur le PaymentIntent pour le retrouver
                    # au webhook et garantir l'idempotence du crédit.
                    payment_intent_data={"metadata": {"user_id": user_id, "kind": "credit_pack"}},
                    success_url=f"{SITE_URL}/tarifs.html?paiement=succes",
                    cancel_url=f"{SITE_URL}/tarifs.html?paiement=annule",
                )
                return {"url": session.url}

            # --- Abonnement récurrent (starter / pro / equipe) ---
            product_id = next(
                (pid for pid, p in STRIPE_PRODUCT_TO_PLAN.items() if p == plan), None
            )
            if not product_id:
                return JSONResponse({"error": "Plan invalide"}, status_code=400)
            if product_id.endswith("A_CREER"):
                return JSONResponse(
                    {"error": f"Le plan « {plan} » n'est pas encore configuré côté Stripe."},
                    status_code=500,
                )

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

        supabase = get_supabase_client()

        # --- Idempotence : un même event Stripe ne doit être traité qu'une fois.
        # Stripe peut renvoyer un event plusieurs fois (retries). On enregistre
        # son id ; si l'insertion échoue (déjà présent), on ignore.
        event_id = event.get("id")
        if event_id:
            try:
                supabase.table("stripe_events").insert(
                    {"id": event_id, "type": event.get("type")}
                ).execute()
            except Exception:
                print(f"[webhook] Event {event_id} déjà traité — ignoré (doublon).")
                return {"received": True, "duplicate": True}

        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            user_id = session.get("client_reference_id") or session.get("metadata", {}).get("user_id")
            meta = session.get("metadata", {}) or {}

            if meta.get("kind") == "credit_pack" and user_id:
                # Achat unique du pack crédits : on crédite un NOUVEAU lot, avec
                # sa propre expiration (+90 j). Le payment_intent, unique, sert
                # de garde-fou d'idempotence de crédit (contrainte SQL unique).
                payment_intent_id = session.get("payment_intent")
                expires_at = (_now() + timedelta(days=pricing.CREDIT_PACK["validity_days"])).isoformat()
                try:
                    supabase.table("credit_packs").insert({
                        "user_id": user_id,
                        "minutes_purchased": pricing.CREDIT_PACK["minutes"],
                        "minutes_remaining": pricing.CREDIT_PACK["minutes"],
                        "expires_at": expires_at,
                        "stripe_payment_intent_id": payment_intent_id,
                    }).execute()
                    print(f"[webhook] Pack crédits crédité à {user_id} ({pricing.CREDIT_PACK['minutes']} min).")
                except Exception as exc:
                    # Doublon de payment_intent (contrainte unique) = déjà crédité.
                    print(f"[webhook] Pack crédits déjà crédité pour {payment_intent_id} — ignoré : {exc}")

            elif user_id and meta.get("plan") in pricing.PLANS:
                plan = meta["plan"]
                # Nouvel abonnement : on fixe le plan ET on repart d'un quota
                # mensuel neuf.
                supabase.table("profiles").upsert({
                    "user_id": user_id, "plan": plan,
                    "minutes_used": 0, "quota_period_start": _period_start_str(),
                }).execute()
                print(f"[webhook] Plan de {user_id} mis à jour vers '{plan}'.")

        elif event["type"] == "invoice.paid":
            # Renouvellement mensuel d'un abonnement : on remet à zéro le quota
            # d'abonnement. Les crédits achetés ne sont JAMAIS réinitialisés.
            invoice = event["data"]["object"]
            user_id = (invoice.get("metadata", {}) or {}).get("user_id")
            sub_id = invoice.get("subscription")
            if not user_id and sub_id:
                try:
                    sub = stripe.Subscription.retrieve(sub_id)
                    user_id = (sub.get("metadata", {}) or {}).get("user_id")
                except Exception as exc:
                    print(f"[webhook] invoice.paid : impossible de retrouver l'abonnement {sub_id} : {exc}")
            if user_id:
                supabase.table("profiles").upsert({
                    "user_id": user_id, "minutes_used": 0,
                    "quota_period_start": _period_start_str(),
                }).execute()
                print(f"[webhook] Quota mensuel de {user_id} réinitialisé (invoice.paid).")

        elif event["type"] in ("customer.subscription.deleted", "customer.subscription.updated"):
            subscription = event["data"]["object"]
            user_id = subscription.get("metadata", {}).get("user_id")
            status = subscription.get("status")

            if user_id and status in ("canceled", "unpaid", "incomplete_expired"):
                supabase.table("profiles").upsert({"user_id": user_id, "plan": "free"}).execute()
                print(f"[webhook] Abonnement de {user_id} terminé — retour au plan 'free'.")

        return {"received": True}

    return web_app


# =====================================================================
# Ajustement d'un clip existant (édition par texte, moteur EDL)
# =====================================================================

def _rerender_from_edl(edl_dict: dict, edl_words: list, resolution: str,
                       source_local: str, out_path: str, tmp: str):
    """Re-rend un clip à partir d'un EDL stocké + les mots + la source locale."""
    from sortclip import EDL, map_words_to_output, build_ass, render as edl_render, validate
    from sortclip.edl import Canvas

    edl = EDL.model_validate(edl_dict)
    # Réancre la source sur le fichier fraîchement téléchargé + la résolution.
    w, h = OUTPUT_RESOLUTIONS.get(resolution, (1080, 1920))
    edl = edl.model_copy(update={
        "source": edl.source.model_copy(update={"path": source_local}),
        "canvas": Canvas(w=w, h=h, fps=edl.canvas.fps),
    })
    edl, _ = validate(edl, word_count=len(edl_words or []))
    ass_path = os.path.join(tmp, "clip.ass")
    words_out = map_words_to_output(edl_words or [], edl)
    build_ass(words_out, edl.captions, edl.canvas, ass_path)
    result = edl_render(edl, out_path, ass_path=ass_path)
    if result.returncode != 0:
        raise RuntimeError(f"re-rendu échoué : {result.stderr[-500:]}")


@app.function(image=image, secrets=[modal.Secret.from_name("sortclip-secrets")], timeout=1200)
def rerender_clip(user_id: str, clip_id: str):
    """Re-génère la vidéo d'un clip depuis son EDL (après ajustement), la
    ré-uploade au même emplacement et incrémente edl_rev (signal de fin)."""
    supabase = get_supabase_client()
    row = (
        supabase.table("clips")
        .select("edl,edl_words,edl_resolution,source_path,storage_path,user_id,edl_rev")
        .eq("id", clip_id).maybe_single().execute().data
    )
    if not row or row.get("user_id") != user_id or not row.get("edl"):
        print(f"[rerender_clip] clip {clip_id} introuvable / non éligible")
        return
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "source.mp4")
            data = supabase.storage.from_(SOURCE_BUCKET).download(row["source_path"])
            Path(src).write_bytes(data)
            out = os.path.join(tmp, "clip.mp4")
            _rerender_from_edl(row["edl"], row.get("edl_words"),
                               row.get("edl_resolution") or "1080p", src, out, tmp)
            with open(out, "rb") as f:
                supabase.storage.from_(CLIPS_BUCKET).upload(
                    row["storage_path"], f, {"content-type": "video/mp4", "upsert": "true"}
                )
        supabase.table("clips").update(
            {"edl_rev": (row.get("edl_rev") or 0) + 1}
        ).eq("id", clip_id).execute()
        print(f"[rerender_clip] clip {clip_id} ré-rendu (rev {(row.get('edl_rev') or 0) + 1})")
    except Exception as exc:
        # Source probablement indisponible, ou rendu en échec. On journalise ;
        # le front finira par afficher un délai dépassé.
        print(f"[rerender_clip] échec pour {clip_id} : {exc}")


@app.function(image=image, secrets=[modal.Secret.from_name("sortclip-secrets")])
@modal.asgi_app()
def adjust():
    """Endpoint d'ajustement par texte d'un clip existant.
    Header : Authorization: Bearer <token>. Body : {"clipId": "...", "instruction": "..."}.
    Applique la consigne comme un PATCH sur l'EDL stocké (déterministe, ou LLM
    en repli), sauvegarde le nouvel EDL, et lance un re-rendu en arrière-plan."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    web_app = FastAPI()
    web_app.add_middleware(
        CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
        allow_methods=["POST", "OPTIONS"], allow_headers=["*"],
    )

    @web_app.post("/")
    async def handle(payload: dict, request: Request):
        try:
            auth_header = request.headers.get("authorization", "")
            auth_result = verify_user_token(auth_header.replace("Bearer ", ""))
            if not auth_result:
                return JSONResponse({"error": "Non authentifié"}, status_code=401)
            user_id, _email = auth_result

            clip_id = payload.get("clipId")
            instruction = (payload.get("instruction") or "").strip()
            edited_events = payload.get("events")   # édition visuelle (timeline)
            if not clip_id or (not instruction and edited_events is None):
                return JSONResponse({"error": "clipId et (instruction ou events) requis"}, status_code=400)

            supabase = get_supabase_client()
            row = (
                supabase.table("clips").select("edl,user_id,edl_rev")
                .eq("id", clip_id).maybe_single().execute().data
            )
            if not row or row.get("user_id") != user_id:
                return JSONResponse({"error": "Clip introuvable"}, status_code=404)
            if not row.get("edl"):
                return JSONResponse(
                    {"error": "Ce clip a été généré avant l'édition et ne peut pas être ajusté. Régénère-le pour l'activer."},
                    status_code=400,
                )

            from sortclip import EDL, apply_text_adjustment
            edl = EDL.model_validate(row["edl"])

            if edited_events is not None:
                # Édition visuelle : on remplace la liste d'événements par celle
                # de la timeline (chaque événement reconstruit et validé ; les
                # invalides sont ignorés). validate() finit le nettoyage au rendu.
                from sortclip.edl import (FramingEvent, EmphasisEvent,
                                          HoldOnSpeakerEvent, SpeedEvent)
                kinds = {"framing": FramingEvent, "emphasis": EmphasisEvent,
                         "hold_on_speaker": HoldOnSpeakerEvent, "speed": SpeedEvent}
                new_events = []
                for ev in edited_events:
                    cls = kinds.get((ev or {}).get("op"))
                    if not cls:
                        continue
                    try:
                        new_events.append(cls(**ev))
                    except Exception:
                        continue
                edl2 = edl.model_copy(update={"events": new_events})
                notes = [f"{len(new_events)} événement(s) enregistré(s)"]
            else:
                edl2, notes = apply_text_adjustment(edl, instruction)
                # Repli LLM si la consigne n'a pas été comprise par le déterministe.
                if any("non reconnue" in n for n in notes):
                    try:
                        from anthropic import Anthropic
                        from sortclip import director, apply_patch
                        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
                        ops = director.adjust_with_text(client, edl, instruction)
                        if ops:
                            res = apply_patch(edl, ops)
                            edl2, notes = res.edl, [f"{len(res.applied)} ajustement(s) appliqué(s)"]
                    except Exception as exc:
                        print(f"[adjust] repli LLM indisponible : {exc}")

            supabase.table("clips").update(
                {"edl": edl2.model_dump(mode="json")}
            ).eq("id", clip_id).execute()
            rerender_clip.spawn(user_id=user_id, clip_id=clip_id)

            return {"status": "processing", "notes": notes, "rev": row.get("edl_rev") or 0}
        except Exception as exc:
            print(f"[adjust] erreur : {exc}")
            return JSONResponse({"error": f"Erreur serveur : {exc}"}, status_code=500)

    return web_app


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("sortclip-secrets")],
    schedule=modal.Cron("0 * * * *"),   # toutes les heures
)
def cleanup_free_clips():
    """Supprime les clips des comptes GRATUITS de plus de 72 h (rétention du
    plan free), du bucket de stockage et de la table. L'avertissement se fait
    en amont, in-app, via le compte à rebours affiché sur chaque clip."""
    supabase = get_supabase_client()
    retention_h = pricing.plan_retention_hours("free")
    cutoff = (_now() - timedelta(hours=retention_h)).isoformat()

    free = supabase.table("profiles").select("user_id").eq("plan", "free").execute().data or []
    free_ids = [r["user_id"] for r in free]
    if not free_ids:
        return

    # On traite par lots pour rester léger (comptes gratuits potentiellement nombreux).
    total = 0
    for i in range(0, len(free_ids), 100):
        batch = free_ids[i:i + 100]
        old = (
            supabase.table("clips").select("id,storage_path")
            .in_("user_id", batch).lt("created_at", cutoff).execute().data or []
        )
        if not old:
            continue
        paths = [c["storage_path"] for c in old if c.get("storage_path")]
        if paths:
            try:
                supabase.storage.from_(CLIPS_BUCKET).remove(paths)
            except Exception as exc:
                print(f"[cleanup] échec suppression stockage (on continue) : {exc}")
        supabase.table("clips").delete().in_("id", [c["id"] for c in old]).execute()
        total += len(old)

    if total:
        print(f"[cleanup] {total} clip(s) gratuit(s) de plus de {retention_h} h supprimé(s).")


# Rétention des vidéos SOURCE (bucket "videos"). Les clips restent intacts ;
# on ne supprime que la vidéo longue d'origine, qui n'est utile que pour
# ré-ajuster un clip (timeline / texte). Passé ce délai, l'ajustement n'est
# plus possible pour ce clip — sauf s'il est marqué « protégé » (bouton 🔒).
RETENTION_SOURCE_DAYS = 30


def _list_source_files(supabase, prefix: str) -> list[dict]:
    """Liste récursivement les fichiers d'un dossier du bucket source."""
    out = []
    try:
        entries = supabase.storage.from_(SOURCE_BUCKET).list(prefix) or []
    except Exception as exc:
        print(f"[cleanup-src] list('{prefix}') a échoué : {exc}")
        return out
    for e in entries:
        name = e.get("name")
        if not name:
            continue
        full = f"{prefix}/{name}" if prefix else name
        # Un dossier n'a pas de métadonnées de fichier (id/metadata None).
        if e.get("id") is None and e.get("metadata") is None:
            out.extend(_list_source_files(supabase, full))
        else:
            out.append({"path": full, "meta": e})
    return out


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("sortclip-secrets")],
    schedule=modal.Cron("30 3 * * *"),   # une fois par jour, 03h30 UTC
)
def cleanup_old_sources():
    """Supprime du bucket « videos » les vidéos SOURCE de plus de
    RETENTION_SOURCE_DAYS jours, SAUF celles référencées par un clip protégé
    (clips.protected = true). Les clips eux-mêmes ne sont jamais touchés ici."""
    supabase = get_supabase_client()
    cutoff = _now() - timedelta(days=RETENTION_SOURCE_DAYS)

    # 1) Sources à protéger : source_path de tout clip marqué protégé.
    protected = (
        supabase.table("clips").select("source_path")
        .eq("protected", True).execute().data or []
    )
    protected_paths = {r["source_path"] for r in protected if r.get("source_path")}
    print(f"[cleanup-src] {len(protected_paths)} source(s) protégée(s).")

    # 2) Parcours des dossiers utilisateurs à la racine du bucket.
    try:
        roots = supabase.storage.from_(SOURCE_BUCKET).list("") or []
    except Exception as exc:
        print(f"[cleanup-src] impossible de lister le bucket : {exc}")
        return

    to_delete = []
    for r in roots:
        name = r.get("name")
        if not name or (r.get("id") is not None and r.get("metadata") is not None):
            # On ne s'intéresse qu'aux dossiers (un fichier à la racine est ignoré).
            continue
        for f in _list_source_files(supabase, name):
            path = f["path"]
            if path in protected_paths:
                continue
            meta = f.get("meta") or {}
            created = meta.get("created_at") or meta.get("updated_at")
            old = False
            if created:
                try:
                    ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    old = ts < cutoff
                except Exception:
                    old = False
            if old:
                to_delete.append(path)

    if not to_delete:
        print("[cleanup-src] aucune source ancienne à supprimer.")
        return

    for i in range(0, len(to_delete), 100):
        batch = to_delete[i:i + 100]
        try:
            supabase.storage.from_(SOURCE_BUCKET).remove(batch)
        except Exception as exc:
            print(f"[cleanup-src] échec suppression lot (on continue) : {exc}")
    print(f"[cleanup-src] {len(to_delete)} source(s) de plus de "
          f"{RETENTION_SOURCE_DAYS} j supprimée(s).")
