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
                "native_calls": sum(r.get("tool_calls_total", 0) for r in selected),
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
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph, Table, TableStyle

    out = external_path(out)
    write_private(out / "summary.json", data)
    pdf = out / "Benchmark-LLM.pdf"
    width, height = A4
    c = canvas.Canvas(str(pdf), pagesize=A4, pageCompression=1)
    c.setTitle("LLM benchmark — results and methodology")
    c.setAuthor("LLM Benchmark")
    c.setSubject("Anonymized model comparison using Spanish conversations")
    navy, teal, gray = colors.HexColor("#142B42"), colors.HexColor("#087F8C"), colors.HexColor("#536575")
    margin, usable = 34, width - 68

    def paragraph(text, x, top, w, size=9, color=navy, bold=False):
        style = ParagraphStyle("body", fontName="Helvetica-Bold" if bold else "Helvetica",
                               fontSize=size, leading=size * 1.35, textColor=color)
        p = Paragraph(text, style)
        _, h = p.wrap(w, height)
        p.drawOn(c, x, top - h)
        return top - h

    def pct(a, b):
        return f"{100 * a / b:.0f}%" if b else "—"

    c.setFillColor(navy)
    c.rect(0, height - 104, width, 104, fill=1, stroke=0)
    paragraph("CONVERSATIONAL EVALUATION · SPANISH", margin, height - 22, usable,
              size=8, color=colors.HexColor("#9ADDE0"), bold=True)
    paragraph("LLM Benchmark", margin, height - 41, usable, size=25,
              color=colors.white, bold=True)
    paragraph("Observed results and test methodology", margin, height - 77, usable,
              size=10, color=colors.HexColor("#D9E4EC"))
    y = height - 122
    cards = [(str(data["models"]), "models"), (str(data["planned_cases"]), "cases per model"),
             (f'{data["evaluable"]}/{data["planned_combinations"]}', "evaluable cases"),
             (f'USD {sum(data["known_costs"].values()):.2f}', "cumulative calculated cost")]
    cw = usable / 4
    for i, (value, label) in enumerate(cards):
        paragraph(value, margin + i * cw, y, cw - 8, size=18, color=teal, bold=True)
        paragraph(label, margin + i * cw, y - 25, cw - 8, size=7.5, color=gray)
    y -= 61
    y = paragraph("RESULTS BY PROJECT", margin, y, usable, size=10, bold=True) - 8
    headings = ["Project / model", "Cases", "Done", "Flow", "Tools (1)", "Rules (2)", "TTFT (3)", "Latency", "USD (4)"]
    cells = [headings]
    for r in data["rows"]:
        cells.append([r["project"].replace("Project ", "P") + " · " + r["model"],
                      f'{r["evaluable"]}/{r["planned"]}' + ("*" if r["infrastructure_errors"] else ""),
                      pct(r["complete"], r["evaluable"]),
                      pct(r["paths_passed"], r["paths_total"]),
                      pct(r["tool_checks_passed"], r["tool_checks_total"]),
                      pct(r["rules_passed"], r["rules_total"]),
                      f'{r["ttft_s"]:.2f} s' if r["ttft_s"] is not None else "—",
                      f'{r["latency_s"]:.2f} s' if r["latency_s"] is not None else "—",
                      f'{r["cost_usd"]:.3f}' if r["tested"] else "—"])
    table = Table(cells, colWidths=[141, 45, 45, 40, 45, 47, 48, 55, usable - 466], rowHeights=19)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F0F5F8"), colors.white]),
        ("TEXTCOLOR", (0, 1), (-1, -1), navy),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    tw, th = table.wrap(usable, height)
    table.drawOn(c, margin, y - th)
    y -= th + 8
    y = paragraph(
        "Done = complete conversation; Flow = expected milestones in order, allowing extra steps. "
        "* Provider/context rejections are excluded from quality percentages. "
        "(1) Expected function calls: name, arguments and turn; announcing a call is insufficient. "
        "(2) Explicit case checks; these do not cover every prompt rule. "
        "(3) TTFT: first text or tool delta; latency: full response. Per-call medians "
        "from complete conversations, excluding quota waits. (4) Known cost of the cases in this table.",
        margin, y, usable, size=7, color=gray) - 12
    y = paragraph("METHODOLOGY", margin, y, usable, size=10, bold=True) - 6
    y = paragraph(
        "<b>Inputs.</b> Full original prompts, without truncation; identical cases and test data "
        "across models. Simulated users follow deterministic scripts with branch-specific responses. "
        "<b>Flow.</b> History is retained and node transitions are followed; "
        "segment-based projects are evaluated separately. <b>Tools.</b> Native function calls "
        "use mock responses; backend execution is not measured. Assumed platform interfaces "
        "are marked unverified. <b>Measurement.</b> One run per case, without automatic "
        "retries; calls are serial within each provider, with batches overlapping across providers. "
        "API-reported usage, documented rates and a USD25 cap per model. "
        "Quota rejections are distinguished from LLM failures.",
        margin, y, usable, size=8) - 10
    y = paragraph("INTERPRETATION AND LIMITATIONS", margin, y, usable, size=10, bold=True) - 5
    for note in data["notes"][:3]:
        y = paragraph("• " + html.escape(note), margin, y, usable, size=7.8) - 3
    observed = " · ".join(f"{name}: {value:,}" for name, value in data["context_observed"].items())
    y = paragraph("<b>Largest accepted input (tokens):</b> " + html.escape(observed)
                  + ". Observed acceptance does not establish quality across the full advertised context window.",
                  margin, y - 4, usable, size=7.3, color=gray)
    if y < 36:
        raise ValueError("One-pager overflow; shorten notes before exporting")
    c.setStrokeColor(colors.HexColor("#D8E2E9"))
    c.line(margin, 29, width - margin, 29)
    paragraph("As of: " + html.escape(data.get("generated_at_utc", ""))
              + " · Anonymized results · Private conversations · 1 / 1",
              margin, 22, usable, size=7, color=gray)
    c.save()
    pdf.chmod(0o600)
    return pdf
