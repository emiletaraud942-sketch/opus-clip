// Supabase Edge Function : démarre le traitement d'une vidéo via l'API Vizard.
// Appelée par le site juste après un upload réussi dans le bucket "videos".
//
// Body attendu : { "path": "user_id/1699999_video.mp4" }
// Auth requise : header Authorization: Bearer <token utilisateur Supabase>
//
// Secrets requis (à définir avec `supabase secrets set`) :
//   VIZARD_API_KEY
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const VIZARD_CREATE_URL =
  "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/create";

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

  const { path } = await req.json();
  if (!path || !path.startsWith(`${user.id}/`)) {
    return new Response(JSON.stringify({ error: "Chemin de vidéo invalide" }), { status: 400 });
  }

  // Vizard a besoin d'une URL accessible pour récupérer la vidéo :
  // on génère une URL signée valable 2h sur le fichier privé dans "videos".
  const { data: signed, error: signError } = await supabase.storage
    .from("videos")
    .createSignedUrl(path, 60 * 60 * 2);

  if (signError || !signed) {
    return new Response(JSON.stringify({ error: "Impossible de générer l'URL de la vidéo" }), { status: 500 });
  }

  const vizardRes = await fetch(VIZARD_CREATE_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "VIZARDAI_API_KEY": Deno.env.get("VIZARD_API_KEY")!,
    },
    body: JSON.stringify({
      lang: "fr",
      preferLength: [0, 1], // laisse Vizard choisir des durées courtes et moyennes
      videoUrl: signed.signedUrl,
      videoType: 1, // 1 = fichier vidéo distant (remote file)
      ratioOfClip: 1, // 1 = format vertical 9:16
      subtitleSwitch: 1,
      headlineSwitch: 1,
    }),
  });

  const vizardData = await vizardRes.json();
  if (!vizardRes.ok || vizardData.code !== 2000) {
    return new Response(JSON.stringify({ error: vizardData.errMsg ?? "Erreur Vizard" }), { status: 502 });
  }

  const projectId = vizardData.projectId;

  await supabase.from("clip_jobs").insert({
    user_id: user.id,
    source_path: path,
    project_id: projectId,
    status: "processing",
  });

  return new Response(JSON.stringify({ projectId }), {
    headers: { "Content-Type": "application/json" },
  });
});
