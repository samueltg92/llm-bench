"""Pre-rendered, script-free results for attachment previews and offline readers."""

from html import escape

from .review_metrics import METRIC_HELP


def e(value):
    return escape(str(value), quote=True)


def fmt(value, digits=2):
    return "—" if value is None else f"{value:,.{digits}f}"


def definition(label, key=None):
    help_text = METRIC_HELP.get(key or label)
    if not help_text:
        return e(label)
    return (f'<details class="static-help"><summary>{e(label)} ⓘ</summary>'
            f'<p class="note">{e(help_text)}</p></details>')


def table(headers, rows):
    return ('<div class="scroll"><table class="metrics static-table"><thead><tr>'
            + ''.join(f'<th scope="col">{e(h)}</th>' for h in headers)
            + '</tr></thead><tbody>'
            + ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)
            + '</tbody></table></div>')


def stat_cell(row):
    if not row or not row.get("n"):
        return "—"
    digits = 5 if row["unit"] == "USD" else 2
    return (f'<b>{fmt(row["mean"], digits)}</b><div class="note">'
            f'{fmt(row["min"], digits)}–{fmt(row["max"], digits)}<br>N = {e(row["n"])}</div>')


def stats_table(rows, models):
    lookup = {(r["metric"], r["model"]): r for r in rows}
    metrics = list(dict.fromkeys(r["metric"] for r in rows))
    cells = []
    for metric in metrics:
        r = next(r for r in rows if r["metric"] == metric)
        cells.append([definition(f'{metric} ({r["unit"]})', metric)
                      + f'<div class="note">{e(r["sample"])}</div>',
                      *[stat_cell(lookup.get((metric, m))) for m in models]])
    return table(["Metric / sample", *models], cells)


def ratio(row, passed, total):
    n = row.get(total)
    return f'{fmt(100 * row.get(passed, 0) / n, 0)}% ({row.get(passed, 0)}/{n})' if n else "—"


def scenario_table(records, models):
    status = {"ok": "complete", "not_run": "not run", "error": "execution error",
              "quota_capacity": "quota blocked", "empty_response": "empty response",
              "output_limit": "truncated output", "tool_iteration_limit": "tool iteration limit",
              "call_limit": "call limit", "skipped_context": "context exceeded",
              "context_failed": "context exceeded"}
    fields = [
        ("Complete conversations", lambda r: ratio(r, "complete", "evaluable")),
        ("Ordered flow milestones", lambda r: ratio(r, "paths_passed", "paths_total")),
        ("Expected tools", lambda r: ratio(r, "tool_checks_passed", "tool_checks_total")),
        ("Explicit case rules", lambda r: ratio(r, "rules_passed", "rules_total")),
        ("TTFT · median per call", lambda r: fmt(r.get("ttft_s")) + " s"),
        ("First user-facing text · median per turn", lambda r: fmt(r.get("turn_first_text_s")) + " s"),
        ("Full latency · median per call", lambda r: fmt(r.get("latency_s")) + " s"),
        ("Flow latency · median per turn", lambda r: fmt(r.get("turn_latency_s")) + " s"),
        ("Native / invalid function calls", lambda r: fmt(r.get("native_calls"), 0) + " / " + fmt(r.get("invalid_calls"), 0)),
        ("Language flags: confirmed / pending", lambda r: fmt(r.get("language_confirmed"), 0) + " / " + fmt(r.get("language_pending"), 0)),
        ("Largest accepted input", lambda r: fmt(r.get("max_input_tokens"), 0) + " tokens"),
        ("Total known cost", lambda r: "USD " + fmt(r.get("cost_usd"), 5)),
        ("Provider or context rejections", lambda r: fmt(r.get("infrastructure_errors"), 0)),
    ]
    selected = {r["model"]: r for r in records}
    rows = [[definition("Scenario status"), *[
        e(status.get(selected[m]["status"], selected[m]["status"])) if m in selected else "—"
        for m in models]]]
    for label, formatter in fields:
        rows.append([definition(label, label.split(" · ")[0]), *[
            e(formatter(selected[m]["metrics"])) if m in selected and selected[m]["metrics"].get("tested") else "—"
            for m in models]])
    return table(["Metric", *models], rows)


def render_static(data):
    """All shareable data rendered as escaped HTML before any JavaScript runs."""
    records = data["records"]
    models = list(dict.fromkeys(r["model"] for r in records))
    projects = list(dict.fromkeys(r["project"] for r in records))
    parts = ['<main id="static-report"><section class="box"><h2>Complete reading view</h2>',
             '<p>All results below are embedded as HTML and can be read without JavaScript or internet access. '
             'Expand a project or scenario, and tap a metric name to read its explanation. '
             'Swipe wide tables horizontally to compare models.</p>',
             '<button id="return-interactive" hidden>Return to interactive view</button>',
             f'<p class="note">As of: {e(data["generated_at"])} · '
             f'{len(records)} model/scenario combinations. '
             + ('SYNTHETIC OFFLINE RESULTS — not measured LLM performance. ' if data["synthetic"] else '')
             + 'Anonymous projects; prompts and private conversations are excluded.</p>',
             '<p>Consolidated cells show mean, minimum–maximum and N (sample count). '
             'Means use individual samples, not project medians. Missing values are not zero. '
             'Percentage checks use 0 for fail and 100 for pass. Timing uses complete conversations; '
             'quality includes evaluable failures. Quota waits are excluded from latency. '
             'Tools use simulated responses. Costs are calculated, not invoices.</p>',
             '<p>Available cases can differ by model. The matched cohort contains only cases evaluable '
             'across all selected models, retaining quality failures. Empty cohorts show no measurements.</p>',
             '<p>Scenario timings use medians of accepted calls and recorded turns, including incomplete '
             'conversations. Scenario cost is the sum of known call costs in that selection. '
             'Earlier attempts and diagnostics outside the selection are not included.</p>',
             f'<p class="note">{e(data["followup_count"])} targeted follow-up attempts replace their original '
             f'cases regardless of outcome; {e(data["historical_count"])} earlier-profile records are kept '
             'separate from the current selection. Initial/current differences are observational: '
             'cache, run timing, sample sizes and targeted retries can differ. Change in mean = '
             'current minus initial, in the metric units (percentage points for %).</p>',
             '<h3>Recorded model profiles</h3>']
    parts.extend(f'<p><b>{e(m)}:</b> {e(data["model_profiles"].get(m, "Profile not recorded"))}</p>' for m in models)
    parts.append('</section>')
    for project in ["All projects", *projects]:
        parts.append(f'<details class="box static-project" open><summary><b>{e(project)}</b></summary>')
        for cohort in ["Available cases", "Same cases across all models"]:
            rows = [r for r in data["consolidated"] if r["project"] == project and r["cohort"] == cohort]
            parts.append(f'<h3>{e(cohort)}</h3>')
            parts.append(stats_table(rows, models) if rows else '<p>No consolidated measurements available.</p>')
        initial = [r for r in data["baseline"].get("consolidated", [])
                   if r["project"] == project and r["cohort"] == "Available cases"]
        if initial:
            parts.append('<details><summary><b>Initial vs current measurements</b></summary>')
            for model in models:
                before = {r["metric"]: r for r in initial if r["model"] == model}
                after = {r["metric"]: r for r in data["consolidated"]
                         if r["project"] == project and r["model"] == model and r["cohort"] == "Available cases"}
                parts.append(f'<h3>{e(model)}</h3>')
                parts.append(f'<p>Initial: {e(data["baseline"].get("model_profiles", {}).get(model, "Not recorded"))}'
                             f'<br>Current: {e(data["model_profiles"].get(model, "Not recorded"))}</p>')
                rows = []
                for metric in dict.fromkeys([*before, *after]):
                    a, b = before.get(metric), after.get(metric)
                    r = b or a
                    delta = b["mean"] - a["mean"] if a and b and a.get("n") and b.get("n") else None
                    rows.append([definition(f'{metric} ({r["unit"]})', metric), stat_cell(a), stat_cell(b),
                                 fmt(delta, 5 if r["unit"] == "USD" else 2)])
                parts.append(table(["Metric", "Initial", "Current", "Change in mean"], rows))
            parts.append('</details>')
        selected = [r for r in records if r["project"] == project]
        if selected:
            parts.append('<h3>Simulated scenarios</h3>')
            for case in dict.fromkeys(r["case"] for r in selected):
                parts.append(f'<details class="static-scenario"><summary><b>{e(case)}</b></summary>')
                parts.append(scenario_table([r for r in selected if r["case"] == case], models))
                parts.append('</details>')
        parts.append('</details>')
    parts.append('</main>')
    return ''.join(parts)
