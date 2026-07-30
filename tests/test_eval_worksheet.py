"""Outil d'annotation à faible friction (evals/subs/, Partie 0.1) —
sortclip.eval.worksheet."""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.eval.worksheet import (
    pick_sample_words, write_worksheet_csv, read_worksheet_csv, ingest_worksheet,
)


def _words(n):
    return [{"word": f"mot{i}", "start": i * 1.0, "end": i * 1.0 + 0.4} for i in range(n)]


def test_pick_sample_words_returns_all_when_fewer_than_n():
    words = _words(5)
    sample = pick_sample_words(words, n=20)
    assert len(sample) == 5
    assert [i for i, _ in sample] == [0, 1, 2, 3, 4]


def test_pick_sample_words_spreads_across_clip():
    words = _words(100)
    sample = pick_sample_words(words, n=20)
    assert len(sample) == 20
    indices = [i for i, _ in sample]
    assert indices[0] < 10           # couvre le début
    assert indices[-1] > 90          # couvre la fin
    assert indices == sorted(set(indices))   # pas de doublon, croissant


def test_write_worksheet_csv_prefills_proposed_values(tmp_path):
    words = _words(3)
    out = write_worksheet_csv(words, tmp_path / "feuille.csv", n=3)
    with out.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert rows[0]["mot_propose"] == "mot0"
    assert rows[0]["debut_propose_s"] == "0.0"
    assert rows[0]["correct_oui_non"] == ""   # à remplir par l'utilisateur


def test_read_worksheet_csv_keeps_proposed_when_marked_oui(tmp_path):
    csv_path = tmp_path / "feuille.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["index", "mot_propose", "debut_propose_s", "fin_propose_s",
                    "correct_oui_non", "mot_correct", "debut_correct_s"])
        w.writerow([0, "bonjour", "1.0", "1.4", "oui", "", ""])
    result = read_worksheet_csv(csv_path)
    assert result == [{"word": "bonjour", "start": 1.0, "end": 1.4}]


def test_read_worksheet_csv_applies_correction_when_marked_non(tmp_path):
    csv_path = tmp_path / "feuille.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["index", "mot_propose", "debut_propose_s", "fin_propose_s",
                    "correct_oui_non", "mot_correct", "debut_correct_s"])
        w.writerow([0, "bonjours", "1.0", "1.4", "non", "bonjour", "1.05"])
    result = read_worksheet_csv(csv_path)
    assert result == [{"word": "bonjour", "start": 1.05, "end": 1.45}]


def test_read_worksheet_csv_ignores_unverified_rows(tmp_path):
    csv_path = tmp_path / "feuille.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["index", "mot_propose", "debut_propose_s", "fin_propose_s",
                    "correct_oui_non", "mot_correct", "debut_correct_s"])
        w.writerow([0, "bonjour", "1.0", "1.4", "", "", ""])   # pas encore vérifié
    assert read_worksheet_csv(csv_path) == []


def test_ingest_worksheet_writes_all_three_files(tmp_path):
    transcript = _words(5)
    csv_path = tmp_path / "feuille.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["index", "mot_propose", "debut_propose_s", "fin_propose_s",
                    "correct_oui_non", "mot_correct", "debut_correct_s"])
        w.writerow([0, "mot0", "0.0", "0.4", "oui", "", ""])
    clip_dir = ingest_worksheet(transcript, csv_path, "clip_test", "musique_continue",
                               out_root=tmp_path / "subs")
    meta = json.loads((clip_dir / "meta.json").read_text())
    reference = json.loads((clip_dir / "reference.json").read_text())
    candidate = json.loads((clip_dir / "candidate.json").read_text())
    assert meta["category"] == "musique_continue"
    assert reference["words"] == [{"word": "mot0", "start": 0.0, "end": 0.4}]
    assert candidate["words"] == transcript


if __name__ == "__main__":
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"])
    sys.exit(r.returncode)
