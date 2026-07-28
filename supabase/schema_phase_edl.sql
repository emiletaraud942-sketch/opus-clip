-- =====================================================================
-- SortClip — Migration : édition par texte (moteur EDL)
-- À exécuter dans Supabase : SQL Editor > New query. Idempotent.
-- Stocke l'EDL de chaque clip pour permettre l'ajustement ultérieur.
-- =====================================================================

alter table clips add column if not exists edl jsonb;            -- EDL déclaratif du clip
alter table clips add column if not exists edl_words jsonb;      -- transcript nettoyé (temps source)
alter table clips add column if not exists edl_resolution text;  -- "1080p" / "4k"
alter table clips add column if not exists edl_rev int not null default 0;  -- incrémenté à chaque re-rendu
