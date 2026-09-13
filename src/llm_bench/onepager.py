"""Anonymized, one-page executive report from explicitly selected observations."""

import html
import statistics
from collections import Counter
from pathlib import Path

from .privacy import external_path, write_private


def median(values):
    values = [value for value in values if value is not None]
    return statistics.median(values) if values else None


def common_cohort(runs, model_keys):
    """Intersect evaluable cases, retaining model failures rather than only passes."""
    required = set(model_keys)
    available = {}
    for run in runs:
        if run["status"] in {"error", "skipped_context", "context_failed", "quota_capacity"}:
            continue
        key = (run["project"], run["scenario_sha256"], run["source_sha256"], run["repetition"])
        available.setdefault(key, set()).add(run["model_key"])
    common = {key for key, models in available.items() if required <= models}
    return [r for r in runs if r["model_key"] in required
            and (r["project"], r["scenario_sha256"], r["source_sha256"], r["repetition"]) in common]


def summarize(runs, calls, planned, model_names, *, known_costs, reserved, notes=None):
    """Keep failures in coverage/cost; latency uses complete conversations only.

    Tool compliance uses positive call expectations, including unreachable calls;
    absence constraints are reported with rule checks rather than inflating recall.
    No raw text, tool/node names, scenario IDs, hashes or paths enter the output.
    """
    rows = []
    evaluated_total = 0
    for project, count in sorted(planned.items()):
        for model, name in model_names.items():
            selected = [r for r in runs if r["project"] == project and r["model_key"] == model]
            evaluable = [r for r in selected if r["status"] not in {
                "error", "skipped_context", "context_failed", "quota_capacity",
            }]
            evaluated_total += len(evaluable)
            ids = {r["run_id"] for r in selected if r["status"] == "ok"}
            cs = [c for c in calls if c.get("run_id") in ids and c.get("status") == "ok"]
            assertions = [a for r in evaluable for a in r.get("assertions", [])]
            positive_tools = [a for a in assertions if a["id"].startswith(
                ("expected_tool:", "unreached_tool:")
            )]
            rules = [a for a in assertions if not a["id"].startswith(
                ("expected_tool:", "unreached_tool:")
            )]
            route_runs = [r for r in evaluable if r.get("expected_path_match") is not None]
            rows.append({
                "project": "Project " + project.split("_")[-1], "model": name,
                "planned": count, "tested": len(selected), "complete": len(ids),
                "evaluable": len(evaluable), "infrastructure_errors": len(selected) - len(evaluable),
                "strict_pass": sum(r["status"] == "ok"
                                   and r.get("assertions_total", 0) > 0
                                   and r.get("expected_path_match") is not False
                                   and r.get("assertions_passed") == r.get("assertions_total")
                                   and r.get("invalid_tool_calls", 0) == 0 for r in selected),
                "ttft_s": median(c.get("ttft_ms") / 1000 for c in cs
                                 if c.get("ttft_ms") is not None),
                "first_text_s": median(c.get("first_text_ms") / 1000 for c in cs
                                       if c.get("first_text_ms") is not None),
                "latency_s": median(c.get("total_latency_ms") / 1000 for c in cs
                                    if c.get("total_latency_ms") is not None),
                "tool_checks_passed": sum(a["pass"] for a in positive_tools),
                "tool_checks_total": len(positive_tools),
                "rules_passed": sum(a["pass"] for a in rules), "rules_total": len(rules),
                "paths_passed": sum(r["expected_path_match"] for r in route_runs),
                "paths_total": len(route_runs),
                "native_calls": sum(r.get("native_tool_calls", r.get("tool_calls_total", 0)) for r in selected),
                "invalid_calls": sum(r.get("invalid_tool_calls", 0) for r in selected),
                "language_flags": sum(r.get("language_violations", 0) for r in selected),
                "cost_usd": sum(c.get("cost_usd") or 0 for c in calls
                                if c.get("run_id") in {r["run_id"] for r in selected}),
                "statuses": dict(Counter(r["status"] for r in selected)),
            })
    contexts = {}
    for model, name in model_names.items():
        accepted = [c.get("prompt_tokens", 0) or 0 for c in calls
                    if c.get("model_key") == model and c.get("usage_source") == "provider"
                    and c.get("status") in {"ok", "empty_response", "output_limit"}]
        contexts[name] = max(accepted, default=0)
    return {"rows": rows, "planned_cases": sum(planned.values()),
            "planned_combinations": sum(planned.values()) * len(model_names),
            "models": len(model_names), "tested": len(runs),
            "evaluable": evaluated_total,
            "complete": sum(r["status"] == "ok" for r in runs),
            "known_costs": {model_names[k]: float(v) for k, v in known_costs.items()
                            if k in model_names},
            "reserved": {model_names[k]: float(v) for k, v in reserved.items()
                         if k in model_names},
            "context_observed": contexts, "notes": notes or []}


def render(data, out: Path):
    """Compact project comparison, paginated when the selected matrix grows."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    out = external_path(out)
    write_private(out / "summary.json", data)
    pdf = out / "Benchmark-LLM.pdf"
    width, _ = A4
    usable = width - 68
    navy = colors.HexColor("#142B42")
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=8, leading=10.5,
                          textColor=navy, spaceAfter=6)
    heading = ParagraphStyle("heading", parent=body, fontName="Helvetica-Bold", fontSize=11,
                             leading=14, spaceBefore=9, spaceAfter=7)
    cell_style = ParagraphStyle("cell", parent=body, fontSize=7, leading=9, spaceAfter=0)
    story = []

    def paragraph(text, style=body):
        story.append(Paragraph(text, style))

    def pct(a, b):
        return f"{100 * a / b:.0f}%" if b else "—"

    paragraph("LLM Benchmark — results and methodology", heading)
    if data.get("synthetic"):
        paragraph("<b>SYNTHETIC OFFLINE RESULTS — not measured model performance.</b>")
    paragraph(f"<b>{data['models']} models · {data['planned_cases']} planned scenarios per model · "
              f"{data['evaluable']}/{data['planned_combinations']} evaluable observations</b><br/>"
              f"Cumulative known calculated cost: USD {sum(data['known_costs'].values()):.5f}")
    paragraph("RESULTS BY PROJECT", heading)
    cells = [["Project / model", "Cases", "Done", "Flow", "Tools", "Rules", "TTFT", "Latency", "USD"]]
    for r in data["rows"]:
        label = html.escape(r["project"] + " · " + r["model"])
        cells.append([Paragraph(label, cell_style),
                      f"{r['evaluable']}/{r['planned']}" + ("*" if r['infrastructure_errors'] else ""),
                      pct(r['complete'], r['evaluable']), pct(r['paths_passed'], r['paths_total']),
                      pct(r['tool_checks_passed'], r['tool_checks_total']),
                      pct(r['rules_passed'], r['rules_total']),
                      f"{r['ttft_s']:.2f} s" if r['ttft_s'] is not None else "—",
                      f"{r['latency_s']:.2f} s" if r['latency_s'] is not None else "—",
                      f"{r['cost_usd']:.3f}" if r['tested'] else "—"])
    table = Table(cells, colWidths=[141, 45, 45, 40, 45, 47, 48, 55, usable - 466],
                  repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F0F5F8"), colors.white]),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.extend([table, Spacer(1, 8)])
    paragraph("Done = completed conversation; Flow = ordered expected milestones, allowing extra steps. "
              "Tools = passed positive function-call expectations (name, schema-valid arguments and turn). "
              "Rules = passed explicit assertions. TTFT = first text or tool delta; Latency = full API response. "
              "Timing columns are per-call medians from complete conversations, excluding quota waits. "
              "USD = sum of known call costs in this selection, not a per-call price. "
              "* Provider/context rejections are excluded from quality percentages.")
    paragraph("METHODOLOGY AND LIMITATIONS", heading)
    paragraph("Users supply their prompts, node graph, tool contracts, simulated user turns and expected outcomes. "
              "Conversation history is retained; valid model-requested transitions change the active prompt. "
              "Independent segments are evaluated separately. Tool responses are mocked: backend performance "
              "is not measured. Native function calls and text-protocol emulation are recorded separately; "
              "announcing a call is insufficient. Rules are programmed assertions, not exhaustive semantic grading. "
              "Language signals use the configured allowed languages and require review. Prompt modes, "
              "reasoning settings, repetitions and retry policies are recorded in the experiment; compare matching conditions. "
              "Calculated costs use configured rates and available usage; unknown costs are not assumed free.")
    for note in data.get("notes", []):
        paragraph(html.escape(note))
    observed = " · ".join(f"{name}: {value:,}" for name, value in data['context_observed'].items())
    paragraph("<b>Largest accepted input (tokens):</b> " + html.escape(observed)
              + ". Acceptance does not establish quality throughout the advertised context window.")

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 7)
        canvas.drawString(34, 22, "As of: " + data.get("generated_at_utc", "")
                          + f" · Anonymized results · Page {doc.page}")

    doc = SimpleDocTemplate(str(pdf), pagesize=A4, leftMargin=34, rightMargin=34,
                            topMargin=26, bottomMargin=38, title="LLM benchmark — results and methodology",
                            author="LLM Benchmark")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    pdf.chmod(0o600)
    return pdf
