"""Allowlisted metrics export. Never copy private histories or arbitrary metadata."""

import math
import re

from .review_metrics import METRIC_HELP

NUMERIC_FIELDS = set("""planned tested complete evaluable infrastructure_errors
ttft_s latency_s turn_first_text_s turn_latency_s tool_checks_passed tool_checks_total
rules_passed rules_total paths_passed paths_total native_calls invalid_calls
language_confirmed language_pending max_input_tokens cost_usd""".split())
STATUSES = set("""ok not_run error quota_capacity empty_response output_limit
tool_iteration_limit call_limit skipped_context context_failed""".split())
SAMPLES = {
    "planned case", "common case", "evaluable conversation",
    "conversation with expected milestones", "positive tool expectation", "explicit assertion",
    "call in a complete conversation", "turn in a complete conversation",
    "provider-measured accepted call", "call with known cost, including incomplete cases",
    "conversation with calls and fully known cost", "recorded API call; excluded from latency",
    "recorded case, including quota blocks",
}


def number(value):
    return value if type(value) in {int, float} and math.isfinite(value) else None


def shareable_data(data):
    """Retain model/profile labels for the publication guard to review, numeric results only.

    Always call the publication guard on the completed HTML too. Labels are configurable
    text and must be reviewed; structural filtering is not a secret scanner.
    """
    projects = list(dict.fromkeys(r["project"] for r in data["records"]))
    # Preserve already anonymous project numbering when possible.
    aliases = {p: p for p in projects} if all(re.fullmatch(r"Project [0-9]+", p) for p in projects) else {
        p: f"Project {i}" for i, p in enumerate(projects, 1)
    }
    aliases["All projects"] = "All projects"
    cases = {}
    for project in projects:
        cases[project] = {c: f"Scenario {i}" for i, c in enumerate(dict.fromkeys(
            r["case"] for r in data["records"] if r["project"] == project), 1)}
    models = {r["model"] for r in data["records"]}

    def metrics(row):
        return {key: number(row.get(key)) for key in NUMERIC_FIELDS}

    def rows(source):
        return [{"project": aliases[r["project"]], "model": r["model"], **metrics(r)}
                for r in source if r["project"] in aliases and r["model"] in models]

    def consolidated(source):
        result = []
        for r in source:
            if (r["project"] not in aliases or r["model"] not in models
                    or r["metric"] not in METRIC_HELP):
                continue
            cohort = r.get("cohort")
            if cohort == "Same cases across all 4":
                cohort = "Same cases across all models"
            if cohort not in {"Available cases", "Same cases across all models"}:
                continue
            result.append({"project": aliases[r["project"]], "model": r["model"],
                           "metric": r["metric"], "cohort": cohort,
                           "unit": r["unit"] if r.get("unit") in {"%", "s", "calls", "flags", "tokens", "USD"} else "",
                           "sample": r["sample"] if r.get("sample") in SAMPLES else "recorded sample",
                           **{key: number(r.get(key)) for key in ("n", "mean", "min", "max")}})
        return result

    def profiles(source):
        return {model: str(profile) for model, profile in source.items() if model in models}

    baseline = data.get("baseline", {})
    return {
        "shareable": True,
        "records": [{"project": aliases[r["project"]], "case": cases[r["project"]][r["case"]],
                     "model": r["model"], "status": r["status"] if r.get("status") in STATUSES else "not_run",
                     "metrics": metrics(r.get("metrics", {})), "history": []}
                    for r in data["records"]],
        "project_rows": rows(data["project_rows"]), "common_rows": rows(data["common_rows"]),
        "project_names": {}, "consolidated": consolidated(data["consolidated"]),
        "model_profiles": profiles(data["model_profiles"]),
        "baseline": {"consolidated": consolidated(baseline.get("consolidated", [])),
                     "model_profiles": profiles(baseline.get("model_profiles", {}))},
        "profile_notes": {}, "followup_count": number(data["followup_count"]),
        "historical_count": number(data["historical_count"]),
        "generated_at": data["generated_at"], "synthetic": bool(data["synthetic"]),
    }
