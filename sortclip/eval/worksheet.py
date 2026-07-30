"""
Outil d'annotation à faible friction pour le jeu evals/subs/ (Partie 0.1).

Plutôt que de demander d'écrire un JSON d'annotation à la main, on part de la
transcription DÉJÀ produite par AssemblyAI sur un vrai clip (le "candidat"),
on en extrait ~20 mots répartis dans le clip, et on écrit un tableau (CSV)
que l'utilisateur remplit en écoutant le clip — il corrige seulement ce qui
est faux, il ne retranscrit pas depuis zéro.

Usage :
    python -m sortclip.eval worksheet make --transcript words.json --out feuille.csv
    (l'utilisateur écoute le clip et remplit les colonnes mot_correct/
    debut_correct_s/correct)
    python -m sortclip.eval worksheet ingest --transcript words.json \\
        --csv feuille_remplie.csv --clip-id mon_clip --category musique_continue \\
        --out evals/subs/
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

WORKSHEET_COLUMNS = [
    "index", "mot_propose", "debut_propose_s", "fin_propose_s",
    "correct_oui_non", "mot_correct", "debut_correct_s",
]


def pick_sample_words(words: list[dict], n: int = 20) -> list[tuple[int, dict]]:
    """Choisit `n` mots répartis RÉGULIÈREMENT dans le clip (pas les n
    premiers — ça couvrirait mal la fin) : renvoie [(index_original, mot), ...].
    Si le clip a moins de `n` mots, les prend tous."""
    if not words:
        return []
    n = min(n, len(words))
    if n == len(words):
        return list(enumerate(words))
    step = len(words) / n
    indices = sorted({int(i * step) for i in range(n)})
    return [(i, words[i]) for i in indices]


def write_worksheet_csv(words: list[dict], out_path: str | Path, n: int = 20) -> Path:
    """Écrit le tableau à remplir. Colonnes déjà pré-remplies (mot_propose,
    debut_propose_s, fin_propose_s) avec la sortie AssemblyAI existante ;
    l'utilisateur écoute le clip et ne remplit que correct_oui_non (et
    mot_correct/debut_correct_s si "non")."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sample = pick_sample_words(words, n=n)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(WORKSHEET_COLUMNS)
        for original_index, word in sample:
            w.writerow([
                original_index, word.get("word", ""),
                round(float(word.get("start", 0.0)), 3),
                round(float(word.get("end", 0.0)), 3),
                "", "", "",
            ])
    return out_path


def read_worksheet_csv(csv_path: str | Path) -> list[dict]:
    """Relit un tableau REMPLI et produit la liste de mots-repères de
    référence : utilise la correction si `correct_oui_non` vaut "non" (ou
    toute valeur différente de "oui"/vide), sinon garde la valeur proposée.
    Une ligne dont ni la case "oui" ni de correction n'est renseignée est
    ignorée (l'utilisateur n'a pas encore écouté ce mot) — jamais d'erreur,
    juste moins de repères."""
    rows = []
    with Path(csv_path).open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            confirmed = (row.get("correct_oui_non") or "").strip().lower()
            if confirmed not in ("oui", "non"):
                continue   # pas encore vérifié par l'utilisateur -> ignoré
            if confirmed == "oui":
                word = row["mot_propose"]
                start = float(row["debut_propose_s"])
                end = float(row["fin_propose_s"])
            else:
                word = (row.get("mot_correct") or "").strip() or row["mot_propose"]
                start_raw = (row.get("debut_correct_s") or "").strip()
                start = float(start_raw) if start_raw else float(row["debut_propose_s"])
                # Durée du mot conservée par défaut (pas de fin corrigée dans
                # le tableau, pour rester simple à remplir) : approximation
                # raisonnable pour un mot-repère.
                end = start + (float(row["fin_propose_s"]) - float(row["debut_propose_s"]))
            rows.append({"word": word, "start": start, "end": end})
    return rows


def ingest_worksheet(transcript_words: list[dict], csv_path: str | Path,
                     clip_id: str, category: str, out_root: str | Path = "evals/subs") -> Path:
    """Écrit evals/subs/<clip_id>/{meta.json, reference.json, candidate.json}
    à partir du tableau rempli — le candidate.json est la transcription
    ORIGINALE complète (celle qu'on veut évaluer), reference.json ne contient
    que les mots-repères vérifiés par l'utilisateur (read_worksheet_csv)."""
    clip_dir = Path(out_root) / clip_id
    clip_dir.mkdir(parents=True, exist_ok=True)
    reference_words = read_worksheet_csv(csv_path)
    (clip_dir / "meta.json").write_text(
        json.dumps({"category": category}, ensure_ascii=False, indent=2))
    (clip_dir / "reference.json").write_text(
        json.dumps({"words": reference_words}, ensure_ascii=False, indent=2))
    (clip_dir / "candidate.json").write_text(
        json.dumps({"words": transcript_words}, ensure_ascii=False, indent=2))
    return clip_dir
