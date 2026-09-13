from llm_bench.onepager import common_cohort, summarize


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
    assert row["rules_total"] == 4  # Absence constraints remain part of the rule score.
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


def test_forbidden_call_failure_is_visible_in_rule_compliance():
    run = {"project": "project_1", "model_key": "a", "run_id": "x", "status": "ok",
           "assertions": [{"id": "expected_tool:read", "pass": True, "kind": "tool_expectation"},
                          {"id": "forbidden_tool:write", "pass": False, "kind": "tool_expectation"}]}
    row = summarize([run], [], {"project_1": 1}, {"a": "Model A"},
                    known_costs={}, reserved={})["rows"][0]
    assert (row["tool_checks_passed"], row["tool_checks_total"]) == (1, 1)
    assert (row["rules_passed"], row["rules_total"]) == (0, 1)


def test_common_cases_keep_quality_failures_but_require_all_providers():
    base = {"project": "project_1", "source_sha256": "source", "repetition": 1}
    runs = [{**base, "scenario_sha256": case, "model_key": model, "status": state}
            for case, model, state in [("one", "a", "ok"), ("one", "b", "empty_response"),
                                       ("two", "a", "ok"), ("two", "b", "error"),
                                       ("three", "a", "ok")]]
    cohort = common_cohort(runs, ["a", "b"])
    assert len(cohort) == 2
    assert {r["status"] for r in cohort} == {"ok", "empty_response"}
