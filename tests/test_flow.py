import json

import pytest

from llm_bench.experiment import jobs_for, plan
from llm_bench.prompt import build, prepare
from llm_bench.runner import conversation, resolve_tool


@pytest.mark.parametrize("mode", ["active_node", "full"])
def test_complete_flow_preserves_history_and_changes_prompt(
    bundle, scenario, model, bench, scripted, tmp_path, mode
):
    result = conversation(
        bundle,
        scenario,
        "fake",
        model,
        scripted,
        {"input": 1, "output": 2, "last_verified": "2026-09-12"},
        bench,
        tmp_path,
        mode,
    )
    assert result["status"] == "ok"
    assert result["routing_path"] == ["Inicio", "Consulta", "Cierre"]
    assert result["expected_path_match"] is True
    assert result["invalid_tool_calls"] == 0
    assert result["rule_compliance"] == 1
    assert result["llm_calls"] == 6
    assert "Nodo activo: Consulta" in scripted.requests[2]["messages"][0]["content"]
    assert scripted.requests[2]["messages"][1]["role"] == "assistant"
    assert any(m.get("role") == "tool" for m in scripted.requests[2]["messages"])
    assert "Nodo activo: Cierre" in scripted.requests[5]["messages"][0]["content"]
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert calls[0]["first_text_ms"] is None
    assert calls[0]["first_tool_ms"] is not None
    transcript = json.loads(next((tmp_path / "transcripts").glob("*.json")).read_text())
    assert transcript["turns"][0]["first_text_ms"] >= calls[3]["first_text_ms"]


def call(name, args):
    return {"function": {"name": name, "arguments": json.dumps(args)}}


@pytest.mark.parametrize(
    "name,args,active,allowed,error",
    [
        (
            "route_node",
            {"target_node": "Cierre"},
            "start",
            {"route_node"},
            "transition_not_allowed",
        ),
        ("consultar", {}, "query", {"consultar"}, "prerequisite_not_met"),
        ("verificar", {"documento": 123}, "start", {"verificar"}, "invalid_arguments_or_schema"),
        ("consultar", {}, "start", {"verificar"}, "tool_not_available_at_call_start"),
    ],
)
def test_rejects_invalid_tools_without_changing_state(
    bundle, scenario, name, args, active, allowed, error
):
    after, response, event = resolve_tool(call(name, args), bundle, scenario, active, {}, allowed)
    assert after == active
    assert response["ok"] is False
    assert event["error"] == error


def test_single_segments_collapse_and_no_map(bundle, scenario, model):
    bundle.composition = "single_node"
    scenario.segment = "Inicio"
    scenario.nodes_subset = []
    for node in bundle.nodes:
        node.transitions = {}
        node.tool_names = [n for n in node.tool_names if n != "route_node"]
    outputs = [
        build(bundle, scenario, "start", mode, {}) for mode in ("full", "active_node", "subset")
    ]
    assert outputs[0] == outputs[1] == outputs[2]
    assert "MAPA DE NODOS" not in outputs[0][0]
    assert len(list(jobs_for([(bundle, scenario)], ["active_node", "full"]))) == 1


def test_context_guard_checked_after_tool_history_growth(
    bundle, scenario, model, bench, scripted, tmp_path
):
    system, ts, _ = build(bundle, scenario, "start", "active_node", scenario.variables)
    from llm_bench.tokens import request_tokens

    # Force initial failure and prove no network request or fabricated timing.
    model.context_window = request_tokens([{"role": "system", "content": system}], ts)
    result = conversation(bundle, scenario, "fake", model, scripted, None, bench, tmp_path)
    assert result["status"] == "skipped_context"
    assert scripted.requests == []
    row = json.loads((tmp_path / "calls.jsonl").read_text())
    assert row["ttft_ms"] is None
    assert result["rule_compliance"] == 0


def test_same_canonical_input_across_adapters(bundle, scenario, model):
    system, ts, _ = build(bundle, scenario, "start", "active_node", {})
    _, _, native = prepare(system, [], ts, model)
    model.supports_system = model.supports_tools = False
    messages, tools, text = prepare(system, [], ts, model)
    assert native["canonical_input_sha256"] == text["canonical_input_sha256"]
    assert native["request_sha256"] != text["request_sha256"]
    assert tools == [] and messages[0]["role"] == "user"


def test_dry_plan_never_creates_provider(bundle, scenario, model, bench, monkeypatch):
    monkeypatch.setattr("llm_bench.experiment.create", lambda *a: pytest.fail("network client"))
    rows = plan([(bundle, scenario)], {"fake": model}, {}, bench, ["full", "active_node"])
    assert len(rows) == 2
    assert all(r["estimated_cost_usd"] is None for r in rows)


def test_call_cap_matches_plan_and_stops_network(
    bundle, scenario, model, bench, scripted, tmp_path
):
    bench["max_calls_per_conversation"] = 3
    rows = plan([(bundle, scenario)], {"fake": model}, {}, bench, ["active_node"])
    assert rows[0]["max_calls_per_conversation"] == 3
    result = conversation(bundle, scenario, "fake", model, scripted, None, bench, tmp_path)
    assert result["status"] == "call_limit"
    assert len(scripted.requests) == result["llm_calls"] == 3
    assert result["turns_completed"] == 0


def test_rate_limit_wait_is_separate_from_latency(scenario, scripted, monkeypatch):
    from llm_bench.runner import measure

    now = [1_000_000_000]
    monkeypatch.setattr("llm_bench.runner.time.perf_counter_ns", lambda: now[0])
    monkeypatch.setattr(
        "llm_bench.runner.time.sleep",
        lambda seconds: now.__setitem__(0, now[0] + int(seconds * 1e9)),
    )
    scripted.min_request_interval_s = 65
    first, _, _ = measure(scripted, [], [], scenario, None)
    second, _, _ = measure(scripted, [], [], scenario, None)
    assert first["rate_limit_wait_ms"] == 0
    assert second["rate_limit_wait_ms"] == 65000
    assert second["ttft_ms"] == second["total_latency_ms"] == 0
