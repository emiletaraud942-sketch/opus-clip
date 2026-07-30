"""Tests F4 (zones de sécurité par plateforme) — sortclip.safe_zones +
intégration dans le validateur.

Rappel important (voir sortclip/safe_zones.py) : les valeurs de zones sont
DES ORDRES DE GRANDEUR PROVISOIRES, marqués {{À_COMPLÉTER}} — non mesurées
sur les vraies interfaces. Ces tests vérifient le MÉCANISME (clamp, jamais de
régression, plateforme "default" neutre), pas l'exactitude des pourcentages.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.safe_zones import caption_y_is_safe, clamp_caption_y
from sortclip.edl import EDL, Source, Canvas, Captions, Background
from sortclip.validate import validate


def _edl(platform="default", caption_y=0.78):
    return EDL(
        source=Source(path="x.mp4", duration=60),
        canvas=Canvas(w=1080, h=1920, fps=30, platform=platform),
        out_duration=10.0,
        keeps=[{"start": 0, "end": 10}],
        events=[],
        captions=Captions(enabled=True, y=caption_y),
        background=Background(),
    )


def test_default_platform_never_unsafe():
    assert caption_y_is_safe(0.99, "default") is True
    assert clamp_caption_y(0.99, "default") == 0.99   # inchangé


def test_tiktok_deep_bottom_is_unsafe():
    assert caption_y_is_safe(0.95, "tiktok") is False


def test_tiktok_moderate_position_is_safe():
    assert caption_y_is_safe(0.5, "tiktok") is True


def test_clamp_never_lowers_a_safe_position():
    # Une position déjà sûre ne doit JAMAIS être modifiée par clamp.
    assert clamp_caption_y(0.3, "tiktok") == 0.3


def test_clamp_raises_an_unsafe_position_upward():
    clamped = clamp_caption_y(0.95, "tiktok")
    assert clamped < 0.95
    assert caption_y_is_safe(clamped, "tiktok") is True


def test_validate_leaves_default_platform_unaffected():
    edl = _edl(platform="default", caption_y=0.98)
    out, issues = validate(edl, word_count=10)
    assert out.captions.y == 0.98   # aucune plateforme connue -> inchangé
    assert not any("zone d'interface" in i.message for i in issues)


def test_validate_corrects_unsafe_tiktok_caption_position():
    edl = _edl(platform="tiktok", caption_y=0.97)
    out, issues = validate(edl, word_count=10)
    assert out.captions.y < 0.97
    assert caption_y_is_safe(out.captions.y, "tiktok")
    assert any("zone d'interface" in i.message for i in issues)


def test_validate_does_not_touch_safe_tiktok_caption_position():
    edl = _edl(platform="tiktok", caption_y=0.5)
    out, issues = validate(edl, word_count=10)
    assert out.captions.y == 0.5
    assert not any("zone d'interface" in i.message for i in issues)


def test_validate_never_raises_with_captions_disabled():
    edl = _edl(platform="tiktok", caption_y=0.99)
    edl = edl.model_copy(update={"captions": edl.captions.model_copy(update={"enabled": False})})
    out, issues = validate(edl, word_count=10)
    assert out.captions.enabled is False   # inchangé, pas d'erreur
