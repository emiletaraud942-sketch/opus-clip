-- =====================================================================
-- SortClip — Catégories manuelles pour ranger les clips
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
--
-- Distinct du filtre par campagne (authorization_origin/detail, déclaré une
-- fois à l'upload) : ici l'utilisateur crée lui-même ses catégories et
-- range chaque clip dedans a posteriori, librement, à tout moment.
-- Gérée entièrement depuis le front via le client Supabase (RLS), comme le
-- reste des opérations directes sur `clips` (ex: le bouton "Protéger la
-- source") — aucun endpoint Modal nécessaire.
-- =====================================================================

create table if not exists clip_categories (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (char_length(trim(name)) > 0 and char_length(name) <= 60),
  created_at timestamptz not null default now(),
  unique (user_id, name)
);

alter table clip_categories enable row level security;

drop policy if exists "Users manage their own categories" on clip_categories;
create policy "Users manage their own categories"
  on clip_categories for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- category_id nullable : un clip sans catégorie reste valide (comportement
-- par défaut). ON DELETE SET NULL : supprimer une catégorie ne supprime
-- jamais les clips qui y étaient rangés, elle les rend juste "sans catégorie".
alter table clips add column if not exists category_id uuid references clip_categories(id) on delete set null;
