-- =====================================================================
-- SortClip — Transcript complet de la source (F6, AUDIT.md)
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
--
-- Jusqu'ici, seul le transcript RÉTRÉCI au clip (edl_words) était persisté —
-- rallonger un clip au-delà de sa sélection initiale aurait nécessité de
-- re-transcrire toute la vidéo (coût réel, contraire à l'invariant "les
-- retouches ne consomment jamais de minutes"). Le transcript complet est
-- déjà calculé une fois à la génération (transcribe()) : on le persiste
-- simplement pour pouvoir le réutiliser gratuitement.
-- =====================================================================

create table if not exists source_transcripts (
  source_path text primary key,
  words jsonb not null,
  created_at timestamptz not null default now()
);

alter table source_transcripts enable row level security;
-- Pas de policy utilisateur : lu et écrit UNIQUEMENT par le backend
-- (service_role, qui contourne la RLS) — le contenu n'a pas besoin d'être
-- exposé directement, seulement consommé côté serveur pour reconstruire un
-- clip étendu.
