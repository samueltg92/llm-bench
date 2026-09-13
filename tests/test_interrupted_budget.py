import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from llm_bench import interrupted_budget
from llm_bench.budget import call_bound, reserve
from llm_bench.config import Model


def example(tmp_path):
    ledger = tmp_path / "budget.json"
    ledger.write_text(json.dumps({"limit_usd": 25, "max_operation_usd": 25, "reserved_usd": 0,
                                  "models": {"m": {"limit_usd": 25, "reserved_usd": 0}}}))
    model = Model(provider="openai_compat", model_id="synthetic", context_window=1000)
    price = {"input": 1, "output": 2, "last_verified": "synthetic"}
    bound = call_bound(model, price, 100)
    reservation = reserve(ledger, bound * 28, "test", allocations={"m": bound * 28})
    root = tmp_path / "interrupted"
    root.mkdir()
    manifest = {"execution_complete": False, "budget_reservation_id": reservation["id"],
                "bench": {"concurrency": 1, "repetitions": 1, "warmup": False,
                          "retries": {"max_attempts": 1}}, "models": {"m": model.model_dump()},
                "pricing": {"m": price}, "scenarios": [{"configuration": {"max_output_tokens": 100}}],
                "preflight": [{"max_calls_per_conversation": 28}]}
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "calls.jsonl").write_text(json.dumps({"call_id": "one", "model_key": "m", "max_output_tokens": 100,
                                                 "attempts": [{"attempt": 1}]}))
    return ledger, root, bound


def audit(root):
    files = ["runner.py", "providers/openai_compat.py", "privacy.py", "experiment.py"]
    source = Path(interrupted_budget.__file__).parent
    return {"evidence_sha256": hashlib.sha256((root / "manifest.json").read_bytes() + b"\0" + (root / "calls.jsonl").read_bytes()).hexdigest(),
            "termination_exit_code": 143, "process_stopped_confirmed": True,
            "serial_record_before_next_request_confirmed": True, "sdk_retries_disabled_confirmed": True,
            "reviewed_code_sha256": {f: hashlib.sha256((source / f).read_bytes()).hexdigest() for f in files}}


def test_interrupted_audit_retains_full_bounds_plus_one_unknown_and_is_idempotent(tmp_path):
    ledger, root, bound = example(tmp_path)
    state = json.loads(ledger.read_text())
    state["reservations"].append({"note": "legacy annotation without reservation identity"})
    ledger.write_text(json.dumps(state))
    before = (root / "manifest.json").read_bytes()
    result = interrupted_budget.reconcile_stopped_serial(ledger, root, audit(root))
    assert Decimal(result["retained_usd"]) == bound * 2
    assert Decimal(json.loads(ledger.read_text())["reserved_usd"]) == bound * 2
    assert result == interrupted_budget.reconcile_stopped_serial(ledger, root, audit(root))
    assert (root / "manifest.json").read_bytes() == before  # Never fabricate completion.


@pytest.mark.parametrize("field", ["process_stopped_confirmed", "sdk_retries_disabled_confirmed",
                                    "serial_record_before_next_request_confirmed", "evidence_sha256"])
def test_missing_stop_or_transport_evidence_keeps_entire_reservation(tmp_path, field):
    ledger, root, _ = example(tmp_path)
    proof = audit(root)
    proof.pop(field)
    before = ledger.read_bytes()
    with pytest.raises(ValueError):
        interrupted_budget.reconcile_stopped_serial(ledger, root, proof)
    assert ledger.read_bytes() == before


def test_parallel_execution_cannot_use_single_unknown_bound(tmp_path):
    ledger, root, _ = example(tmp_path)
    p = root / "manifest.json"
    d = json.loads(p.read_text())
    d["bench"]["concurrency"] = 2
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="serial"):
        interrupted_budget.reconcile_stopped_serial(ledger, root, audit(root))
