"""Create a private, anonymized PDF and detailed CSV from a version-matched suite."""

import argparse
import csv
import io
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from check_public import guard_data, violations
from dotenv import load_dotenv
from pypdf import PdfReader

from llm_bench import config
from llm_bench.adjudication import silent_close, silent_handoff
from llm_bench.experiment import inputs
from llm_bench.language_review import review_signals
from llm_bench.onepager import common_cohort, median, render, summarize
from llm_bench.privacy import external_path, fingerprint, write_private
from llm_bench.report import conversation_review, read_lines
from llm_bench.review_html import render_review


def main():
    parser = argparse.ArgumentParser(description=__doc__, fromfile_prefix_chars="@")
    for name in ("data-dir", "config-dir", "budget-file", "out", "guard", "env-file"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--run-root", type=Path, action="append", required=True)
    parser.add_argument("--cost-root", type=Path, required=True)
    parser.add_argument("--exclude-runs-file", type=Path)
    parser.add_argument("--adjudication-policy", type=Path)
    parser.add_argument("--language-policy", type=Path)
    parser.add_argument("--private-project-labels", type=Path,
                        help="Private display names used only in the offline conversation viewer")
    parser.add_argument("--private-scenario-labels", type=Path,
                        help="Private English scenario titles and descriptions, keyed by scenario hash")
    parser.add_argument("--note", action="append", default=[])
    args = parser.parse_args()
    load_dotenv(external_path(args.env_file), override=True)
    os.environ["BENCH_PRIVATE_GUARD"] = str(external_path(args.guard))
    pairs = inputs(args.data_dir, [], True)
    expected = {(fingerprint(s.model_dump()), b.source_sha256) for b, s in pairs}
    pair_lookup = {(fingerprint(s.model_dump()), b.source_sha256): (b, s) for b, s in pairs}
    policies = (json.loads(external_path(args.adjudication_policy).read_text())
                if args.adjudication_policy else {})
    adjudications = []
    language_policy = (json.loads(external_path(args.language_policy).read_text())
                       if args.language_policy else {})
    language_reviews = []
    planned = Counter(b.project for b, s in pairs)
    names = {"mistral-small-4": "Mistral Small 4", "glm-5.3-flash": "GLM 5.3 Flash",
             "gpt-oss-120b": "GPT-OSS 120B", "gemma-4-31b": "Gemma 4 31B (min.)"}
    excluded = set()
    if args.exclude_runs_file:
        excluded = set(json.loads(external_path(args.exclude_runs_file).read_text())["ids"])
    runs, calls, sources, interpreted = [], [], {}, {}
    observations = set()
    for root in args.run_root:
        for path in sorted(external_path(root).rglob("runs.jsonl")):
            for run in read_lines(path):
                if run["run_id"] in excluded or run.get("synthetic"):
                    continue
                identity = (run["scenario_sha256"], run["source_sha256"])
                if identity not in expected or run["model_key"] not in names:
                    continue
                key = (run["model_key"], *identity, run["repetition"])
                if key in observations:
                    raise ValueError("Duplicate observation: explicitly select diagnostic exclusions")
                observations.add(key)
                transcript_path = path.parent / "transcripts" / f"{run['run_id']}.json"
                transcript = json.loads(transcript_path.read_text())
                language_reviews.extend(review_signals(transcript, language_policy))
                bundle, scenario = pair_lookup[identity]
                corrected = silent_close(transcript, scenario, bundle,
                                         policies.get(run["project"], {}))
                corrected = corrected or silent_handoff(transcript, scenario, bundle,
                                                         policies.get(run["project"], {}))
                if corrected:
                    run = corrected["summary"]
                    adjudications.append({"run_id": run["run_id"], **corrected["adjudication"]})
                runs.append(run)
                interpreted[run["run_id"]] = corrected or transcript
                sources[run["run_id"]] = path.parent
                calls.extend(corrected["calls"] if corrected else transcript["calls"])
    costs, seen_calls, unknown = defaultdict(float), set(), 0
    for path in external_path(args.cost_root).rglob("calls.jsonl"):
        for call in read_lines(path):
            cid = call.get("call_id")
            if cid and cid in seen_calls:
                continue
            seen_calls.add(cid)
            if call.get("cost_usd") is not None:
                costs[call["model_key"]] += call["cost_usd"]
            else:
                unknown += 1
    budget = json.loads(external_path(args.budget_file).read_text())
    reserved = {k: v["reserved_usd"] for k, v in budget["models"].items()}
    data = summarize(runs, calls, planned, names, known_costs=costs, reserved=reserved,
                     notes=args.note)
    data["calls_without_measured_cost"] = unknown
    data["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data["adjudicated_silent_closes"] = len(adjudications)
    def review_metrics(row, selected_runs, selected_calls, *, include_incomplete=False):
        ids = {r["run_id"] for r in selected_runs}
        decisions = Counter(r["decision"] for r in language_reviews if r["run_id"] in ids)
        row.update(language_confirmed=decisions["confirmed_foreign"],
                   language_pending=decisions["pending"],
                   language_false_positive=decisions["false_positive_spanish"],
                   max_input_tokens=max((c.get("prompt_tokens") or 0 for c in selected_calls
                                         if c.get("usage_source") == "provider"
                                         and c.get("status") in {"ok", "empty_response", "output_limit"}),
                                        default=0))
        turns = [turn for run in selected_runs if include_incomplete or run["status"] == "ok"
                 for turn in interpreted[run["run_id"]].get("turns", [])]
        row["turn_first_text_s"] = median(t["first_text_ms"] / 1000 for t in turns
                                          if t.get("first_text_ms") is not None)
        row["turn_latency_s"] = median(t["total_ms"] / 1000 for t in turns
                                       if t.get("total_ms") is not None)
        row["turn_samples"] = len(turns)
        return row

    common = common_cohort(runs, names)
    common_counts = {p: sum(r["project"] == p for r in common) // len(names) for p in planned}
    common_rows = summarize(common, calls, common_counts, names,
                            known_costs={}, reserved={})["rows"]
    data["common_case_rows"] = common_rows
    for rows, population in [(data["rows"], runs), (common_rows, common)]:
        for row in rows:
            selected = [r for r in population if names[r["model_key"]] == row["model"]
                        and "Project " + r["project"].split("_")[-1] == row["project"]]
            ids = {r["run_id"] for r in selected}
            review_metrics(row, selected, [c for c in calls if c.get("run_id") in ids])
    data["language_review"] = dict(Counter(r["decision"] for r in language_reviews))
    language_note = (
        f"Language: {data['language_review'].get('confirmed_foreign', 0)} confirmed flags, "
        f"{data['language_review'].get('false_positive_spanish', 0)} false positives and "
        f"{data['language_review'].get('pending', 0)} pending review."
    )
    data["notes"] = [note.replace("{language_review}", language_note) for note in data["notes"]]
    data["pricing_sources"] = {k: v.get("source_url") for k, v in
                               config.yaml_data(args.config_dir / "pricing.yaml")["models"].items()
                               if k in names}
    out = external_path(args.out)
    pdf = render(data, out)
    reader = PdfReader(pdf)
    if len(reader.pages) != 1:
        raise ValueError("Expected exactly one PDF page")
    text = reader.pages[0].extract_text() + str(reader.metadata) + json.dumps(data)
    terms, fragments, words = guard_data()
    problems = violations("docs/benchmark-summary.txt", text.encode(), terms, fragments, words)
    if problems:
        raise ValueError("Artifact publication guard rejected output: " + ", ".join(problems))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(data["rows"][0]))
    writer.writeheader()
    writer.writerows(data["rows"])
    write_private(out / "Detailed-results.csv", buffer.getvalue(), plain=True)
    # Private review bundle is kept separate from the sanitized deliverable.
    review = out.parent / "conversation-review"
    originals = {}
    for run in runs:
        original = sources[run["run_id"]] / "transcripts" / f"{run['run_id']}.json"
        originals[run["run_id"]] = json.loads(original.read_text())
        write_private(review / "transcripts" / original.name, originals[run["run_id"]])
    conversation_review(review)
    write_private(review / "adjudications.json", adjudications)
    write_private(review / "language-review.json", language_reviews)
    scenario_labels = (json.loads(external_path(args.private_scenario_labels).read_text())
                       if args.private_scenario_labels else {})
    comparisons, coverage = [], []
    case_numbers = Counter()
    adjudicated_ids = {a["run_id"] for a in adjudications}
    for bundle, scenario in pairs:
        case_numbers[bundle.project] += 1
        alias = f"P{bundle.project.split('_')[-1]}-C{case_numbers[bundle.project]:02d}"
        scenario_hash = fingerprint(scenario.model_dump())
        label = scenario_labels.get(scenario_hash, {})
        for model, name in names.items():
            matches = [r for r in runs if r["model_key"] == model
                       and r["scenario_sha256"] == scenario_hash
                       and r["source_sha256"] == bundle.source_sha256]
            for run in matches or [None]:
                row = {"project": "Project " + bundle.project.split("_")[-1],
                       "case": alias, "model": name,
                       "repetition": run["repetition"] if run else 1,
                       "status": run["status"] if run else "not_run",
                       "path_match": run.get("expected_path_match") if run else None}
                coverage.append(row)
                original = originals[run["run_id"]] if run else {}
                selected_calls = [c for c in calls if run and c.get("run_id") == run["run_id"]]
                case_metrics = summarize([run] if run else [], selected_calls,
                                         {bundle.project: 1}, {model: name},
                                         known_costs={}, reserved={})["rows"][0]
                accepted = [c for c in selected_calls if c.get("usage_source") == "provider"
                            and c.get("status") in {"ok", "empty_response", "output_limit"}]
                for field, source in [("ttft_s", "ttft_ms"), ("first_text_s", "first_text_ms"),
                                      ("latency_s", "total_latency_ms")]:
                    case_metrics[field] = median(c[source] / 1000 for c in accepted
                                                 if c.get(source) is not None)
                review_metrics(case_metrics, [run] if run else [], selected_calls,
                               include_incomplete=True)
                comparisons.append({**row, "case": alias + f" · R{row['repetition']}",
                                    "description": label.get("description", "Original scenario content is preserved in the private source files."),
                                    "scenario_name": label.get("title", "Scenario " + alias),
                                    "metrics": case_metrics,
                                    "history": original.get("history", []),
                                    "raw_status": original.get("summary", {}).get("status", "not_run"),
                                    "adjudicated": bool(run and run["run_id"] in adjudicated_ids)})
    private_names = {}
    if args.private_project_labels:
        labels = json.loads(external_path(args.private_project_labels).read_text())
        private_names = {"Project " + k.split("_")[-1]: v for k, v in labels.items()
                         if k in {b.project for b, _ in pairs} and isinstance(v, str)}
    render_review(comparisons, review / "Conversaciones.html", project_rows=data["rows"],
                  common_rows=common_rows, project_names=private_names,
                  generated_at=data["generated_at_utc"])
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(coverage[0]))
    writer.writeheader()
    writer.writerows(coverage)
    write_private(out / "Case-coverage.csv", buffer.getvalue(), plain=True)
    # Only calls with an accepted response count as observed node prompts.
    node_coverage = []
    bundles = {b.project: b for b, _ in pairs}
    for project, bundle in bundles.items():
        nonempty = {n.id for n in bundle.nodes if n.prompt.strip()}
        for model, name in names.items():
            observed = {c.get("active_node_before") for c in calls
                        if c.get("model_key") == model and c.get("project") == project
                        and c.get("usage_source") == "provider"
                        and c.get("status") in {"ok", "empty_response", "output_limit"}}
            node_coverage.append({"project": "Project " + project.split("_")[-1],
                                  "model": name, "nodes_in_source": len(bundle.nodes),
                                  "nodes_with_text": len(nonempty),
                                  "nodes_observed": len(observed & nonempty),
                                  "global_prompt_included": bool(bundle.global_system and observed)})
    write_private(out / "Node-coverage.json", node_coverage)
    write_private(out / "validation.json", {"pages": 1, "publication_guard_passed": True,
                  "selected_conversations": len(runs), "excluded_diagnostics": len(excluded),
                  "private_review_directory": "../conversation-review"})
    print(json.dumps({"pages": 1, "guard_passed": True, "observations": len(runs),
                      "known_costs": costs, "calls_without_measured_cost": unknown}))


if __name__ == "__main__":
    main()
