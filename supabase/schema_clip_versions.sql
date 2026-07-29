-- =====================================================================
-- SortClip — Historique des versions d'un clip (A4 : historique + annulation)
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
--
-- Chaque retouche (texte, timeline, ou retour à une version antérieure) crée
-- une NOUVELLE ligne ici — l'historique n'est jamais réécrit ni supprimé.
-- Écrit uniquement par le backend (clé service role, contourne la RLS) ;
-- lu directement par le front (RLS : un utilisateur ne voit que les versions
-- des clips qui lui appartiennent).
-- =====================================================================

create table if not exists clip_versions (
  id uuid primary key default gen_random_uuid(),
  clip_id uuid not null references clips(id) on delete cascade,
  version int not null,
  note text,                       -- consigne d'origine, ou « Retour à la version N »
  edl jsonb not null,
  edl_words jsonb,
  edl_resolution text,
  created_at timestamptz not null default now(),
  unique (clip_id, version)
);

create index if not exists clip_versions_clip_idx on clip_versions (clip_id, version desc);

alter table clip_versions enable row level security;

drop policy if exists "Users read their own clip versions" on clip_versions;
create policy "Users read their own clip versions"
  on clip_versions for select
  using (exists (
    select 1 from clips c where c.id = clip_versions.clip_id and c.user_id = auth.uid()
  ));
