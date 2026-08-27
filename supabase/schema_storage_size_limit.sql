-- =====================================================================
-- SortClip — Relève la limite de taille de fichier des buckets Storage
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
--
-- Bug réel rencontré : l'upload de sauvegarde de la source (nécessaire pour
-- pouvoir retoucher un clip plus tard) échouait avec "Payload too large /
-- EntityTooLarge" sur un podcast de 72 Mo — le bucket "videos" avait encore
-- la limite par défaut de Supabase (50 Mo). Le traitement continuait quand
-- même (ce n'est pas bloquant), mais la source n'était alors JAMAIS
-- conservée : impossible de retoucher/rallonger le clip ensuite.
--
-- `MAX_UPLOAD_SIZE_BYTES` (index.html) autorise 5 Go côté client — on
-- aligne les buckets sur cette même limite pour que rien ne bloque en
-- silence entre les deux.
--
-- ATTENTION — ce que cette migration NE PEUT PAS garantir : il existe aussi
-- un plafond GLOBAL de taille de requête au niveau du PROJET Supabase
-- (Dashboard > Project Settings > Storage > "Global file size limit"),
-- indépendant de ce réglage par bucket, que cette commande SQL ne peut pas
-- changer. Ce plafond global prime toujours sur celui-ci. Sur le plan
-- gratuit il est généralement bloqué bas (~50 Mo) : si l'erreur persiste
-- après cette migration pour un gros fichier, c'est le signe qu'il faut
-- relever ce réglage dans le dashboard, ou passer sur un plan Supabase
-- supérieur (Pro) si le plan gratuit ne permet pas de le relever assez.
-- =====================================================================

update storage.buckets
  set file_size_limit = 5368709120   -- 5 Go, aligné sur MAX_UPLOAD_SIZE_BYTES (index.html)
  where id in ('videos', 'clips');
