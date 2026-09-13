"""Render a concise, externally sourced provider comparison without API calls."""

import argparse
import csv
import json
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from llm_bench.privacy import external_path, write_private


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(external_path(args.input).read_text())
    out = external_path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    width, _ = landscape(A4)
    usable = width - 64
    navy, teal = colors.HexColor("#16324B"), colors.HexColor("#087E8B")
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=8.6, leading=11,
                          textColor=navy, spaceAfter=6)
    small = ParagraphStyle("small", parent=body, fontSize=7.7, leading=9.6, spaceAfter=0)
    title = ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=21,
                           leading=25, spaceAfter=8)
    heading = ParagraphStyle("heading", parent=body, fontName="Helvetica-Bold", fontSize=11,
                             leading=14, spaceBefore=7, spaceAfter=5)

    def para(value, style=body):
        return Paragraph(escape(str(value)), style)

    def link(label, url, style=small):
        if not url.startswith("https://"):
            raise ValueError("Source links must use HTTPS")
        return Paragraph(f'<link href="{escape(url, quote=True)}" color="#087E8B">'
                         f'{escape(label)}</link>', style)

    def number(value, latency=False):
        return "—" if value is None else (f"{value:.2f}" if latency else f"{value:.4g}")

    def table_style(header=True):
        rules = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("LEFTPADDING", (0, 0), (-1, -1), 6),
                 ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                 ("TOPPADDING", (0, 0), (-1, -1), 2),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                 ("LINEBELOW", (0, 0), (-1, -1), .25, colors.HexColor("#DBE4EA"))]
        if header:
            rules += [("BACKGROUND", (0, 0), (-1, 0), navy),
                      ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                      ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                      ("FONTSIZE", (0, 0), (-1, -1), 8)]
        return rules

    story = [para(data["title"], title), para(data["subtitle"]), Spacer(1, 4)]
    rows = [["Provider / endpoint", "Input $", "Output $", "Cache $", "P50 s", "P95 s", "Assessment"]]
    group_rows = []
    exported = []
    for model in data["models"]:
        group_rows.append(len(rows))
        label = f"{model['name']}  |  {model['profile']}  |  {model['note']}"
        group_label = link(label, model["url"]) if model.get("url") else para(label, small)
        rows.append([group_label, "", "", "", "", "", ""])
        if len(model["providers"]) > 3:
            raise ValueError("Brief supports at most three shortlisted endpoints per model")
        for p in model["providers"]:
            rows.append([link(p["name"], p["url"]), number(p.get("input")),
                         number(p.get("output")), number(p.get("cache")),
                         number(p.get("p50"), True), number(p.get("p95"), True),
                         para(p["assessment"], small)])
            exported.append({"model": model["name"], "profile": model["profile"], **p})
        if not model["providers"]:
            rows.append([para("No qualifying endpoint verified", small), "—", "—", "—", "—", "—", ""])
    table = Table(rows, colWidths=[143, 48, 48, 48, 43, 43, usable - 373])
    rules = table_style()
    for i in group_rows:
        rules += [("SPAN", (0, i), (-1, i)),
                  ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#E9F2F5"))]
    table.setStyle(TableStyle(rules))
    story += [table, Spacer(1, 7), para(data["table_note"], small), PageBreak(),
              para("Routing, reasoning and interpretation", title)]
    for section in data["sections"]:
        story.append(para(section["heading"], heading))
        for text in section.get("paragraphs", []):
            story.append(para(text))
        if section.get("rows"):
            cells = [[para(v, small) for v in row] for row in section["rows"]]
            subtable = Table(cells, colWidths=[130, usable - 130])
            subtable.setStyle(TableStyle(table_style(header=False)))
            story.append(subtable)
    story.append(para("Sources and reproducibility", heading))
    for source in data["sources"]:
        story.append(link(source["label"], source["url"], small))
    story.append(Spacer(1, 4))
    story.append(para(data["closing_note"], small))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(teal)
        canvas.line(32, 27, width - 32, 27)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(navy)
        canvas.drawString(32, 16, data["as_of"] + " | Public-source research | USD per 1M tokens")
        canvas.drawRightString(width - 32, 16, str(doc.page))
        canvas.restoreState()

    pdf = out / "OpenRouter-provider-brief.pdf"
    doc = SimpleDocTemplate(str(pdf), pagesize=landscape(A4), leftMargin=32, rightMargin=32,
                            topMargin=27, bottomMargin=36, title=data["title"],
                            author="LLM Benchmark", pageCompression=1)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    pdf.chmod(0o600)
    csv_path = out / "OpenRouter-provider-details.csv"
    shortlist_count = len(exported)
    exported.extend(data.get("additional_csv_rows", []))
    fields = list(dict.fromkeys(key for row in exported for key in row))
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(exported)
    csv_path.chmod(0o600)
    write_private(out / "source-data.json", data)
    print(json.dumps({"pdf": pdf.name, "shortlisted_endpoints": shortlist_count,
                      "detail_rows": len(exported)}))


if __name__ == "__main__":
    main()
