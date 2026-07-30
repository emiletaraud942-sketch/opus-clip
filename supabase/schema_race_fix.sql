-- =====================================================================
-- SortClip — Correction de la course quota/concurrence (AUDIT.md #2)
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
--
-- Constat : process() vérifiait le quota (available_minutes) et la
-- concurrence (count_active_jobs) de façon SYNCHRONE, puis appelait
-- process_video.spawn() — le débit réel (reserve_minutes) et l'insertion de
-- clip_jobs n'avaient lieu que dans le worker spawné, après téléchargement.
-- Deux requêtes émises dans cette fenêtre (double-clic, deux onglets)
-- passaient TOUTES LES DEUX la vérification avant qu'aucune ne s'enregistre.
--
-- Correction : une fonction Postgres unique qui vérifie la concurrence ET
-- une estimation du quota, PUIS insère la ligne clip_jobs, le tout dans UNE
-- transaction sérialisée par utilisateur via pg_advisory_xact_lock (verrou
-- transactionnel : libéré automatiquement à la fin de l'appel, donc sûr même
-- avec un pooler en mode transaction — contrairement à pg_advisory_lock/
-- unlock qui suppose de garder la même connexion entre deux appels REST,
-- ce qu'un pooler ne garantit pas).
--
-- La vérification de quota ici reste une ESTIMATION (le client fournit la
-- durée pressentie, ou 0 pour un lien YouTube dont la durée n'est connue
-- qu'après téléchargement) : le contrôle AUTORITAIRE sur la durée réelle
-- (ffprobe) reste fait plus loin dans process_video, inchangé. Cette
-- fonction ferme la course sur la CONCURRENCE complètement, et RÉDUIT
-- fortement la fenêtre de course sur le quota (elle ne l'élimine pas dans
-- le cas YouTube, où la durée réelle n'est simplement pas encore connue au
-- moment de cet appel — impossible à fermer plus tôt sans télécharger la
-- vidéo avant de répondre à la requête HTTP).
-- =====================================================================

create or replace function reserve_processing_slot(
  p_user_id uuid,
  p_source_path text,
  p_max_concurrent int,
  p_plan_minutes numeric,
  p_needed_minutes numeric
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
  -- Sérialise tous les appels concurrents pour CET utilisateur. Verrou
  -- transactionnel (xact) : relâché automatiquement à la fin de cette
  -- transaction, jamais tenu entre deux appels HTTP séparés.
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
      -- La période a changé : le reset "paresseux" définitif reste fait par
      -- le code Python (_reset_period_if_needed) au moment du débit réel ;
      -- ici on ne fait que refléter que l'ancien solde ne compte plus.
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

  insert into clip_jobs (user_id, source_path, status)
    values (p_user_id, p_source_path, 'processing')
    returning id into v_job_id;

  return jsonb_build_object('allowed', true, 'job_id', v_job_id);
end;
$$;

-- Exécutable par le rôle service_role uniquement (appelé depuis le backend
-- Modal avec la clé service role) — les rôles anon/authenticated n'ont pas
-- besoin d'appeler cette fonction directement.
revoke all on function reserve_processing_slot(uuid, text, int, numeric, numeric) from public;
grant execute on function reserve_processing_slot(uuid, text, int, numeric, numeric) to service_role;
