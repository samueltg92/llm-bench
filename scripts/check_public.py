#!/usr/bin/env python3
"""Scan staged or committed bytes, never print matched content.

Optional private guard file: git config bench.privateGuard /external/guard.json
Schema: {"terms": ["private name"], "whole_word_terms": ["private acronym"],
         "corpus_files": ["/external/input.json"]}.
The guard itself must stay outside Git. Local commit and push hooks both use it.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {".gitignore", ".env.example", "README.md", "SECURITY.md", "pyproject.toml", "uv.lock"}
DIRECTORIES = {"src", "tests", "scripts", "config", "docs", ".githooks", ".github"}
SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        rb"\b(?:sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})"
    ),
    re.compile(
        rb"""(?i)(?:api_key|password|secret|access_token)\s*[=:]\s*["']([A-Za-z0-9/+_=-]{24,})["']"""
    ),
]


def git(*args, check=True):
    return subprocess.run(
        ["git", *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=check
    ).stdout


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for val in value.values():
            yield from strings(val)
    elif isinstance(value, list):
        for val in value:
            yield from strings(val)


def guard_data():
    location = (
        os.environ.get("BENCH_PRIVATE_GUARD")
        or git("config", "--get", "bench.privateGuard", check=False).decode().strip()
    )
    terms, fragments, whole_word_terms = [], set(), []
    if location:
        data = json.loads(Path(location).read_text())
        terms = [term.casefold().encode() for term in data.get("terms", []) if term]
        whole_word_terms = [term.casefold() for term in data.get("whole_word_terms", []) if term]
        for source in data.get("corpus_files", []):
            data = json.loads(Path(source).read_text())
            for text in strings(data):
                for line in text.splitlines():
                    line = " ".join(line.split())
                    if len(line) >= 80:
                        # Several windows detect partial pasted prompt paragraphs as well.
                        for offset in range(0, len(line) - 79, 40):
                            fragments.add(line[offset : offset + 80].casefold().encode())
    return terms, fragments, whole_word_terms


def violations(name, content, terms, fragments, whole_word_terms=()):
    reasons = []
    path = Path(name)
    if name not in ROOT_FILES and (not path.parts or path.parts[0] not in DIRECTORIES):
        reasons.append("path_not_allowlisted")
    if any(part in {"results", "bundles", "transcripts", "private", ".env"} for part in path.parts):
        reasons.append("private_path")
    if path.suffix.lower() in {".pem", ".key", ".pdf", ".docx", ".zip", ".sqlite", ".db"}:
        reasons.append("private_or_binary_file")
    if b"\x00" in content:
        reasons.append("binary_content")
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        reasons.append("credential_pattern")
    if name == ".env.example":
        for line in content.decode().splitlines():
            if (
                line.strip()
                and not line.lstrip().startswith("#")
                and "=" in line
                and line.split("=", 1)[1].strip()
            ):
                reasons.append("populated_env_template")
    combined = (name + "\n" + content.decode(errors="replace")).casefold().encode()
    if any(term in combined for term in terms):
        reasons.append("private_name")
    # Underscores and hyphens delimit identifiers; substrings inside ordinary words do not.
    if any(
        re.search(
            r"(?<![^\W_])" + re.escape(term) + r"(?![^\W_])", combined.decode(errors="replace")
        )
        for term in whole_word_terms
    ):
        reasons.append("private_name")
    collapsed = " ".join(combined.decode(errors="replace").split()).encode()
    if any(fragment in collapsed for fragment in fragments):
        reasons.append("private_corpus_overlap")
    for key, value in os.environ.items():
        if (
            re.search(r"(API_KEY|TOKEN|SECRET|PASSWORD)$", key, re.I)
            and len(value) >= 8
            and value.encode() in content
        ):
            reasons.append("credential_value")
    return sorted(set(reasons))


def scan(staged=False, history=False):
    terms, fragments, whole_word_terms = guard_data()
    errors, checked = [], 0
    if history:
        revisions = git("rev-list", "--all").decode().splitlines()
    else:
        revisions = [None if staged else "HEAD"]
    seen = set()
    for revision in revisions:
        names = (
            (
                git("ls-files", "-z")
                if revision is None
                else git("ls-tree", "-r", "--name-only", "-z", revision)
            )
            .decode()
            .split("\0")
        )
        for name in filter(None, names):
            content = git("show", (":" if revision is None else revision + ":") + name)
            signature = (name, content)
            if signature in seen:
                continue
            seen.add(signature)
            checked += 1
            found = violations(name, content, terms, fragments, whole_word_terms)
            if found:
                # Even filenames can be private. Report only a sequential identifier.
                errors.append({"file_index": checked, "reasons": found})
        if revision:
            message = git("show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", revision)
            found = violations("README.md", message, terms, fragments, whole_word_terms)
            if found:
                errors.append({"commit_message": True, "reasons": found})
    print(
        json.dumps(
            {
                "files_checked": checked,
                "private_guard_loaded": bool(terms or fragments or whole_word_terms),
                "violations": errors,
            }
        )
    )
    return bool(errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    try:
        sys.exit(int(scan(args.staged, args.history)))
    except Exception:
        print("Publication scan could not complete; refusing to proceed.", file=sys.stderr)
        sys.exit(2)
