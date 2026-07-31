"""
Patchs sur un EDL — les ajustements NE régénèrent jamais l'EDL.

Deux niveaux :
  - apply_patch(edl, ops)         : patchs bas niveau, par `id` d'événement
                                    (remove / modify / add). Ne lève jamais.
  - apply_text_adjustment(edl, s) : traduit une consigne en français (« moins
                                    de zooms », « sous-titres plus gros »…) en
                                    patchs déterministes, SANS IA. C'est le
                                    chemin rapide et gratuit ; le réalisateur
                                    LLM (director.adjust_with_text) sert de
                                    repli pour les consignes libres.

Invariant : l'utilisateur doit voir changer ce qu'il a demandé et RIEN d'autre.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .edl import EDL


@dataclass
class PatchOp:
    action: str                       # "remove" | "modify" | "add"
    event_id: str | None = None
    field: str | None = None
    value: Any = None
    new: dict | None = None           # pour "add" : dict d'un événement


@dataclass
class PatchResult:
    edl: EDL
    applied: list[PatchOp] = field(default_factory=list)
    rejected: list[PatchOp] = field(default_factory=list)


def apply_patch(edl: EDL, ops: list[PatchOp]) -> PatchResult:
    """Applique une liste de patchs. Ne lève jamais : chaque op est soit
    appliquée, soit rejetée. Les événements sont référencés par `id` stable."""
    events = list(edl.events)
    by_id = {e.id: i for i, e in enumerate(events)}
    applied: list[PatchOp] = []
    rejected: list[PatchOp] = []

    for op in ops:
        try:
            if op.action == "remove":
                idx = by_id.get(op.event_id)
                if idx is None:
                    rejected.append(op); continue
                events[idx] = None
                applied.append(op)

            elif op.action == "modify":
                idx = by_id.get(op.event_id)
                if idx is None or events[idx] is None or not op.field:
                    rejected.append(op); continue
                events[idx] = events[idx].model_copy(update={op.field: op.value})
                applied.append(op)

            elif op.action == "add" and op.new:
                from .edl import (FramingEvent, EmphasisEvent,
                                  HoldOnSpeakerEvent, SpeedEvent, TextOverlayEvent)
                kinds = {"framing": FramingEvent, "emphasis": EmphasisEvent,
                         "hold_on_speaker": HoldOnSpeakerEvent, "speed": SpeedEvent,
                         "text_overlay": TextOverlayEvent}
                cls = kinds.get(op.new.get("op"))
                if not cls:
                    rejected.append(op); continue
                events.append(cls(**{k: v for k, v in op.new.items() if k != "op"}))
                applied.append(op)
            else:
                rejected.append(op)
        except Exception:
            rejected.append(op)

    events = [e for e in events if e is not None]
    return PatchResult(edl=edl.model_copy(update={"events": events}),
                       applied=applied, rejected=rejected)


# --------------------------------------------------------------------------
# Ajustement par texte — déterministe, sans IA, pour les consignes courantes.
# --------------------------------------------------------------------------

_COLOR_WORDS = {
    "blanc": "#FFFFFF", "jaune": "#FFEB3B", "rose": "#F43F8E",
    "cyan": "#22E5FF", "vert": "#22FF88", "orange": "#F39200",
}

# Texte incrusté (titre/accroche) — F8.3 de l'audit (AUDIT.md), absent avant
# ce correctif : une consigne qui en demandait un était silencieusement
# ignorée (repli LLM qui ne savait pas non plus le faire, faute de type
# d'événement pour ça), rapportée comme "faite" sans rien changer.
# "titre" seul (pas "sous-titre"/"sous titre", déjà géré par ailleurs) déclenche
# le texte incrusté — bug réel rencontré : sans l'exclusion, "sous-titres"
# contient la sous-chaîne "titre" et court-circuitait TOUTE consigne de
# sous-titres (gras, position, style...) vers ce bloc.
_OVERLAY_TRIGGER_RE = re.compile(r"(?<!sous-)(?<!sous )titre|incrust|overlay", re.IGNORECASE)
_OVERLAY_LEADING_STRIP = re.compile(
    r"^(ajoute|mets|met|écris|ecris|affiche|rajoute)\s+"
    r"(un|une|le|la|des|du)?\s*(titre|texte)?\s*(fixe)?\s*[:\-]?\s*",
    re.IGNORECASE,
)
_OVERLAY_TRAILING_STRIP = re.compile(
    r"\s*(tout\s+)?(en\s+haut|en\s+bas|au\s+centre|au\s+milieu)"
    r"(\s+de\s+la\s+vidéo|\s+de\s+la\s+video|\s+du\s+clip)?\s*$",
    re.IGNORECASE,
)
_QUOTE_RE = re.compile(r"[\"“‘'«](.+?)[\"”’'»]")


def _extract_overlay_text_and_position(instruction: str) -> tuple[str | None, str]:
    """Extrait le texte à incruster et sa position depuis une consigne libre.
    Priorité au texte ENTRE GUILLEMETS (fiable, sans ambiguïté) ; à défaut,
    heuristique de retrait des verbes/position en tête et fin de phrase —
    imparfait sur une phrase vraiment libre, mais couvre les formulations
    courantes ("ajoute le titre XXX en haut", "écris XXX en bas", "mets
    'XXX' comme titre"). Renvoie (texte ou None si rien d'exploitable,
    position)."""
    s_lower = instruction.lower()
    position = "top"
    if "en bas" in s_lower:
        position = "bottom"
    elif "au centre" in s_lower or "au milieu" in s_lower:
        position = "center"
    elif "en haut" in s_lower:
        position = "top"

    quoted = _QUOTE_RE.search(instruction)
    if quoted:
        text = quoted.group(1).strip()
        return (text or None), position

    stripped = _OVERLAY_LEADING_STRIP.sub("", instruction).strip()
    stripped = _OVERLAY_TRAILING_STRIP.sub("", stripped).strip()
    stripped = re.sub(r"^(comme titre|en titre)\s*[:\-]?\s*", "", stripped, flags=re.IGNORECASE).strip()
    return (stripped or None), position


def _normalize_word(w: str) -> str:
    return (w or "").strip().strip(".,!?…:;»«\"'").lower()


def _find_word_index(words: list[dict], target: str) -> int | None:
    """Cherche `target` (mot ou courte expression) dans `words` (temps
    SOURCE, transcript nettoyé) — comparaison insensible à la casse et à la
    ponctuation. Renvoie l'index du PREMIER mot correspondant, ou None."""
    target_norm = _normalize_word(target)
    if not target_norm:
        return None
    for i, w in enumerate(words):
        if _normalize_word(w.get("word", "")) == target_norm:
            return i
    return None


_QUOTED_OR_LAST_WORD_RE = re.compile(r"[\"“‘'«](.+?)[\"”’'»]")


def _extract_target_word(instruction: str) -> str | None:
    """Extrait le mot/l'expression cible d'une consigne d'emphase — priorité
    au texte entre guillemets, sinon le dernier mot de la phrase (couvre
    « mets en valeur le mot X », « surligne X »)."""
    quoted = _QUOTED_OR_LAST_WORD_RE.search(instruction)
    if quoted:
        return quoted.group(1).strip() or None
    words = re.findall(r"[\wÀ-ÿ'-]+", instruction)
    return words[-1] if words else None


def apply_text_adjustment(edl: EDL, instruction: str,
                          words: list[dict] | None = None) -> tuple[EDL, list[str]]:
    """Traduit une consigne FR en patchs déterministes. Retourne (EDL, notes).
    Ne touche QUE ce que la consigne vise. Renvoie une note par ajustement (ou
    une note « non compris » si rien ne correspond — au caller de basculer sur
    le réalisateur LLM).

    `words` : transcript nettoyé en TEMPS SOURCE (mêmes mots que `edl_words`
    stocké avec le clip) — nécessaire UNIQUEMENT pour les consignes de mise
    en valeur d'un mot précis (recherche du mot + conversion en temps de
    sortie). Sans ce paramètre, ces consignes tombent en repli LLM."""
    s = (instruction or "").lower()
    notes: list[str] = []
    edl2 = edl

    # --- Texte incrusté (titre/accroche) ---
    if _OVERLAY_TRIGGER_RE.search(s):
        text, position = _extract_overlay_text_and_position(instruction or "")
        if text:
            from .edl import TextOverlayEvent
            overlay = TextOverlayEvent(t=0.0, text=text[:200], position=position)
            edl2 = edl2.model_copy(update={"events": [*edl2.events, overlay]})
            notes.append(f"texte incrusté ajouté ({position}) : « {text} »")
            return edl2, notes
        # Déclencheur reconnu mais aucun texte exploitable extrait : mieux
        # vaut basculer sur le repli LLM (qui peut demander/déduire un texte
        # via le contexte) que de créer un overlay vide ou de faire semblant.

    # --- Cadrages / zooms ---
    if "moins de zoom" in s or "moins de cadrage" in s or "trop de zoom" in s:
        framings = sorted((e for e in edl2.events if e.op == "framing"), key=lambda x: x.t)
        if framings:
            n_remove = len(framings) // 2
            # A2 : retire les événements les MOINS justifiés (motivation) en
            # premier — déterministe, aucun jugement LLM nécessaire. À
            # motivation égale (0.5 par défaut, EDL sans score réel posé par
            # le réalisateur), on retombe sur l'alternance un-sur-deux de
            # l'ancien comportement, pour ne rien changer sur les EDL existants.
            ordered = sorted(
                enumerate(framings),
                key=lambda item: (item[1].motivation, item[0] % 2 == 0),
            )
            remove_ids = {f.id for _, f in ordered[:n_remove]}
            new_events = [e for e in edl2.events if e.op != "framing" or e.id not in remove_ids]
            edl2 = edl2.model_copy(update={"events": new_events})
            notes.append(f"zooms réduits ({len(framings)} -> {len(framings) - len(remove_ids)}, "
                        f"retire les moins justifiés)")

    # « plus de zooms » n'est PAS traité ici : ajouter un zoom pertinent
    # suppose d'identifier un nouveau moment fort, ce qui n'est pas
    # déterministe. On laisse volontairement `notes` sans correspondance pour
    # cette consigne — le repli LLM (plus bas dans l'appelant, sur
    # « non reconnue ») s'en charge.
    if "aucun zoom" in s or "sans zoom" in s or "pas de zoom" in s:
        new_events = [e for e in edl2.events if e.op != "framing"]
        edl2 = edl2.model_copy(update={"events": new_events})
        notes.append("tous les zooms retirés")

    # --- Resserrer sur le locuteur (bouton A3, cadrage large sur tout le clip) ---
    if "resserr" in s or "recadre sur le locuteur" in s or "recadrer sur le locuteur" in s:
        framings = [e for e in edl2.events if e.op == "framing"]
        # A1 (AUDIT.md) : centre le crop sur le visage détecté à la
        # génération, au lieu de toujours centrer géométriquement. Retombe sur
        # 0.5 (centré, comportement historique) si aucun visage fiable.
        face_x = edl2.source.face_x if edl2.source.face_x is not None else 0.5
        if framings:
            new_events = [
                e.model_copy(update={"value": "tight", "face_x": face_x}) if e.op == "framing" else e
                for e in edl2.events
            ]
            edl2 = edl2.model_copy(update={"events": new_events})
            notes.append("cadrage resserré sur tout le clip")
        else:
            from .edl import FramingEvent
            tight = FramingEvent(t=0.0, value="tight", transition="cut", face_x=face_x)
            edl2 = edl2.model_copy(update={"events": [*edl2.events, tight]})
            notes.append("cadrage resserré ajouté (gros plan)")

    # --- Plan large sur tout le clip (A1) ---
    if "plan large" in s or "dézoome" in s or "dezoome" in s or "recule" in s:
        new_events = [
            e.model_copy(update={"value": "wide"}) if e.op == "framing" else e
            for e in edl2.events
        ]
        edl2 = edl2.model_copy(update={"events": new_events})
        notes.append("cadrage élargi sur tout le clip")

    # --- Taille des sous-titres ---
    if "sous-titre" in s or "sous titre" in s or "texte" in s:
        if "plus gros" in s or "plus grand" in s or "plus grand" in s:
            edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(
                update={"size": min(160, edl2.captions.size + 12)})})
            notes.append(f"sous-titres agrandis ({edl2.captions.size})")
        elif "plus petit" in s or "plus discret" in s:
            edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(
                update={"size": max(20, edl2.captions.size - 12)})})
            notes.append(f"sous-titres réduits ({edl2.captions.size})")
        # Couleur des sous-titres
        for word, hexv in _COLOR_WORDS.items():
            if word in s:
                edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(
                    update={"primary": hexv})})
                notes.append(f"sous-titres en {word}")
                break
        # Position verticale (A1) — y=0 haut, y=1 bas (cf. sortclip.edl.Captions.y).
        if "monte" in s or "plus haut" in s or "remonte" in s:
            edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(
                update={"y": max(0.05, edl2.captions.y - 0.10)})})
            notes.append(f"sous-titres remontés (y={edl2.captions.y:.2f})")
        elif "descend" in s or "plus bas" in s:
            edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(
                update={"y": min(0.95, edl2.captions.y + 0.10)})})
            notes.append(f"sous-titres descendus (y={edl2.captions.y:.2f})")
        # Graisse (A1)
        if "en gras" in s or "plus gras" in s:
            edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(update={"bold": True})})
            notes.append("sous-titres en gras")
        elif "pas gras" in s or "sans gras" in s or "moins gras" in s:
            edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(update={"bold": False})})
            notes.append("sous-titres sans gras")

    # --- Style karaoké / classique (A1) — hors du bloc "sous-titre" ci-dessus :
    # une consigne comme « style karaoké » ou « mot par mot » ne contient pas
    # forcément le mot « sous-titre ».
    if "karaoké" in s or "karaoke" in s or "mot par mot" in s or "mot-à-mot" in s:
        edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(update={"style": "karaoke"})})
        notes.append("style karaoké (mot à mot)")
    elif "sous-titres classiques" in s or "sans karaoké" in s or "sans karaoke" in s:
        edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(update={"style": "plain"})})
        notes.append("style classique")

    # --- Coupe le début / la fin (A1) ---
    # Retire une durée fixe (2 s) au début ou à la fin du montage, en
    # rétrécissant le premier/dernier intervalle gardé — jamais en dessous
    # d'une seconde restante, pour ne pas produire un clip vide.
    TRIM_SECONDS = 2.0
    # NOTE : out_duration est une PROPRIÉTÉ CALCULÉE depuis `keeps` (voir
    # edl.py) — on ne la passe jamais à model_copy(update=...), elle se
    # recalcule automatiquement dès que `keeps` change.
    if ("coupe le début" in s or "coupe le debut" in s or "raccourcis le début" in s
            or "enlève le début" in s or "enleve le debut" in s):
        keeps = sorted(edl2.keeps, key=lambda k: k.start)
        if keeps and (keeps[0].end - keeps[0].start) > (TRIM_SECONDS + 1.0):
            new_first = keeps[0].model_copy(update={"start": keeps[0].start + TRIM_SECONDS})
            new_keeps = [new_first, *keeps[1:]]
            edl2 = edl2.model_copy(update={"keeps": new_keeps})
            notes.append(f"début raccourci de {TRIM_SECONDS:.0f}s")
    if ("coupe la fin" in s or "raccourcis la fin" in s
            or "enlève la fin" in s or "enleve la fin" in s):
        keeps = sorted(edl2.keeps, key=lambda k: k.start)
        if keeps and (keeps[-1].end - keeps[-1].start) > (TRIM_SECONDS + 1.0):
            new_last = keeps[-1].model_copy(update={"end": keeps[-1].end - TRIM_SECONDS})
            new_keeps = [*keeps[:-1], new_last]
            edl2 = edl2.model_copy(update={"keeps": new_keeps})
            notes.append(f"fin raccourcie de {TRIM_SECONDS:.0f}s")

    # --- Fond ---
    if "plus flou" in s:
        edl2 = edl2.model_copy(update={"background": edl2.background.model_copy(
            update={"sigma": min(100, edl2.background.sigma + 10)})})
        notes.append("fond plus flou")
    elif "moins flou" in s or "net" in s:
        edl2 = edl2.model_copy(update={"background": edl2.background.model_copy(
            update={"sigma": max(0, edl2.background.sigma - 10)})})
        notes.append("fond moins flou")

    # --- Fond en couleur unie (F5) ---
    if "fond" in s and ("couleur unie" in s or "couleur uni" in s or "fond noir" in s):
        color = "#000000" if "noir" in s else edl2.background.color
        edl2 = edl2.model_copy(update={"background": edl2.background.model_copy(
            update={"mode": "solid", "color": color})})
        notes.append(f"fond en couleur unie ({color})")

    # --- Plein cadre, sans fond (bandes noires plutôt que flou) (F5) ---
    if ("plein cadre" in s or "sans fond" in s or "retire le fond" in s
            or "enlève le fond" in s or "pas de fond" in s):
        edl2 = edl2.model_copy(update={"background": edl2.background.model_copy(update={"mode": "none"})})
        notes.append("plein cadre (sans fond, bandes noires)")

    # --- Sous-titres : activer/désactiver complètement (F4) ---
    if "retire les sous-titres" in s or "enlève les sous-titres" in s or "sans sous-titres" in s or "désactive les sous-titres" in s or "desactive les sous-titres" in s:
        edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(update={"enabled": False})})
        notes.append("sous-titres désactivés")
    elif "remets les sous-titres" in s or "réactive les sous-titres" in s or "reactive les sous-titres" in s or "affiche les sous-titres" in s:
        edl2 = edl2.model_copy(update={"captions": edl2.captions.model_copy(update={"enabled": True})})
        notes.append("sous-titres réactivés")

    # --- Figer le cadrage (F3) : un seul plan fixe sur tout le clip, au
    # niveau de cadrage DOMINANT déjà présent (pas un niveau imposé comme
    # « plan large »/« resserre ») — répond à « arrête de changer de plan,
    # garde celui-là ». Distinct de "plan large"/"resserre" ci-dessus.
    if "fige le cadrage" in s or "garde un seul cadrage" in s or "arrête de zoomer" in s or "arrete de zoomer" in s or "un seul plan" in s:
        framings = [e for e in edl2.events if e.op == "framing"]
        if framings:
            counts: dict[str, int] = {}
            for f in framings:
                counts[f.value] = counts.get(f.value, 0) + 1
            dominant = max(counts, key=counts.get)
        else:
            dominant = "medium"
        new_events = [e for e in edl2.events if e.op != "framing"]
        from .edl import FramingEvent
        face_x = edl2.source.face_x if edl2.source.face_x is not None else 0.5
        new_events.append(FramingEvent(t=0.0, value=dominant, transition="cut", face_x=face_x))
        edl2 = edl2.model_copy(update={"events": new_events})
        notes.append(f"cadrage figé sur tout le clip ({dominant})")

    # --- Mise en valeur d'un mot précis (F3/F8.2) ---
    if (("mets en valeur" in s or "met en valeur" in s or "surligne" in s
         or "mets l'emphase sur" in s or "met l'emphase sur" in s) and words):
        target = _extract_target_word(instruction or "")
        idx = _find_word_index(words, target) if target else None
        if idx is not None:
            from .captions import map_words_to_output
            words_out = map_words_to_output(words, edl2)
            if idx < len(words_out):
                from .edl import EmphasisEvent
                t_out = words_out[idx]["start"]
                emphasis = EmphasisEvent(t=t_out, word_index=idx, style="pop")
                edl2 = edl2.model_copy(update={"events": [*edl2.events, emphasis]})
                notes.append(f"mise en valeur ajoutée sur « {words[idx]['word']} »")

    # --- Retirer une mise en valeur précise, ou toutes (F3) ---
    if (("retire la mise en valeur sur" in s or "enlève la mise en valeur sur" in s
         or "retire l'emphase sur" in s or "enlève l'emphase sur" in s) and words):
        target = _extract_target_word(instruction or "")
        idx = _find_word_index(words, target) if target else None
        if idx is not None:
            new_events = [e for e in edl2.events if not (e.op == "emphasis" and e.word_index == idx)]
            if len(new_events) < len(edl2.events):
                edl2 = edl2.model_copy(update={"events": new_events})
                notes.append(f"mise en valeur retirée sur « {words[idx]['word']} »")
    elif "retire toutes les mises en valeur" in s or "aucune mise en valeur" in s or "sans mise en valeur" in s:
        new_events = [e for e in edl2.events if e.op != "emphasis"]
        if len(new_events) < len(edl2.events):
            edl2 = edl2.model_copy(update={"events": new_events})
            notes.append("toutes les mises en valeur retirées")

    if not notes:
        notes.append("consigne non reconnue par l'ajustement déterministe")
    return edl2, notes
