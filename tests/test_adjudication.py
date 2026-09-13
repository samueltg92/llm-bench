from types import SimpleNamespace

import pytest

from llm_bench.adjudication import silent_close


def evidence():
    return {"summary": {"status": "empty_response", "turns_completed": 0},
            "calls": [{"status": "ok", "cost_usd": .01},
                      {"status": "empty_response", "finish_reason": "FinishReason.STOP",
                       "active_node_before": "leaf", "tool_calls_count": 0, "cost_usd": .01}],
            "turns": [{"text": "Hasta luego", "completed": False, "tools": [
                {"name": "route_node", "valid": True, "active_node_after": "leaf"}]}]}


def policy_and_inputs():
    policy = {"id": "review-v1", "source_sha256": "source",
              "terminal_node_ids": ["leaf"], "farewell_patterns": ["hasta luego"]}
    scenario = SimpleNamespace(turns=[1], max_turns=1)
    bundle = SimpleNamespace(source_sha256="source", node=lambda _: SimpleNamespace(
        id="leaf", transitions={}))
    return policy, scenario, bundle


def test_terminal_silence_is_interpreted_without_changing_evidence():
    original = evidence()
    policy, scenario, bundle = policy_and_inputs()
    result = silent_close(original, scenario, bundle, policy)
    assert result["summary"]["status"] == "ok"
    assert result["summary"]["turns_completed"] == 1
    assert original["summary"]["status"] == "empty_response"
    assert result["calls"][-1].get("ttft_ms") is None


@pytest.mark.parametrize("fault", ["no_farewell", "missing_turn", "wrong_source", "length", "error"])
def test_arbitrary_empty_outputs_cannot_be_reclassified(fault):
    original = evidence()
    policy, scenario, bundle = policy_and_inputs()
    if fault == "no_farewell":
        original["turns"][-1]["text"] = "Un momento"
    elif fault == "missing_turn":
        scenario.turns.append(2)
        scenario.max_turns = 2
    elif fault == "wrong_source":
        policy["source_sha256"] = "other"
    elif fault == "length":
        original["calls"][-1]["finish_reason"] = "length"
    else:
        original["calls"][0]["status"] = "error"
    assert silent_close(original, scenario, bundle, policy) is None
