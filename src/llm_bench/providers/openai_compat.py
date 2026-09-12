import os

import httpx
from openai import OpenAI

from .base import StreamEvent, Usage


class OpenAICompat:
    def __init__(self, model, timeouts=None):
        self.model = model
        times = timeouts or {"connect": 10, "read": 120}
        self.client = OpenAI(
            api_key=os.environ[model.api_key_env],
            base_url=model.base_url,
            timeout=httpx.Timeout(times["read"], connect=times["connect"]),
            max_retries=0,
        )

    def close(self):
        self.client.close()

    def stream_chat(self, *, messages, tools, temperature, max_output_tokens):
        params = {
            "model": self.model.model_id,
            "stream": True,
            "messages": [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages],
            self.model.output_parameter: max_output_tokens,
            **self.model.extra,
        }
        if self.model.supports_temperature:
            params["temperature"] = temperature
        if tools:
            params["tools"] = tools
        if self.model.include_usage:
            params["stream_options"] = {"include_usage": True}
        with self.client.chat.completions.create(**params) as stream:
            for chunk in stream:
                if chunk.usage:
                    usage = chunk.usage
                    yield StreamEvent(
                        kind="usage",
                        usage=Usage(
                            prompt_tokens=usage.prompt_tokens,
                            completion_tokens=usage.completion_tokens,
                            cached_prompt_tokens=getattr(
                                usage.prompt_tokens_details, "cached_tokens", 0
                            )
                            or 0,
                            reasoning_tokens=getattr(
                                usage.completion_tokens_details, "reasoning_tokens", 0
                            )
                            or 0,
                        ),
                    )
                for choice in chunk.choices:
                    delta = choice.delta
                    if delta.content:
                        yield StreamEvent(kind="text", text=delta.content)
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        yield StreamEvent(kind="reasoning", text=reasoning)
                    for call in delta.tool_calls or []:
                        yield StreamEvent(
                            kind="tool_call",
                            index=call.index,
                            call_id=call.id or "",
                            name=call.function.name or "" if call.function else "",
                            arguments=call.function.arguments or "" if call.function else "",
                        )
                    if choice.finish_reason:
                        yield StreamEvent(kind="done", finish_reason=choice.finish_reason)
