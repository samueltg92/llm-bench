"""Offline transport smoke test. Never interpret its output as model performance."""

from .base import StreamEvent, Usage


class FakeProvider:
    def close(self):
        pass

    def stream_chat(self, *, messages, tools, temperature, max_output_tokens):
        yield StreamEvent(kind="text", text="Hola, esta es una respuesta de prueba local.")
        yield StreamEvent(
            kind="usage", usage=Usage(prompt_tokens=20, completion_tokens=10, source="synthetic")
        )
        yield StreamEvent(kind="done", finish_reason="stop")
