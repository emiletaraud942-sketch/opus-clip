"""
Schéma EDL — le format pivot de SortClip.

INVARIANTS (à ne jamais violer) :
  1. L'EDL est DÉCLARATIF. Il décrit un résultat, jamais une commande.
     Aucune chaîne "ffmpeg" ne doit apparaître dans ce fichier.
  2. `keeps` est exprimé en TEMPS SOURCE (secondes dans le fichier d'origine).
  3. `events` est exprimé en TEMPS SORTIE (secondes dans le clip final,
     APRÈS suppression des silences). Utiliser out_to_src() pour convertir.
  4. Tout événement porte un `id` stable, généré à la création.
     Les patchs référencent cet id, jamais une position dans la liste.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------
# Vocabulaire fermé : le LLM ne peut composer QUE avec ces opérations.
# Ajouter une op ici = ajouter son rendu dans compile.py. Jamais l'un sans l'autre.
# --------------------------------------------------------------------------

Framing = Literal["wide", "medium", "tight"]
Transition = Literal["cut", "punch", "smooth"]

# Facteur de recadrage : proportion de la largeur source conservée.
FRAMING_ZOOM: dict[str, float] = {"wide": 1.00, "medium": 0.82, "tight": 0.68}


def new_id() -> str:
    return uuid.uuid4().hex[:8]


class Interval(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self):
        if self.end <= self.start:
            raise ValueError(f"intervalle vide ou inversé: {self.start} -> {self.end}")
        return self

    @property
    def dur(self) -> float:
        return self.end - self.start


class FramingEvent(BaseModel):
    op: Literal["framing"] = "framing"
    id: str = Field(default_factory=new_id)
    t: float = Field(ge=0, description="Seconde sur la timeline de SORTIE")
    value: Framing
    transition: Transition = "cut"
    # A2 : à quel point cet événement est justifié (0-1) + pourquoi, posés par
    # le réalisateur LLM. Optionnels (défaut neutre 0.5, sans raison) pour
    # rester rétro-compatibles avec les EDL déjà stockés. Permet à « moins de
    # zooms » de retirer les événements les MOINS justifiés au lieu d'un
    # index arbitraire, et affiche la raison au survol dans l'interface.
    motivation: float = Field(default=0.5, ge=0.0, le=1.0)
    raison: str = ""


class EmphasisEvent(BaseModel):
    op: Literal["emphasis"] = "emphasis"
    id: str = Field(default_factory=new_id)
    t: float = Field(ge=0)
    word_index: int = Field(ge=0, description="Index dans le transcript nettoyé")
    style: Literal["pop", "underline", "scale"] = "pop"
    motivation: float = Field(default=0.5, ge=0.0, le=1.0)
    raison: str = ""


class HoldOnSpeakerEvent(BaseModel):
    op: Literal["hold_on_speaker"] = "hold_on_speaker"
    id: str = Field(default_factory=new_id)
    t: float = Field(ge=0)
    face_id: str


class SpeedEvent(BaseModel):
    op: Literal["speed"] = "speed"
    id: str = Field(default_factory=new_id)
    t: float = Field(ge=0)
    factor: float = Field(ge=0.5, le=2.0)


class TextOverlayEvent(BaseModel):
    """Texte libre incrusté (titre, accroche, mention) — F8.3/F5 de l'audit
    (AUDIT.md) : absent jusqu'ici, ajouté suite à une demande utilisateur
    concrète ("ajoute un titre fixe en haut"). Reste dans le périmètre EDL
    (pas de piste vidéo supplémentaire, juste un texte dessiné par-dessus)."""
    op: Literal["text_overlay"] = "text_overlay"
    id: str = Field(default_factory=new_id)
    t: float = Field(ge=0, description="Seconde de SORTIE où le texte apparaît")
    duration: float = Field(default=1e9, gt=0, description="Durée d'affichage ; par défaut tout le clip")
    text: str = Field(min_length=1, max_length=200)
    position: Literal["top", "center", "bottom"] = "top"
    size: int = Field(default=54, ge=16, le=160)
    color: str = "#FFFFFF"


Event = Annotated[
    Union[FramingEvent, EmphasisEvent, HoldOnSpeakerEvent, SpeedEvent, TextOverlayEvent],
    Field(discriminator="op"),
]

# --------------------------------------------------------------------------
# Couches statiques
# --------------------------------------------------------------------------


class Background(BaseModel):
    mode: Literal["blur", "solid", "none"] = "blur"
    sigma: int = Field(default=25, ge=0, le=100)
    color: str = "#1A1A1A"


class Captions(BaseModel):
    enabled: bool = True
    # "plain" = couleur uniforme (défaut SortClip). "karaoke" = mot-à-mot animé.
    style: Literal["karaoke", "plain"] = "plain"
    y: float = Field(default=0.78, ge=0.0, le=1.0, description="Hauteur relative")
    font: str = "DejaVu Sans"
    size: int = Field(default=64, ge=20, le=160)
    bold: bool = False
    primary: str = "#FFFFFF"
    highlight: str = "#F39200"
    words_per_line: int = Field(default=4, ge=1, le=10)


class Watermark(BaseModel):
    # Filigrane du plan gratuit (invisible pour les plans payants).
    enabled: bool = False
    text: str = "Sortclip"
    opacity: float = Field(default=0.55, ge=0.0, le=1.0)


class Canvas(BaseModel):
    w: int = 1080
    h: int = 1920
    fps: int = 30
    # F4 : plateforme visée, pour appliquer ses zones de sécurité d'interface
    # (voir sortclip.safe_zones). "default" = aucune zone interdite connue —
    # comportement inchangé pour tout EDL existant (champ additif, rétro-compatible).
    platform: Literal["default", "tiktok", "reels", "shorts"] = "default"


class Source(BaseModel):
    path: str
    duration: float = Field(gt=0)
    # Renseignées par probe_source(). Nécessaires pour figer une taille de
    # premier plan IDENTIQUE sur tous les segments : `concat` refuse des
    # dimensions ou des SAR hétérogènes.
    width: int | None = None
    height: int | None = None


# --------------------------------------------------------------------------
# L'EDL
# --------------------------------------------------------------------------


class EDL(BaseModel):
    version: int = 1
    preset: str | None = None
    canvas: Canvas = Field(default_factory=Canvas)
    source: Source
    keeps: list[Interval] = Field(min_length=1, description="TEMPS SOURCE")
    events: list[Event] = Field(default_factory=list, description="TEMPS SORTIE")
    background: Background = Field(default_factory=Background)
    captions: Captions = Field(default_factory=Captions)
    watermark: Watermark = Field(default_factory=Watermark)

    @property
    def out_duration(self) -> float:
        return sum(k.dur for k in self.keeps)

    def out_to_src(self, t_out: float) -> float:
        """Convertit une seconde de la timeline de sortie vers la timeline source."""
        acc = 0.0
        for k in self.keeps:
            if t_out < acc + k.dur:
                return k.start + (t_out - acc)
            acc += k.dur
        return self.keeps[-1].end

    def event_by_id(self, event_id: str) -> Event | None:
        return next((e for e in self.events if e.id == event_id), None)

    def framing_spans(self) -> list[tuple[float, float, str]]:
        """Découpe la timeline de sortie en segments à cadrage constant.

        Retourne [(debut, fin, cadrage)]. C'est ce que consomme le compilateur :
        un crop fixe par segment, puis concaténation. Plus robuste qu'une
        expression FFmpeg variable dans le temps.
        """
        marks = sorted(
            [e for e in self.events if e.op == "framing"], key=lambda e: e.t
        )
        cuts: list[tuple[float, str]] = [(0.0, "wide")]
        for e in marks:
            if e.t <= 0.0:
                cuts[0] = (0.0, e.value)
            else:
                cuts.append((e.t, e.value))

        spans: list[tuple[float, float, str]] = []
        for i, (t, value) in enumerate(cuts):
            end = cuts[i + 1][0] if i + 1 < len(cuts) else self.out_duration
            if end - t > 1e-3:
                spans.append((t, end, value))
        return spans or [(0.0, self.out_duration, "wide")]
