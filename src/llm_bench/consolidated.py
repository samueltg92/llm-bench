"""Sample-level model summaries with explicit units, denominators and coverage."""

import statistics
from collections import Counter

from .onepager import common_cohort

BLOCKED = {"error", "quota_capacity", "skipped_context", "context_failed"}


def distribution(values):
    values = [v for v in values if v is not None]
    return {"n": len(values), "mean": statistics.fmean(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None}


def consolidate(runs, calls, interpreted, language_reviews, model_names, planned):
    result = []
    common = common_cohort(runs, model_names)
    for cohort, population in [("Available cases", runs), ("Same cases across all 4", common)]:
        for project in [None, *sorted(planned)]:
            scoped = [r for r in population if project is None or r["project"] == project]
            planned_count = planned[project] if project else sum(planned.values())
            for key, name in model_names.items():
                selected = [r for r in scoped if r["model_key"] == key]
                ids = {r["run_id"] for r in selected}
                evaluable = [r for r in selected if r["status"] not in BLOCKED]
                complete_ids = {r["run_id"] for r in evaluable if r["status"] == "ok"}
                cs = [c for c in calls if c.get("run_id") in ids]
                timed = [c for c in cs if c.get("run_id") in complete_ids and c["status"] == "ok"]
                turns = [t for run_id in complete_ids for t in interpreted[run_id].get("turns", [])]
                accepted = [c for c in cs if c.get("usage_source") == "provider"
                            and c.get("status") in {"ok", "empty_response", "output_limit"}]
                assertions = [a for r in evaluable for a in r.get("assertions", [])]
                tool_checks = [a for a in assertions if a["id"].startswith(("expected_tool:", "unreached_tool:"))]
                rule_checks = [a for a in assertions if a not in tool_checks]
                decisions = Counter((r["run_id"], r["decision"]) for r in language_reviews if r["run_id"] in ids)
                base = {"project": "Project " + project.split("_")[-1] if project else "All projects",
                        "cohort": cohort, "model": name}

                def add(metric, unit, samples, values):
                    result.append({**base, "metric": metric, "unit": unit,
                                   "sample": samples, **distribution(values)})

                count = planned_count if cohort == "Available cases" else len(selected)
                add("Evaluable case coverage", "%", "planned case" if cohort == "Available cases" else "common case",
                    [100] * len(evaluable) + [0] * max(0, count - len(evaluable)))
                add("Complete conversations", "%", "evaluable conversation", [100 * (r["status"] == "ok") for r in evaluable])
                add("Ordered flow milestones", "%", "conversation with expected milestones",
                    [100 * r["expected_path_match"] for r in evaluable if r.get("expected_path_match") is not None])
                add("Expected tools", "%", "positive tool expectation", [100 * a["pass"] for a in tool_checks])
                add("Explicit case rules", "%", "explicit assertion", [100 * a["pass"] for a in rule_checks])
                for label, field in [("TTFT", "ttft_ms"), ("Full latency", "total_latency_ms"),
                                     ("First native tool delta", "first_tool_ms")]:
                    add(label, "s", "call in a complete conversation", [c[field] / 1000 for c in timed if c.get(field) is not None])
                for label, field in [("First user-facing text", "first_text_ms"), ("Flow latency", "total_ms")]:
                    add(label, "s", "turn in a complete conversation", [t[field] / 1000 for t in turns if t.get(field) is not None])
                for label, field in [("Native function calls", "tool_calls_total"), ("Invalid function calls", "invalid_tool_calls")]:
                    add(label, "calls", "evaluable conversation", [r.get(field, 0) for r in evaluable])
                for label, decision in [("Confirmed language flags", "confirmed_foreign"), ("Pending language flags", "pending")]:
                    add(label, "flags", "evaluable conversation", [decisions[r["run_id"], decision] for r in evaluable])
                add("Accepted input size", "tokens", "provider-measured accepted call", [c.get("prompt_tokens") for c in accepted])
                add("Reasoning tokens", "tokens", "provider-measured accepted call", [c.get("reasoning_tokens") for c in accepted])
                add("Known cost per API call", "USD", "call with known cost, including incomplete cases", [c.get("cost_usd") for c in cs])
                conversation_costs = []
                for run_id in ids:
                    costs = [c.get("cost_usd") for c in cs if c.get("run_id") == run_id]
                    if costs and all(v is not None for v in costs):
                        conversation_costs.append(sum(costs))
                add("Known cost per conversation", "USD", "conversation with calls and fully known cost", conversation_costs)
                add("Quota wait", "s", "recorded API call; excluded from latency", [c["rate_limit_wait_ms"] / 1000 for c in cs if c.get("rate_limit_wait_ms") is not None])
                add("Infrastructure block rate", "%", "recorded case, including quota blocks", [100 * (r["status"] in BLOCKED) for r in selected])
    return result


def render_consolidated(data, out):
    """Four compact model pages supplement the one-page project comparison."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph, Table, TableStyle

    path = out / "Model-consolidated.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("LLM consolidated metrics — mean, minimum and maximum")
    c.setAuthor("LLM Benchmark")
    width, height = A4
    navy = colors.HexColor("#142B42")
    style = ParagraphStyle("body", fontName="Helvetica", fontSize=8, leading=11, textColor=navy)

    def paragraph(text, y):
        p = Paragraph(text, style)
        _, h = p.wrap(width - 68, height)
        p.drawOn(c, 34, y - h)
        return y - h - 10

    models = list(data["known_costs"])
    for page, model in enumerate(models, 1):
        c.setFillColor(navy)
        c.rect(0, height - 100, width, 100, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(34, height - 40, model)
        c.setFont("Helvetica", 10)
        c.drawString(34, height - 64, "Consolidated metrics | All projects | Available cases")
        c.setFont("Helvetica", 9)
        c.drawString(34, height - 84, data.get("model_profiles", {}).get(model, "Profile recorded in source manifest"))
        rows = [r for r in data["consolidated"] if r["model"] == model and r["project"] == "All projects" and r["cohort"] == "Available cases"]
        cells = [["Metric", "Unit", "Mean", "Min", "Max", "N"]]
        for r in rows:
            digits = 5 if r["unit"] == "USD" else 2
            values = [f"{r[k]:,.{digits}f}" if r[k] is not None else "—" for k in ["mean", "min", "max"]]
            cells.append([r["metric"], r["unit"], *values, str(r["n"])])
        table = Table(cells, colWidths=[184, 35, 81, 73, 81, width - 522], rowHeights=20)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F0F5F8"), colors.white]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        _, th = table.wrap(width - 68, height)
        table.drawOn(c, 34, height - 120 - th)
        y = height - 132 - th
        y = paragraph("<b>How to read.</b> Arithmetic means use individual samples, not averages of project medians. N is the sample count. Missing observations stay blank. Percentage checks use 100 for pass and 0 for fail; their minimum and maximum are therefore binary.", y)
        y = paragraph("<b>Populations.</b> Timing uses calls or turns from complete conversations, excluding quota waits. Quality includes evaluable failures. Input, reasoning and cost measurements also include recorded portions of incomplete conversations. Backend tool responses are simulated. Detailed sample units and matched-case summaries are in Model-consolidated.csv and the private viewer.", y)
        y = paragraph(f"<b>Cumulative calculated cost, including earlier attempts and diagnostics:</b> USD {data['known_costs'][model]:.5f}. This total differs from per-call and per-conversation averages. Unknown usage is not assumed free; these calculations are not provider invoices.", y)
        if y < 42:
            raise ValueError("Consolidated page overflow")
        c.setFont("Helvetica", 7)
        c.setFillColor(navy)
        c.drawString(34, 22, f"As of: {data['generated_at_utc']} | Anonymized results | {page}/{len(models)}")
        c.showPage()
    c.save()
    path.chmod(0o600)
    return path
