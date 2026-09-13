"""Resume a private suite in individually reserved cases, without retrying failures.

Run with PYTHONPATH=src. Separate provider workers share the atomic budget ledger.
Each provider remains serial and retains its rolling request pacer across cases.
"""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from llm_bench import config
from llm_bench.budget import amount, reconcile_usage, reserve, settle_completed
from llm_bench.experiment import execute, inputs, plan
from llm_bench.privacy import external_path, fingerprint, write_private
from llm_bench.rate_limit import RequestPacer
from llm_bench.report import read_lines, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data-dir", "config-dir", "env-file", "budget-file", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--models", required=True)
    parser.add_argument("--resume-from", type=Path, action="append", default=[])
    parser.add_argument("--workers", type=int, default=1, choices=range(1, 5))
    parser.add_argument("--repetitions", type=int, default=1, choices=range(1, 11))
    args = parser.parse_args()
    load_dotenv(external_path(args.env_file), override=True)
    pairs = inputs(args.data_dir, [], True)
    catalog = config.models(args.config_dir / "models.yaml")
    selected = {key: catalog[key] for key in args.models.split(",")}
    for model in selected.values():
        if not model.enabled or not os.environ.get(model.api_key_env):
            raise ValueError("Requested model is disabled or missing credentials")
    prices = config.yaml_data(args.config_dir / "pricing.yaml")["models"]
    bench = config.yaml_data(args.config_dir / "bench.yaml")
    bench.update(repetitions=1, concurrency=1, warmup=False,
                 cross_provider_parallelism=args.workers)
    if bench["retries"]["max_attempts"] != 1:
        raise ValueError("Suite requires one attempt per request; no automatic retries")
    # Resume by source + full scenario hash; failed model responses are observations too.
    done = set()
    for root in [args.out, *args.resume_from]:
        for path in external_path(root).rglob("runs.jsonl"):
            for run in read_lines(path):
                if not run.get("synthetic"):
                    done.add((run["model_key"], run["scenario_sha256"],
                              run["source_sha256"], run["repetition"]))

    def worker(item):
        key, model = item
        rate = bench.get("rate_limits_by_model", {}).get(key)
        pacers = {key: RequestPacer(**rate)} if rate else {}
        outcomes, transport_failures = [], 0
        for bundle, scenario in pairs:
            for repetition in range(1, args.repetitions + 1):
                identity = (key, fingerprint(scenario.model_dump()), bundle.source_sha256,
                            repetition)
                if identity in done:
                    continue
                # Each case creates a new provider; preserve its configured minimum
                # spacing across that boundary as well as within conversations.
                pause = max(0, float(bench.get("min_request_interval_s", 0))) if outcomes else 0
                while pause:
                    step = min(pause, 30)
                    time.sleep(step)
                    pause -= step
                preview = plan([(bundle, scenario)], {key: model}, prices, bench, ["active_node"])
                allocation = amount(preview[0]["budget_reserve_usd"])
                try:
                    entry = reserve(args.budget_file, allocation, "suite_case",
                                    allocations={key: allocation})
                except ValueError:
                    outcomes.append({"status": "budget_blocked", "scenario": scenario.id})
                    write_private(args.out / f"progress-{key}.json", outcomes)
                    return
                # Each isolated execution has one repetition; record the planned repeat index.
                case_bench = {**bench, "suite_repetition": repetition}
                directory = execute(
                    [(bundle, scenario)], {key: model}, prices, case_bench, ["active_node"],
                    args.out / key, preflight=preview, reservation=entry, request_pacers=pacers,
                )
                settle_completed(args.budget_file, directory)
                reconciled = True
                try:
                    reconcile_usage(args.budget_file, directory)
                except (ValueError, KeyError, OSError):
                    reconciled = False
                report(directory)
                result = read_lines(directory / "runs.jsonl")[0]
                outcome = {"scenario": scenario.id, "status": result["status"],
                           "run_id": result["run_id"], "repetition": repetition,
                           "directory": str(directory), "usage_reconciled": reconciled}
                outcomes.append(outcome)
                write_private(args.out / f"progress-{key}.json", outcomes)
                print(json.dumps({"model": key, "finished": len(outcomes),
                                  "project": bundle.project, "status": result["status"]}),
                      flush=True)
                cs = read_lines(directory / "calls.jsonl")
                failed_transport = any(c.get("error_type") in {
                    "RateLimitError", "AuthenticationError", "PermissionDeniedError",
                    "ClientError", "ServerError", "APIConnectionError",
                } for c in cs)
                transport_failures = transport_failures + 1 if failed_transport else 0
                if transport_failures >= 3:
                    outcomes.append({"status": "provider_blocked_three_consecutive_errors"})
                    write_private(args.out / f"progress-{key}.json", outcomes)
                    return

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(worker, selected.items()))


if __name__ == "__main__":
    main()
