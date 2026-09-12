"""Conservative, persistent reservations before any paid CLI operation."""

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from .privacy import external_path


def amount(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid budget amount")
    return result.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)


def call_bound(model, price, output_tokens):
    if not price or price.get("last_verified") in (None, "PENDING", "PENDIENTE"):
        raise ValueError("Verified pricing is required before reserving budget")
    # Full configured context, uncached input, and the complete output allowance.
    # This deliberately over-reserves tiny requests and never counts cache discounts.
    return amount(
        (model.context_window * amount(price["input"]) + output_tokens * amount(price["output"]))
        / Decimal(1_000_000)
    )


def reserve(path: Path, usd, purpose: str):
    import fcntl

    path = external_path(path)
    requested = amount(usd)
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
        entry = {
            "id": uuid.uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "purpose": purpose,
            "reserved_usd": str(requested),
        }
        state["reserved_usd"] = str(reserved + requested)
        state.setdefault("reservations", []).append(entry)
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
        return entry
