// Supabase Edge Function : interroge Vizard pour savoir si les clips
// d'un projet sont prêts, et les enregistre dans la table "clips".
//
// Body attendu : { "projectId": "..." }
// Auth requise : header Authorization: Bearer <token utilisateur Supabase>
//
// Secrets requis : VIZARD_API_KEY
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const VIZARD_QUERY_URL =
  "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/query";

Deno.serve(async (req) => {
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  const authHeader = req.headers.get("Authorization") ?? "";
  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_ANON_KEY")!,
    { global: { headers: { Authorization: authHeader } } },
  );

  const { data: { user }, error: authError } = await supabase.auth.getUser();
  if (authError || !user) {
    return new Response(JSON.stringify({ error: "Non authentifié" }), { status: 401 });
  }

  const { projectId } = await req.json();

  const { data: job } = await supabase
    .from("clip_jobs")
    .select("*")
    .eq("project_id", projectId)
    .eq("user_id", user.id)
    .single();

  if (!job) {
    return new Response(JSON.stringify({ error: "Projet introuvable" }), { status: 404 });
  }

  const vizardRes = await fetch(`${VIZARD_QUERY_URL}/${projectId}`, {
    headers: { "VIZARDAI_API_KEY": Deno.env.get("VIZARD_API_KEY")! },
  });
  const vizardData = await vizardRes.json();

  // code 2000 avec des vidéos = terminé ; sinon = encore en traitement.
  const videos = vizardData.videos ?? [];
  if (vizardData.code !== 2000 || videos.length === 0) {
    return new Response(JSON.stringify({ status: "processing" }), {
      headers: { "Content-Type": "application/json" },
    });
  }

  if (job.status !== "done") {
    const rows = videos.map((v: any) => ({
      user_id: user.id,
      project_id: projectId,
      video_id: v.videoId,
      title: v.title,
      video_url: v.videoUrl,
      viral_score: v.viralScore,
      viral_reason: v.viralReason,
      transcript: v.transcript,
    }));
    await supabase.from("clips").insert(rows);
    await supabase.from("clip_jobs").update({ status: "done" }).eq("project_id", projectId);
  }

  return new Response(JSON.stringify({ status: "done", clips: videos }), {
    headers: { "Content-Type": "application/json" },
  });
});
