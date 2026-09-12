import json
from types import SimpleNamespace

import httpx
from google.genai import types
from openai import OpenAI

from llm_bench.config import Model
from llm_bench.providers.google_genai import GoogleGenAI
from llm_bench.providers.openai_compat import OpenAICompat


def test_openai_stream_merges_function_deltas_and_real_usage(scenario):
    from llm_bench.runner import measure

    chunks = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "buscar", "arguments": '{"q":'},
                            }
                        ]
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"hola"}'}}]},
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": {"cached_tokens": 10},
            },
        },
    ]
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    provider = OpenAICompat.__new__(OpenAICompat)
    provider.model = Model(provider="openai_compat", model_id="fake")
    provider.client = OpenAI(
        api_key="test-placeholder",
        base_url="https://example.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        max_retries=0,
    )
    try:
        row, message, _ = measure(
            provider, [{"role": "user", "content": "Hola"}], [], scenario, None
        )
    finally:
        provider.close()
    assert row["status"] == "ok" and row["usage_source"] == "provider"
    assert row["cached_prompt_tokens"] == 10
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"q": "hola"}
    assert captured[0]["stream"] is True


def test_google_preserves_thought_signatures_and_tool_results():
    recorded = []

    def stream(**kwargs):
        recorded.append(kwargs)
        yield types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(name="buscar", args={"q": "hola"}),
                                thought_signature=b"synthetic-signature",
                            )
                        ],
                    ),
                    finish_reason="STOP",
                )
            ],
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100, candidates_token_count=10, thoughts_token_count=5
            ),
        )

    provider = GoogleGenAI.__new__(GoogleGenAI)
    provider.model = Model(provider="google_genai", model_id="fake")
    provider.client = SimpleNamespace(models=SimpleNamespace(generate_content_stream=stream))
    first = list(
        provider.stream_chat(
            messages=[
                {"role": "system", "content": "Español"},
                {"role": "user", "content": "Consulta"},
            ],
            tools=[],
            temperature=0.2,
            max_output_tokens=100,
        )
    )
    event = next(e for e in first if e.kind == "tool_call")
    history = [
        {"role": "user", "content": "Consulta"},
        {
            "role": "assistant",
            "content": "",
            "_google_parts": [event.native_part],
            "tool_calls": [
                {"id": event.call_id, "function": {"name": "buscar", "arguments": event.arguments}}
            ],
        },
        {"role": "tool", "tool_call_id": event.call_id, "content": '{"ok":true}'},
    ]
    list(provider.stream_chat(messages=history, tools=[], temperature=0.2, max_output_tokens=100))
    assert recorded[1]["contents"][1].parts[0].thought_signature == b"synthetic-signature"
    assert recorded[1]["contents"][2].parts[0].function_response.name == "buscar"
    usage = next(e.usage for e in first if e.kind == "usage")
    assert usage.reasoning_included is False and usage.reasoning_tokens == 5
