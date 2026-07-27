-- =====================================================================
-- SortClip — Migration Phase 3 : télémétrie de coût
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
-- =====================================================================

-- Un enregistrement par traitement réussi : coût réel dérivé, pour valider
-- (ou corriger) COST_PER_SOURCE_MINUTE_EUR dans pricing_config.py.
create table if not exists processing_costs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete set null,
  source_id text,
  plan text,
  source_duration_s numeric,
  cost_llm_eur numeric,
  cost_transcription_eur numeric,
  cost_total_eur numeric,
  created_at timestamptz not null default now()
);
create index if not exists processing_costs_plan_idx on processing_costs (plan, created_at);

-- Écrit uniquement par le backend (service_role, qui contourne la RLS).
-- On active la RLS sans policy utilisateur : personne d'autre ne lit ces coûts.
alter table processing_costs enable row level security;


-- =====================================================================
-- VUE D'ADMINISTRATION — requêtes à lancer dans le SQL Editor
-- (À copier-coller au besoin ; ce ne sont pas des objets créés.)
-- =====================================================================

-- 1) Coût moyen par minute de source (le chiffre à comparer à 0,03 €) :
--   select
--     round(sum(cost_total_eur) / nullif(sum(source_duration_s) / 60.0, 0), 4)
--       as cout_moyen_par_minute_eur,
--     count(*) as nb_traitements,
--     round(sum(source_duration_s) / 3600.0, 1) as heures_totales
--   from processing_costs;

-- 2) Coût moyen par minute, PAR PLAN :
--   select plan,
--     count(*) as nb,
--     round(sum(cost_total_eur) / nullif(sum(source_duration_s) / 60.0, 0), 4)
--       as cout_min_eur,
--     round(avg(cost_total_eur), 4) as cout_moyen_par_video_eur
--   from processing_costs group by plan order by plan;

-- 3) Taux de consommation du quota par plan (minutes_used / quota du plan) :
--   select p.plan,
--     count(*) as nb_utilisateurs,
--     round(avg(p.minutes_used), 1) as minutes_utilisees_moy,
--     round(avg(p.minutes_used) / q.quota * 100, 1) as taux_conso_pct
--   from profiles p
--   join (values ('free',25),('starter',75),('pro',160),('equipe',450))
--        as q(plan, quota) on q.plan = p.plan
--   group by p.plan, q.quota order by p.plan;

-- 4) Marge réelle par plan (prix mensuel − coût moyen des traitements du mois) :
--   with couts as (
--     select plan, sum(cost_total_eur) as cout_mois, count(distinct user_id) as users
--     from processing_costs
--     where created_at >= date_trunc('month', now())
--     group by plan
--   )
--   select c.plan,
--     pr.prix as prix_mensuel_eur,
--     round(c.cout_mois / nullif(c.users, 0), 4) as cout_moy_par_user_eur,
--     round((pr.prix - c.cout_mois / nullif(c.users, 0)) / nullif(pr.prix, 0) * 100, 1)
--       as marge_pct
--   from couts c
--   join (values ('starter',9.99),('pro',19.0),('equipe',49.0))
--        as pr(plan, prix) on pr.plan = c.plan
--   order by c.plan;
