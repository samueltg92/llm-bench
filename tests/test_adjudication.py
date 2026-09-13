from types import SimpleNamespace

import pytest

from llm_bench.adjudication import silent_close, silent_handoff


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
    bundle = SimpleNamespace(source_sha256="source", routing=SimpleNamespace(tool_name="route_node"), node=lambda _: SimpleNamespace(
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


@pytest.mark.parametrize("fault", [None, "failed_transfer", "unexpected_tool", "missing_turn",
                                  "wrong_node", "output_limit", "later_call"])
def test_terminal_handoff_requires_expected_success_and_complete_evidence(fault):
    original = evidence()
    policy, scenario, bundle = policy_and_inputs()
    policy.update(handoff_by_node={"leaf": ["transfer"]}, handoff_patterns=["Le transfiero"])
    original["calls"][0]["call_id"] = "transfer-call"
    tool = {"name": "transfer", "valid": True, "mock_error": False, "call_id": "transfer-call"}
    original["turns"][-1].update(text="Le transfiero con un experto.", tools=[tool],
                                 assertions=[{"id": "expected_tool:transfer", "pass": True}])
    if fault == "failed_transfer":
        tool["mock_error"] = True
    elif fault == "unexpected_tool":
        original["turns"][-1]["assertions"][0]["pass"] = False
    elif fault == "missing_turn":
        scenario.turns.append(2)
        scenario.max_turns = 2
    elif fault == "wrong_node":
        policy["handoff_by_node"] = {"other": ["transfer"]}
    elif fault == "output_limit":
        original["calls"][-1]["finish_reason"] = "length"
    elif fault == "later_call":
        tool["call_id"] = "earlier-call"
    result = silent_handoff(original, scenario, bundle, policy)
    if fault:
        assert result is None
    else:
        assert result["summary"]["status"] == "ok"
        assert original["summary"]["status"] == "empty_response"
