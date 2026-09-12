from dataclasses import dataclass, field
from typing import Iterator, Protocol


@dataclass
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0
    reasoning_included: bool = True
    source: str = "provider"


@dataclass
class StreamEvent:
    kind: str
    text: str = ""
    index: int = 0
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    usage: Usage | None = None
    finish_reason: str | None = None
    native_part: dict = field(default_factory=dict)


class LLMProvider(Protocol):
    def stream_chat(
        self, *, messages: list[dict], tools: list[dict], temperature: float, max_output_tokens: int
    ) -> Iterator[StreamEvent]: ...

    def close(self): ...
