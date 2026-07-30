"""
Harnais d'évaluation SortClip (Partie 0 du chantier « refonte de la couche IA »).

But : rendre mesurable l'effet de tout changement de prompt ou d'algorithme sur
la qualité du montage, AVANT de fusionner ce changement. Sans ce harnais, une
modification de prompt est un pari, pas une amélioration vérifiée.

État réel au moment de l'écriture (à lire avant d'utiliser ce module) :
- Les fonctions de métriques (metrics.py) sont complètes et testées sur des
  fixtures SYNTHÉTIQUES (tests/test_eval_metrics.py) — ce sont de vrais calculs,
  vérifiés.
- Le jeu de référence réel demandé (40 clips réels, 4 genres, EDL de référence
  annotés à la main par un humain) N'EXISTE PAS et n'a pas pu être constitué en
  autonomie : il n'y a ni corpus vidéo réel disponible, ni moyen de produire un
  jugement de qualité de montage sans supervision humaine. Voir
  evals/golden/README.md pour le détail et ce qu'il reste à faire.
- `sortclip.eval.cli` fonctionne déjà (une seule commande, largement sous
  15 minutes) mais tourne sur un dossier golden vide ou provisoire tant que ce
  jeu n'est pas fourni — le rapport le signale à chaque exécution plutôt que de
  masquer le manque.
"""

from .metrics import (
    framing_agreement,
    emphasis_precision_recall,
    event_density_delta,
    validator_rejection_rate,
    instruction_satisfaction,
    is_stable,
)

__all__ = [
    "framing_agreement",
    "emphasis_precision_recall",
    "event_density_delta",
    "validator_rejection_rate",
    "instruction_satisfaction",
    "is_stable",
]
