-- =====================================================================
-- SortClip — Lexique de corrections par utilisateur (A7)
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
--
-- Chaque correction de mot faite dans « Mes clips » (nom propre, jargon…)
-- est mémorisée ici et appliquée automatiquement à la transcription des
-- PROCHAINES vidéos du même utilisateur (avant la génération des clips,
-- sans jamais relancer la transcription elle-même).
-- =====================================================================

create table if not exists user_lexicon (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  wrong_word text not null,     -- forme fautive, normalisée (minuscules, sans ponctuation)
  right_word text not null,     -- forme corrigée telle que saisie par l'utilisateur
  created_at timestamptz not null default now(),
  unique (user_id, wrong_word)
);

alter table user_lexicon enable row level security;

drop policy if exists "Users read their own lexicon" on user_lexicon;
create policy "Users read their own lexicon"
  on user_lexicon for select
  using (auth.uid() = user_id);
