-- =====================================================================
-- SortClip — Correction de la course sur le DÉBIT réel de quota
-- (audit complet du repo, finding #5). À exécuter dans Supabase :
-- SQL Editor > New query. Idempotent.
--
-- Constat : reserve_processing_slot() (schema_race_fix.sql) ferme la course
-- sur la CONCURRENCE et réduit la fenêtre sur une ESTIMATION de quota, au
-- moment de l'upload. Mais le débit RÉEL (durée ffprobe exacte, une fois la
-- vidéo téléchargée) restait fait par un simple couple lecture/écriture non
-- atomique en Python (available_minutes() puis reserve_minutes(), dans
-- process_video). Deux vidéos du même utilisateur, chacune individuellement
-- sous son quota, peuvent toutes deux lire le même solde disponible avant
-- que l'une ne débite réellement -> dépassement de quota possible.
--
-- Correction : même principe que reserve_processing_slot (verrou
-- transactionnel pg_advisory_xact_lock par utilisateur) mais pour le débit
-- réel — vérifie ET débite (crédits d'abord, puis abonnement) en une seule
-- transaction sérialisée. Remplace l'appel Python à available_minutes() +
-- reserve_minutes() dans process_video (voir modal_app.py).
-- =====================================================================

create or replace function reserve_real_minutes(
  p_user_id uuid,
  p_source_id text,
  p_needed_minutes numeric,
  p_plan_minutes numeric
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_period_start date;
  v_minutes_used numeric;
  v_credit_total numeric;
  v_available numeric;
  v_remaining numeric;
  v_pack record;
  v_take numeric;
  v_row_id uuid;
  v_reservations jsonb := '[]'::jsonb;
begin
  -- Sérialise tous les appels concurrents pour CET utilisateur — même
  -- verrou (par hash de user_id) que reserve_processing_slot, transactionnel
  -- (relâché automatiquement à la fin de l'appel, sûr avec un pooler en mode
  -- transaction).
  perform pg_advisory_xact_lock(hashtext(p_user_id::text));

  select quota_period_start, minutes_used into v_period_start, v_minutes_used
    from profiles where user_id = p_user_id;
  if v_period_start is null or v_period_start < date_trunc('month', now())::date then
    v_minutes_used := 0;
  end if;

  select coalesce(sum(minutes_remaining), 0) into v_credit_total
    from credit_packs
    where user_id = p_user_id and expires_at > now() and minutes_remaining > 0;

  v_available := (p_plan_minutes - coalesce(v_minutes_used, 0)) + v_credit_total;

  if p_needed_minutes > v_available + 1e-6 then
    return jsonb_build_object('allowed', false, 'reason', 'insufficient_minutes', 'available', v_available);
  end if;

  v_remaining := p_needed_minutes;

  -- Consomme les crédits périssables d'abord (du plus proche à expirer au
  -- plus lointain), même ordre que _active_packs() en Python.
  -- `for update` verrouille les lignes lues pour empêcher un autre appel
  -- concurrent (déjà exclu par le verrou avisory ci-dessus, mais garde-fou
  -- en cas d'appel hors de cette fonction).
  for v_pack in
    select id, minutes_remaining from credit_packs
      where user_id = p_user_id and expires_at > now() and minutes_remaining > 0
      order by expires_at asc
      for update
  loop
    exit when v_remaining <= 1e-9;
    v_take := least(v_remaining, v_pack.minutes_remaining);
    update credit_packs set minutes_remaining = minutes_remaining - v_take where id = v_pack.id;
    insert into usage_log (user_id, source_id, minutes_debited, debited_from, credit_pack_id, status)
      values (p_user_id, p_source_id, v_take, 'credit_pack', v_pack.id, 'reserved')
      returning id into v_row_id;
    v_reservations := v_reservations || jsonb_build_object(
      'id', v_row_id, 'minutes_debited', v_take, 'debited_from', 'credit_pack', 'credit_pack_id', v_pack.id
    );
    v_remaining := v_remaining - v_take;
  end loop;

  if v_remaining > 1e-9 then
    insert into profiles (user_id, minutes_used, quota_period_start)
      values (p_user_id, coalesce(v_minutes_used, 0) + v_remaining, date_trunc('month', now())::date)
      on conflict (user_id) do update
        set minutes_used = excluded.minutes_used, quota_period_start = excluded.quota_period_start;
    insert into usage_log (user_id, source_id, minutes_debited, debited_from, status)
      values (p_user_id, p_source_id, v_remaining, 'subscription', 'reserved')
      returning id into v_row_id;
    v_reservations := v_reservations || jsonb_build_object(
      'id', v_row_id, 'minutes_debited', v_remaining, 'debited_from', 'subscription', 'credit_pack_id', null
    );
  end if;

  return jsonb_build_object('allowed', true, 'reservations', v_reservations);
end;
$$;

revoke all on function reserve_real_minutes(uuid, text, numeric, numeric) from public;
grant execute on function reserve_real_minutes(uuid, text, numeric, numeric) to service_role;
