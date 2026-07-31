-- =====================================================================
-- SortClip — Origine de l'autorisation, obligatoire par projet
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
--
-- Garde-fou produit obligatoire avant tout traitement : chaque vidéo doit
-- déclarer sur quelle base elle a le droit d'être clippée. Choix imposé
-- (pas de texte libre pour l'origine elle-même, seulement pour le détail
-- des deux options qui en ont besoin) :
--   - own_content            : contenu propre à l'utilisateur
--   - official_program       : programme officiel — detail = nom du programme
--   - paid_campaign          : campagne rémunérée — detail = lien de la campagne
--   - written_authorization  : autorisation écrite de l'ayant droit
--
-- Ce champ sert AUSSI de fonctionnalité de suivi de revenus (rattacher un
-- clip à sa campagne) — stocké à la fois sur clip_jobs (la vidéo source) et
-- sur clips (chaque clip en hérite, pour pouvoir filtrer/grouper sans
-- remonter à la source). Colonnes NULLABLES : les lignes déjà existantes
-- n'ont pas ce champ et ne doivent pas être invalidées rétroactivement —
-- c'est la couche applicative (endpoint /process) qui le rend obligatoire
-- pour toute NOUVELLE soumission.
-- =====================================================================

alter table clip_jobs add column if not exists authorization_origin text;
alter table clip_jobs add column if not exists authorization_detail text;
alter table clips add column if not exists authorization_origin text;
alter table clips add column if not exists authorization_detail text;

alter table clip_jobs drop constraint if exists clip_jobs_authorization_origin_check;
alter table clip_jobs add constraint clip_jobs_authorization_origin_check
  check (authorization_origin is null or authorization_origin in (
    'own_content', 'official_program', 'paid_campaign', 'written_authorization'
  ));

alter table clips drop constraint if exists clips_authorization_origin_check;
alter table clips add constraint clips_authorization_origin_check
  check (authorization_origin is null or authorization_origin in (
    'own_content', 'official_program', 'paid_campaign', 'written_authorization'
  ));

-- reserve_processing_slot doit désormais persister l'origine sur clip_jobs
-- dès sa création (avant même que process_video ne démarre) — on remplace
-- la fonction (nouvelle arité) plutôt que de la modifier en place.
drop function if exists reserve_processing_slot(uuid, text, int, numeric, numeric);

create or replace function reserve_processing_slot(
  p_user_id uuid,
  p_source_path text,
  p_max_concurrent int,
  p_plan_minutes numeric,
  p_needed_minutes numeric,
  p_authorization_origin text,
  p_authorization_detail text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_active_count int;
  v_period_start date;
  v_minutes_used numeric;
  v_credit_minutes numeric;
  v_available numeric;
  v_job_id uuid;
begin
  perform pg_advisory_xact_lock(hashtext(p_user_id::text));

  select count(*) into v_active_count from clip_jobs
    where user_id = p_user_id and status = 'processing';
  if v_active_count >= p_max_concurrent then
    return jsonb_build_object('allowed', false, 'reason', 'too_many_concurrent');
  end if;

  if p_needed_minutes > 0 then
    select quota_period_start, minutes_used into v_period_start, v_minutes_used
      from profiles where user_id = p_user_id;
    if v_period_start is null or v_period_start < date_trunc('month', now())::date then
      v_minutes_used := 0;
    end if;

    select coalesce(sum(minutes_remaining), 0) into v_credit_minutes
      from credit_packs
      where user_id = p_user_id and expires_at > now() and minutes_remaining > 0;

    v_available := (p_plan_minutes - coalesce(v_minutes_used, 0)) + v_credit_minutes;

    if p_needed_minutes > v_available + 1e-6 then
      return jsonb_build_object(
        'allowed', false, 'reason', 'insufficient_minutes',
        'available', v_available
      );
    end if;
  end if;

  insert into clip_jobs (user_id, source_path, status, authorization_origin, authorization_detail)
    values (p_user_id, p_source_path, 'processing', p_authorization_origin, p_authorization_detail)
    returning id into v_job_id;

  return jsonb_build_object('allowed', true, 'job_id', v_job_id);
end;
$$;

revoke all on function reserve_processing_slot(uuid, text, int, numeric, numeric, text, text) from public;
grant execute on function reserve_processing_slot(uuid, text, int, numeric, numeric, text, text) to service_role;
