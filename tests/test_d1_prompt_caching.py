"""D1 : vérifie que director.direct() et director.adjust_with_text() posent
bien un point de coupure de cache (cache_control) sur le bloc système STABLE,
avec le bon TTL, et JAMAIS sur le contenu variable (hint / consigne) — avec un
client Anthropic factice (aucun appel réseau réel, aucun coût).

Rappel mission : « Place cache_control sur le dernier bloc stable... Ne place
pas le point de coupure sur le bloc variable... TTL d'une heure sur le chemin
du réalisateur, cinq minutes sur le correcteur. »"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip import director


class _FakePlaceEventsBlock:
    type = "tool_use"
    name = "place_events"
    input = {"framings": [], "emphases": []}


class _FakeEditEventsBlock:
    type = "tool_use"
    name = "edit_events"
    input = {"patches": []}


class _FakeResponse:
    def __init__(self, block):
        self.content = [block]
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5,
                                     "cache_creation_input_tokens": 0,
                                     "cache_read_input_tokens": 0})()


class _FakeMessages:
    def __init__(self, block):
        self.block = block
        self.last_call = None

    def create(self, **kwargs):
        self.last_call = kwargs
        return _FakeResponse(self.block)


class _FakeClient:
    def __init__(self, block):
        self.messages = _FakeMessages(block)


def main():
    ok = 0
    words_out = [{"word": "Bonjour", "start": 0.0, "end": 0.4},
                 {"word": "jamais", "start": 12.4, "end": 12.9}]

    # 1. direct() sans hint : un seul bloc système, caché, TTL 1h.
    client = _FakeClient(_FakePlaceEventsBlock())
    director.direct(client, words_out)
    system = client.messages.last_call["system"]
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "réalisateur" in system[0]["text"]
    print("  1. direct() sans hint : bloc système caché, TTL 1h"); ok += 1

    # 2. direct() avec hint : le hint est un bloc SÉPARÉ, non caché.
    client2 = _FakeClient(_FakePlaceEventsBlock())
    director.direct(client2, words_out, hint="Consigne spécifique à ce clip")
    system2 = client2.messages.last_call["system"]
    assert len(system2) == 2
    assert "cache_control" in system2[0] and system2[0]["cache_control"]["ttl"] == "1h"
    assert "cache_control" not in system2[1]
    assert system2[1]["text"] == "Consigne spécifique à ce clip"
    print("  2. direct() avec hint : hint non caché, dans un bloc séparé"); ok += 1

    # 3. adjust_with_text() : bloc système caché, TTL 5m (chemin correcteur).
    from sortclip.edl import EDL, Source, Canvas, Captions, Background
    edl = EDL(source=Source(path="x.mp4", duration=60), canvas=Canvas(w=1080, h=1920, fps=30),
              keeps=[{"start": 0, "end": 30}], events=[], captions=Captions(), background=Background())
    client3 = _FakeClient(_FakeEditEventsBlock())
    director.adjust_with_text(client3, edl, "resserre le cadrage")
    system3 = client3.messages.last_call["system"]
    assert isinstance(system3, list) and len(system3) == 1
    assert system3[0]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    print("  3. adjust_with_text() : bloc système caché, TTL 5m"); ok += 1

    # 4. La consigne libre (variable) ne doit JAMAIS être dans le bloc système
    # caché : elle doit rester dans le message utilisateur.
    assert "resserre le cadrage" not in system3[0]["text"]
    print("  4. la consigne variable n'est pas dans le bloc caché"); ok += 1

    print(f"\n{ok} étapes validées.")


if __name__ == "__main__":
    main()
