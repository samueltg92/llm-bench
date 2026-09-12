"""Private artifact storage, independent of ignore rules."""

import json
import os
import re
from pathlib import Path
from threading import Lock

_write_lock = Lock()


def external_path(path: Path) -> Path:
    path = path.expanduser().resolve()
    if any((parent / ".git").exists() for parent in [path, *path.parents]):
        raise ValueError("Private data/output must be outside every Git working tree")
    return path


def secret_values() -> list[str]:
    return [
        v
        for k, v in os.environ.items()
        if re.search(r"(API_KEY|TOKEN|SECRET|PASSWORD)$", k, re.I) and len(v) >= 8
    ]


def redact(text: str) -> str:
    for value in sorted(secret_values(), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)Bearer\s+[a-z0-9_.-]+", "Bearer [REDACTED]", text)
    return text


def write_private(path: Path, data, *, append: bool = False, plain: bool = False):
    path = external_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    content = data if plain else json.dumps(data, ensure_ascii=False, default=str)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    os.chmod(path, 0o600)
    with _write_lock, os.fdopen(fd, "w") as f:
        f.write(redact(content) + ("\n" if append else ""))


def fingerprint(value) -> str:
    import hashlib

    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()
