"""
Configuration économique centralisée de Sortclip.

Toutes les constantes qui pilotent le calcul des quotas et des marges vivent
ici — jamais dupliquées dans le code. Module PUR (aucun import de modal ni de
réseau) pour être testable unitairement et importable côté Modal.

La règle : les quotas sont exprimés en MINUTES DE SOURCE analysées, car c'est
la grandeur à laquelle les coûts d'infrastructure sont proportionnels (une
vidéo n'a pas de coût fixe, une minute de source si).

Le quota affiché est un PLAFOND DE RISQUE, pas une prévision de coût : il
suppose un taux moyen de consommation autour de 40 % (UTILIZATION_ASSUMPTION).
"""

import math

# --- Paramètres économiques (source de vérité unique) ---
COST_PER_SOURCE_MINUTE_EUR = 0.03   # coût tout compris estimé (LLM + transcription
                                    # + signaux + rendu + stockage). À valider par
                                    # la télémétrie (usage_log) — voir Phase 3.
TARGET_MARGIN = 0.87                # marge cible sur les abonnements
STRIPE_PCT = 0.015                  # commission Stripe variable
STRIPE_FIXED_EUR = 0.25             # commission Stripe fixe par paiement
UTILIZATION_ASSUMPTION = 0.40       # taux moyen de consommation du quota observé
QUOTA_MULTIPLIER = 2.5              # = 1 / UTILIZATION_ASSUMPTION (arrondi)


def _minutes_seuil(price_eur: float) -> float:
    """Minutes rentables à 100 % d'usage pour un prix donné (avant multiplieur).
    C'est le point mort : au-delà, un abonné consommant tout son quota coûte
    plus qu'il ne rapporte."""
    budget_cout = price_eur * (1 - TARGET_MARGIN)
    cout_stripe = price_eur * STRIPE_PCT + STRIPE_FIXED_EUR
    budget_calcul = budget_cout - cout_stripe
    return budget_calcul / COST_PER_SOURCE_MINUTE_EUR


def _round_presentable(raw: float) -> int:
    """Arrondit un quota brut à un nombre présentable. Le pas grandit avec la
    magnitude pour rester lisible en marketing (5 → 20 → 50 → 100)."""
    if raw < 100:
        step = 5
    elif raw < 200:
        step = 20
    elif raw < 500:
        step = 50
    else:
        step = 100
    return int(round(raw / step) * step)


def quota_from_price(price_eur: float) -> int:
    """Dérive le quota mensuel affiché (en minutes de source) depuis un prix.

    Le quota = minutes rentables × QUOTA_MULTIPLIER, arrondi à un nombre
    présentable. Sert à recalculer les quotas si COST_PER_SOURCE_MINUTE_EUR
    évolue. Reproduit exactement la table de référence du cahier des charges
    (voir test_pricing_config.py)."""
    return _round_presentable(_minutes_seuil(price_eur) * QUOTA_MULTIPLIER)


# --- Définition des plans ---
# minutes  : quota mensuel de source (0 = à définir hors formule)
# max_video_seconds : durée max d'UNE vidéo (garde-fou anti-abus)
# concurrency : nombre de traitements simultanés autorisés
# watermark : filigrane "Sortclip" sur les clips exportés
PRICE_PRO_EUR = 19.0
PRICE_EQUIPE_EUR = 49.0
PRICE_STARTER_EUR = 9.99

# Plafond dur, tous plans confondus : aucune source de plus de 4 h.
GLOBAL_MAX_SOURCE_SECONDS = 4 * 60 * 60

PLANS = {
    "free": {
        "price_eur": 0.0,
        "minutes": 25,
        "max_video_seconds": 30 * 60,
        "concurrency": 1,
        "watermark": True,
        "retention_hours": 72,
    },
    "starter": {
        "price_eur": PRICE_STARTER_EUR,
        "minutes": 75,   # = quota_from_price(9.99)
        "max_video_seconds": 90 * 60,
        "concurrency": 1,
        "watermark": False,
        "retention_hours": 30 * 24,
    },
    "pro": {
        "price_eur": PRICE_PRO_EUR,
        "minutes": quota_from_price(PRICE_PRO_EUR),   # 160
        "max_video_seconds": GLOBAL_MAX_SOURCE_SECONDS,
        "concurrency": 3,
        "watermark": False,
        "retention_hours": 30 * 24,
    },
    "equipe": {
        "price_eur": PRICE_EQUIPE_EUR,
        "minutes": quota_from_price(PRICE_EQUIPE_EUR),   # 450
        "max_video_seconds": GLOBAL_MAX_SOURCE_SECONDS,
        "concurrency": 10,
        "watermark": False,
        "retention_hours": 30 * 24,
    },
}

# Pack crédits : achat unique, marge volontairement plus basse (~82 %).
CREDIT_PACK = {
    "price_eur": 7.90,
    "minutes": 60,
    "validity_days": 90,
    "watermark": False,
    "max_video_seconds": 90 * 60,   # équivalent Starter
}


# --- Coûts réels observés (télémétrie Phase 3) ---
# Sert à VALIDER ou invalider COST_PER_SOURCE_MINUTE_EUR : on enregistre le coût
# réel de chaque traitement dans la table processing_costs, puis on compare.
USD_TO_EUR = 0.92

# Tarifs LLM par million de tokens (entrée, sortie), en USD.
LLM_PRICES_USD = {
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

# Transcription AssemblyAI : estimation par minute d'audio (USD). À ajuster
# selon ton offre réelle une fois la télémétrie observée.
ASSEMBLYAI_USD_PER_MINUTE = 0.0062


def llm_cost_eur(model: str, input_tokens: int, output_tokens: int) -> float:
    """Coût réel d'un appel LLM en euros, d'après les tokens facturés."""
    p_in, p_out = LLM_PRICES_USD.get(model, (3.0, 15.0))
    usd = (input_tokens / 1_000_000) * p_in + (output_tokens / 1_000_000) * p_out
    return usd * USD_TO_EUR


def transcription_cost_eur(source_seconds: float) -> float:
    return (source_seconds / 60.0) * ASSEMBLYAI_USD_PER_MINUTE * USD_TO_EUR


# --- Audit E4 (AUDIT.md #1) --------------------------------------------
# processing_costs ne capturait QUE cost_llm_eur + cost_transcription_eur —
# aucune ligne ne mesurait le calcul (FFmpeg/librosa/opencv, facturé par
# Modal au CPU-seconde/GPU-seconde) ni le stockage Supabase, alors que le
# calcul vidéo est très probablement la part dominante du coût réel d'un
# SaaS de traitement vidéo (bien plus que les tokens LLM — voir
# audit/repro_e4_cost_model.py : l'estimation LLM+transcription seule tombe
# à ~0,008 €/min, huit fois sous les 0,03 €/min supposés). Sans mesurer le
# calcul et le stockage, la validation de COST_PER_SOURCE_MINUTE_EUR par la
# télémétrie était structurellement impossible.
#
# {{À_COMPLÉTER}} : les deux tarifs ci-dessous sont des ESTIMATIONS à partir
# des tarifs publics Modal/Supabase au moment de l'écriture, PAS calibrées
# contre une vraie facture — à corriger dès qu'une facture Modal/Supabase
# réelle est disponible pour comparaison.
MODAL_CPU_EUR_PER_CPU_SECOND = 0.0000131 * USD_TO_EUR  # ~$0.0000131/vCPU-s (Modal, tarif CPU standard)
SUPABASE_STORAGE_EUR_PER_GB_MONTH = 0.021 * USD_TO_EUR  # ~$0.021/Go/mois (Supabase Storage)


def compute_cost_eur(wall_seconds: float, vcpus: float = 2.0) -> float:
    """Coût ESTIMÉ du calcul Modal (FFmpeg + signaux CPU) pour un traitement,
    à partir du temps mesuré (wall-clock) et d'une hypothèse de nombre de
    vCPU alloués (2 par défaut — à ajuster selon la config réelle de la
    fonction Modal). Ne couvre PAS un éventuel GPU (non utilisé aujourd'hui,
    voir modal_app.py : encodeur libx264 CPU par défaut)."""
    return wall_seconds * vcpus * MODAL_CPU_EUR_PER_CPU_SECOND


def storage_cost_eur(total_bytes: int, retention_days: float) -> float:
    """Coût ESTIMÉ du stockage Supabase pour la durée de rétention réelle
    (pas un mois entier si la rétention est plus courte, ex. 72h en gratuit)."""
    gb = total_bytes / (1024 ** 3)
    months = retention_days / 30.0
    return gb * months * SUPABASE_STORAGE_EUR_PER_GB_MONTH


def plan_minutes(plan: str) -> int:
    return PLANS.get(plan, PLANS["free"])["minutes"]


def plan_retention_hours(plan: str) -> int:
    return PLANS.get(plan, PLANS["free"])["retention_hours"]


def plan_max_video_seconds(plan: str) -> int:
    return PLANS.get(plan, PLANS["free"])["max_video_seconds"]


def plan_concurrency(plan: str) -> int:
    return PLANS.get(plan, PLANS["free"])["concurrency"]
