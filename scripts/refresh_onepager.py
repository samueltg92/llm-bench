"""Refresh a private report as case records arrive; never invoke an LLM API."""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from llm_bench.privacy import external_path, write_private


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", type=Path, required=True)
    parser.add_argument("--once", action="store_true", help="Refresh once and exit")
    parser.add_argument("--max-hours", type=float, default=12)
    parser.add_argument("--interval-seconds", type=float, default=60)
    args = parser.parse_args()
    if not 0 < args.max_hours <= 24 or args.interval_seconds < 10:
        parser.error("Require at most 24 hours and interval >= 10 seconds")
    argfile = external_path(args.args_file)
    values = argfile.read_text().splitlines()
    out = external_path(Path(values[values.index("--out") + 1]))
    roots = [external_path(Path(values[i + 1])) for i, value in enumerate(values)
             if value == "--run-root"]
    policy_files = [external_path(Path(values[i + 1])) for i, value in enumerate(values)
                    if value in {"--adjudication-policy", "--language-policy"}]
    deadline = time.monotonic() + args.max_hours * 3600
    last_signature = None
    state = {"status": "watching", "refreshes": 0, "generation_errors": 0}
    while time.monotonic() < deadline:
        paths = [argfile, *policy_files,
                 *(p for root in roots for p in root.rglob("runs.jsonl"))]
        signature = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
        if signature != last_signature:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("create_onepager.py")),
                 "@" + str(argfile)], capture_output=True, text=True,
            )
            state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            if result.returncode:
                # A concurrent append may briefly leave an incomplete record.
                # Retry on the next interval; never expose private traceback text.
                state["generation_errors"] += 1
                state["status"] = "generation_error_retry_pending"
            else:
                last_signature = signature
                data = json.loads((out / "summary.json").read_text())
                state.update(status="watching", refreshes=state["refreshes"] + 1,
                             case_records=data["tested"], evaluable_cases=data["evaluable"],
                             language_pending=data.get("language_review", {}).get("pending", 0))
                if data["tested"] >= data["planned_combinations"]:
                    # All dispositions are present, including explicit quota blocks.
                    # This does not mean every conversation succeeded or every
                    # language signal has been reviewed.
                    state["status"] = "all_case_records_present_review_status_in_report"
            write_private(out / "refresh-status.json", state)
            print(json.dumps(state), flush=True)
            if state["status"] == "all_case_records_present_review_status_in_report":
                return
            if args.once:
                if result.returncode:
                    raise SystemExit(result.returncode)
                return
        remaining = args.interval_seconds
        while remaining > 0 and time.monotonic() < deadline:
            step = min(remaining, 30, max(0, deadline - time.monotonic()))
            time.sleep(step)
            remaining -= step
    state.update(status="watch_window_ended", updated_at_utc=datetime.now(timezone.utc).isoformat())
    write_private(out / "refresh-status.json", state)
    print(json.dumps(state), flush=True)


if __name__ == "__main__":
    main()
