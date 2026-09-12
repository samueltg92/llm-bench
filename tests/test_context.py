import json

from llm_bench.context import audit
from llm_bench.runner import conversation


def test_audit_separates_declared_estimated_and_observed_context(
    bundle, scenario, model, bench, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "llm_bench.experiment.create",
        lambda *a: (_ for _ in ()).throw(AssertionError("Network forbidden")),
    )
    model.context_window = 20000
    logs = tmp_path / "observed"
    logs.mkdir()
    (logs / "calls.jsonl").write_text(
        json.dumps(
            {
                "model_key": "sample",
                "usage_source": "provider",
                "finish_reason": "stop",
                "prompt_tokens": 1500,
            }
        )
        + "\n"
    )
    rows = audit([(bundle, scenario)], {"sample": model}, {}, bench, tmp_path / "audit", logs)
    assert {r["mode"] for r in rows} == {"active_node", "full"}
    for row in rows:
        assert row["largest_provider_reported_input_seen"] == 1500
        assert row["full_window_empirically_verified"] is False
        assert (
            row["headroom_before_history_tokens_est"]
            == 20000 - row["largest_prompt_tokens_est"] - scenario.max_output_tokens
        )
    assert (tmp_path / "audit" / "CONTEXT.md").is_file()


def test_tool_result_growth_is_checked_before_the_next_provider_request(
    bundle, scenario, model, bench, scripted, tmp_path
):
    model.context_window = 4000
    scenario.tool_mocks["verificar"].response = {"result": "contenido " * 10000}
    result = conversation(bundle, scenario, "fake", model, scripted, None, bench, tmp_path)
    assert result["status"] == "skipped_context"
    assert len(scripted.requests) == 1
    rows = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert rows[0]["status"] == "ok"
    assert rows[1]["context_est_tokens"] > model.context_window
    assert rows[1]["status"] == "skipped_context"
