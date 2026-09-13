from llm_bench.consolidated import consolidate, distribution


def test_distribution_preserves_missing_values_and_uses_arithmetic_mean():
    assert distribution([None]) == {"n": 0, "mean": None, "min": None, "max": None}
    assert distribution([1, 2, None, 9]) == {"n": 3, "mean": 4, "min": 1, "max": 9}


def test_global_means_use_samples_not_project_means_and_keep_failure_costs():
    base = {"model_key": "a", "source_sha256": "s", "scenario_sha256": "x", "repetition": 1,
            "assertions": [], "expected_path_match": True}
    runs = [{**base, "run_id": "one", "project": "project_1", "status": "ok"},
            {**base, "run_id": "two", "project": "project_2", "status": "ok"},
            {**base, "run_id": "bad", "project": "project_2", "scenario_sha256": "y", "status": "empty_response"},
            {**base, "run_id": "blocked", "project": "project_2", "scenario_sha256": "z", "status": "quota_capacity"}]
    calls = [{"run_id": run_id, "status": "ok", "ttft_ms": latency * 1000, "cost_usd": cost}
             for run_id, latency, cost in [("one", 1, .1), ("one", 2, .1), ("one", 3, .1),
                                           ("two", 10, .1), ("bad", 100, .2)]]
    rows = consolidate(runs, calls, {r["run_id"]: {"turns": []} for r in runs}, [],
                       {"a": "Model A"}, {"project_1": 1, "project_2": 4})
    def metric(name):
        return next(r for r in rows if r["metric"] == name and r["project"] == "All projects"
                    and r["cohort"] == "Available cases")
    assert metric("TTFT")["mean"] == 4  # Not the mean of the project means (6).
    assert metric("TTFT")["max"] == 10  # Incomplete conversations excluded from timing.
    assert metric("Known cost per API call")["n"] == 5  # Their cost is retained.
    assert round(metric("Known cost per API call")["mean"], 3) == .12
    assert metric("Evaluable case coverage")["mean"] == 60
    assert metric("Complete conversations")["n"] == 3  # Quota block is not a quality failure.


def test_positive_tool_expectations_and_rule_assertions_keep_separate_denominators():
    run = {"run_id": "x", "model_key": "a", "project": "project_1", "status": "ok",
           "source_sha256": "s", "scenario_sha256": "x", "repetition": 1,
           "assertions": [{"id": "expected_tool:read", "pass": True},
                          {"id": "unreached_tool:transfer", "pass": False},
                          {"id": "forbidden_tool:write", "pass": True},
                          {"id": "word_limit", "pass": True}]}
    rows = consolidate([run], [], {"x": {"turns": []}}, [], {"a": "Model A"}, {"project_1": 1})
    rows = {r["metric"]: r for r in rows if r["project"] == "All projects" and r["cohort"] == "Available cases"}
    assert (rows["Expected tools"]["mean"], rows["Expected tools"]["n"]) == (50, 2)
    assert (rows["Explicit case rules"]["mean"], rows["Explicit case rules"]["n"]) == (100, 2)
