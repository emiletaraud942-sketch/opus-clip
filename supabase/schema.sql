-- À exécuter dans Supabase : SQL Editor > New query.

-- Plan de chaque utilisateur : 'free' (défaut), 'pro' ou 'equipe'.
-- Tant qu'aucune facturation (Stripe) n'est branchée, on met à jour cette
-- valeur manuellement pour un utilisateur donné, ex :
--   update profiles set plan = 'pro' where user_id = '<uuid-utilisateur>';
create table if not exists profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  plan text not null default 'free' check (plan in ('free', 'pro', 'equipe')),
  created_at timestamptz not null default now()
);

alter table profiles enable row level security;

create policy "Users read their own profile"
  on profiles for select
  using (auth.uid() = user_id);

create table if not exists clip_jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  source_path text not null,
  status text not null default 'processing',
  error text,
  created_at timestamptz not null default now()
);

create table if not exists clips (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  source_path text not null,
  storage_path text not null,
  title text,
  score int,
  reason text,
  start_time numeric,
  end_time numeric,
  created_at timestamptz not null default now()
);

alter table clip_jobs enable row level security;
alter table clips enable row level security;

create policy "Users manage their own jobs"
  on clip_jobs for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "Users manage their own clips"
  on clips for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
