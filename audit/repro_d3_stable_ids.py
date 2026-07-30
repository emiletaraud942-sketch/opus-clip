"""Audit D3 — « Les événements portent-ils des identifiants stables, ou sont-ils
référencés par position ? » Test décisif demandé : supprime le 3e événement,
modifie celui qui était le 5e. Le bon événement doit être touché."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.edl import EDL, Source, Canvas, Captions, Background, FramingEvent
from sortclip.patch import PatchOp, apply_patch

events = [FramingEvent(t=float(i), value="tight") for i in range(6)]  # 6 événements, index 0..5
edl0 = EDL(
    source=Source(path="x.mp4", duration=60), canvas=Canvas(w=1080, h=1920, fps=30),
    keeps=[{"start": 0, "end": 30}], events=events,
    captions=Captions(), background=Background(),
)

third_event_id = edl0.events[2].id     # "3e événement" (index 2)
fifth_event_id = edl0.events[4].id     # "celui qui était le 5e" (index 4)
print("Avant : 3e événement id =", third_event_id, "(t=", edl0.events[2].t, ")")
print("Avant : 5e événement id =", fifth_event_id, "(t=", edl0.events[4].t, ")")

# Supprime le 3e (par id), PUIS modifie ce qui était le 5e (par id, pas par
# position — si l'implémentation référençait par position, ceci toucherait
# le mauvais événement après la suppression qui décale les index).
result = apply_patch(edl0, [
    PatchOp(action="remove", event_id=third_event_id),
    PatchOp(action="modify", event_id=fifth_event_id, field="value", value="wide"),
])

remaining = {e.id: e for e in result.edl.events}
print()
print("3e événement toujours présent ?", third_event_id in remaining)
print("5e événement présent, valeur modifiée ?",
      fifth_event_id in remaining and remaining[fifth_event_id].value == "wide")
print("Nombre d'événements restants :", len(result.edl.events), "(attendu 5)")
print("Patches appliqués :", len(result.applied), "/ rejetés :", len(result.rejected))

ok = (third_event_id not in remaining and fifth_event_id in remaining
      and remaining[fifth_event_id].value == "wide" and len(result.edl.events) == 5)
print()
print("VERDICT :", "identifiants STABLES — le bon événement est touché après suppression" if ok
      else "ÉCHEC — référencement par position suspecté")
