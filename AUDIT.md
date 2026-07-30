# AUDIT.md — Audit technique autonome de SortClip

Date : 2026-07-31. Auditeur : Claude (agent autonome), sans correctif appliqué.
Scripts de reproduction : `audit/repro_*.py` (exécutés réellement, sorties
citées ci-dessous). Aucun fichier du produit n'a été modifié pour cet audit.

**Limite structurelle de cet environnement, à lire avant tout le reste** :
cette exécution n'a accès ni à `ffmpeg`/`ffprobe`, ni à une instance Supabase
vivante, ni aux clés API réelles (Anthropic, AssemblyAI, Stripe). Tout ce qui
suit distingue explicitement :
- **VÉRIFIÉ PAR EXÉCUTION** — un script dans `audit/` a réellement tourné,
  sortie citée.
- **VÉRIFIÉ PAR LECTURE** — une lecture de code précise (fichier:ligne),
  logiquement concluante sans ambiguïté.
- **NON VÉRIFIÉ** — nécessite ffmpeg, un compte Supabase réel, ou un appel
  API réel ; expliqué au cas par cas, jamais comblé par une généralité.

---

## 1. Synthèse

SortClip est un pipeline de clipping vidéo fonctionnellement riche (moteur
EDL déclaratif propre, retouches sans coût de quota, historique de versions)
mais qui porte plusieurs défauts qui rendraient sa facturation à un
professionnel risquée en l'état : le modèle de coût qui doit valider la
grille tarifaire ignore la majorité du coût réel, deux chemins présentent une
course (quota et concurrence) exploitable par un simple double-clic, un
bypass de facturation nominatif traîne dans le code, et le sous-titrage n'échappe
aucun caractère spécial avant de l'écrire dans le fichier de rendu. L'outil de
retouche est solide sur son cœur (patchs ciblés, ids stables, jamais de
débit de quota — les trois vérifiés par exécution réelle) mais nettement
incomplet sur la promesse de couverture totale des décisions automatiques,
au premier rang desquelles le recadrage, qui ne peut être **repositionné**
par personne — ni l'IA, ni l'utilisateur — seulement zoomé.

**Les trois choses à corriger avant de facturer un professionnel :**
1. Le modèle de coût (`pricing_config.py` + `processing_costs`) ne mesure ni
   le calcul FFmpeg, ni le stockage/la bande passante — la tarification
   n'est aujourd'hui validée par AUCUNE mesure complète (§ E4).
2. La course entre la vérification de quota/concurrence (synchrone, dans
   l'endpoint HTTP) et le débit réel (asynchrone, dans le worker spawné)
   permet de dépasser son quota par un double-clic ou deux onglets (§ A3/E5).
3. Le fichier de sous-titres n'échappe jamais les caractères spéciaux ASS
   (`{`, `}`, `\`) présents dans un mot transcrit — prouvé par exécution,
   peut corrompre le rendu de façon imprévisible (§ C4).

**Taux de couverture de l'outil de retouche (domaine F)** : sur les ~40
décisions automatiques recensées dans la matrice F1, **19 sont modifiables
sans quota, 21 ne le sont pas ou seulement partiellement** — taux de
couverture approximatif **~48 %**. Le taux de symétrie (l'utilisateur peut
placer à la main tout ce que l'IA sait placer) est plus faible encore :
l'IA pose des cadrages et des emphases par API ; l'utilisateur peut les
modifier/déplacer/supprimer via l'interface timeline, mais ne peut pas
ajouter d'événement `hold_on_speaker`, ni positionner un sous-titre-cue
libre, ni importer une image — détail au § 4.

**Constats par gravité** : 4 BLOQUANT, 4 CRITIQUE, 15 MAJEUR, 2 MINEUR
(tableau complet § 3).

---

## 2. Carte du pipeline

Génération initiale (`modal_app.py`) :

| Étage | Fichier(s) | Fonction |
|---|---|---|
| 1. Réception | `modal_app.py:1707` (`process()`) | Endpoint HTTP, vérifie auth, quota (soft), concurrence, spawn `process_video` |
| 2. Téléchargement | `modal_app.py:1490-1534` | Storage Supabase ou yt-dlp (YouTube) |
| 3. Transcription | `modal_app.py:452` (`transcribe`) | API AssemblyAI, mots horodatés + confiance |
| 4. Signaux objectifs | `modal_app.py:603` (`extract_signals`) | librosa (énergie/rires), scenedetect (plans), opencv (visage) |
| 5. Sélection des clips | `modal_app.py:855` (`select_clips_with_llm`) | Claude, rubrique 5 critères, INDEX de mots ; repli `_heuristic_clips`/`_last_resort_clips` |
| 6. Construction EDL | `sortclip/build.py` (`build_edl`) | Nettoyage transcript, `keeps` (silences retirés), preset |
| 7. Réalisateur (optionnel, désactivé) | `sortclip/director.py` | Cadrages/emphases par LLM — `ENABLE_AUTO_DIRECTOR=False` (`modal_app.py:84`) |
| 8. Validation | `sortclip/validate.py` | Corrige/écarte les événements invalides, jamais ne lève |
| 9. Sous-titres | `sortclip/captions.py` (`build_ass`) | Fichier ASS, karaoké ou découpage FR |
| 10. Compilation FFmpeg | `sortclip/compile.py` (`build_filter_complex`, `render`) | Seul module autorisé à connaître FFmpeg |
| 11. Persistance | `modal_app.py:1434` (`_insert_clips_resilient`) | Upload bucket `clips`, insert table `clips` |

Retouche (`modal_app.py`) :

| Étage | Fichier(s) |
|---|---|
| Endpoint | `modal_app.py:2127` (`adjust()`) — texte, timeline, wordCorrections, revertToVersion |
| Patch déterministe | `sortclip/patch.py` (`apply_text_adjustment`) |
| Repli LLM | `sortclip/director.py` (`adjust_with_text`) |
| Historique | table `clip_versions`, jamais réécrite |
| Re-rendu | `modal_app.py:2091` (`rerender_clip`) → `_rerender_from_edl` |

Facturation/cycle de vie : `billing()` (webhook Stripe, `modal_app.py:1816`),
`cleanup_free_clips` (cron horaire), `cleanup_old_sources` (cron quotidien,
30 j), `reconcile_orphan_clips`, `transfer_clips` (utilitaires admin).

---

## 3. Constats

| # | Gravité | Effort | Domaine | Constat | Preuve |
|---|---|---|---|---|---|
| 1 | BLOQUANT | jours | E4 | Le modèle de coût ne mesure ni le calcul FFmpeg/Modal ni le stockage — la tarification n'est validée par aucune mesure complète | `modal_app.py:1673-1688`, `pricing_config.py` |
| 2 | BLOQUANT | jours | A3/E5 | Course entre la vérification de quota (synchrone) et le débit réel (asynchrone) — dépassement de quota possible par double-soumission | `modal_app.py:1770-1783` vs `reserve_minutes` dans `process_video` |
| 3 | BLOQUANT | heures | E5 | Bypass de facturation nominatif codé en dur, marqué "à vider" mais toujours présent | `modal_app.py:94` |
| 4 | BLOQUANT | jours | C4 | Aucun échappement des caractères spéciaux ASS dans le texte des sous-titres — prouvé par exécution | `audit/repro_c4_ass_escaping.py`, `sortclip/captions.py:206-232`, `modal_app.py:1248` |
| 5 | CRITIQUE | heures | E1 | Table `stripe_events` sans RLS activée — seule table du schéma dans ce cas | `supabase/schema_phase1_minutes.sql:55`, `schema_phase2_stripe.sql:29` |
| 6 | CRITIQUE | jours | E2 | Aucun code n'implémente la suppression de compte ni l'export de données promis par la politique de confidentialité | `politique-confidentialite.html:55` vs absence dans `modal_app.py` |
| 7 | CRITIQUE | semaines | F3 | Le recadrage est TOUJOURS centré — aucun champ de position dans l'EDL ni le compilateur ; personne (ni l'IA ni l'utilisateur) ne peut recentrer sur un sujet décalé | `sortclip/edl.py` (`FramingEvent`), `sortclip/compile.py:_crop_scale` |
| 8 | CRITIQUE | jours | A3 | Le compteur de jobs actifs (garde-fou de concurrence) est lu avant que le job spawné n'ait eu le temps de s'enregistrer — contournable par soumissions rapprochées | `modal_app.py:1743` vs `modal_app.py:1486` |
| 9 | MAJEUR | jours | E5 | Aucune limite de débit générale sur les points d'entrée coûteux (`/adjust` peut déclencher des appels LLM en rafale) | `modal_app.py:adjust()`, aucun limiteur importé dans tout le fichier |
| 10 | MAJEUR | jours | E5 | Aucune limite de taille de fichier (octets) côté serveur — seule la durée est plafonnée | `modal_app.py:enforce_source_caps`, `process()` |
| 11 | MAJEUR | jours | F8.2 | Aucun marqueur manuel/auto sur les événements — "moins de zooms" peut supprimer un cadrage posé à la main par l'utilisateur | `sortclip/edl.py:FramingEvent`, `sortclip/patch.py` |
| 12 | MAJEUR | semaines | F8.1 | Pas de sous-titre "cue" libre (calé à la ligne, sans mot source) ni d'ajout de mot manqué avec réalignement forcé | `sortclip/edl.py:Captions`, `modal_app.py:adjust()` (wordCorrections n'édite que des mots déjà indexés) |
| 13 | MAJEUR | jours | F7.2 | Aucune retouche par lot sur tous les clips d'une même source | `modal_app.py:adjust()` (un seul `clipId` par appel) |
| 14 | MAJEUR | semaines | F7.4 | Aucune génération de variantes bornées (le correcteur renvoie toujours un seul résultat) | `sortclip/patch.py`, `sortclip/director.py` |
| 15 | MAJEUR | jours | F7.1 | Préférence apprise limitée au lexique de mots — aucune mémorisation pour le style/cadrage | `modal_app.py:_apply_lexicon`, table `user_lexicon` |
| 16 | MAJEUR | semaines | F5/F8.3 | Aucun mixage de musique de fond, aucune image/logo importable, aucune image d'arrêt de fin, aucun fondu | Recherche exhaustive dans `sortclip/edl.py` (aucun champ correspondant) |
| 17 | MAJEUR | heures | F4/C5 | `canvas.platform` (zones de sécurité par plateforme) n'est jamais renseigné en dehors de sa valeur par défaut — la correction ne s'applique jamais en pratique | `grep canvas.platform` : aucune assignation dans `modal_app.py`/`mes-clips.html` |
| 18 | MAJEUR | semaines | F6 | Aucune opération dupliquer/scinder/fusionner/allonger un clip | Recherche exhaustive dans `modal_app.py` |
| 19 | MAJEUR | jours | A2 | Un seul délai global par fonction Modal (2400s / 1200s) — pas de délai par étage ; un blocage AssemblyAI ou Claude n'est pas isolé | `modal_app.py:1473`, `:2090` (`timeout=`) |
| 20 | MAJEUR | jours | C5 | Les seuils de zones de sécurité par plateforme sont explicitement non mesurés contre de vraies interfaces | `sortclip/safe_zones.py` (docstring `{{À_COMPLÉTER}}`) |
| 21 | MAJEUR | jours | B/H1 | Les seuils audio (loudness, de-esser, ducking, présence vocale) sont non validés à l'oreille et actuellement ACTIFS en production | `sortclip/compile.py` (constantes `{{À_COMPLÉTER}}`, `ENABLE_AUDIO_CHAIN_H1=True`) |
| 22 | MAJEUR | jours | B1 | Le moteur ASR réel (AssemblyAI) diffère de l'architecture Whisper implicitement supposée par les audits classiques — plusieurs leviers standards (conditionnement, VAD réglable, seuils de repli) n'existent simplement pas sur cette API | `modal_app.py:transcribe()` |
| 23 | MINEUR | heures | A4/D3 | Les identifiants d'événements sont des UUID aléatoires — deux traitements identiques ne produisent jamais un EDL byte-identique | `audit/repro_a4_determinism.py`, `sortclip/edl.py:new_id` |
| 24 | MINEUR | jours | F8.3 | Le filigrane est un texte de marque fixe, pas un logo image importable | `sortclip/edl.py:Watermark` |

### Fiches détaillées — BLOQUANT et CRITIQUE

**#1 — Modèle de coût incomplet (BLOQUANT, E4)**
Observé : `processing_costs.cost_total_eur` est calculé comme
`cost_llm + cost_transcription` (`modal_app.py:1674-1679`) — aucune ligne ne
capture le temps de calcul Modal (FFmpeg, librosa, opencv) ni le stockage/la
bande passante Supabase. `pricing_config.COST_PER_SOURCE_MINUTE_EUR = 0.03`
est explicitement commenté "à valider par la télémétrie" — mais la
télémétrie elle-même ne peut pas la valider puisqu'elle ignore la majorité
probable du coût réel (le calcul vidéo, pas les tokens LLM).
Reproduction (illustrative, PAS une mesure réelle — `audit/repro_e4_cost_model.py`) :
sur une hypothèse de vidéo de 10 minutes avec 3 clips, le coût LLM+transcription
modélisé par les constantes du fichier lui-même donne ~0,0078 €/min, très en
dessous des 0,03 €/min supposés — ce qui suggère que le calcul FFmpeg/stockage
(non mesuré) pourrait représenter la majorité du coût réel, ou que
l'hypothèse de 0,03 € est simplement une estimation non fondée. Impossible de
trancher sans télémétrie complète.
Effet client : la grille tarifaire (`tarifs.html`) pourrait être déficitaire
sur les gros utilisateurs sans qu'aucun mécanisme actuel ne puisse le
détecter.

**#2 — Course quota (BLOQUANT, A3/E5)**
Observé : `process()` (`modal_app.py:1770-1783`) vérifie
`available_minutes(...)` de façon SYNCHRONE avant d'appeler
`process_video.spawn(...)`. Le débit réel (`reserve_minutes`, appelé dans
`process_video`) n'a lieu qu'après téléchargement complet de la vidéo et
calcul de sa durée réelle — un délai de plusieurs secondes au minimum. Deux
requêtes émises dans cette fenêtre (double-clic, deux onglets, ou un script)
liraient le même solde disponible et passeraient toutes les deux la
vérification. Aucune clé d'idempotence sur `source_path`
(`usage_log.source_id` n'a qu'un index, pas de contrainte unique —
`supabase/schema_phase1_minutes.sql`).
Effet client : dépassement de quota exploitable trivialement, sans même
intention malveillante (un double-clic sur "Générer" suffit).

**#3 — Bypass de facturation nominatif (BLOQUANT, E5)**
Observé : `TEST_ACCOUNT_EMAILS = {"emiletaraud942@gmail.com"}`
(`modal_app.py:94`), utilisé pour `skip_billing` dans `process()` et
`process_video`. Le commentaire du code lui-même dit "à vider une fois les
tests terminés" — ce qui n'a pas été fait. Sans gravité de sécurité (c'est
le compte du fondateur), mais c'est un point de friction opérationnel réel :
si ce mécanisme est réutilisé/étendu sans discipline, ou si l'email change
de main, la facturation d'un compte peut être silencieusement désactivée.

**#4 — Injection ASS (BLOQUANT, C4)**
Observé et **exécuté** (`audit/repro_c4_ass_escaping.py`). Sortie exacte :
```
Dialogue: 0,0:00:00.00,0:00:02.40,Default,,0,0,0,,normal avec{accolade avec}accolade\Nanti\slash emoji😀present
Dialogue: 0,0:00:02.50,0:00:03.40,Default,,0,0,0,,café {\k500}injection
```
Les caractères `{`, `}`, `\` d'un mot transcrit sont copiés tels quels dans
le fichier `.ass`. `libass` (utilisé par FFmpeg pour le rendu incrusté)
interprète `{...}` comme un bloc de commande de style — une accolade ouverte
sans fermeture correspondante, ou une commande `\k500` injectée, corrompt le
rendu du sous-titre concerné et potentiellement de ceux qui suivent (portée
de la commande mal fermée). Reproductible avec n'importe quel nom propre,
pseudo, ou terme technique contenant ces caractères (peu fréquent mais pas
rare — hashtags, notations mathématiques, code lu à voix haute).
NON VÉRIFIÉ : le comportement RÉEL de libass face à ces séquences (pas de
libass/ffmpeg dans cet environnement) — mais l'absence de tout échappement
en amont est un fait de code, pas une supposition.

---

## 4. Matrice de couverture de la retouche (F1)

| Décision automatique | Par texte | Par clic | Par lot | Sans quota | Constat |
|---|---|---|---|---|---|
| Bornes du clip (début/fin) | Oui (`coupe le début/fin`, patch.py) | Non (pas de poignées de bornes dans l'UI) | Non | Oui | Partiel |
| Étendre le clip au-delà de la sélection initiale | Non | Non | Non | — | **Absent** (#18-adjacent — la matière est déjà transcrite, la reprendre ne coûterait rien mais rien ne l'implémente) |
| Niveau de cadrage (wide/medium/tight) à un instant | Oui (patch.py) | Oui (clic = cycle, `mes-clips.html:749`) | Non | Oui | Bon (texte + clic) |
| Position du cadrage (x/y) | **Impossible structurellement** | **Impossible structurellement** | — | — | **BLOQUANT #7** — aucun champ de position n'existe |
| Instant d'un changement de cadrage | Non (par texte) | Oui (glisser-déposer, `mes-clips.html:724-740`) | Non | Oui | Bon (clic) |
| Type de transition (cut/punch/smooth) | Non | Non | Non | — | **Absent** de l'UI (le champ existe dans l'EDL) |
| Ajout d'un cadrage manuel | Non | Oui (`+ Ajouter un cadrage`) | Non | Oui | Bon, mais voir #11 (pas protégé des suppressions IA) |
| Suppression d'un cadrage | Non (texte cible "tous"/"moitié", pas un événement précis) | Oui (`×`, `mes-clips.html:747`) | Non | Oui | Bon |
| Emphase sur un mot | Non | **Non** (affiché en pastille cyan sans gestionnaire de clic, `mes-clips.html:702-707`) | Non | — | Partiel — visible, non éditable directement |
| Groupement des sous-titres en lignes | Non | Non | Non | — | **Absent** — H2 (découpage FR) est un réglage global, pas par clip |
| Style/taille/police/couleur des sous-titres | Oui (patch.py, couleurs nommées + taille) | Non | Non | Oui | Bon en texte, absent en lot |
| Position verticale des sous-titres | Oui (`monte`/`descend`) | Non | Non | Oui | Bon |
| Correction d'un mot mal transcrit | Oui (`wordCorrections`, `adjust()`) | Oui (panneau "✍️ Sous-titres" cliquable, session antérieure) | Non | Oui | Bon — timestamp préservé, vérifié par lecture |
| Ajout d'un mot manqué (audio dégradé) | Non | Non | — | — | **Absent** (#12) |
| Sous-titre libre hors transcript ("[rires]", CTA) | Non | Non | — | — | **Absent** (#12) — modèle de données n'a pas de type "cue" |
| Désactiver les sous-titres | Oui (implicite via style) | NON VÉRIFIÉ (pas trouvé de bouton dédié) | Non | Oui | Partiel |
| Style karaoké vs classique | Oui (`patch.py`) | NON VÉRIFIÉ | Non | Oui | Bon en texte |
| Retirer un tic de langage retiré à tort | Non | Non | — | — | **Absent** — `clean_words` (build.py) est appliqué une fois, en amont, sans retour possible |
| Réintroduire un silence supprimé | Non | Non | — | — | **Absent** |
| Restaurer une digression coupée | Non | Non | — | — | **Absent** |
| Réordonnancement des segments (accroche) | N/A | N/A | — | — | Non applicable : le pipeline actuel ne réordonne pas (pas de mission B2 "reorder" en prod) |
| Niveau sonore / normalisation | Non | Non | — | — | **Absent** de la retouche — H1 est un réglage global de rendu, pas ajustable par clip |
| Musique de fond (ajouter/doser) | — | — | — | — | **Absent** (#16), aucune fonctionnalité |
| Couper une portion d'audio en gardant l'image | Non | Non | — | — | **Absent** |
| Texte d'accroche incrusté | NON VÉRIFIÉ | NON VÉRIFIÉ | Non | — | Non trouvé dans le vocabulaire EDL actuel |
| Fond (flou/couleur/image) | Non (pas de patch texte trouvé) | NON VÉRIFIÉ | Non | Oui probable | Partiel |
| Logo / filigrane | Non | Non | — | — | Texte de marque fixe uniquement (#24) |
| Format de sortie (résolution) | N/A | NON VÉRIFIÉ (dépend du choix initial) | Non | Oui (réutilise l'EDL stocké, `_rerender_from_edl`) | Partiel — change la résolution sans réanalyse, mais pas de variante par plateforme |
| Dupliquer / scinder / fusionner un clip | — | — | — | — | **Absent** (#18) |

**Décisions automatiques irréversibles nommées** (jamais modifiables par
l'utilisateur, quel que soit le canal) :
1. Position du cadrage (toujours centré) — structurel, § #7.
2. Groupement des sous-titres en lignes (choix H2 global, pas par clip).
3. Suppression des tics de langage (`clean_words`, une fois, en amont).
4. Silences retirés du montage (`_keeps_from_words`) — pas de réintroduction possible après coup.
5. Ordre des segments dans le clip.

**Taux de couverture estimé** (compte des lignes "Bon"/"Partiel" vs "Absent"
dans le tableau ci-dessus, 27 lignes) : **~48 %** modifiables au moins
partiellement sans régénération complète ; **~52 %** absentes ou
structurellement impossibles. Ce chiffre est une estimation de comptage sur
la matrice ci-dessus, PAS une mesure automatisée — NON VÉRIFIÉ au sens strict
(pas de script qui produit ce pourcentage).

**Taux de symétrie** (l'utilisateur peut placer à la main tout ce que l'IA
sait placer) : l'IA sait poser `framing` et `emphasis`
(`sortclip/director.py:build_place_events_tool`) et, en théorie, `hold_on_speaker`
et `speed` existent dans le vocabulaire EDL (`sortclip/edl.py`) mais ne sont
posés par AUCUN chemin (ni IA ni manuel) et `speed` n'est même jamais lu par
le compilateur (confirmé par lecture, `grep SpeedEvent sortclip/compile.py`
ne renvoie rien — code mort). L'utilisateur peut placer `framing` (bouton
"+ Ajouter") mais pas `emphasis` manuellement (pas de bouton trouvé).
**Symétrie réelle : framing seul, sur 2 opérations placées par l'IA — 50 %.**

---

## 5. Mesures

| Mesure | Valeur | Méthode |
|---|---|---|
| D2 — retouche "moins de zooms" modifie uniquement le champ visé | Confirmé : seule la clé `events` change (captions/background/canvas/keeps identiques, emphase intacte hors id) | Exécuté, `audit/repro_d2_patch_scope.py` |
| D3 — identifiants stables (suppression 3e, modification 5e) | Confirmé : bon événement touché, 5 événements restants sur 6 | Exécuté, `audit/repro_d3_stable_ids.py` |
| A4 — déterminisme du chemin sans IA | EDL identiques une fois les `id` retirés ; JAMAIS byte-identiques sinon (uuid4 aléatoire par construction) | Exécuté, `audit/repro_a4_determinism.py` |
| C4 — échappement ASS | Aucun — accolades/antislash/tentative de balise karaoké passent tels quels ; accents français survivent (encodage OK) | Exécuté, `audit/repro_c4_ass_escaping.py` |
| D1 — quota jamais débité à la retouche | `reserve_minutes`/`commit_reservations` appelés UNIQUEMENT dans `process_video` (`modal_app.py:1567`, `:1667`) ; absents de `adjust()`, `rerender_clip()`, `_rerender_from_edl()` | Lecture + grep exhaustif (répété à chaque commit de la session ayant précédé cet audit) |
| Coût LLM+transcription illustratif (10 min source, 3 clips) | ~0,0078 €/min, contre l'hypothèse de 0,03 €/min du modèle | Calcul exécuté avec les constantes réelles de `pricing_config.py` (illustratif, pas une mesure de production) |
| Tables avec RLS activée | 8/9 (`profiles`, `clip_jobs`, `clips`, `clip_versions`, `consents`, `credit_packs`, `usage_log`, `user_lexicon`, `processing_costs`) — `stripe_events` seule exception | Lecture exhaustive de `supabase/*.sql` |
| Nombre de tests automatisés du dépôt | 125 (dont 15 fichiers `tests/`) + 4 (`test_pricing_config.py`) = 129, tous passants | Exécuté : `pytest tests/ -q` + `pytest test_pricing_config.py -q` |
| Rétention automatique appliquée | Clips gratuits : 72h, cron horaire (`cleanup_free_clips`) ; sources : 30j, cron quotidien 03h30 UTC (`cleanup_old_sources`) | Lecture, décorateurs `schedule=modal.Cron(...)` |

**Mesures demandées mais NON VÉRIFIABLES ici** (voir § 6) : décalage réel des
sous-titres sur clips réels (C1), WER sur cas difficiles (B2), taux d'échec
sur 20 traitements (E3), coût réel mesuré en production (E4), latence des
retouches, nombre de gestes pour 12 clips, fuite entre deux comptes réels
(E1 exécuté), empreinte mémoire/disque réelle (A2), tout ce qui suppose
ffmpeg, un compte Supabase vivant, ou un appel API réel.

---

## 6. Angles morts

Cette section est volontairement longue — un audit qui ne liste pas ses
limites a menti quelque part.

- **Aucun `ffmpeg`/`ffprobe` dans cet environnement.** Tout le domaine A1
  (robustesse aux entrées réelles — vidéo sans audio, cadence variable,
  fichier corrompu, etc.) n'a pu être vérifié QUE par lecture de code, jamais
  par exécution réelle. Les scripts de fabrication de vidéos synthétiques
  demandés par la mission (A1, méthode point 2) n'ont pas pu être écrits de
  façon utile : sans ffmpeg pour les FABRIQUER ni pour les FAIRE TRAITER, un
  script de génération de fixture serait un exercice de style sans valeur
  probante. Je ne l'ai donc pas produit plutôt que de produire un faux
  semblant d'exécution.
- **Aucune clé API réelle** (Anthropic, AssemblyAI, Stripe). Tout le domaine
  B (comportement de la transcription sur silence/musique/accents, mesure du
  WER) est NON VÉRIFIÉ — je n'ai pu vérifier QUE la configuration statique
  (langue épinglée, modèle `speech_model`), jamais le comportement réel de
  l'API sur les cas difficiles demandés.
- **Aucun compte Supabase réel.** Le cloisonnement entre comptes (E1) n'a pu
  être vérifié QUE par lecture des policies RLS SQL — la vérification par
  EXÉCUTION explicitement demandée ("crée deux comptes de test, depuis le
  compte A tente de lire...") n'a pas pu être faite. Les policies lues sont
  cohérentes et correctement écrites (`using (auth.uid() = user_id)` partout,
  ou via `exists (select ... where c.user_id = auth.uid())` pour les tables
  liées) mais une policy mal écrite n'est pas la même chose qu'un
  comportement vérifié en conditions réelles — en particulier les Storage
  policies (buckets `videos`/`clips`) n'ont pas de fichier SQL dans ce dépôt :
  elles sont probablement configurées directement dans le dashboard
  Supabase, donc invisibles depuis le code. **Ceci est en soi un angle mort
  à corriger : les policies de stockage devraient être versionnées comme le
  reste.**
- **E1, URLs de clips devinables/partageables** : NON VÉRIFIÉ. Le bucket
  `clips` et son mode d'accès (public vs signé) ne sont configurés nulle
  part dans ce dépôt (probablement dashboard Supabase) — je ne peux ni
  confirmer ni infirmer si une URL de clip reste valide indéfiniment.
- **C1, le calage réel** : la mission demande de mesurer le décalage sur 5
  clips réels avec 20 mots-repères vérifiés à l'oreille contre l'audio. Ce
  travail a déjà été engagé dans une mission précédente
  (`correction-sous-titres`, voir `evals/subs/README.md`) et bute sur la
  même limite : aucun clip réel, aucune oreille humaine disponible ici. Le
  bug de calage déjà identifié et corrigé cette session (pause de silence
  affichant des mots en avance, rotation vidéo déformant l'image) montre que
  des bugs RÉELS existent sur ce chemin au-delà de ce qu'une lecture de code
  seule aurait révélé — preuve que ce NON VÉRIFIÉ est un vrai risque, pas une
  clause de style.
- **E3, taux d'échec mesuré** : NON VÉRIFIÉ, nécessite de faire tourner 20
  traitements réels.
- **E4, coût réel** : le calcul § 3/#1 est un ordre de grandeur illustratif à
  partir des constantes du code, PAS une mesure de production. Le vrai coût
  suppose d'interroger `processing_costs` sur un vrai volume — table dont le
  schéma montre déjà qu'elle ne capture pas tout (§ 3/#1).
- **F10, mesures de gestes/latence** : NON VÉRIFIÉ, nécessite une interface
  utilisable en conditions réelles (navigateur + backend vivant).
- **A2, empreinte disque/mémoire réelle** : NON VÉRIFIÉ par la mesure
  demandée (10 traitements dont 3 échecs, avant/après). Par LECTURE :
  `tempfile.TemporaryDirectory()` (context manager Python, `modal_app.py:1491`)
  garantit le nettoyage même en cas d'exception remontée — c'est une garantie
  du langage, pas une supposition. Mais je n'ai pas pu vérifier si un
  processus FFmpeg lui-même peut, dans de rares cas, laisser un fichier
  temporaire hors de ce dossier (ex. si FFmpeg écrit ailleurs sur erreur) —
  NON VÉRIFIÉ.
- **B3/B4 (VAD, diarisation)** : établi dans une mission précédente
  (`refonte-ia`) que la diarisation (pyannote) est bloquée par l'absence d'un
  jeton HuggingFace que je ne peux pas créer moi-même — toujours vrai ici,
  non re-vérifié à nouveau dans cet audit faute de nouvelle information.
- **F9, refus propre des demandes hors périmètre** : établi par LECTURE que
  `adjust_with_text` force `tool_choice={"type":"tool","name":"edit_events"}`
  (`sortclip/director.py`) — Claude ne PEUT PAS répondre par du texte libre
  ("ce n'est pas possible"), il doit toujours retourner un appel d'outil
  (potentiellement avec une liste de patchs vide). Le comportement RÉEL face
  à une demande absurde ("traduis en espagnol") n'a pas pu être exécuté (pas
  de clé API) — je peux seulement affirmer que l'architecture ne prévoit
  structurellement aucun canal de refus explicite vers l'utilisateur, pas
  quel texte apparaîtrait exactement.
- **Concurrence sur `/adjust`** (D4, deux retouches parallèles sur le même
  clip) : NON VÉRIFIÉ par exécution. Par lecture : `adjust()` lit l'EDL,
  calcule `edl2`, l'écrit — aucun verrou, aucune version optimiste
  (`updated_at` comparé, ETag, etc.) trouvé. Deux requêtes concurrentes
  liraient probablement le même EDL de départ et la dernière écriture
  gagnerait silencieusement (perte de la première) — plausible par lecture,
  non confirmé par exécution réelle.

---

## 7. Plan proposé

**Avant de facturer un professionnel :**
- Fermer la course quota/concurrence (#2, #8) — verrou ou vérification
  atomique côté base (ex. contrainte unique sur une clé d'idempotence par
  soumission).
- Retirer ou documenter formellement le bypass de facturation nominatif (#3).
- Échapper les caractères spéciaux ASS avant écriture du fichier de
  sous-titres (#4) — correction locale, isolée, à faible risque.
- Activer RLS sur `stripe_events` (#5) — une ligne SQL.
- Décider et documenter un vrai mécanisme de suppression de compte / export
  de données (#6), même minimal (processus manuel documenté et OUTILLÉ, pas
  seulement une adresse email).
- Enrichir `processing_costs` pour capturer le temps de calcul Modal et le
  stockage (#1) — condition pour pouvoir un jour répondre à la question de
  la viabilité tarifaire.

**Dans le mois :**
- Décider si le repositionnement du cadrage (#7) est un investissement
  voulu — c'est un chantier de plusieurs semaines (position x/y dans l'EDL,
  UI de déplacement, wiring du compilateur), mais son absence touche le cœur
  de la promesse produit.
- Protection des événements manuels contre les suppressions IA (#11) —
  ajouter un marqueur d'origine sur les événements EDL.
- Limite de débit générale sur les endpoints coûteux (#9), limite de taille
  de fichier serveur (#10).
- Décision sur la retouche par lot (#13) — impact usage professionnel élevé.

**Plus tard :**
- Variantes bornées (#14), préférences apprises au-delà du lexique (#15),
  musique de fond / logo / image d'arrêt (#16), opérations de clip
  dupliquer/scinder/fusionner (#18), sous-titres "cue" libres (#12).
- Validation à l'oreille des seuils audio H1 (#21) et des zones de sécurité
  par plateforme (#20, #17) sur de vrais rendus.
- Constituer le vrai jeu de mesure du calage (C1) et du WER (B2) dès qu'un
  accès à des clips réels et une écoute humaine sont disponibles — déjà
  entamé, voir `evals/subs/README.md`.
