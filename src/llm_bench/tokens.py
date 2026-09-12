import json
from functools import lru_cache

import tiktoken


@lru_cache(maxsize=1)
def encoding():
    return tiktoken.get_encoding("o200k_base")


def count(value) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return len(encoding().encode(text, disallowed_special=()))


def request_tokens(messages: list[dict], tools: list[dict]) -> int:
    # Cross-model estimate, including message framing and function schemas.
    return count(messages) + (count(tools) if tools else 0) + 8 * len(messages)
