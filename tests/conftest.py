import json
from pathlib import Path

import pytest

from llm_bench.config import Model
from llm_bench.extract import extract
from llm_bench.providers.base import StreamEvent, Usage
from llm_bench.scenario import Scenario


@pytest.fixture
def bundle():
    return extract(Path(__file__).parent / "fixtures" / "mini_export.json", "project_1")


@pytest.fixture
def model():
    return Model(provider="fake", model_id="fake", last_verified="synthetic")


@pytest.fixture
def scenario():
    return Scenario(
        id="test_flow",
        project="project_1",
        max_turns=2,
        turns=[
            {
                "content": "Mi documento es DEMO-001, consulta el estado.",
                "expect_tools": [
                    {"tool": "verificar", "arguments": {"documento": "DEMO-001"}},
                    {"tool": "consultar"},
                ],
                "expected_node": "Consulta",
            },
            {"content": "Gracias, termina.", "expected_node": "Cierre"},
        ],
        tool_mocks={"verificar": {"response": {"ok": True}, "state_updates": {"verified": True}}},
        tool_prerequisites={"consultar": {"verified": True}},
        expected_path=["Inicio", "Consulta", "Cierre"],
        nodes_subset=["Inicio", "Consulta", "Cierre"],
    )


@pytest.fixture
def bench():
    return {
        "repetitions": 1,
        "concurrency": 1,
        "warmup": False,
        "max_tool_iterations": 3,
        "on_context_overflow": "skip",
        "timeouts": {"connect": 10, "read": 120},
        "retries": {"max_attempts": 1, "base_delay_s": 0},
    }


class ScriptProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def stream_chat(self, **kwargs):
        self.requests.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        yield StreamEvent(kind="metadata")
        if isinstance(response, str):
            yield StreamEvent(kind="text", text=response)
        else:
            for i, (name, arguments) in enumerate(response):
                yield StreamEvent(
                    kind="tool_call",
                    index=i,
                    call_id=f"call_{len(self.requests)}_{i}",
                    name=name,
                    arguments=json.dumps(arguments),
                )
        yield StreamEvent(kind="usage", usage=Usage(prompt_tokens=100, completion_tokens=20))
        yield StreamEvent(kind="done", finish_reason="stop")

    def close(self):
        pass


@pytest.fixture
def scripted():
    return ScriptProvider(
        [
            [("verificar", {"documento": "DEMO-001"})],
            [("route_node", {"target_node": "Consulta"})],
            [("consultar", {})],
            "Tu estado está activo y la consulta está completa.",
            [("route_node", {"target_node": "Cierre"})],
            "Gracias por comunicarte, que tengas un buen día.",
        ]
    )
