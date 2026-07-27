-- =====================================================================
-- SortClip — Migration Phase 2 : offres Starter + Pack crédits (Stripe)
-- À exécuter dans Supabase : SQL Editor > New query.
-- Idempotent. La plupart des tables ont été créées en Phase 1
-- (schema_phase1_minutes.sql) ; ce fichier ne fait que RÉ-ASSURER les
-- contraintes dont la Phase 2 dépend. L'exécuter après la Phase 1.
-- =====================================================================

-- 1) Le plan 'starter' doit être autorisé sur profiles ----------------
alter table profiles drop constraint if exists profiles_plan_check;
alter table profiles add constraint profiles_plan_check
  check (plan in ('free', 'starter', 'pro', 'equipe'));

-- 2) Idempotence du crédit : un même paiement Stripe ne crédite qu'une
--    fois. La contrainte unique sur le payment_intent est la garde-fou.
--    (Déjà posée en Phase 1 ; ré-assurée ici si absente.)
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'credit_packs_stripe_payment_intent_id_key'
  ) then
    alter table credit_packs
      add constraint credit_packs_stripe_payment_intent_id_key
      unique (stripe_payment_intent_id);
  end if;
end $$;

-- 3) Idempotence des webhooks : table des events déjà traités ----------
create table if not exists stripe_events (
  id text primary key,
  type text,
  processed_at timestamptz not null default now()
);

-- Rien d'autre à migrer : credit_packs et usage_log existent déjà.
-- Aucun abonné existant ne perd de capacité (aucune colonne supprimée,
-- aucun quota réduit).
