import copy
import platform
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from .budget import call_bound
from .bundle import Bundle
from .pricing import calculate
from .privacy import external_path, fingerprint, write_private
from .prompt import build, prepare
from .providers.base import Usage
from .providers.registry import create
from .runner import context_status, conversation, measure
from .scenario import Scenario, load
from .tokens import count, request_tokens


def inputs(data_dir: Path, projects: list[str], all_segments=False):
    data_dir = external_path(data_dir)
    scenarios = [load(path) for path in sorted((data_dir / "scenarios").glob("*.yaml"))]
    selected = [
        s
        for s in scenarios
        if (not projects or s.project in projects) and (all_segments or s.reference)
    ]
    if not selected:
        raise ValueError("No matching private scenarios")
    if set(projects) - {s.project for s in selected}:
        raise ValueError("Requested project has no scenario")
    result = []
    for scenario in selected:
        bundle = Bundle.model_validate_json(
            (data_dir / "bundles" / f"{scenario.project}.json").read_text()
        )
        scenario.validate_bundle(bundle)
        result.append((bundle, scenario))
    return result


def jobs_for(pairs, modes):
    seen = set()
    for bundle, scenario in pairs:
        for mode in modes:
            effective = "single_node" if bundle.composition == "single_node" else mode
            key = (scenario.id, effective)
            if key not in seen:
                seen.add(key)
                yield bundle, scenario, mode


def plan(pairs, models, prices, bench, modes, dedup=False):
    rows = []
    for key, model in models.items():
        for bundle, scenario, mode in jobs_for(pairs, modes):
            active = bundle.node(scenario.segment).id if scenario.segment else bundle.start_node
            system, tools, effective = build(
                bundle, scenario, active, mode, scenario.variables, dedup
            )
            messages, definitions, _ = prepare(system, [], tools, model)
            initial_tokens = request_tokens(messages, definitions)
            # Estimate pessimistically using the largest node across the whole graph.
            max_tokens = initial_tokens
            if effective == "active_node":
                for node in bundle.nodes:
                    sys, ts, _ = build(bundle, scenario, node.id, mode, scenario.variables)
                    ms, ts, _ = prepare(sys, [], ts, model)
                    max_tokens = max(max_tokens, request_tokens(ms, ts))
            turns = min(len(scenario.turns), scenario.max_turns)
            call_count = turns * (bench["max_tool_iterations"] + 1)
            mock_tokens = max(
                [count(m.response) for m in scenario.tool_mocks.values()]
                + [count(scenario.default_mock.response)]
            )
            # Planning estimate, not a bill: generated paths and tool-call multiplicity are unknown.
            history_growth = count([t.content for t in scenario.turns]) + count(
                bundle.initial_assistant_message
            )
            estimate_input = sum(
                max_tokens + history_growth + i * (scenario.max_output_tokens + mock_tokens + 32)
                for i in range(call_count)
            )
            usage = Usage(
                prompt_tokens=estimate_input,
                completion_tokens=call_count * scenario.max_output_tokens,
                source="estimated",
            )
            estimate = calculate(usage, prices.get(key))
            try:
                bound_cost = (
                    float(call_bound(model, prices.get(key), scenario.max_output_tokens))
                    * call_count
                )
            except (ValueError, TypeError, KeyError):
                bound_cost = None
            reps = bench["repetitions"]
            rows.append(
                {
                    "project": bundle.project,
                    "scenario": scenario.id,
                    "model": key,
                    "prompt_mode": effective,
                    "dedup": dedup and effective in ("full", "subset"),
                    "enabled": model.enabled,
                    "initial_prompt_tokens_est": initial_tokens,
                    "largest_prompt_tokens_est": max_tokens,
                    "context_status": context_status(
                        initial_tokens + scenario.max_output_tokens, model.context_window, bench
                    ),
                    "estimated_cost_usd": estimate["cost_usd"] * reps
                    if estimate["cost_usd"] is not None
                    else None,
                    "budget_reserve_usd": bound_cost * reps * bench["retries"]["max_attempts"]
                    if bound_cost is not None
                    else None,
                    "cost_status": estimate["cost_status"],
                    "max_calls_per_conversation": call_count,
                }
            )
    return rows


def execute(pairs, models, prices, bench, modes, out, dedup=False, preflight=None):
    out = external_path(out)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = out / stamp
    manifest = {
        "version": "0.1.0",
        "created_at": stamp,
        "host": platform.node(),
        "dependencies": {
            p: version(p)
            for p in (
                "openai",
                "google-genai",
                "httpx",
                "tiktoken",
                "pydantic",
                "lingua-language-detector",
            )
        },
        "python": platform.python_version(),
        "bench": bench,
        "modes": modes,
        "dedup": dedup,
        "models": {k: m.model_dump() for k, m in models.items()},
        "pricing": prices,
        "preflight": preflight,
        "token_estimator": "o200k_base",
        "tools_execution": "mock",
        "scenarios": [
            {
                "id": s.id,
                "sha256": fingerprint(s.model_dump()),
                "source_sha256": b.source_sha256,
                "configuration": s.model_dump(),
                "input_warnings": b.warnings,
            }
            for b, s in pairs
        ],
        "warnings": [
            "Private artifacts: never publish this directory",
            "p95 with few repetitions is exploratory",
            "Mock durations do not measure external tool service latency",
            "Context estimates are cross-model approximations, not token guarantees",
        ],
    }
    write_private(directory / "manifest.json", manifest)

    def run_model(item):
        key, model = item
        provider = create(model, bench["timeouts"])
        try:
            if bench.get("warmup", True):
                scenario = Scenario(
                    id="warmup",
                    project="project_1",
                    turns=[{"content": "Hola"}],
                    max_output_tokens=512,
                )
                row, _, _ = measure(
                    provider,
                    [{"role": "user", "content": "Hola"}],
                    [],
                    scenario,
                    prices.get(key),
                    bench["retries"],
                )
                row.update(
                    {
                        "warmup": True,
                        "model_key": key,
                        "provider": model.provider,
                        "model_id": model.model_id,
                        "timestamp_utc": stamp,
                    }
                )
                write_private(directory / "calls.jsonl", row, append=True)
            for bundle, scenario, mode in jobs_for(pairs, modes):
                for repetition in range(1, bench["repetitions"] + 1):
                    conversation(
                        bundle,
                        scenario,
                        key,
                        model,
                        provider,
                        prices.get(key),
                        copy.deepcopy(bench),
                        directory,
                        mode=mode,
                        repetition=repetition,
                        dedup=dedup,
                    )
        finally:
            provider.close()

    if bench["concurrency"] == 1:
        for item in models.items():
            run_model(item)
    else:
        with ThreadPoolExecutor(max_workers=bench["concurrency"]) as pool:
            list(pool.map(run_model, models.items()))
    import json

    call_file = directory / "calls.jsonl"
    recorded = (
        [json.loads(line) for line in call_file.read_text().splitlines()]
        if call_file.exists()
        else []
    )
    manifest["system_sha256"] = sorted(
        {c["system_sha256"] for c in recorded if "system_sha256" in c}
    )
    write_private(directory / "manifest.json", manifest)
    return directory
