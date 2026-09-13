"""Refine unused output capacity only for previously audited, completed executions."""

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

from .budget import _save, amount
from .privacy import external_path


def reconcile_finished_output(path, run_dir, *, review_note):
    """Keep uncached input and completion PLUS reasoning; preserve uncertain attempts.

    A prior full-output usage reconciliation must authenticate the same immutable
    manifest and call records. Adding reasoning separately deliberately double-counts
    it when it is already included in completion usage. This is not invoice settlement.
    No interrupted execution, unaudited legacy margin or failed attempt is released.
    """
    import fcntl

    if not isinstance(review_note, str) or not review_note.strip():
        raise ValueError("An explicit completed-output review note is required")
    path, run_dir = external_path(path), external_path(run_dir)
    manifest_bytes = (run_dir / "manifest.json").read_bytes()
    call_bytes = (run_dir / "calls.jsonl").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("execution_complete") is not True:
        raise ValueError("Only completed executions may refine output capacity")
    digest = hashlib.sha256(manifest_bytes + b"\0" + call_bytes).hexdigest()
    released = {key: amount(0) for key in manifest["budget_usage"]}
    reviewed = 0
    for line in call_bytes.splitlines():
        if not line.strip():
            continue
        call = json.loads(line)
        key = call["model_key"]
        price = manifest["pricing"][key]
        if price.get("last_verified") in (None, "PENDING", "PENDIENTE"):
            raise ValueError("Verified prices are required")
        for attempt in call.get("attempts", []):
            usage = attempt.get("usage", {})
            if attempt.get("status") != "ok" or usage.get("source") != "provider":
                continue
            prompt, completion, reasoning = (usage.get(field) for field in
                                             ("prompt_tokens", "completion_tokens", "reasoning_tokens"))
            output = call.get("max_output_tokens")
            if not all(type(v) is int and v >= 0 for v in (prompt, completion, reasoning, output)):
                continue
            if not prompt or not completion or not output:
                continue
            included = usage.get("reasoning_included")
            if type(included) is not bool:
                continue
            previous_output = max(output, completion + (0 if included else reasoning))
            previous = amount((prompt * amount(price["input"])
                               + previous_output * amount(price["output"])) / Decimal(1_000_000))
            conservative = amount((prompt * amount(price["input"])
                                   + (completion + reasoning) * amount(price["output"]))
                                  / Decimal(1_000_000))
            released[key] += max(amount(0), previous - conservative)
            reviewed += 1
    fd = os.open(path.with_suffix(path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text())
        matches = [entry for entry in state["reservations"]
                   if entry.get("id") == manifest["budget_reservation_id"]]
        if len(matches) != 1:
            raise ValueError("Execution must match exactly one reservation")
        entry = matches[0]
        previous = entry.get("usage_reconciliation", {})
        if (previous.get("basis") != "provider_uncached_input_plus_full_output_allowance_not_invoice"
                or previous.get("evidence_sha256") != digest
                or entry.get("settlement", {}).get("run_dir") != str(run_dir)):
            raise ValueError("Matching prior completed usage audit is required")
        if "output_usage_reconciliation" in entry:
            audit = entry["output_usage_reconciliation"]
            if audit["evidence_sha256"] != digest:
                raise ValueError("Reconciled output evidence has changed")
            return audit
        old = {key: amount(value) for key, value in previous["retained_by_model_usd"].items()}
        if set(old) != set(released) or any(released[k] > old[k] for k in old):
            raise ValueError("Output release exceeds audited retained capacity")
        total = amount(state["reserved_usd"])
        pools = state.get("models")
        if pools and sum(amount(v["reserved_usd"]) for v in pools.values()) != total:
            raise ValueError("Inconsistent cumulative and per-model budget")
        if sum(released.values()) > total:
            raise ValueError("Output release exceeds cumulative capacity")
        if pools:
            if any(value > amount(pools[key]["reserved_usd"]) for key, value in released.items()):
                raise ValueError("Output release would make model balance negative")
            for key, value in released.items():
                pools[key]["reserved_usd"] = str(amount(pools[key]["reserved_usd"]) - value)
        state["reserved_usd"] = str(total - sum(released.values()))
        entry["output_usage_reconciliation"] = {
            "created_at": datetime.now(timezone.utc).isoformat(), "evidence_sha256": digest,
            "review_note": review_note, "provider_attempts_reviewed": reviewed,
            "basis": "uncached_input_plus_completion_plus_reasoning_not_invoice",
            "retained_by_model_usd": {k: str(old[k] - released[k]) for k in old},
            "released_by_model_usd": {k: str(v) for k, v in released.items()},
        }
        _save(path, state)
        return entry["output_usage_reconciliation"]
