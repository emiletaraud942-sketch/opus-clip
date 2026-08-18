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
-- `MAX_UPLOAD_SIZE_BYTES` (index.html) autorise déjà 2 Go côté client — on
-- aligne les buckets sur cette même limite pour que rien ne bloque en
-- silence entre les deux.
--
-- ATTENTION — ce que cette migration NE PEUT PAS garantir : sur le plan
-- Supabase gratuit, il existe aussi un plafond GLOBAL de taille de requête
-- imposé par la plateforme (indépendant de ce réglage par bucket), que
-- cette commande SQL ne peut pas changer. Si l'erreur persiste après cette
-- migration pour un gros fichier, c'est le signe qu'il faut passer sur un
-- plan Supabase supérieur (Pro) pour lever ce plafond global.
-- =====================================================================

update storage.buckets
  set file_size_limit = 2147483648   -- 2 Go, aligné sur MAX_UPLOAD_SIZE_BYTES (index.html)
  where id in ('videos', 'clips');
