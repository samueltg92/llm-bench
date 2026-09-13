from llm_bench.onepager import summarize


def test_summary_keeps_failed_coverage_but_excludes_failed_latency():
    base = {"project": "project_1", "model_key": "model-a", "assertions": [
        {"id": "expected_tool:lookup", "pass": True, "kind": "tool_expectation"},
        {"id": "forbidden_tool:write", "pass": True, "kind": "tool_expectation"},
        {"id": "language_rule", "pass": True, "kind": "rule"},
    ], "expected_path_match": True, "assertions_passed": 3, "assertions_total": 3,
            "known_successful_cost_usd": 0.1}
    runs = [{**base, "run_id": "a", "status": "ok"},
            {**base, "run_id": "b", "status": "empty_response"}]
    calls = [{"run_id": "a", "status": "ok", "ttft_ms": 1000, "cost_usd": .1},
             {"run_id": "b", "status": "ok", "ttft_ms": 100, "cost_usd": .1}]
    data = summarize(runs, calls, {"project_1": 3}, {"model-a": "Model A"},
                     known_costs={"model-a": .2}, reserved={"model-a": 1})
    row = data["rows"][0]
    assert (row["tested"], row["complete"], row["planned"]) == (2, 1, 3)
    assert row["ttft_s"] == 1
    assert row["tool_checks_total"] == 2  # No inflation from forbidden-call assertions.
    assert row["cost_usd"] == .2
    assert "lookup" not in str(data)
    assert "scenario" not in str(data)


def test_provider_rejection_is_not_scored_as_model_quality():
    run = {"project": "project_1", "model_key": "a", "run_id": "x", "status": "error",
           "assertions": [{"id": "expected_tool:lookup", "pass": False,
                           "kind": "tool_expectation"}], "expected_path_match": False}
    data = summarize([run], [], {"project_1": 1}, {"a": "Model A"},
                     known_costs={}, reserved={})
    row = data["rows"][0]
    assert row["tested"] == 1 and row["evaluable"] == 0
    assert row["tool_checks_total"] == row["paths_total"] == 0
