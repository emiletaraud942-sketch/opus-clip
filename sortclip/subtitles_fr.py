"""
Découpage linguistique des sous-titres — français (Partie H2).

L'exactitude de la transcription est un terrain déjà occupé par tous les
outils du marché. La QUALITÉ DU DÉCOUPAGE ne l'est pas, et c'est un axe où
les outils conçus en anglais échouent systématiquement sur le français.

Règles implémentées (voir tests/test_h2_subtitles_fr.py pour la preuve que
chacune tient) :
  - jamais couper entre un déterminant et son nom ;
  - jamais couper après une forme élidée (l', d', qu', j', n'…) ;
  - garder « ne … pas/jamais/plus » ensemble ;
  - jamais isoler en fin de ligne un mot d'une ou deux lettres ;
  - préférer couper sur une frontière de proposition (virgule, conjonction) ;
  - typographie française : espace insécable avant ; : ! ? et dans « » ;
  - deux lignes maximum par bloc, budget de caractères configurable
    (défaut 22, dans la fourchette 20-24 demandée pour le vertical).

Module PUR : aucune dépendance à FFmpeg/ASS ici — juste du texte et des
horodatages déjà en mémoire. Le rendu ASS (captions.py) consomme la sortie de
break_lines_fr() pour composer ses blocs.
"""

from __future__ import annotations

import re

_DETERMINERS = {
    "le", "la", "les", "un", "une", "des", "ce", "cet", "cette", "ces",
    "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
    "notre", "nos", "votre", "vos", "leur", "leurs", "au", "aux", "du",
}
_COORDINATING_CONJUNCTIONS = {"et", "mais", "ou", "donc", "or", "ni", "car"}
_NEGATION_HEAD = {"ne", "n"}   # "n" : forme élidée "n'" une fois la ponctuation retirée

# Ponctuation où insérer une espace insécable EN FRANÇAIS (U+00A0), à la
# différence des outils anglophones qui n'en mettent jamais.
_NBSP = " "


def _core(word: str) -> str:
    """Mot sans ponctuation ni apostrophe finale, en minuscules."""
    return re.sub(r"[^\wÀ-ÿ]", "", word or "").lower()


def is_elided(word: str) -> bool:
    """Vrai si `word` est une forme élidée seule : l', d', qu', j', n', c',
    s', m', t', jusqu', quoiqu' (apostrophe ' ou ’ typographique)."""
    core = (word or "").strip().lower()
    return bool(re.match(r"^(l|d|qu|j|n|c|s|m|t|jusqu|quoiqu)['’]$", core))


def is_forbidden_break(word_before: str, word_after: str | None) -> bool:
    """Vrai si couper une ligne juste après `word_before` (avant `word_after`,
    ou en fin de bloc si None) violerait une règle du français."""
    before_core = _core(word_before)
    if before_core in _DETERMINERS:
        return True                              # jamais séparer déterminant/nom
    if is_elided(word_before):
        return True                              # jamais couper après une forme élidée
    if before_core in _NEGATION_HEAD:
        return True                              # jamais séparer « ne » de sa négation
    if 1 <= len(before_core) <= 2 and before_core not in _COORDINATING_CONJUNCTIONS:
        return True                              # jamais isoler un mot d'1-2 lettres en fin de ligne
    return False


def prefers_break(word_before: str) -> bool:
    """Point de coupure PRÉFÉRÉ (pas obligatoire) : virgule ou conjonction de
    coordination — frontière de proposition naturelle."""
    if (word_before or "").rstrip().endswith(","):
        return True
    return _core(word_before) in _COORDINATING_CONJUNCTIONS


def break_lines_fr(words: list[dict], max_chars: int = 22) -> list[list[dict]]:
    """Découpe une liste de mots (avec au moins la clé "word") en lignes
    respectant les règles françaises ci-dessus. N'ÉTEND jamais moins qu'un
    mot par ligne ; si le point de coupure au budget est interdit, la ligne
    s'allonge jusqu'au prochain point sûr (garde-fou : au double du budget,
    on coupe quand même pour éviter une ligne interminable sur un texte sans
    ponctuation)."""
    if not words:
        return []
    lines: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0

    for i, w in enumerate(words):
        text = w["word"]
        current.append(w)
        current_len += len(text) + (1 if len(current) > 1 else 0)
        is_last = i == len(words) - 1
        next_word = words[i + 1]["word"] if not is_last else None

        if is_last:
            lines.append(current)
            current, current_len = [], 0
            continue

        if current_len >= max_chars:
            safe = not is_forbidden_break(text, next_word)
            forced = current_len >= max_chars * 2   # garde-fou anti-ligne infinie
            if safe or forced:
                lines.append(current)
                current, current_len = [], 0

    if current:
        lines.append(current)
    return lines


def group_into_blocks(lines: list[list[dict]], max_lines: int = 2) -> list[list[list[dict]]]:
    """Regroupe des lignes en blocs affichés ensemble (deux lignes maximum,
    comme demandé pour le format vertical)."""
    return [lines[i:i + max_lines] for i in range(0, len(lines), max_lines)]


def apply_french_typography(text: str) -> str:
    """Insère les espaces insécables requises par la typographie française :
    avant `;` `:` `!` `?`, et à l'intérieur des guillemets `« »`. Les outils
    anglophones ne le font jamais — axe de qualité distinct de l'exactitude de
    la transcription."""
    if not text:
        return text
    # Avant ; : ! ? (mais pas si déjà précédé d'une espace insécable).
    text = re.sub(r"(?<! )\s*([;:!?])", lambda m: _NBSP + m.group(1), text)
    # Après « et avant » (guillemets français).
    text = re.sub(r"«\s*", "«" + _NBSP, text)
    text = re.sub(r"\s*»", _NBSP + "»", text)
    return text
