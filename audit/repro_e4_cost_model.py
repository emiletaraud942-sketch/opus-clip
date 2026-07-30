"""Audit E4 — Estimation ILLUSTRATIVE (pas une mesure réelle de production)
du coût LLM+transcription d'un traitement typique, avec les constantes RÉELLES
de pricing_config.py, pour comparer à l'hypothèse COST_PER_SOURCE_MINUTE_EUR.

Ce script ne mesure PAS le coût réel (aucune télémétrie de production
disponible ici) : il vérifie seulement si l'ordre de grandeur du modèle de
coût déclaré est plausible au vu des composants qu'il modélise RÉELLEMENT
(LLM + transcription) — et souligne par omission ce qu'il ne modélise PAS
(calcul FFmpeg/Modal, stockage, bande passante). Voir AUDIT.md § 3 constat #1."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pricing_config as pc

# Hypothèse illustrative : vidéo source de 10 minutes, ~150 mots/minute de
# parole (débit FR moyen) -> ~1500 mots. Transcript indexé envoyé au LLM de
# sélection : approx 1 token par mot + balises d'index -> ~2500 tokens
# d'entrée, réponse JSON de sélection ~800 tokens. 3 clips générés -> 3
# appels légende TikTok (haiku) ~400 tokens en entrée / 150 en sortie chacun.
source_min = 10.0
n_clips = 3

transcription = pc.transcription_cost_eur(source_min * 60)
selection_llm = pc.llm_cost_eur("claude-sonnet-4-5", 2500, 800)
caption_llm = n_clips * pc.llm_cost_eur("claude-haiku-4-5-20251001", 400, 150)
total = transcription + selection_llm + caption_llm

print(f"Transcription (AssemblyAI, {source_min:.0f} min)     : {transcription:.4f} EUR")
print(f"Sélection LLM (sonnet, 1 appel)             : {selection_llm:.4f} EUR")
print(f"Légendes TikTok (haiku, {n_clips} clips)          : {caption_llm:.4f} EUR")
print(f"TOTAL LLM+transcription (hors FFmpeg/stockage) : {total:.4f} EUR")
print(f"-> par minute de source : {total / source_min:.4f} EUR/min")
print(f"Hypothèse du modèle (pricing_config.COST_PER_SOURCE_MINUTE_EUR) : "
      f"{pc.COST_PER_SOURCE_MINUTE_EUR} EUR/min")
print()
print("Composants EXPLICITEMENT absents de ce calcul ET de processing_costs")
print("(modal_app.py:1673-1688, colonnes cost_llm_eur + cost_transcription_eur")
print("seulement) : temps de calcul Modal (FFmpeg, librosa, opencv), stockage")
print("Supabase, bande passante. Le modèle de coût ne peut donc PAS être validé")
print("par sa propre télémétrie tant que ces composants n'y sont pas ajoutés.")
