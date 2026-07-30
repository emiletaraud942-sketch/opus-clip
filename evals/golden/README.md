# Jeu de référence (golden set) — état réel

## Ce qui était demandé

40 clips réels, répartis en 4 genres × 10 (podcast à deux voix, monologue face
caméra, table ronde à trois et plus, extrait de live/gaming), dont 10 cas
difficiles (locuteur qui bouge, silences longs, personnes qui se coupent,
accent marqué, texte déjà incrusté à l'image). Pour chacun : le fichier
source, le transcript mot-à-mot, et un EDL de référence **annoté à la main**
représentant un bon montage.

## Ce qui existe réellement ici

Un seul dossier, `synthetic_001/`, qui est une **fixture de test fabriquée**
(pas une vraie vidéo, pas un jugement de qualité de montage réel). Il sert
uniquement à vérifier que la chaîne `sortclip.eval run/compare` fonctionne de
bout en bout — chargement, calcul des 6 métriques, écriture du rapport,
détection de régression. Voir `tests/test_eval_metrics.py` pour la
vérification unitaire des métriques elles-mêmes sur d'autres fixtures.

## Pourquoi le vrai jeu n'a pas été constitué en autonomie

1. **Aucun corpus vidéo réel accessible.** Le dépôt ne contient que les 6
   clips d'exemple marketing (`examples/clip1-6.mp4`), qui sont déjà des
   sorties du produit — pas des sources brutes couvrant les 4 genres et les
   10 cas difficiles demandés.
2. **Un « bon montage » ne se déduit pas automatiquement.** L'EDL de référence
   doit représenter un jugement éditorial humain (quel cadrage, où couper,
   quels mots mettre en emphase) — c'est précisément ce que le harnais est
   censé évaluer. Le produire moi-même reviendrait à valider le système avec
   ses propres sorties, ce qui invaliderait toute l'évaluation.
3. **Fabriquer de fausses références serait pire que ne rien avoir** : un
   golden set inventé donnerait une confiance illusoire à toute décision
   « garder / annuler » prise dessus, exactement le risque que la Partie 0
   est censée éliminer.

## Ce qu'il reste à faire (nécessite une action humaine)

Pour chaque clip du vrai jeu :
1. Fournir 40 vidéos sources réelles (ou des extraits), couvrant les 4 genres.
2. Générer leur transcript (pipeline `transcribe()` existant — nécessite la
   clé AssemblyAI et un budget associé).
3. Faire annoter, par un humain qui connaît le produit, un EDL de référence
   par clip (cadrages, emphases, coupes) représentant un bon montage.
4. Déposer chaque clip sous `evals/golden/<clip_id>/` avec `meta.json`
   (genre, difficile ou non) et `reference.json` (l'EDL de référence, au
   format attendu par `sortclip/eval/golden.py`).

Une fois ces 40 dossiers en place, `python -m sortclip.eval run --set golden
--report` les évalue tous automatiquement — aucune autre modification de code
n'est nécessaire.
