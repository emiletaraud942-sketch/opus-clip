"""
CLI du harnais d'évaluation (Partie 0.4).

    python -m sortclip.eval run --set golden --report
    python -m sortclip.eval compare --baseline <report.json> --candidate <report.json>

État réel : ces commandes tournent et produisent un vrai rapport, mais sur le
jeu golden PROVISOIRE (voir evals/golden/README.md) — pas sur les 40 clips réels
annotés demandés, qui n'ont pas pu être constitués en autonomie (pas de corpus
vidéo réel, pas de jugement de qualité de montage sans supervision humaine).

`compare` prend deux fichiers de rapport JSON (produits par `run --report`),
pas directement deux références git : comparer deux commits suppose de pouvoir
RE-GÉNÉRER un candidat pour chaque clip (appel réalisateur + rendu), ce qui
demande la clé API Anthropic, un accès réseau et de vraies sources vidéo —
indisponibles dans cet environnement. Le point d'entrée reste prêt : sur une
machine avec ces accès, ajouter le rendu réel en amont de `run` suffit.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import secrets
import shutil
import sys
import time
from pathlib import Path

from .golden import list_golden_clips, GoldenClip
from .metrics import (
    framing_agreement,
    emphasis_precision_recall,
    event_density_delta,
    validator_rejection_rate,
    instruction_satisfaction,
    is_stable,
)


def _evaluate_clip(clip: GoldenClip) -> dict:
    """Calcule les métriques disponibles pour un clip golden. Si le clip n'a
    pas de candidat (pas de rendu re-généré), on ne calcule que ce qui ne
    demande pas de candidat (rien, en pratique) et on le signale."""
    result = {"clip_id": clip.clip_id, "genre": clip.genre, "difficult": clip.difficult}

    if clip.candidate is None:
        result["status"] = "no_candidate"
        result["note"] = (
            "Aucun candidat régénéré pour ce clip (nécessite l'appel réalisateur "
            "+ rendu réel, indisponibles dans cet environnement). Métriques non "
            "calculables, hors densité/rejet fournis directement dans le candidat "
            "s'il existe."
        )
        return result

    result["status"] = "evaluated"
    ref, cand = clip.reference, clip.candidate

    result["framing_agreement"] = framing_agreement(
        cand.get("framing_spans", []), ref.get("framing_spans", []),
        out_duration=ref.get("out_duration", 0.0),
    )
    p, r = emphasis_precision_recall(
        cand.get("emphasis_word_indices", []), ref.get("emphasis_word_indices", [])
    )
    result["emphasis_precision"] = p
    result["emphasis_recall"] = r
    result["event_density_delta"] = event_density_delta(
        cand.get("n_events", 0), ref.get("n_events", 0), ref.get("out_duration", 0.0)
    )
    result["validator_rejection_rate"] = validator_rejection_rate(
        cand.get("n_rejected", 0), cand.get("n_emitted", 0)
    )
    if "repeat_edl" in cand:
        result["stable"] = is_stable(cand["edl"], cand["repeat_edl"])
    return result


def cmd_run(args: argparse.Namespace) -> int:
    t0 = time.time()
    clips = list_golden_clips(args.set)
    per_clip = [_evaluate_clip(c) for c in clips]

    evaluated = [r for r in per_clip if r["status"] == "evaluated"]
    summary = {
        "set": args.set,
        "n_clips_declared": len(clips),
        "n_clips_evaluated": len(evaluated),
        "elapsed_seconds": round(time.time() - t0, 2),
        "clips": per_clip,
    }
    if evaluated:
        for key in ("framing_agreement", "emphasis_precision", "emphasis_recall",
                    "event_density_delta", "validator_rejection_rate"):
            vals = [r[key] for r in evaluated if key in r]
            if vals:
                summary[f"mean_{key}"] = round(sum(vals) / len(vals), 4)
        instr_results = [r.get("instruction_ok") for r in evaluated if "instruction_ok" in r]
        summary["instruction_satisfaction"] = instruction_satisfaction(instr_results)
        stability = [r["stable"] for r in evaluated if "stable" in r]
        summary["stability_rate"] = (sum(1 for s in stability if s) / len(stability)
                                     if stability else None)

    print(f"[eval] {len(clips)} clip(s) déclaré(s) dans le jeu « {args.set} », "
          f"{len(evaluated)} évalué(s) (avec candidat), en {summary['elapsed_seconds']}s.")
    if len(clips) == 0:
        print("[eval] AUCUN clip dans ce jeu — voir evals/golden/README.md : "
              "le jeu de référence réel n'a pas pu être constitué en autonomie.")
    elif not evaluated:
        print("[eval] Aucun clip n'a de candidat régénéré : rien à comparer "
              "tant qu'un rendu réel (API + audio) n'est pas rejoué. Voir la note "
              "par clip dans le rapport.")

    if args.report:
        out_dir = Path("evals/reports")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{args.set}-{int(time.time())}.json"
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"[eval] rapport écrit : {out_path}")

    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    baseline = json.loads(Path(args.baseline).read_text())
    candidate = json.loads(Path(args.candidate).read_text())

    keys = sorted({
        k for k in list(baseline.keys()) + list(candidate.keys())
        if k.startswith("mean_") or k in ("instruction_satisfaction", "stability_rate")
    })
    print(f"{'métrique':32s} {'baseline':>10s} {'candidat':>10s} {'delta':>10s}")
    print("-" * 66)
    any_regression = False
    for k in keys:
        b = baseline.get(k)
        c = candidate.get(k)
        if b is None or c is None:
            print(f"{k:32s} {'—':>10s} {'—':>10s} {'—':>10s}")
            continue
        delta = c - b
        # Pour ces métriques, plus haut = mieux, SAUF *_delta et *_rejection_rate
        # (plus bas = mieux) — le signe de la régression s'inverse en conséquence.
        lower_is_better = "delta" in k or "rejection" in k
        regressed = (delta > 0) if lower_is_better else (delta < 0)
        marker = " ⚠ régression" if regressed else ""
        if regressed:
            any_regression = True
        print(f"{k:32s} {b:10.4f} {c:10.4f} {delta:+10.4f}{marker}")

    if any_regression:
        print("\n⚠ Au moins une métrique se dégrade — revenir en arrière et "
              "documenter l'hypothèse (règle de progression de la mission).")
    return 0


def cmd_pairs(args: argparse.Namespace) -> int:
    """Partie 0.3 : génère des paires anonymisées avant/après, en ordre
    aléatoire, plus une grille de notation CSV vierge (3 critères). N'a besoin
    d'aucune API ni GPU — travaille sur des rendus DÉJÀ produits (fichiers
    .mp4 déposés par l'appelant), un par clip_id, dans les dossiers
    --baseline-dir et --candidate-dir."""
    baseline_dir = Path(args.baseline_dir)
    candidate_dir = Path(args.candidate_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    baseline_files = {p.stem: p for p in baseline_dir.glob("*") if p.is_file()}
    candidate_files = {p.stem: p for p in candidate_dir.glob("*") if p.is_file()}
    common = sorted(set(baseline_files) & set(candidate_files))
    if not common:
        print(f"[eval] aucun clip commun entre {baseline_dir} et {candidate_dir} — "
              "rien à apparier. Dépose des rendus (même nom de fichier des deux "
              "côtés) avant de relancer.")
        return 1

    rng = random.Random(args.seed)
    mapping = []   # pour l'expérimentateur uniquement, jamais montré à l'évaluateur
    for clip_id in common:
        token = secrets.token_hex(4)
        left_is_baseline = rng.random() < 0.5
        left_path = baseline_files[clip_id] if left_is_baseline else candidate_files[clip_id]
        right_path = candidate_files[clip_id] if left_is_baseline else baseline_files[clip_id]
        left_name = f"{token}_A{left_path.suffix}"
        right_name = f"{token}_B{right_path.suffix}"
        shutil.copyfile(left_path, out_dir / left_name)
        shutil.copyfile(right_path, out_dir / right_name)
        mapping.append({
            "clip_id": clip_id, "token": token,
            "A_is": "baseline" if left_is_baseline else "candidate",
            "B_is": "candidate" if left_is_baseline else "baseline",
        })

    # Clé de dépouillement — à ne PAS envoyer à l'évaluateur.
    (out_dir / "_mapping_NE_PAS_DISTRIBUER.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2)
    )

    # Grille de notation vierge, 3 critères, une ligne par paire.
    grid_path = out_dir / "grille_notation.csv"
    with grid_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["token", "A_meilleur_justesse_coupes", "B_meilleur_justesse_coupes",
                    "A_meilleur_cadrage", "B_meilleur_cadrage",
                    "A_meilleur_sous_titres", "B_meilleur_sous_titres", "commentaire"])
        for row in mapping:
            w.writerow([row["token"], "", "", "", "", "", "", ""])

    print(f"[eval] {len(common)} paire(s) anonymisée(s) écrite(s) dans {out_dir}")
    print(f"[eval] grille de notation : {grid_path}")
    print(f"[eval] clé de dépouillement (privée) : {out_dir / '_mapping_NE_PAS_DISTRIBUER.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sortclip.eval")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Évalue un jeu de clips golden.")
    p_run.add_argument("--set", default="golden")
    p_run.add_argument("--report", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_cmp = sub.add_parser("compare", help="Compare deux rapports JSON.")
    p_cmp.add_argument("--baseline", required=True, help="Chemin du rapport JSON de référence.")
    p_cmp.add_argument("--candidate", required=True, help="Chemin du rapport JSON candidat.")
    p_cmp.set_defaults(func=cmd_compare)

    p_pairs = sub.add_parser("pairs", help="Génère des paires anonymisées avant/après pour l'évaluation humaine.")
    p_pairs.add_argument("--baseline-dir", required=True)
    p_pairs.add_argument("--candidate-dir", required=True)
    p_pairs.add_argument("--out", default="evals/pairs")
    p_pairs.add_argument("--seed", type=int, default=None)
    p_pairs.set_defaults(func=cmd_pairs)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
