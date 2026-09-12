import copy
import json
import random
import re
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from .evaluation import evaluate_turn, language_check, path_matches
from .metrics import describe, total_known
from .pricing import calculate, usage_record
from .privacy import fingerprint, write_private
from .prompt import build, prepare
from .providers.base import Usage
from .scenario import render
from .tokens import count, request_tokens


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def retryable(exc):
    import httpx
    from openai import APIConnectionError, APITimeoutError

    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return (
        isinstance(exc, (httpx.TransportError, APIConnectionError, APITimeoutError))
        or status == 429
        or (isinstance(status, int) and 500 <= status < 600)
    )


def measure(provider, messages, tools, scenario, price, retries=None, tool_mode="native"):
    policy = retries or {"max_attempts": 1, "base_delay_s": 0}
    attempts = []
    for attempt in range(policy["max_attempts"]):
        text, reasoning, native_parts, tool_deltas = "", "", [], {}
        usage = Usage()
        first = first_text = first_tool = None
        finish = None
        error = None
        wait_start = time.perf_counter_ns()
        interval = getattr(provider, "min_request_interval_s", 0)
        previous_start = getattr(provider, "last_request_started_ns", None)
        if interval and previous_start is not None:
            remaining = interval - (wait_start - previous_start) / 1e9
            if remaining > 0:
                time.sleep(remaining)
        pacer = getattr(provider, "request_pacer", None)
        if pacer:
            pacer.acquire(request_tokens(messages, tools), scenario.max_output_tokens)
        rate_wait_ms = (time.perf_counter_ns() - wait_start) / 1e6 if interval or pacer else 0
        start = end = time.perf_counter_ns()
        provider.last_request_started_ns = start
        try:
            for event in provider.stream_chat(
                messages=messages,
                tools=tools,
                temperature=scenario.temperature,
                max_output_tokens=scenario.max_output_tokens,
            ):
                end = time.perf_counter_ns()
                if event.native_part:
                    native_parts.append(event.native_part)
                if event.kind == "text" and event.text:
                    first = first or end
                    text += event.text
                    if tool_mode == "native":
                        first_text = first_text or end
                    else:
                        # Hold marker prefixes until distinguishable from speakable text.
                        stripped = text.lstrip()
                        marker = "<<TOOL_CALL>"
                        if marker in stripped:
                            first_tool = first_tool or end
                        visible = re.sub(
                            r"<<TOOL_CALL>.*?(?:<END>|$)", "", text, flags=re.S
                        ).strip()
                        if visible and not marker.startswith(visible):
                            first_text = first_text or end
                elif event.kind == "tool_call":
                    if event.name or event.arguments:
                        first = first or end
                        first_tool = first_tool or end
                    delta = tool_deltas.setdefault(
                        event.index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if event.call_id:
                        delta["id"] = event.call_id
                    delta["function"]["name"] += event.name
                    delta["function"]["arguments"] += event.arguments
                elif event.kind == "reasoning":
                    reasoning += event.text
                elif event.kind == "usage" and event.usage:
                    usage = event.usage
                elif event.kind == "done":
                    finish = event.finish_reason
            if finish is None:
                raise RuntimeError("Incomplete stream")
        except Exception as exc:
            error = exc
        elapsed = (end - start) / 1e6
        attempt_data = {
            "attempt": attempt + 1,
            "status": "error" if error else "ok",
            "error_type": type(error).__name__ if error else None,
            "elapsed_ms": (time.perf_counter_ns() - start) / 1e6,
            "partial_text": text,
            "partial_tools": list(tool_deltas.values()),
            "usage": asdict(usage),
            "rate_limit_wait_ms": rate_wait_ms,
        }
        attempts.append(attempt_data)
        if error and retryable(error) and attempt + 1 < policy["max_attempts"]:
            time.sleep(policy["base_delay_s"] * 2**attempt + random.random() * 0.1)
            continue
        tool_calls = list(tool_deltas.values())
        if tool_mode == "text_protocol" and not error:
            for match in re.finditer(r"<<TOOL_CALL>(.*?)<END>", text, re.S):
                try:
                    call = json.loads(match[1])
                    tool_calls.append(
                        {
                            "id": "call_" + uuid.uuid4().hex,
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["arguments"]),
                            },
                        }
                    )
                except (ValueError, KeyError, TypeError):
                    tool_calls.append(
                        {
                            "id": "call_" + uuid.uuid4().hex,
                            "type": "function",
                            "function": {"name": "invalid_protocol", "arguments": "{}"},
                        }
                    )
            unmatched = text.count("<<TOOL_CALL>") - len(tool_calls)
            if unmatched:
                tool_calls.append(
                    {
                        "id": "call_" + uuid.uuid4().hex,
                        "type": "function",
                        "function": {"name": "invalid_protocol", "arguments": "{}"},
                    }
                )
            text = re.sub(r"<<TOOL_CALL>.*?(?:<END>|$)", "", text, flags=re.S)
        for call in tool_calls:
            call["id"] = call["id"] or "call_" + uuid.uuid4().hex
        if not error and (usage.prompt_tokens is None or usage.completion_tokens is None):
            usage.source = "estimated"
            if usage.prompt_tokens is None:
                usage.prompt_tokens = request_tokens(messages, tools)
            if usage.completion_tokens is None:
                usage.completion_tokens = count(
                    text + json.dumps(tool_calls, ensure_ascii=False) + reasoning
                )
                usage.reasoning_included = True
        generation = (end - first) / 1e6 if first else None
        row = {
            "status": "error" if error else "ok",
            "error_type": type(error).__name__ if error else None,
            "error_message": "Provider request failed; response body omitted for privacy"
            if error
            else None,
            "retries": attempt,
            "flagged": attempt > 0,
            "rate_limit_wait_ms": sum(a["rate_limit_wait_ms"] for a in attempts),
            "ttft_ms": (first - start) / 1e6 if first and not error else None,
            "first_text_ms": (first_text - start) / 1e6 if first_text and not error else None,
            "first_tool_ms": (first_tool - start) / 1e6 if first_tool and not error else None,
            "total_latency_ms": elapsed if not error else None,
            "generation_ms": generation if not error else None,
            "output_tps": usage.completion_tokens / (generation / 1000)
            if not error and generation and usage.completion_tokens is not None
            else None,
            "finish_reason": finish,
            **usage_record(usage),
            **(
                calculate(usage, price)
                if not error
                else {"cost_usd": None, "cost_status": "unknown_usage"}
            ),
            "spend_complete": not error and attempt == 0,
            "attempts": attempts,
            "tool_calls_count": len(tool_calls),
        }
        if not error and finish in {"length", "MAX_TOKENS", "FinishReason.MAX_TOKENS"}:
            row["status"] = "output_limit"
        elif not error and not text.strip() and not tool_calls:
            row["status"] = "empty_response"
        assistant = {"role": "assistant", "content": text}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        if native_parts:
            assistant["_google_parts"] = native_parts
        if reasoning:
            assistant["reasoning_content"] = reasoning
        return row, assistant, first_text


def context_status(tokens, window, bench):
    ratio = tokens / window
    if ratio > bench.get("context_skip_ratio", 0.95):
        return "skipped_context"
    if ratio > bench.get("context_warning_ratio", 0.85):
        return "degraded"
    return "ok"


def resolve_tool(call, bundle, scenario, active, variables, allowed_names, routed=False):
    name = call["function"]["name"]
    before = active
    tool = next((t for t in bundle.tools if t.name == name), None)
    reason, arguments = None, {}
    try:
        arguments = json.loads(call["function"]["arguments"])
        if not isinstance(arguments, dict):
            reason = "arguments_not_object"
        elif name not in allowed_names or tool is None:
            reason = "tool_not_available_at_call_start"
        elif routed:
            reason = "request_new_node_prompt_before_more_tools"
        else:
            jsonschema.validate(arguments, tool.parameters_schema)
            prerequisite = scenario.tool_prerequisites.get(name, {})
            if any(variables.get(k) != v for k, v in prerequisite.items()):
                reason = "prerequisite_not_met"
            elif name == "route_node":
                target = arguments.get("target_node")
                if target not in bundle.node(active).transitions:
                    reason = "transition_not_allowed"
                else:
                    active = bundle.node(active).transitions[target]
    except (ValueError, TypeError, jsonschema.ValidationError, jsonschema.SchemaError):
        reason = "invalid_arguments_or_schema"
    mock = scenario.tool_mocks.get(name, scenario.default_mock)
    started = time.perf_counter_ns()
    if reason:
        response = {"ok": False, "error": reason}
    elif name == "route_node":
        # Routing is local transport, not a backend fixture. Missing-backend
        # defaults must never prevent a valid conversation transition.
        response = {"ok": True, "active_node": bundle.node(active).name}
    elif mock.error:
        active = before
        response = {"ok": False, "error": "simulated_tool_failure"}
    else:
        if mock.delay_ms:
            time.sleep(mock.delay_ms / 1000)
        variables.update(mock.state_updates)
        response = copy.deepcopy(mock.response)
    ended = time.perf_counter_ns()
    event = {
        "name": name,
        "arguments": arguments,
        "valid": reason is None,
        "error": reason,
        "mock_error": mock.error and reason is None and name != "route_node",
        "schema_verified": bool(tool and (not tool.synthetic or name == "route_node")),
        "duration_ms": (ended - started) / 1e6,
        "execution_mode": "mock",
        "executed_ns": ended,
        "active_node_before": before,
        "active_node_after": active,
    }
    return active, response, event


def conversation(
    bundle,
    scenario,
    model_key,
    model,
    provider,
    price,
    bench,
    out: Path,
    mode="active_node",
    repetition=1,
    dedup=False,
):
    scenario.validate_bundle(bundle)
    run_id = uuid.uuid4().hex
    active = bundle.node(scenario.segment).id if scenario.segment else bundle.start_node
    path = [bundle.node(active).name]
    variables = copy.deepcopy(scenario.variables)
    history = []
    if bundle.initial_assistant_message:
        history.append(
            {"role": "assistant", "content": render(bundle.initial_assistant_message, variables)}
        )
    calls, turns, all_tools = [], [], []
    status, degraded = "ok", False
    terminal = False
    effective = "single_node" if bundle.composition == "single_node" else mode
    active_dedup = dedup and effective in ("full", "subset")
    base = {
        "run_id": run_id,
        "project": bundle.project,
        "scenario": scenario.id,
        "scenario_sha256": fingerprint(scenario.model_dump()),
        "source_sha256": bundle.source_sha256,
        "model_key": model_key,
        "provider": model.provider,
        "model_id": model.model_id,
        "prompt_mode": effective,
        "dedup": active_dedup,
        "segment": scenario.segment,
        "repetition": repetition,
        "concurrent": bench.get("concurrency", 1) > 1,
        "synthetic": model.provider == "fake",
    }
    transcript = {**base, "history": history, "turns": turns, "calls": calls}
    try:
        for turn_index, turn in enumerate(scenario.turns[: scenario.max_turns]):
            turn_start = time.perf_counter_ns()
            first_text_ns = None
            turn_rate_wait_ms = first_text_rate_wait_ms = 0
            turn_tools, turn_text = [], ""
            content = turn.content_by_node.get(bundle.node(active).name, turn.content)
            history.append({"role": "user", "content": render(content, variables)})
            final_response = False
            for call_index in range(bench["max_tool_iterations"] + 1):
                if len(calls) >= bench.get("max_calls_per_conversation", float("inf")):
                    status = "call_limit"
                    break
                system, definitions, effective = build(
                    bundle, scenario, active, mode, variables, active_dedup
                )
                messages, api_tools, hashes = prepare(system, history, definitions, model)
                budget = request_tokens(messages, api_tools) + scenario.max_output_tokens
                state = context_status(budget, model.context_window, bench)
                if (
                    state == "skipped_context"
                    and bench.get("on_context_overflow") == "subset"
                    and mode != "subset"
                    and bundle.composition != "single_node"
                ):
                    mode = "subset"
                    system, definitions, effective = build(
                        bundle, scenario, active, mode, variables, active_dedup
                    )
                    messages, api_tools, hashes = prepare(system, history, definitions, model)
                    budget = request_tokens(messages, api_tools) + scenario.max_output_tokens
                    state = context_status(budget, model.context_window, bench)
                meta = {
                    **base,
                    "timestamp_utc": utc_now(),
                    "call_id": uuid.uuid4().hex,
                    "turn_index": turn_index,
                    "call_index_in_turn": call_index,
                    "prompt_mode_effective": effective,
                    "active_node_before": active,
                    "context_est_tokens": budget,
                    "context_ratio": budget / model.context_window,
                    "context_window_tokens": model.context_window,
                    "max_output_tokens": scenario.max_output_tokens,
                    "context_estimator": "o200k_base_cross_model_estimate",
                    "warmup": False,
                    **hashes,
                }
                write_private(
                    out / "systems" / f"{hashes['system_sha256']}.txt",
                    messages[0]["content"],
                    plain=True,
                )
                if state == "skipped_context":
                    status = (
                        "context_failed" if bench.get("on_context_overflow") == "fail" else state
                    )
                    row = {
                        **meta,
                        "status": status,
                        "error_type": "context_budget_exceeded",
                        "ttft_ms": None,
                        "total_latency_ms": None,
                        "cost_usd": None,
                    }
                    calls.append(row)
                    write_private(out / "calls.jsonl", row, append=True)
                    break
                degraded = degraded or state == "degraded"
                row, assistant, call_first_text = measure(
                    provider,
                    messages,
                    api_tools,
                    scenario,
                    price,
                    bench["retries"],
                    hashes["tool_mode"],
                )
                if row.get("usage_source") == "provider" and row.get("prompt_tokens") is not None:
                    row["provider_reported_input_ratio"] = (
                        row["prompt_tokens"] / model.context_window
                    )
                    row["provider_reported_input_plus_output_budget"] = (
                        row["prompt_tokens"] + scenario.max_output_tokens
                    )
                if (
                    row["status"] == "empty_response"
                    and row["finish_reason"] == "stop"
                    and call_index > 0
                    and "?" in turn_text
                    and calls[-1]["active_node_before"] != calls[-1]["active_node_after"]
                ):
                    # A completed silent response immediately after routing can
                    # yield to the question already spoken in this user turn.
                    # Record the silence explicitly; never fabricate a TTFT.
                    row.update(status="ok", awaiting_user_after_route=True)
                first_text_ns = first_text_ns or call_first_text
                turn_rate_wait_ms += row.get("rate_limit_wait_ms", 0)
                if not first_text_ns or first_text_ns == call_first_text:
                    first_text_rate_wait_ms = turn_rate_wait_ms
                history.append(assistant)
                turn_text += assistant.get("content") or ""
                row.update({**meta, "degraded": state == "degraded", "invalid_tool_calls": 0})
                if row["status"] != "ok":
                    status = row["status"]
                elif assistant.get("tool_calls"):
                    if call_index >= bench["max_tool_iterations"]:
                        status = "tool_iteration_limit"
                    else:
                        allowed_names = set(bundle.node(active).tool_names)
                        routed = False
                        for call in assistant["tool_calls"]:
                            previous = active
                            active, response, event = resolve_tool(
                                call, bundle, scenario, active, variables, allowed_names, routed
                            )
                            event["call_id"] = row["call_id"]
                            if active != previous:
                                path.append(bundle.node(active).name)
                                routed = True
                            row["invalid_tool_calls"] += int(not event["valid"])
                            all_tools.append(event)
                            turn_tools.append(event)
                            history.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call["id"],
                                    "content": json.dumps(response, ensure_ascii=False),
                                }
                            )
                            if (
                                event["valid"]
                                and not event["mock_error"]
                                and call["function"]["name"] in scenario.terminal_tools
                            ):
                                terminal = True
                        if active in {bundle.node(n).id for n in scenario.terminal_nodes}:
                            terminal = True
                else:
                    final_response = True
                row["active_node_after"] = active
                calls.append(row)
                write_private(out / "calls.jsonl", row, append=True)
                write_private(out / "transcripts" / f"{run_id}.json", transcript)
                if status != "ok" or final_response:
                    break
            assertions = evaluate_turn(
                turn,
                turn_index,
                turn_text,
                turn_tools,
                bundle.node(active).name,
                scenario.rules,
                first_text_ns,
            )
            for name, limit in scenario.tool_limits_per_turn.items():
                assertions.append(
                    {
                        "id": f"tool_limit:{name}",
                        "kind": "tool_expectation",
                        "pass": sum(t["name"] == name for t in turn_tools) <= limit,
                    }
                )
            turns.append(
                {
                    "turn_index": turn_index,
                    "text": turn_text,
                    "tools": turn_tools,
                    "completed": final_response,
                    "first_text_ms": max(
                        0, (first_text_ns - turn_start) / 1e6 - first_text_rate_wait_ms
                    )
                    if first_text_ns
                    else None,
                    "total_ms": max(
                        0, (time.perf_counter_ns() - turn_start) / 1e6 - turn_rate_wait_ms
                    ),
                    "rate_limit_wait_ms": turn_rate_wait_ms,
                    "assertions": assertions,
                    "language": language_check(turn_text),
                }
            )
            if status != "ok" or terminal:
                break
            if scenario.stop_on_end_phrases and any(
                p and p.casefold() in turn_text.casefold() for p in bundle.end_phrases
            ):
                break
    except Exception as exc:
        status = "error"
        transcript["error_type"] = type(exc).__name__
        # Errors may embed private inputs or credentials; never serialize exception bodies.
    finally:
        assertions = [a for t in turns for a in t["assertions"]]
        # Unreached scripted assertions count as failures, not silently dropped observations.
        for missing_index, turn in enumerate(
            scenario.turns[len(turns) : scenario.max_turns], start=len(turns)
        ):
            for expectation in turn.expect_tools:
                if expectation.min_calls > 0:
                    assertions.append(
                        {
                            "id": f"unreached_tool:{expectation.tool}",
                            "pass": False,
                            "kind": "tool_expectation",
                        }
                    )
            if turn.expected_node:
                assertions.append({"id": "unreached_node", "pass": False, "kind": "routing"})
            for rule in scenario.rules:
                if rule.turn == missing_index or (rule.turn is None and status != "ok"):
                    assertions.append({"id": rule.id, "pass": False, "kind": "rule"})
        spend_complete = all(c.get("spend_complete", False) for c in calls)
        total_cost = total_known(c.get("cost_usd") for c in calls) if spend_complete else None
        measured = [c for c in calls if c["status"] == "ok"]
        tool_assertions = [a for a in assertions if a["kind"] == "tool_expectation"]
        summary = {
            **base,
            "status": status,
            "degraded": degraded,
            "effective_modes": sorted({c.get("prompt_mode_effective", effective) for c in calls}),
            "routing_path": path,
            "expected_path_match": path_matches(path, scenario.expected_path),
            "turns_completed": sum(t["completed"] for t in turns),
            "llm_calls": len(calls),
            "tool_calls_total": sum(c.get("tool_calls_count", 0) for c in calls),
            "invalid_tool_calls": sum(not t["valid"] for t in all_tools),
            "mock_failures": sum(t["mock_error"] for t in all_tools),
            "tool_schema_unverified": sum(not t["schema_verified"] for t in all_tools),
            "tool_expectations_passed": sum(a["pass"] for a in tool_assertions),
            "tool_expectations_total": len(tool_assertions),
            "mock_duration_ms": describe(t["duration_ms"] for t in all_tools),
            "turn_first_text_ms": describe(t["first_text_ms"] for t in turns),
            "language_violations": sum(t["language"]["status"] == "flagged" for t in turns),
            "language_unknown": sum(t["language"]["status"] == "unknown" for t in turns),
            "assertions_passed": sum(a["pass"] for a in assertions),
            "assertions_total": len(assertions),
            "rule_compliance": sum(a["pass"] for a in assertions) / len(assertions)
            if assertions
            else None,
            "assertions": assertions,
            "conversation_ttft": describe(c.get("ttft_ms") for c in measured),
            "conversation_total_latency_ms": total_known(c.get("total_latency_ms") for c in calls),
            "conversation_cost_usd": total_cost,
            "cost_per_1k_conversations_usd": total_cost * 1000 if total_cost is not None else None,
            "known_successful_cost_usd": sum(c.get("cost_usd") or 0 for c in measured),
            "spend_complete": spend_complete,
            "retries": sum(c.get("retries", 0) for c in calls),
            "conversation_prompt_tokens": total_known(c.get("prompt_tokens") for c in calls),
            "conversation_completion_tokens": total_known(
                c.get("completion_tokens") for c in calls
            ),
        }
        transcript.update({"summary": summary, "tools": all_tools})
        write_private(out / "transcripts" / f"{run_id}.json", transcript)
        write_private(out / "runs.jsonl", summary, append=True)
    return summary
