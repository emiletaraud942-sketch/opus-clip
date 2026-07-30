-- =====================================================================
-- SortClip — Complète le modèle de coût (AUDIT.md #1)
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
--
-- processing_costs ne capturait que cost_llm_eur + cost_transcription_eur —
-- rien sur le calcul Modal (FFmpeg/CPU) ni le stockage. Ajoute les deux
-- colonnes manquantes ; cost_total_eur continuera d'être recalculé par le
-- backend (modal_app.py) pour inclure ces deux nouvelles composantes.
-- =====================================================================

alter table processing_costs add column if not exists cost_compute_eur numeric;
alter table processing_costs add column if not exists cost_storage_eur numeric;

-- Requête de suivi mise à jour (à relancer à la place de celle de
-- schema_phase3_telemetry.sql une fois ces colonnes peuplées) :
--   select
--     round(sum(cost_total_eur) / nullif(sum(source_duration_s) / 60.0, 0), 4)
--       as cout_moyen_par_minute_eur,
--     round(sum(cost_llm_eur) / nullif(sum(cost_total_eur), 0) * 100, 1) as part_llm_pct,
--     round(sum(cost_transcription_eur) / nullif(sum(cost_total_eur), 0) * 100, 1) as part_transcription_pct,
--     round(sum(cost_compute_eur) / nullif(sum(cost_total_eur), 0) * 100, 1) as part_calcul_pct,
--     round(sum(cost_storage_eur) / nullif(sum(cost_total_eur), 0) * 100, 1) as part_stockage_pct,
--     count(*) as nb_traitements
--   from processing_costs;
