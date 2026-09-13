import json
from decimal import Decimal

import pytest

from llm_bench.budget import reconcile_usage, reserve, settle_completed
from llm_bench.completed_output_budget import reconcile_finished_output
from llm_bench.config import Model


def evidence(tmp_path, *, missing_usage=False):
    path = tmp_path / "budget.json"
    path.write_text(json.dumps({"limit_usd": 25, "reserved_usd": 0, "max_operation_usd": 25,
                               "models": {"sample": {"limit_usd": 25, "reserved_usd": 0}}}))
    reserve(path, 1, "historical margin", allocations={"sample": 1})
    entry = reserve(path, 1, "case", allocations={"sample": 1})
    directory = tmp_path / "run"
    directory.mkdir()
    manifest = {"execution_complete": True, "budget_reservation_id": entry["id"],
                "budget_usage": {"sample": {"retained_usd": ".0024", "attempts": 2}},
                "models": {"sample": Model(provider="openai_compat", model_id="synthetic",
                                             context_window=1000).model_dump()},
                "pricing": {"sample": {"input": 1, "output": 2, "last_verified": "synthetic"}}}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    usage = {"source": "provider", "prompt_tokens": 50, "completion_tokens": 10,
             "reasoning_tokens": 5, "reasoning_included": True, "cached_prompt_tokens": 50}
    if missing_usage:
        usage.pop("reasoning_tokens")
    call = {"call_id": "unique", "model_key": "sample", "max_output_tokens": 100,
            "attempts": [{"attempt": 1, "status": "error", "usage": {}},
                         {"attempt": 2, "status": "ok", "usage": usage}]}
    (directory / "calls.jsonl").write_text(json.dumps(call) + "\n")
    settle_completed(path, directory)
    reconcile_usage(path, directory)
    return path, directory


def test_retains_failure_historical_margin_uncached_input_and_double_reasoning(tmp_path):
    path, directory = evidence(tmp_path)
    result = reconcile_finished_output(path, directory, review_note="Synthetic final usage reviewed")
    assert result["released_by_model_usd"] == {"sample": "0.000170"}
    state = json.loads(path.read_text())
    # Historical 1 + failed full context/output .0012 + input .00005 + output/reasoning .00003.
    assert Decimal(state["reserved_usd"]) == Decimal("1.001280")
    assert state["models"]["sample"]["limit_usd"] == 25
    assert "output_usage_reconciliation" not in state["reservations"][0]
    before = path.read_bytes()
    reconcile_finished_output(path, directory, review_note="Same evidence")
    assert path.read_bytes() == before


def test_missing_reasoning_evidence_preserves_full_output(tmp_path):
    path, directory = evidence(tmp_path, missing_usage=True)
    result = reconcile_finished_output(path, directory, review_note="Unknown reasoning count")
    assert result["released_by_model_usd"] == {"sample": "0.000000"}
    assert Decimal(json.loads(path.read_text())["reserved_usd"]) == Decimal("1.001450")


@pytest.mark.parametrize("fault", ["incomplete", "changed_calls", "no_prior_audit", "pool_mismatch", "no_review"])
def test_uncertain_output_cannot_release_budget(tmp_path, fault):
    path, directory = evidence(tmp_path)
    if fault == "incomplete":
        target = directory / "manifest.json"
        m = json.loads(target.read_text())
        m["execution_complete"] = False
        target.write_text(json.dumps(m))
    elif fault == "changed_calls":
        target = directory / "calls.jsonl"
        target.write_text(target.read_text() + "\n")
    elif fault in {"no_prior_audit", "pool_mismatch"}:
        state = json.loads(path.read_text())
        if fault == "no_prior_audit":
            state["reservations"][-1].pop("usage_reconciliation")
        else:
            state["models"]["sample"]["reserved_usd"] = "0"
        path.write_text(json.dumps(state))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        reconcile_finished_output(path, directory, review_note="" if fault == "no_review" else "Reviewed")
    assert path.read_bytes() == before
