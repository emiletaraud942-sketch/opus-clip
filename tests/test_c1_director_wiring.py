"""Vérifie que director.direct() envoie bien le transcript ANNOTÉ (C1) au LLM
quand il est fourni, à la place du transcript plat — avec un client Anthropic
factice (aucun appel réseau réel, aucun coût)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pydantic  # noqa: F401
except ImportError:
    print("SKIP : pydantic non installé (dispo via fastapi sur Modal).")
    sys.exit(0)

from sortclip import director


class _FakeToolBlock:
    type = "tool_use"
    name = "place_events"
    input = {"framings": [], "emphases": []}


class _FakeResponse:
    content = [_FakeToolBlock()]
    usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()


class _FakeMessages:
    def __init__(self):
        self.last_call = None

    def create(self, **kwargs):
        self.last_call = kwargs
        return _FakeResponse()


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def main():
    ok = 0
    words_out = [{"word": "Bonjour", "start": 0.0, "end": 0.4},
                 {"word": "jamais", "start": 12.4, "end": 12.9}]

    # 1. Sans transcript annoté : comportement inchangé (texte plat).
    client = _FakeClient()
    director.direct(client, words_out)
    user_content = client.messages.last_call["messages"][0]["content"]
    assert "(0)Bonjour (1)jamais" in user_content
    print("  1. sans annotation : transcript plat envoyé"); ok += 1

    # 2. Avec transcript annoté : c'est LUI qui doit être envoyé, pas le plat.
    client2 = _FakeClient()
    annotated = "(0)Bonjour (1)jamais (énergie +2.1σ)"
    director.direct(client2, words_out, annotated_transcript=annotated)
    user_content2 = client2.messages.last_call["messages"][0]["content"]
    assert "énergie +2.1σ" in user_content2
    print("  2. avec annotation : transcript annoté envoyé au LLM"); ok += 1

    # 3. Le prompt système explique les marqueurs (sinon le LLM les ignore).
    system2 = client2.messages.last_call["system"]
    assert "énergie" in system2 and "débit" in system2
    print("  3. prompt système explique les marqueurs de prosodie"); ok += 1

    print(f"\n{ok} étapes validées.")


if __name__ == "__main__":
    main()
