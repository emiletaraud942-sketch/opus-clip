"""
Zones de sécurité par plateforme (Partie F4).

Les interfaces des réseaux (boutons, légende, pseudo, barre de progression)
recouvrent une partie de l'écran — surtout en bas et à droite. Un sous-titre
placé trop bas passe derrière ces éléments.

Module PUR : proportions du canevas (0.0 = haut, 1.0 = bas), aucune
dépendance à FFmpeg/vision.

⚠️ {{À_COMPLÉTER : les valeurs ci-dessous sont des ORDRES DE GRANDEUR
provisoires, PAS mesurées sur les interfaces réelles. La mission demande
explicitement de mesurer par capture d'écran plutôt que de se fier à des
valeurs trouvées en ligne — ce que je n'ai pas pu faire en autonomie (pas
d'accès aux applications mobiles réelles). Ne pas déployer ces chiffres en
production sans les avoir vérifiés à l'œil sur un téléphone, pour chaque
plateforme, avec sa version actuelle de l'application.}}
"""

from __future__ import annotations

# Zone du bas considérée interdite pour les sous-titres, en proportion de la
# hauteur du canevas DEPUIS LE BAS (ex. 0.20 = les 20% du bas sont à éviter).
UNSAFE_BOTTOM_ZONE = {
    "tiktok": 0.20,
    "reels": 0.18,
    "shorts": 0.15,
    "default": 0.0,   # aucune zone connue -> aucune contrainte
}

# Zone de droite (boutons like/commentaire/partage empilés verticalement),
# en proportion de la largeur depuis la droite — utile pour le centrage des
# visages plus que pour les sous-titres (déjà centrés horizontalement), mais
# gardée disponible pour un usage futur (ex. emplacement du texte d'accroche H4).
UNSAFE_RIGHT_ZONE = {
    "tiktok": 0.16,
    "reels": 0.14,
    "shorts": 0.14,
    "default": 0.0,
}


def caption_y_is_safe(y: float, platform: str) -> bool:
    """`y` est la position du sous-titre en proportion de hauteur (0=haut,
    1=bas), au même sens que Captions.y. Vrai si la position ne tombe PAS dans
    la zone interdite du bas de la plateforme visée."""
    unsafe_zone = UNSAFE_BOTTOM_ZONE.get(platform, 0.0)
    if unsafe_zone <= 0.0:
        return True
    return y <= (1.0 - unsafe_zone)


def clamp_caption_y(y: float, platform: str) -> float:
    """Remonte `y` juste au-dessus de la zone interdite si nécessaire — ne
    modifie rien si la position est déjà sûre ou si la plateforme n'a pas de
    zone connue."""
    unsafe_zone = UNSAFE_BOTTOM_ZONE.get(platform, 0.0)
    if unsafe_zone <= 0.0:
        return y
    limit = 1.0 - unsafe_zone
    return min(y, limit)
