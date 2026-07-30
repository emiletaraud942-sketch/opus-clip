"""Tests H1 (chaîne audio) — vérifie la CONSTRUCTION du graphe FFmpeg et
l'extraction JSON de loudnorm. N'exécute PAS ffmpeg (absent de cet
environnement) : la validité réelle du rendu doit être vérifiée sur Modal,
où ffmpeg est présent. Voir le rapport final pour cette limite explicite.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

import sortclip.compile as compile_mod
from sortclip.edl import EDL, Source, Canvas, Captions, Background
from sortclip.compile import build_filter_complex, build_command, measure_loudness, _audio_keeps_filter


def _edl(n_keeps=1):
    keeps = [{"start": i * 10.0, "end": i * 10.0 + 5.0} for i in range(n_keeps)]
    return EDL(
        source=Source(path="x.mp4", duration=60, width=1920, height=1080),
        canvas=Canvas(w=1080, h=1920, fps=30),
        out_duration=sum(k["end"] - k["start"] for k in keeps),
        keeps=keeps, events=[], captions=Captions(enabled=False), background=Background(),
    )


def test_filter_complex_ends_with_out_unchanged_contract():
    # Contrat existant (test_edl_compile.py) : ne doit jamais casser.
    fc = build_filter_complex(_edl())
    assert fc.endswith("[out]")


def test_audio_chain_toggle_controls_filter_graph():
    # Le flag ENABLE_AUDIO_CHAIN_H1 est une bascule manuelle de test (peut
    # valoir True le temps d'une écoute en prod, cf. rapport du chantier) :
    # ce test vérifie que le graphe suit bien la valeur ACTUELLE du flag,
    # sans supposer laquelle est en place au moment du test.
    original = compile_mod.ENABLE_AUDIO_CHAIN_H1
    try:
        compile_mod.ENABLE_AUDIO_CHAIN_H1 = False
        fc_off = build_filter_complex(_edl())
        assert "[caf]" in fc_off
        assert "highpass=f=80" not in fc_off
        assert "loudnorm=" not in fc_off
    finally:
        compile_mod.ENABLE_AUDIO_CHAIN_H1 = original


def test_filter_complex_contains_audio_chain_when_enabled():
    # Le code H1 reste fonctionnel — activable en forçant le flag (le
    # rapport du chantier explique pourquoi il est désactivé par défaut).
    compile_mod.ENABLE_AUDIO_CHAIN_H1 = True
    try:
        fc = build_filter_complex(_edl())
        assert "[ca]" in fc
        assert "highpass=f=80" in fc
        assert "acompressor=" in fc
        assert "loudnorm=" in fc
        assert "[caf]" in fc
    finally:
        compile_mod.ENABLE_AUDIO_CHAIN_H1 = False


def test_filter_complex_single_pass_fallback_without_measurement():
    compile_mod.ENABLE_AUDIO_CHAIN_H1 = True
    try:
        fc = build_filter_complex(_edl(), loudness_measurement=None)
        assert "measured_I" not in fc   # repli une passe : pas de paramètres mesurés
    finally:
        compile_mod.ENABLE_AUDIO_CHAIN_H1 = False


def test_filter_complex_two_pass_with_measurement():
    compile_mod.ENABLE_AUDIO_CHAIN_H1 = True
    try:
        measurement = {
            "measured_I": -20.1, "measured_TP": -3.2,
            "measured_LRA": 8.4, "measured_thresh": -30.5, "target_offset": 0.7,
        }
        fc = build_filter_complex(_edl(), loudness_measurement=measurement)
        assert "measured_I=-20.1" in fc
        assert "linear=true" in fc
    finally:
        compile_mod.ENABLE_AUDIO_CHAIN_H1 = False


def test_build_command_maps_processed_audio_not_raw():
    cmd = build_command(_edl(), "/tmp/out_test_h1.mp4")
    assert "[caf]" in cmd
    assert "[ca]" not in cmd   # on ne mappe jamais l'audio brut non traité


def test_audio_keeps_filter_multi_keep_uses_concat():
    fc, label = _audio_keeps_filter(_edl(n_keeps=3))
    assert "concat=n=3:v=0:a=1" in fc
    assert label.endswith("_out")


def test_audio_keeps_filter_single_keep_uses_anull():
    fc, label = _audio_keeps_filter(_edl(n_keeps=1))
    assert "anull" in fc


def test_measure_loudness_json_extraction_logic():
    # Reproduit la logique d'extraction du dernier bloc JSON du stderr de
    # loudnorm, sans lancer ffmpeg (on simule un stderr typique).
    fake_stderr = (
        "[Parsed_loudnorm_0 @ 0x...] \n"
        "{\n"
        '"input_i" : "-23.10",\n'
        '"input_tp" : "-4.50",\n'
        '"input_lra" : "6.20",\n'
        '"input_thresh" : "-33.40",\n'
        '"target_offset" : "0.90"\n'
        "}\n"
    )
    matches = re.findall(r"\{[^{}]*\}", fake_stderr, flags=re.DOTALL)
    assert matches, "le bloc JSON doit être trouvé"
    stats = json.loads(matches[-1])
    assert float(stats["input_i"]) == -23.10


def test_measure_loudness_returns_none_without_ffmpeg():
    # ffmpeg n'est pas installé dans cet environnement de test — on vérifie
    # que l'absence ne lève JAMAIS (comportement best-effort attendu).
    result = measure_loudness(_edl())
    assert result is None


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
