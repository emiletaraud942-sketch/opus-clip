"""A3 (chantier « correction-sous-titres ») : « ajoute un test unitaire qui
vérifie, sur un clip à trois intervalles conservés, que le premier mot de
chaque intervalle tombe au bon instant de sortie ».

Chaque intervalle gardé (`keeps`, temps SOURCE) devient, en temps SORTIE, une
tranche qui commence exactement où la précédente s'est terminée (les silences
retirés entre les intervalles disparaissent de la timeline de sortie). Le
premier mot de l'intervalle N doit donc démarrer, en sortie, à la somme des
durées des intervalles 0..N-1 — jamais à son horodatage SOURCE brut."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip.captions import map_words_to_output
from sortclip.edl import Interval


def test_first_word_of_each_kept_interval_lands_at_correct_output_instant():
    # Trois intervalles gardés (temps SOURCE), séparés par des silences retirés :
    #   [10.0, 13.0]  (durée 3.0) -> silence retiré [13.0, 20.0]
    #   [20.0, 22.0]  (durée 2.0) -> silence retiré [22.0, 50.0]
    #   [50.0, 54.0]  (durée 4.0)
    keeps = [
        Interval(start=10.0, end=13.0),
        Interval(start=20.0, end=22.0),
        Interval(start=50.0, end=54.0),
    ]
    # Un mot au tout début de chaque intervalle (temps SOURCE).
    words = [
        {"word": "premier", "start": 10.0, "end": 10.4},
        {"word": "deuxieme", "start": 20.0, "end": 20.4},
        {"word": "troisieme", "start": 50.0, "end": 50.4},
    ]
    words_out = map_words_to_output(words, keeps)
    assert len(words_out) == 3

    # Intervalle 0 : aucun décalage avant lui -> le mot démarre à 0.0 en sortie.
    assert words_out[0]["word"] == "premier"
    assert words_out[0]["start"] == 0.0

    # Intervalle 1 : précédé par la durée de l'intervalle 0 (3.0s) -> démarre à 3.0.
    assert words_out[1]["word"] == "deuxieme"
    assert words_out[1]["start"] == 3.0

    # Intervalle 2 : précédé par les durées cumulées des intervalles 0 et 1
    # (3.0 + 2.0 = 5.0s) -> démarre à 5.0, JAMAIS à son horodatage source brut (50.0).
    assert words_out[2]["word"] == "troisieme"
    assert words_out[2]["start"] == 5.0


def test_word_straddling_a_kept_interval_boundary_is_clamped_inside_it():
    # Un mot à cheval sur le début d'un intervalle gardé ne doit compter, en
    # sortie, que la portion RÉELLEMENT dans l'intervalle (pas de silence
    # "gratuit" ajouté à la durée du mot en sortie).
    keeps = [Interval(start=10.0, end=13.0)]
    words = [{"word": "chevauche", "start": 9.5, "end": 10.5}]
    words_out = map_words_to_output(words, keeps)
    assert len(words_out) == 1
    assert words_out[0]["start"] == 0.0          # ramené à l'entrée de l'intervalle
    assert words_out[0]["end"] == 0.5             # seulement 0.5s réellement gardées


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
