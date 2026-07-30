# Jeu de test de sous-titrage — état réel

La mission « correction-sous-titres » (Partie 0.1) demande **12 clips réels**
couvrant : 3 voix propres, 3 avec musique de fond continue, 2 avec intro
musicale puis parole, 2 à locuteurs qui se chevauchent, 1 à accent/débit
marqué, 1 à longs silences — chacun avec **au moins 20 mots-repères
horodatés à la main** contre l'audio réel.

## Pourquoi ce jeu n'existe pas ici

Je n'ai, dans cet environnement :
- aucun corpus vidéo réel correspondant à ces catégories (musique de fond,
  chevauchement de locuteurs, accents…) ;
- aucun moyen de vérifier un horodatage "à la main" contre un audio réel —
  ça suppose d'écouter le clip, ce que je ne peux pas faire ici ;
- pas d'accès à l'API AssemblyAI ni de vraie source vidéo pour produire une
  transcription candidate à comparer à la référence.

C'est exactement la même limite que le jeu de 40 clips annotés du chantier
« refonte de la couche IA » précédent.

## Ce qui est prêt

L'infrastructure du harnais (`sortclip/eval/subs.py`, `sortclip/eval/subs_metrics.py`,
la commande `python -m sortclip.eval subs --report`) est écrite et testée
unitairement (`tests/test_eval_subs_metrics.py`). Elle calcule :
- le taux d'erreur de mots (WER) contre la référence,
- l'écart médian et l'écart-type des horodatages sur les mots-repères,
- le **profil du décalage** (constant / croissant / irrégulier — tableau 0.2
  de la mission), à partir de la même donnée.

Un seul clip de démonstration (`synthetic_001/`) existe, avec des données
synthétiques clairement marquées comme telles — il sert uniquement à vérifier
que la commande tourne de bout en bout, PAS comme preuve de qualité du
sous-titrage réel.

## Pour constituer le vrai jeu

Il faut, de ta part :
1. 12 vidéos réelles couvrant les catégories ci-dessus (des clips déjà
   produits par SortClip conviennent, tant qu'ils couvrent la diversité
   demandée).
2. Pour chacune, écouter le clip et noter à la main le mot et l'horodatage
   réel (au son) d'au moins 20 mots répartis dans le clip — dans
   `evals/subs/<clip_id>/reference.json`, format :
   `{"words": [{"word": "...", "start": 12.34, "end": 12.61}, ...]}`
   et `evals/subs/<clip_id>/meta.json` : `{"category": "musique_continue"}`
   (catégories : `propre`, `musique_continue`, `intro_musicale`,
   `chevauchement`, `accent_debit`, `silences_longs`).
3. Une fois le jeu réel en place, relancer `python -m sortclip.eval subs --report`
   pour obtenir le vrai tableau de qualification du décalage (0.2) — c'est
   ce tableau qui détermine lequel des correctifs A1/A2/A3/A4 s'applique,
   pas une supposition.
