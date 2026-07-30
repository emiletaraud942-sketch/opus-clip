"""Audit A4 — « Lance deux fois le même traitement sur la même source. Les EDL
produits sont-ils identiques ? » Exécuté réellement sur la portion du pipeline
qui ne dépend pas d'un réseau/API (build_edl est pur, déterministe par
construction — SAUF l'id de chaque événement, généré aléatoirement à CHAQUE
construction). Le réalisateur LLM et select_clips_with_llm dépendent d'un
appel réseau réel (Anthropic) : NON VÉRIFIABLE dans cet environnement, voir
AUDIT.md section Angles morts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.build import build_edl
from sortclip.edl import FramingEvent

words = [
    {"word": f"mot{i}", "start": i * 0.4, "end": i * 0.4 + 0.35}
    for i in range(30)
]

edl1, cleaned1 = build_edl(source_path="x.mp4", source_duration=60, words=words, preset_name=None)
edl2, cleaned2 = build_edl(source_path="x.mp4", source_duration=60, words=words, preset_name=None)

d1 = edl1.model_dump(mode="json")
d2 = edl2.model_dump(mode="json")

print("EDL strictement identiques (dump complet) ?", d1 == d2)

d1_no_ids = {k: v for k, v in d1.items()}
d2_no_ids = {k: v for k, v in d2.items()}


def strip_ids(obj):
    if isinstance(obj, dict):
        return {k: strip_ids(v) for k, v in obj.items() if k != "id"}
    if isinstance(obj, list):
        return [strip_ids(v) for v in obj]
    return obj


print("EDL identiques UNE FOIS les champs `id` retirés ?", strip_ids(d1) == strip_ids(d2))
print()
print("keeps identiques ?", d1["keeps"] == d2["keeps"])
print("out_duration identique ?", edl1.out_duration == edl2.out_duration)
print("nb events identique ?", len(d1["events"]) == len(d2["events"]), "(", len(d1["events"]), ")")

# build_edl (sans réalisateur) ne produit aucun événement (0 ci-dessus) : le
# point sur l'id aléatoire n'est donc pas démontré par CE chemin. Vérification
# directe et déterminante à la place :
e1 = FramingEvent(t=1.0, value="tight")
e2 = FramingEvent(t=1.0, value="tight")
print()
print("Preuve directe (sortclip/edl.py:new_id) — deux FramingEvent construits")
print("avec les MÊMES arguments :")
print("  id 1 =", e1.id, " / id 2 =", e2.id, " / identiques ?", e1.id == e2.id)

print()
print("VERDICT : la partie SANS IA (nettoyage transcript -> keeps -> preset) est")
print("déterministe UNE FOIS les ids retirés, mais deux appels produisent des EDL")
print("qui ne sont PAS byte-identiques (le champ `id` de chaque futur événement")
print("est un uuid4 généré à la construction — voir sortclip/edl.py:new_id()).")
print("La sélection des clips et le réalisateur (appels Anthropic réels,")
print("temperature=0) ne sont PAS exécutables dans cet environnement : NON VÉRIFIÉ.")
