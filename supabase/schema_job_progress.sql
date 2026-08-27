-- =====================================================================
-- SortClip — Progression du traitement (barre de progression front-end)
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
-- `progress` : pourcentage (0-100) de la génération en cours.
-- `stage`    : étape courante, affichée en clair côté front (voir
--              STAGE_LABELS dans index.html).
-- =====================================================================

alter table clip_jobs add column if not exists progress int not null default 0;
alter table clip_jobs add column if not exists stage text not null default 'queued';
