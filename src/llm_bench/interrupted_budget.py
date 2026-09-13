"""Explicit audit of a stopped serial execution, retaining one unknown request."""

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .budget import _save, amount, call_bound
from .config import Model
from .privacy import external_path


def reconcile_stopped_serial(path: Path, run_dir: Path, audit: dict):
    """Require an external termination/code audit, not merely a stale manifest.

    The audited runner closes each call record before starting the next request.
    With one serial worker and SDK retries off, at most one request is unrecorded.
    All recorded calls AND that unknown request retain their full context/output
    bounds. This never declares an interrupted experiment complete.
    """
    import fcntl

    path, run_dir = external_path(path), external_path(run_dir)
    manifest_bytes = (run_dir / "manifest.json").read_bytes()
    call_bytes = (run_dir / "calls.jsonl").read_bytes()
    digest = hashlib.sha256(manifest_bytes + b"\0" + call_bytes).hexdigest()
    if (audit.get("evidence_sha256") != digest or audit.get("termination_exit_code") not in {143, -15}
            or audit.get("process_stopped_confirmed") is not True
            or audit.get("serial_record_before_next_request_confirmed") is not True
            or audit.get("sdk_retries_disabled_confirmed") is not True):
        raise ValueError("A matching explicit termination and serial-transport audit is required")
    manifest = json.loads(manifest_bytes)
    bench = manifest["bench"]
    if (manifest.get("execution_complete") is not False or bench.get("concurrency") != 1
            or bench.get("repetitions") != 1 or bench.get("warmup") is not False
            or bench.get("retries", {}).get("max_attempts") != 1
            or len(manifest["models"]) != 1 or len(manifest["scenarios"]) != 1
            or len(manifest.get("preflight", [])) != 1):
        raise ValueError("Only an interrupted single-case serial execution is eligible")
    key = next(iter(manifest["models"]))
    model = Model.model_validate(manifest["models"][key])
    if model.provider != "openai_compat":
        raise ValueError("This audit supports only the reviewed OpenAI-compatible transport")
    source_files = {name: Path(__file__).parent / name for name in
                    ["runner.py", "providers/openai_compat.py", "privacy.py", "experiment.py"]}
    hashes = {name: hashlib.sha256(p.read_bytes()).hexdigest() for name, p in source_files.items()}
    if audit.get("reviewed_code_sha256") != hashes:
        raise ValueError("Reviewed runner/transport/storage code does not match")
    output = manifest["scenarios"][0]["configuration"]["max_output_tokens"]
    bound = call_bound(model, manifest["pricing"][key], output)
    calls = [json.loads(line) for line in call_bytes.splitlines() if line.strip()]
    seen = set()
    for call in calls:
        if (not call.get("call_id") or call["call_id"] in seen or call["model_key"] != key
                or call.get("max_output_tokens") != output
                or len(call.get("attempts", [])) != 1 or call["attempts"][0].get("attempt") != 1):
            raise ValueError("Invalid or duplicate recorded attempt evidence")
        seen.add(call["call_id"])
    cap = manifest["preflight"][0]["max_calls_per_conversation"]
    if type(cap) is not int or cap < len(calls) + 1:
        raise ValueError("No provable unused serial request slots")
    retained = amount(bound * (len(calls) + 1))
    fd = os.open(path.with_suffix(path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text())
        matches = [r for r in state["reservations"] if r.get("id") == manifest["budget_reservation_id"]]
        if len(matches) != 1:
            raise ValueError("Reservation identity is ambiguous")
        entry = matches[0]
        if "interruption_audit" in entry:
            if entry["interruption_audit"]["evidence_sha256"] != digest:
                raise ValueError("Previously audited evidence has changed")
            return entry["interruption_audit"]
        if "settlement" in entry or "usage_reconciliation" in entry:
            raise ValueError("Reservation was already settled through another path")
        allocated = amount(entry["models"][key])
        if (set(entry["models"]) != {key} or allocated != amount(entry["reserved_usd"])
                or allocated != amount(bound * cap) or retained > allocated):
            raise ValueError("Reservation does not match the audited request cap")
        pools = state["models"]
        total = amount(state["reserved_usd"])
        if sum((amount(v["reserved_usd"]) for v in pools.values()), Decimal(0)) != total:
            raise ValueError("Inconsistent budget ledger")
        released = allocated - retained
        if released > amount(pools[key]["reserved_usd"]) or released > total:
            raise ValueError("Invalid release")
        pools[key]["reserved_usd"] = str(amount(pools[key]["reserved_usd"]) - released)
        state["reserved_usd"] = str(total - released)
        entry["interruption_audit"] = {
            **audit, "created_at": datetime.now(timezone.utc).isoformat(),
            "basis": "full_bounds_for_recorded_calls_plus_one_unknown_serial_request",
            "recorded_calls": len(calls), "unknown_requests_retained": 1,
            "retained_usd": str(retained), "released_usd": str(released),
            "experiment_still_incomplete": True,
        }
        _save(path, state)
        return entry["interruption_audit"]
