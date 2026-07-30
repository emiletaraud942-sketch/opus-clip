"""Audit C4 — « Que se passe-t-il si un mot transcrit contient un caractère
spécial du format ASS (accolade, antislash) ou un emoji ? » Injecte ces
caractères dans un transcript et inspecte le fichier .ass généré. N'exécute
PAS ffmpeg/libass (absent de cet environnement) : vérifie seulement la
CONSTRUCTION du texte, pas le comportement de rendu réel (voir Angles morts)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sortclip.captions import build_ass
from sortclip.edl import Captions, Canvas

DANGEROUS_WORDS = [
    "normal",
    "avec{accolade",       # ouvre une balise de style ASS non fermée
    "avec}accolade",       # ferme une balise qui n'a jamais été ouverte
    "anti\\slash",         # antislash : introduit une commande ASS (\k, \N, \fad...)
    "emoji😀present",       # emoji
    "café",                # accent français simple (contrôle encodage)
    "{\\k500}injection",   # tentative explicite d'injecter une balise karaoké
]

words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(DANGEROUS_WORDS)]
canvas = Canvas(w=1080, h=1920, fps=30)

for style in ("plain", "karaoke"):
    captions = Captions(enabled=True, style=style, words_per_line=4)
    path = f"/tmp/audit_c4_{style}.ass"
    build_ass(words, captions, canvas, path)
    content = Path(path).read_text(encoding="utf-8")
    print(f"=== style={style} ===")
    for line in content.splitlines():
        if line.startswith("Dialogue"):
            print(" ", line)
    print()

print("Vérifications :")
content_plain = Path("/tmp/audit_c4_plain.ass").read_text(encoding="utf-8")
content_karaoke = Path("/tmp/audit_c4_karaoke.ass").read_text(encoding="utf-8")

# Un antislash ou une accolade NON ÉCHAPPÉS dans le texte source finissent
# TELS QUELS dans le Dialogue -> libass les interprétera comme une commande.
raw_brace_present = "avec{accolade" in content_plain or "avec{accolade" in content_karaoke
raw_backslash_present = "anti\\slash" in content_plain or "anti\\slash" in content_karaoke
injection_present = "{\\k500}injection" in content_plain or "{\\k500}injection" in content_karaoke
accents_survive = "café" in content_plain and "café" in content_karaoke

print("Accolade brute (non échappée) injectée telle quelle dans le Dialogue ?", raw_brace_present)
print("Antislash brut (non échappé) injecté tel quel dans le Dialogue ?", raw_backslash_present)
print("Tentative explicite de balise karaoké non neutralisée ?", injection_present)
print("Accents français (café) survivent jusqu'au fichier ?", accents_survive)
print()
print("VERDICT :", "AUCUN échappement des caractères spéciaux ASS avant écriture du Dialogue"
      if (raw_brace_present or raw_backslash_present or injection_present)
      else "échappement détecté")
