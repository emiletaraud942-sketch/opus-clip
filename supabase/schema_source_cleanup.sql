-- =====================================================================
-- SortClip — Nettoyage des sources anciennes
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
-- `protected` : quand true, la vidéo source du clip n'est JAMAIS supprimée
-- (le clip reste ajustable indéfiniment). À cocher via le bouton 🔒 dans
-- « Mes clips ».
-- =====================================================================

alter table clips add column if not exists protected boolean not null default false;
