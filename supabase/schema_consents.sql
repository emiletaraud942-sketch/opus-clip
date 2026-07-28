-- =====================================================================
-- SortClip — Traçabilité des consentements (renonciation à la rétractation)
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
-- Conserve, côté serveur et horodatée, la demande expresse d'exécution
-- immédiate du service (art. L221-28 du Code de la consommation) recueillie
-- au moment du paiement. Écrite par le backend (service role) uniquement.
-- =====================================================================

create table if not exists consents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null default 'retractation_waiver',
  plan text,
  consented_at timestamptz not null default now(),
  ip text,
  user_agent text,
  created_at timestamptz not null default now()
);

create index if not exists consents_user_idx on consents (user_id, created_at desc);

alter table consents enable row level security;

-- L'utilisateur peut relire ses propres consentements ; l'écriture se fait
-- exclusivement côté serveur avec la clé service role (qui contourne la RLS).
drop policy if exists "Users read their own consents" on consents;
create policy "Users read their own consents"
  on consents for select
  using (auth.uid() = user_id);
