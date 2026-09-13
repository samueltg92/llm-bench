"""Explicit scenario assertions and language signals, not an all-purpose quality judge."""

import re
from functools import lru_cache


@lru_cache(maxsize=1)
def detector():
    from lingua import LanguageDetectorBuilder

    return LanguageDetectorBuilder.from_all_languages().with_low_accuracy_mode().build()


def language_check(text: str, allowed_languages=None) -> dict:
    allowed = {code.lower() for code in (allowed_languages if allowed_languages is not None else ["es"])}
    if not allowed:
        return {"status": "disabled", "foreign_word_ratio": None, "segments": []}
    words = re.findall(r"[^\W\d_]+", text, re.UNICODE)
    if len(words) < 4:
        return {"status": "unknown", "foreign_word_ratio": None, "segments": []}
    segments = []
    foreign = 0
    checked = 0
    for span in detector().detect_multiple_languages_of(text):
        fragment = text[span.start_index : span.end_index]
        count = len(re.findall(r"[^\W\d_]+", fragment))
        confidence = detector().compute_language_confidence(fragment, span.language)
        if count < 4 or confidence < 0.8:
            continue
        checked += count
        language = span.language.iso_code_639_1.name.lower()
        if language not in allowed:
            foreign += count
            segments.append(
                {
                    "language": language,
                    "confidence": confidence,
                    "start": span.start_index,
                    "end": span.end_index,
                }
            )
    ratio = foreign / len(words)
    return {
        "status": "flagged" if segments else ("no_signal" if checked else "unknown"),
        "foreign_word_ratio": ratio,
        "segments": segments,
        "method": "lingua_low_accuracy_confidence_0.8_min_4_words",
        "allowed_languages": sorted(allowed),
    }


def subset_match(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and subset_match(actual[k], v) for k, v in expected.items()
        )
    return actual == expected


def evaluate_turn(
    turn,
    index: int,
    text: str,
    tools: list[dict],
    active_name: str,
    rules: list,
    first_text_ns: int | None,
) -> list[dict]:
    results = []
    for expected in turn.expect_tools:
        matches = [
            c
            for c in tools
            if c["name"] == expected.tool
            and c.get("tool_mode", "native") == "native"
            and c["valid"]
            and subset_match(c["arguments"], expected.arguments)
        ]
        valid = len(matches) >= expected.min_calls and (
            expected.max_calls is None or len(matches) <= expected.max_calls
        )
        results.append(
            {
                "id": f"expected_tool:{expected.tool}",
                "pass": valid,
                "matches": len(matches),
                "kind": "tool_expectation",
            }
        )
    for name in turn.forbidden_tools:
        results.append(
            {
                "id": f"forbidden_tool:{name}",
                "pass": not any(c["name"] == name for c in tools),
                "kind": "tool_expectation",
            }
        )
    if turn.expected_node:
        results.append(
            {"id": "expected_node", "pass": active_name == turn.expected_node, "kind": "routing"}
        )
    for rule in rules:
        if rule.turn is not None and rule.turn != index:
            continue
        if rule.kind == "required_regex":
            passed = bool(re.search(str(rule.value), text, re.I))
        elif rule.kind == "forbidden_regex":
            passed = not re.search(str(rule.value), text, re.I)
        elif rule.kind == "max_words":
            passed = len(text.split()) <= int(rule.value)
        else:
            matched = [c for c in tools if c["name"] == rule.value and c["valid"]]
            passed = bool(matched) and (
                first_text_ns is None or min(c["executed_ns"] for c in matched) < first_text_ns
            )
        results.append({"id": rule.id, "pass": bool(passed), "kind": "rule"})
    return results


def path_matches(actual: list[str], expected: list[str]) -> bool | None:
    if not expected:
        return None
    # Ordered milestones, not forced traversal. Repeated nodes are significant.
    it = iter(actual)
    return all(any(item == goal for item in it) for goal in expected)
