"""Conservative, persistent reservations before any paid CLI operation."""

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path

from .privacy import external_path


def _save(path, state):
    temp_fd, temp_path = tempfile.mkstemp(prefix=".budget-", dir=path.parent)
    try:
        with os.fdopen(temp_fd, "w") as target:
            json.dump(state, target, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def amount(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid budget amount") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid budget amount")
    return result.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)


def initialize(path: Path, total, model_limits: dict, max_operation=None):
    """Create a new ledger under the reservation lock; never reset an existing one."""
    import fcntl

    def limit(value):
        result = amount(value)
        if result != Decimal(str(value)):
            raise ValueError("Budget limits support at most six decimal places")
        return result

    total = limit(total)
    operation = total if max_operation is None else limit(max_operation)
    if operation > total:
        raise ValueError("Per-operation limit cannot exceed the total budget")
    if not model_limits or any(not isinstance(key, str) or not key.strip() for key in model_limits):
        raise ValueError("Define at least one named model budget")
    state = {
        "limit_usd": str(total), "reserved_usd": "0.000000",
        "max_operation_usd": str(operation),
        "models": {key: {"limit_usd": str(limit(value)), "reserved_usd": "0.000000"}
                   for key, value in model_limits.items()},
        "reservations": [],
    }
    path = external_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path.with_suffix(path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            raise ValueError("Budget ledger already exists; initialization cannot reset spending")
        _save(path, state)
    return state


def status(path: Path):
    """Available application capacity, not a provider credit balance or invoice."""
    state = json.loads(external_path(path).read_text())

    def pool(value):
        cap, used = amount(value["limit_usd"]), amount(value["reserved_usd"])
        if used > cap:
            raise ValueError("Budget reservations exceed the configured limit")
        return {"limit_usd": str(cap), "reserved_usd": str(used), "remaining_usd": str(cap - used)}

    result = {**pool(state), "max_operation_usd": str(amount(state["max_operation_usd"]))}
    if "models" in state:
        result["models"] = {key: pool(value) for key, value in state["models"].items()}
        if sum(amount(v["reserved_usd"]) for v in state["models"].values()) != amount(state["reserved_usd"]):
            raise ValueError("Inconsistent cumulative and per-model budget")
    result["basis"] = "conservative_application_reservations_not_provider_invoices"
    return result


def call_bound(model, price, output_tokens):
    if not price or price.get("last_verified") in (None, "PENDING", "PENDIENTE"):
        raise ValueError("Verified pricing is required before reserving budget")
    # Full configured context, uncached input, and the complete output allowance.
    # This deliberately over-reserves tiny requests and never counts cache discounts.
    return amount(
        (model.context_window * amount(price["input"]) + output_tokens * amount(price["output"]))
        / Decimal(1_000_000)
    )


def reserve(path: Path, usd, purpose: str, *, allocations: dict | None = None):
    import fcntl

    path = external_path(path)
    requested = amount(usd)
    allocated = {key: amount(value) for key, value in (allocations or {}).items()}
    if allocations is not None and sum(allocated.values()) != requested:
        raise ValueError("Model allocations must equal the operation reservation")
    # A missing/corrupt ledger fails closed; never silently reset prior spending.
    if not path.is_file():
        raise ValueError("A private budget ledger is required before online calls")
    lock_path = path.with_suffix(path.suffix + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text())
        limit = amount(state["limit_usd"])
        reserved = amount(state["reserved_usd"])
        per_operation = amount(state["max_operation_usd"])
        if requested > per_operation:
            raise ValueError("Operation exceeds the private per-operation budget")
        if reserved + requested > limit:
            raise ValueError("Operation exceeds the remaining cumulative budget")
        pools = state.get("models")
        if pools is not None:
            if not allocated:
                raise ValueError("Per-model budget requires explicit model allocations")
            if sum(amount(pool["reserved_usd"]) for pool in pools.values()) != reserved:
                raise ValueError("Inconsistent cumulative and per-model budget")
            for key, value in allocated.items():
                if key not in pools:
                    raise ValueError("Model has no authorized budget")
                pool = pools[key]
                if amount(pool["reserved_usd"]) + value > amount(pool["limit_usd"]):
                    raise ValueError(f"Operation exceeds the remaining model budget: {key}")
            # Validate every allocation before mutating any pool (all or nothing).
            for key, value in allocated.items():
                pools[key]["reserved_usd"] = str(amount(pools[key]["reserved_usd"]) + value)
        entry = {
            "id": uuid.uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "purpose": purpose,
            "reserved_usd": str(requested),
        }
        if allocated:
            entry["models"] = {key: str(value) for key, value in allocated.items()}
        state["reserved_usd"] = str(reserved + requested)
        state.setdefault("reservations", []).append(entry)
        _save(path, state)
        return entry


class ReservedProvider:
    """Count before transport, including failed streams and every explicit retry."""

    def __init__(self, provider, model, price, limit):
        self.provider = provider
        self.model = model
        self.price = price
        self.limit = amount(limit)
        self.retained = amount(0)
        self.attempts = 0

    def stream_chat(self, **kwargs):
        bound = call_bound(self.model, self.price, kwargs["max_output_tokens"])
        if self.retained + bound > self.limit:
            raise ValueError("Request would exceed the operation reservation")
        self.retained += bound
        self.attempts += 1
        yield from self.provider.stream_chat(**kwargs)

    def close(self):
        self.provider.close()


def settle_completed(path: Path, run_dir: Path):
    """Release only unused request capacity after an explicitly completed execution.

    Actual attempts keep their entire uncached, full-context cost bound, even
    when usage is missing or the conversation fails. This is not invoice settlement.
    Interrupted executions retain their original reservation.
    """
    import fcntl

    path, run_dir = external_path(path), external_path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if manifest.get("execution_complete") is not True:
        raise ValueError("Only completed executions can release unused capacity")
    reservation_id = manifest["budget_reservation_id"]
    retained = {k: amount(v["retained_usd"]) for k, v in manifest["budget_usage"].items()}
    fd = os.open(path.with_suffix(path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text())
        matches = [r for r in state["reservations"] if r.get("id") == reservation_id]
        if len(matches) != 1:
            raise ValueError("Execution must match exactly one reservation")
        entry = matches[0]
        if "settlement" in entry:
            if entry["settlement"]["run_dir"] != str(run_dir):
                raise ValueError("Reservation already settled against a different execution")
            return entry["settlement"]
        allocated = {k: amount(v) for k, v in entry["models"].items()}
        if set(retained) != set(allocated):
            raise ValueError("Execution model allocation does not match reservation")
        if sum(allocated.values()) != amount(entry["reserved_usd"]):
            raise ValueError("Inconsistent reservation allocation")
        pools = state.get("models")
        if pools is not None and sum(amount(p["reserved_usd"]) for p in pools.values()) != amount(
            state["reserved_usd"]
        ):
            raise ValueError("Inconsistent cumulative and per-model budget")
        released = {}
        for key, value in retained.items():
            if value > allocated[key]:
                raise ValueError("Attempt bounds exceed the original reservation")
            released[key] = allocated[key] - value
            if pools is not None and amount(pools[key]["reserved_usd"]) < released[key]:
                raise ValueError("Release would make model balance negative")
        if sum(released.values()) > amount(state["reserved_usd"]):
            raise ValueError("Release would make cumulative balance negative")
        if pools is not None:
            for key, value in released.items():
                pools[key]["reserved_usd"] = str(amount(pools[key]["reserved_usd"]) - value)
        state["reserved_usd"] = str(amount(state["reserved_usd"]) - sum(released.values()))
        entry["settlement"] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_dir": str(run_dir),
            "basis": "full_context_bound_for_every_attempt_not_invoice",
            "retained_by_model_usd": {k: str(v) for k, v in retained.items()},
            "released_by_model_usd": {k: str(v) for k, v in released.items()},
        }
        _save(path, state)
        return entry["settlement"]


def reconcile_usage(path: Path, run_dir: Path):
    """Audit complete attempt logs; retain uncached input plus full output allowance.

    Unknown or failed attempts retain their full context bound. This releases
    demonstrably unused input capacity, not invoice charges or historical margins.
    Missing attempt records fail closed, including post-transport processing errors.
    """
    import fcntl
    import hashlib

    from .config import Model

    path, run_dir = external_path(path), external_path(run_dir)
    manifest_bytes = (run_dir / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("execution_complete") is not True:
        raise ValueError("Only completed executions can reconcile usage")
    call_bytes = (run_dir / "calls.jsonl").read_bytes()
    calls = [json.loads(line) for line in call_bytes.splitlines() if line.strip()]
    counts = {k: 0 for k in manifest["budget_usage"]}
    retained = {k: amount(0) for k in counts}
    full_bounds = {k: amount(0) for k in counts}
    seen = set()
    for call in calls:
        if call.get("call_id"):
            if call["call_id"] in seen:
                raise ValueError("Duplicate call records")
            seen.add(call["call_id"])
        attempts = call.get("attempts", [])
        if not attempts:
            if call.get("status") not in {"skipped_context", "context_failed"}:
                raise ValueError("Missing attempt evidence")
            continue
        key = call["model_key"]
        model = Model.model_validate(manifest["models"][key])
        price = manifest["pricing"][key]
        output = call.get("max_output_tokens", 512 if call.get("warmup") else None)
        if not isinstance(output, int) or output < 1:
            raise ValueError("Missing output allowance")
        bound = call_bound(model, price, output)
        for index, attempt in enumerate(attempts, 1):
            if attempt["attempt"] != index:
                raise ValueError("Invalid attempt sequence")
            counts[key] += 1
            full_bounds[key] += bound
            value = bound
            usage = attempt.get("usage", {})
            prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if attempt["status"] == "ok" and usage.get("source") == "provider":
                if all(type(v) is int and v >= 0 for v in (prompt, completion)):
                    reasoning = usage.get("reasoning_tokens", 0)
                    if type(reasoning) is not int or reasoning < 0:
                        raise ValueError("Invalid reasoning usage")
                    charged_output = completion + (
                        0 if usage.get("reasoning_included", True) else reasoning
                    )
                    value = amount(
                        (prompt * amount(price["input"])
                         + max(output, charged_output) * amount(price["output"]))
                        / Decimal(1_000_000)
                    )
                    if value > bound:
                        raise ValueError("Reported usage exceeds reserved request bound")
            retained[key] += value
    for key, evidence in manifest["budget_usage"].items():
        if counts[key] != evidence["attempts"]:
            raise ValueError("Recorded attempts do not match transport counter")
        if full_bounds[key] != amount(evidence["retained_usd"]):
            raise ValueError("Recorded bounds do not match transport accounting")
    evidence_hash = hashlib.sha256(manifest_bytes + b"\0" + call_bytes).hexdigest()
    fd = os.open(path.with_suffix(path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text())
        matches = [r for r in state["reservations"]
                   if r.get("id") == manifest["budget_reservation_id"]]
        if len(matches) != 1:
            raise ValueError("Execution must match exactly one reservation")
        entry = matches[0]
        settlement = entry.get("settlement", {})
        if settlement.get("run_dir") != str(run_dir):
            raise ValueError("Capacity settlement required before usage reconciliation")
        if "usage_reconciliation" in entry:
            previous = entry["usage_reconciliation"]
            if previous["evidence_sha256"] != evidence_hash:
                raise ValueError("Reconciled evidence has changed")
            return previous
        old = {k: amount(v) for k, v in settlement["retained_by_model_usd"].items()}
        if old != full_bounds:
            raise ValueError("Settled bounds do not match evidence")
        released = {k: old[k] - retained[k] for k in old}
        pools = state.get("models")
        total = amount(state["reserved_usd"])
        if pools and sum(amount(v["reserved_usd"]) for v in pools.values()) != total:
            raise ValueError("Inconsistent cumulative and per-model budget")
        if any(v < 0 for v in released.values()) or sum(released.values()) > total:
            raise ValueError("Invalid release")
        if pools:
            for key, value in released.items():
                if value > amount(pools[key]["reserved_usd"]):
                    raise ValueError("Release would make model balance negative")
            for key, value in released.items():
                pools[key]["reserved_usd"] = str(amount(pools[key]["reserved_usd"]) - value)
        state["reserved_usd"] = str(total - sum(released.values()))
        entry["usage_reconciliation"] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "evidence_sha256": evidence_hash,
            "basis": "provider_uncached_input_plus_full_output_allowance_not_invoice",
            "retained_by_model_usd": {k: str(v) for k, v in retained.items()},
            "released_by_model_usd": {k: str(v) for k, v in released.items()},
        }
        _save(path, state)
        return entry["usage_reconciliation"]
