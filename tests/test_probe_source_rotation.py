"""Bug réel signalé en prod : "l'image est totalement déformée" sur une vidéo
uploadée (typiquement filmée au téléphone en portrait, mais ENCODÉE en
paysage avec une métadonnée de rotation 90/270°). probe_source() doit tenir
compte de cette rotation pour renvoyer les dimensions RÉELLEMENT affichées,
sinon foreground_size()/le recadrage calculent sur le mauvais ratio d'aspect.

Teste avec un subprocess.run FACTICE (pas de vrai ffprobe/vraie vidéo dans ce
bac à sable) — vérifie que probe_source() interprète correctement la sortie
JSON d'ffprobe, pas le comportement réel d'ffprobe lui-même."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.compile import probe_source, _rotation_degrees


def _fake_ffprobe_result(stream: dict):
    class _Result:
        stdout = json.dumps({"streams": [stream]})
    return _Result()


def test_rotation_degrees_from_classic_rotate_tag():
    assert _rotation_degrees({"tags": {"rotate": "90"}}) == 90
    assert _rotation_degrees({"tags": {"rotate": "270"}}) == 270
    assert _rotation_degrees({"tags": {"rotate": "0"}}) == 0


def test_rotation_degrees_from_side_data_modern_format():
    assert _rotation_degrees({"side_data_list": [{"rotation": -90}]}) == 270
    assert _rotation_degrees({"side_data_list": [{"rotation": 90}]}) == 90


def test_rotation_degrees_absent_returns_zero():
    assert _rotation_degrees({}) == 0
    assert _rotation_degrees({"tags": {}}) == 0


def test_probe_source_swaps_dimensions_when_rotated_90():
    # Vidéo encodée en paysage (1920x1080) mais avec un tag de rotation 90° :
    # affichée en portrait -> les dimensions RÉELLES sont 1080x1920.
    stream = {"width": 1920, "height": 1080, "tags": {"rotate": "90"}}
    with patch("sortclip.compile.subprocess.run", return_value=_fake_ffprobe_result(stream)):
        w, h = probe_source("fake.mp4")
    assert (w, h) == (1080, 1920)


def test_probe_source_no_swap_when_no_rotation():
    stream = {"width": 1920, "height": 1080}
    with patch("sortclip.compile.subprocess.run", return_value=_fake_ffprobe_result(stream)):
        w, h = probe_source("fake.mp4")
    assert (w, h) == (1920, 1080)


def test_probe_source_no_swap_when_rotation_180():
    # 180° n'inverse pas largeur/hauteur (juste retourné, pas pivoté).
    stream = {"width": 1920, "height": 1080, "tags": {"rotate": "180"}}
    with patch("sortclip.compile.subprocess.run", return_value=_fake_ffprobe_result(stream)):
        w, h = probe_source("fake.mp4")
    assert (w, h) == (1920, 1080)


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
