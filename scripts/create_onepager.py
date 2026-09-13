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
from llm_bench.adjudication import silent_close
from llm_bench.experiment import inputs
from llm_bench.language_review import review_signals
from llm_bench.onepager import render, summarize
from llm_bench.privacy import external_path, fingerprint, write_private
from llm_bench.report import conversation_review, read_lines


def main():
    parser = argparse.ArgumentParser(description=__doc__, fromfile_prefix_chars="@")
    for name in ("data-dir", "config-dir", "budget-file", "out", "guard", "env-file"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--run-root", type=Path, action="append", required=True)
    parser.add_argument("--cost-root", type=Path, required=True)
    parser.add_argument("--exclude-runs-file", type=Path)
    parser.add_argument("--adjudication-policy", type=Path)
    parser.add_argument("--language-policy", type=Path)
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
             "gpt-oss-120b": "GPT-OSS 120B", "gemma-4-31b": "Gemma 4 31B (mín.)"}
    excluded = set()
    if args.exclude_runs_file:
        excluded = set(json.loads(external_path(args.exclude_runs_file).read_text())["ids"])
    runs, calls, sources = [], [], {}
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
                if corrected:
                    run = corrected["summary"]
                    adjudications.append({"run_id": run["run_id"], **corrected["adjudication"]})
                runs.append(run)
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
    data["language_review"] = dict(Counter(r["decision"] for r in language_reviews))
    language_note = (
        f"Idioma: {data['language_review'].get('confirmed_foreign', 0)} señales confirmadas, "
        f"{data['language_review'].get('false_positive_spanish', 0)} falsos positivos y "
        f"{data['language_review'].get('pending', 0)} pendientes de revisión."
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
    write_private(out / "Resultados-detallados.csv", buffer.getvalue(), plain=True)
    # Private review bundle is kept separate from the sanitized deliverable.
    review = out.parent / "conversation-review"
    for run in runs:
        original = sources[run["run_id"]] / "transcripts" / f"{run['run_id']}.json"
        write_private(review / "transcripts" / original.name, json.loads(original.read_text()))
    conversation_review(review)
    write_private(review / "adjudications.json", adjudications)
    write_private(review / "language-review.json", language_reviews)
    write_private(out / "validation.json", {"pages": 1, "publication_guard_passed": True,
                  "selected_conversations": len(runs), "excluded_diagnostics": len(excluded),
                  "private_review_directory": "../conversation-review"})
    print(json.dumps({"pages": 1, "guard_passed": True, "observations": len(runs),
                      "known_costs": costs, "calls_without_measured_cost": unknown}))


if __name__ == "__main__":
    main()
