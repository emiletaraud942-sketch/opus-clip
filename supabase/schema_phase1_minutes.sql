-- =====================================================================
-- SortClip — Migration Phase 1 : quotas en MINUTES DE SOURCE
-- À exécuter dans Supabase : SQL Editor > New query.
-- Idempotent : peut être relancé sans erreur.
-- =====================================================================

-- 1) Nouveau plan 'starter' autorisé sur profiles ---------------------
alter table profiles drop constraint if exists profiles_plan_check;
alter table profiles add constraint profiles_plan_check
  check (plan in ('free', 'starter', 'pro', 'equipe'));

-- 2) Suivi du quota d'abonnement en minutes ---------------------------
-- minutes_used : minutes consommées sur la période en cours.
-- quota_period_start : 1er du mois de la période courante (remise à zéro
-- lazy quand le mois change, ou sur invoice.paid en Phase 2).
alter table profiles add column if not exists minutes_used numeric not null default 0;
alter table profiles add column if not exists quota_period_start date not null default date_trunc('month', now())::date;

-- 3) Lots de crédits (achat unique, expiration propre à chaque lot) ----
create table if not exists credit_packs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  minutes_purchased int not null,
  minutes_remaining numeric not null,
  purchased_at timestamptz not null default now(),
  expires_at timestamptz not null,
  stripe_payment_intent_id text unique,   -- idempotence de crédit
  created_at timestamptz not null default now()
);
create index if not exists credit_packs_user_active_idx
  on credit_packs (user_id, expires_at)
  where minutes_remaining > 0;

-- 4) Journal de consommation (source de vérité du débit + télémétrie) --
-- status : 'reserved' (débité avant traitement) -> 'committed' (succès)
--          ou 'refunded' (échec, minutes restituées).
create table if not exists usage_log (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  source_id text not null,
  minutes_debited numeric not null,
  debited_from text not null check (debited_from in ('subscription', 'credit_pack')),
  credit_pack_id uuid references credit_packs(id) on delete set null,
  status text not null default 'reserved' check (status in ('reserved', 'committed', 'refunded')),
  -- Télémétrie de coût (remplie en Phase 3, nullable ici) :
  source_duration_s numeric,
  cost_llm_eur numeric,
  cost_total_eur numeric,
  created_at timestamptz not null default now()
);
create index if not exists usage_log_user_idx on usage_log (user_id, created_at);
create index if not exists usage_log_source_idx on usage_log (source_id);

-- 5) Idempotence des webhooks Stripe ----------------------------------
create table if not exists stripe_events (
  id text primary key,               -- event id Stripe (evt_...)
  type text,
  processed_at timestamptz not null default now()
);

-- 6) RLS : l'utilisateur lit ses propres lignes -----------------------
alter table credit_packs enable row level security;
alter table usage_log enable row level security;

drop policy if exists "Users read their own credit packs" on credit_packs;
create policy "Users read their own credit packs"
  on credit_packs for select using (auth.uid() = user_id);

drop policy if exists "Users read their own usage" on usage_log;
create policy "Users read their own usage"
  on usage_log for select using (auth.uid() = user_id);
-- credit_packs / usage_log / stripe_events ne sont écrits que par le
-- service_role (backend Modal), qui contourne la RLS. Aucune policy d'écriture
-- pour les utilisateurs : ils ne peuvent pas se créditer eux-mêmes.

-- 7) Migration sans perte des abonnés existants -----------------------
-- Les anciens quotas étaient en vidéos (pro=30, equipe=60). On garantit que
-- personne ne reçoit MOINS que son nouveau quota mensuel en minutes. Comme le
-- suivi minutes démarre à zéro, il n'y a rien à réduire : on initialise juste
-- la période courante. (Aucune colonne "vidéos restantes" n'existait.)
update profiles
  set minutes_used = 0,
      quota_period_start = date_trunc('month', now())::date
  where quota_period_start is null;
