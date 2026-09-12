import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from llm_bench.budget import call_bound, reserve
from llm_bench.config import Model


def ledger(tmp_path, limit=1, per_operation=1):
    path = tmp_path / "budget.json"
    path.write_text(
        json.dumps({"limit_usd": limit, "reserved_usd": 0, "max_operation_usd": per_operation})
    )
    return path


def test_reservations_persist_and_cannot_exceed_total(tmp_path):
    path = ledger(tmp_path)
    reserve(path, "0.75", "test")
    with pytest.raises(ValueError, match="cumulative"):
        reserve(path, "0.26", "test")
    assert Decimal(json.loads(path.read_text())["reserved_usd"]) == Decimal("0.75")
    reserve(path, "0.25", "test")
    with pytest.raises(ValueError):
        reserve(path, "0.000001", "test")


def test_operation_limit_and_missing_ledger_fail_closed(tmp_path):
    path = ledger(tmp_path, limit=25, per_operation="0.25")
    with pytest.raises(ValueError, match="per-operation"):
        reserve(path, "0.26", "test")
    assert json.loads(path.read_text())["reserved_usd"] == 0
    with pytest.raises(ValueError, match="required"):
        reserve(tmp_path / "missing.json", 0.1, "test")


def test_concurrent_reservations_do_not_double_spend(tmp_path):
    path = ledger(tmp_path)

    def attempt(_):
        try:
            reserve(path, ".4", "test")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = list(pool.map(attempt, range(4)))
    assert sum(accepted) == 2
    assert Decimal(json.loads(path.read_text())["reserved_usd"]) == Decimal(".8")


def test_upper_bound_does_not_discount_cached_input():
    model = Model(provider="openai_compat", model_id="synthetic", context_window=1000)
    assert call_bound(
        model, {"input": 1, "output": 2, "cached_input": 0, "last_verified": "synthetic"}, 100
    ) == Decimal(".0012")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1"])
def test_invalid_amount_does_not_change_ledger(tmp_path, value):
    path = ledger(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        reserve(path, value, "test")
    assert path.read_bytes() == before


def test_yes_cannot_bypass_budget_before_benchmark_execution(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from llm_bench.cli import app

    path = ledger(tmp_path, per_operation=".25")
    monkeypatch.setattr("llm_bench.cli.inputs", lambda *args: [])
    monkeypatch.setattr(
        "llm_bench.cli.plan", lambda *args: [{"model": "sample", "budget_reserve_usd": 0.5}]
    )
    monkeypatch.setattr(
        "llm_bench.cli.config.models",
        lambda *args: {"sample": Model(provider="fake", model_id="sample")},
    )
    monkeypatch.setattr(
        "llm_bench.cli.config.yaml_data", lambda *args: {"models": {}, "cost_guard_usd": 0.1}
    )
    monkeypatch.setattr(
        "llm_bench.cli.execute",
        lambda *args: pytest.fail("Budget rejection must precede execution"),
    )
    result = CliRunner().invoke(
        app,
        ["run", "--data-dir", str(tmp_path), "--budget-file", str(path), "--no-warmup", "--yes"],
    )
    assert result.exit_code != 0
    assert "per-operation" in str(result.exception)
    assert json.loads(path.read_text())["reserved_usd"] == 0


def scoped_ledger(tmp_path):
    path = ledger(tmp_path, limit=100, per_operation=100)
    state = json.loads(path.read_text())
    state["models"] = {
        key: {"limit_usd": "25", "reserved_usd": "0"}
        for key in ("model-a", "model-b", "model-c", "model-d")
    }
    path.write_text(json.dumps(state))
    return path


def test_model_cannot_borrow_other_models_balance(tmp_path):
    path = scoped_ledger(tmp_path)
    reserve(path, 25, "test", allocations={"model-a": 25})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="remaining model"):
        reserve(path, 1, "test", allocations={"model-a": 1})
    assert path.read_bytes() == before
    reserve(path, 25, "test", allocations={"model-b": 25})
    assert amount_from_file(path) == Decimal("50")


def amount_from_file(path):
    return Decimal(json.loads(path.read_text())["reserved_usd"])


@pytest.mark.parametrize("allocations", [None, {"unknown": 1}, {"model-a": 2}])
def test_scoped_budget_rejects_missing_unknown_or_mismatched_allocation(tmp_path, allocations):
    path = scoped_ledger(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        reserve(path, 1, "test", allocations=allocations)
    assert path.read_bytes() == before


def test_multimodel_reservation_is_atomic(tmp_path):
    path = scoped_ledger(tmp_path)
    reserve(path, 25, "test", allocations={"model-b": 25})
    before = path.read_bytes()
    with pytest.raises(ValueError):
        reserve(path, 2, "test", allocations={"model-a": 1, "model-b": 1})
    assert path.read_bytes() == before


def test_concurrent_model_reservations_are_serialized(tmp_path):
    path = scoped_ledger(tmp_path)

    def attempt(_):
        try:
            reserve(path, 10, "test", allocations={"model-a": 10})
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(4))) == 2
    assert amount_from_file(path) == Decimal("20")


def test_inconsistent_model_totals_fail_closed(tmp_path):
    path = scoped_ledger(tmp_path)
    state = json.loads(path.read_text())
    state["models"]["model-a"]["reserved_usd"] = "1"
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="Inconsistent"):
        reserve(path, 1, "test", allocations={"model-a": 1})


def completed_manifest(tmp_path, entry, retained):
    directory = tmp_path / "run"
    directory.mkdir(exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps({
        "execution_complete": True, "budget_reservation_id": entry["id"],
        "budget_usage": {k: {"retained_usd": v, "attempts": 1} for k, v in retained.items()},
    }))
    return directory


def test_completed_settlement_keeps_attempt_bounds_and_is_idempotent(tmp_path):
    from llm_bench.budget import settle_completed

    path = scoped_ledger(tmp_path)
    reserve(path, 1, "historical", allocations={"model-a": 1})
    entry = reserve(path, 10, "benchmark", allocations={"model-a": 10})
    directory = completed_manifest(tmp_path, entry, {"model-a": "2"})
    settle_completed(path, directory)
    assert amount_from_file(path) == Decimal("3")
    before = path.read_bytes()
    settle_completed(path, directory)
    assert path.read_bytes() == before
    state = json.loads(before)
    assert state["reservations"][-1]["reserved_usd"] == "10.000000"
    reserve(path, 22, "benchmark", allocations={"model-a": 22})
    with pytest.raises(ValueError, match="remaining model"):
        reserve(path, 1, "benchmark", allocations={"model-a": 1})


@pytest.mark.parametrize("fault", ["incomplete", "unknown", "overspend", "wrong_model"])
def test_invalid_settlement_leaves_ledger_unchanged(tmp_path, fault):
    from llm_bench.budget import settle_completed

    path = scoped_ledger(tmp_path)
    entry = reserve(path, 10, "benchmark", allocations={"model-a": 10})
    directory = completed_manifest(tmp_path, entry, {"model-a": "2"})
    target = directory / "manifest.json"
    m = json.loads(target.read_text())
    if fault == "incomplete":
        m["execution_complete"] = False
    elif fault == "unknown":
        m["budget_reservation_id"] = "unknown"
    elif fault == "overspend":
        m["budget_usage"]["model-a"]["retained_usd"] = "11"
    else:
        m["budget_usage"] = {"model-b": {"retained_usd": "2"}}
    target.write_text(json.dumps(m))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        settle_completed(path, directory)
    assert path.read_bytes() == before


def test_transport_failures_keep_bound_and_exhaustion_prevents_next_request():
    from llm_bench.budget import ReservedProvider

    class Broken:
        calls = 0

        def stream_chat(self, **kwargs):
            self.calls += 1
            raise RuntimeError("transport failed")

    model = Model(provider="openai_compat", model_id="synthetic", context_window=1000)
    price = {"input": 1, "output": 2, "last_verified": "synthetic"}
    transport = Broken()
    provider = ReservedProvider(transport, model, price, ".0024")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            list(provider.stream_chat(max_output_tokens=100))
    assert provider.retained == Decimal(".0024")
    assert provider.attempts == 2
    with pytest.raises(ValueError, match="reservation"):
        list(provider.stream_chat(max_output_tokens=100))
    assert transport.calls == 2


def test_completed_shared_ledger_settlement(tmp_path):
    from llm_bench.budget import settle_completed

    path = ledger(tmp_path, limit=25, per_operation=25)
    entry = reserve(path, 10, "benchmark", allocations={"model-a": 10})
    settle_completed(path, completed_manifest(tmp_path, entry, {"model-a": "2"}))
    assert amount_from_file(path) == Decimal("2")


def test_post_request_processing_error_still_retains_attempt(
    tmp_path, monkeypatch, bundle, scenario, model, bench, scripted
):
    from llm_bench.budget import settle_completed
    from llm_bench.experiment import execute

    path = scoped_ledger(tmp_path)
    entry = reserve(path, 10, "benchmark", allocations={"model-a": 10})
    price = {"input": 1, "output": 2, "last_verified": "synthetic"}
    monkeypatch.setattr("llm_bench.experiment.create", lambda *args: scripted)

    def fail_after_transport(*args):
        raise RuntimeError("processing failure after paid request")

    monkeypatch.setattr("llm_bench.runner.usage_record", fail_after_transport)
    directory = execute(
        [(bundle, scenario)], {"model-a": model}, {"model-a": price}, bench,
        ["active_node"], tmp_path / "results", reservation=entry,
    )
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["execution_complete"] is True
    assert manifest["budget_usage"]["model-a"]["attempts"] == 1
    assert len(scripted.requests) == 1
    settle_completed(path, directory)
    assert amount_from_file(path) == call_bound(model, price, scenario.max_output_tokens)
