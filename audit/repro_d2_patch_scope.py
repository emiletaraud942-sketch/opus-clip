"""Audit D2 — « Une retouche modifie-t-elle uniquement ce qui est vise ? »
Test décisif demandé : applique « moins de zooms » sur un EDL contenant aussi
des emphases et un style de sous-titres, compare les deux EDL champ par champ.
Exécuté réellement (pas lu dans le code) : sortclip est pur Python, importable
sans ffmpeg/modal/API."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.edl import EDL, Source, Canvas, Captions, Background, FramingEvent, EmphasisEvent
from sortclip.patch import apply_text_adjustment

edl0 = EDL(
    source=Source(path="x.mp4", duration=60),
    canvas=Canvas(w=1080, h=1920, fps=30),
    keeps=[{"start": 0, "end": 30}],
    events=[
        FramingEvent(t=1.0, value="tight", motivation=0.9, raison="punchline"),
        FramingEvent(t=5.0, value="wide", motivation=0.2, raison="remplissage"),
        FramingEvent(t=9.0, value="tight", motivation=0.8, raison="reaction"),
        FramingEvent(t=13.0, value="wide", motivation=0.1, raison="remplissage"),
        EmphasisEvent(t=3.0, word_index=4, style="pop", motivation=0.7, raison="mot fort"),
    ],
    captions=Captions(style="karaoke", size=60, primary="#FFEB3B", bold=True),
    background=Background(mode="blur", sigma=25),
)

edl2, notes = apply_text_adjustment(edl0, "moins de zooms")

d0 = edl0.model_dump(mode="json")
d2 = edl2.model_dump(mode="json")

changed_top_level_keys = [k for k in d0 if d0[k] != d2[k]]
print("Notes retournées :", notes)
print("Clés de premier niveau modifiées :", changed_top_level_keys)
print()
print("Captions inchangées ?", d0["captions"] == d2["captions"])
print("Background inchangé ?", d0["background"] == d2["background"])
print("Canvas inchangé ?", d0["canvas"] == d2["canvas"])
print("Keeps inchangés ?", d0["keeps"] == d2["keeps"])

emphases_before = [e for e in d0["events"] if e["op"] == "emphasis"]
emphases_after = [e for e in d2["events"] if e["op"] == "emphasis"]
print("Emphase inchangée (hors id, non pertinent) ?",
      {k: v for k, v in emphases_before[0].items() if k != "id"} ==
      {k: v for k, v in emphases_after[0].items() if k != "id"} if emphases_after else "EMPHASE DISPARUE")

framings_before = len([e for e in d0["events"] if e["op"] == "framing"])
framings_after = len([e for e in d2["events"] if e["op"] == "framing"])
print(f"Cadrages : {framings_before} -> {framings_after}")

print()
print("VERDICT :", "SEUL le champ visé (cadrages) a changé" if not changed_top_level_keys or changed_top_level_keys == ["events"]
      else f"D'AUTRES CHAMPS ONT CHANGÉ : {changed_top_level_keys}")
