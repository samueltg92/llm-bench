import csv
import io
import json
from collections import defaultdict
from pathlib import Path

from .metrics import describe, total_known
from .privacy import external_path, write_private


def read_lines(path):
    return (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if path.exists()
        else []
    )


def cohort(run):
    # Do not pool different prompts, scenarios, segments, provider modes or failed conditions.
    return (
        run["project"],
        run["scenario"],
        run["model_key"],
        run["prompt_mode"],
        tuple(run["effective_modes"]),
        run["dedup"],
        run["segment"],
        run["degraded"],
        run["concurrent"],
        run["synthetic"],
        run["scenario_sha256"],
        run["source_sha256"],
        run["retries"] > 0,
    )


def aggregate(runs, calls):
    groups = defaultdict(list)
    for run in runs:
        groups[cohort(run)].append(run)
    rows = []
    for key, members in groups.items():
        ids = {r["run_id"] for r in members if r["status"] == "ok"}
        measured = [
            c
            for c in calls
            if c.get("run_id") in ids and c["status"] == "ok" and not c.get("warmup")
        ]
        tool_modes = sorted({c.get("tool_mode", "native") for c in measured})
        row = {
            "project": key[0],
            "scenario": key[1],
            "model": key[2],
            "mode": key[3],
            "effective_modes": ",".join(key[4]),
            "dedup": key[5],
            "segment": key[6],
            "degraded": key[7],
            "concurrent": key[8],
            "synthetic": key[9],
            "retried": key[12],
            "tool_modes": ",".join(tool_modes),
            "repetitions": len(members),
            "measured_calls": len(measured),
            "error_rate": sum(r["status"] != "ok" for r in members) / len(members),
            "cost_status": ",".join(
                sorted({c.get("cost_status", "unknown_usage") for c in measured})
            ),
            "usage_source": ",".join(sorted({c.get("usage_source", "unknown") for c in measured})),
            "invalid_tool_calls": sum(r["invalid_tool_calls"] for r in members),
            "language_violations": sum(r["language_violations"] for r in members),
            "language_unknown": sum(r["language_unknown"] for r in members),
            "rules_passed": sum(r["assertions_passed"] for r in members),
            "rules_total": sum(r["assertions_total"] for r in members),
            "tool_expectations_passed": sum(r.get("tool_expectations_passed", 0) for r in members),
            "tool_expectations_total": sum(r.get("tool_expectations_total", 0) for r in members),
            "successful_routes": sum(r["expected_path_match"] is True for r in members),
            "routes_evaluated": sum(r["expected_path_match"] is not None for r in members),
            "turns_completed": sum(r["turns_completed"] for r in members),
        }
        for metric in (
            "ttft_ms",
            "first_text_ms",
            "first_tool_ms",
            "total_latency_ms",
            "output_tps",
        ):
            row.update(
                {
                    f"{metric}_{stat}": value
                    for stat, value in describe(c.get(metric) for c in measured).items()
                }
            )
        costs = [r["conversation_cost_usd"] for r in members]
        total = total_known(costs)
        row["cost_per_conversation_usd"] = total / len(costs) if total is not None else None
        row["cost_per_1k_conversations_usd"] = (
            row["cost_per_conversation_usd"] * 1000 if total is not None else None
        )
        row["prompt_tokens"] = total_known(c.get("prompt_tokens") for c in measured)
        row["completion_tokens"] = total_known(c.get("completion_tokens") for c in measured)
        row["ranking_eligible"] = (
            bool(measured)
            and not any((key[7], key[8], key[9], key[12]))
            and key[3] in ("active_node", "single_node")
            and tool_modes == ["native"]
            and len(key[4]) == 1
        )
        rows.append(row)
    return sorted(
        rows, key=lambda r: r["ttft_ms_p50"] if r["ttft_ms_p50"] is not None else float("inf")
    )


def cell(value):
    if value is None:
        return "n/d"
    return (
        f"{value:.4f}"
        if isinstance(value, float)
        else str(value).replace("|", " ").replace("\n", " ")
    )


def table(rows, fields):
    lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    return "\n".join(
        lines + ["| " + " | ".join(cell(row.get(k)) for k in fields) + " |" for row in rows]
    )


def report(run_dir: Path):
    run_dir = external_path(run_dir)
    runs = read_lines(run_dir / "runs.jsonl")
    calls = read_lines(run_dir / "calls.jsonl")
    rows = aggregate(runs, calls)
    if rows:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        write_private(run_dir / "summary.csv", buffer.getvalue(), plain=True)
    principal = [r for r in rows if r["ranking_eligible"]]
    fields = [
        "project",
        "scenario",
        "model",
        "mode",
        "ttft_ms_p50",
        "ttft_ms_p95",
        "first_text_ms_p50",
        "first_tool_ms_p50",
        "total_latency_ms_p50",
        "output_tps_mean",
        "prompt_tokens",
        "completion_tokens",
        "cost_per_conversation_usd",
        "cost_per_1k_conversations_usd",
        "error_rate",
    ]
    sections = [
        "# LLM benchmark — informe privado",
        "Los prompts y transcripciones permanecen privados. No publicar este directorio.",
        "## Comparación primaria",
        table(principal, fields),
        "## Estrés, contexto elevado y condiciones distintas",
        table([r for r in rows if not r["ranking_eligible"]], fields),
        "## Costos",
        table(
            rows,
            [
                "project",
                "scenario",
                "model",
                "mode",
                "cost_status",
                "cost_per_conversation_usd",
                "cost_per_1k_conversations_usd",
            ],
        ),
        "## Efectividad funcional",
        table(
            rows,
            [
                "project",
                "scenario",
                "model",
                "successful_routes",
                "routes_evaluated",
                "invalid_tool_calls",
                "tool_expectations_passed",
                "tool_expectations_total",
                "rules_passed",
                "rules_total",
                "language_violations",
                "language_unknown",
                "turns_completed",
            ],
        ),
        "## No ejecutado o incompleto",
        table(
            [
                {
                    "project": r["project"],
                    "scenario": r["scenario"],
                    "model": r["model_key"],
                    "status": r["status"],
                }
                for r in runs
                if r["status"] != "ok"
            ],
            ["project", "scenario", "model", "status"],
        ),
        "## Advertencias de validez",
        "- El ranking contiene conversaciones completas; revisar también errores y cumplimiento.\n"
        "- Solo se comparan escenarios, modos y configuraciones equivalentes. Las trayectorias generadas pueden divergir.\n"
        "- TTFT incluye el primer delta de herramienta; first_text mide texto y first_tool mide herramienta.\n"
        "- Tres repeticiones producen un p95 exploratorio, no una estimación robusta de cola.\n"
        "- Contexto elevado (>85%) es una etiqueta preventiva, no evidencia de degradación medida.\n"
        "- Herramientas simuladas: su duración no representa la latencia del servicio real.\n"
        "- Reintentos excluidos del ranking; gasto total desconocido si faltan cargos de intentos fallidos.\n"
        "- Tokens estimados y precios sin verificar se identifican explícitamente.\n"
        "- La detección de idioma es heurística; texto corto, nombres propios y respuestas mixtas requieren revisión.\n"
        "- El cumplimiento solo cubre las reglas explícitas del escenario; no evalúa todas las instrucciones automáticamente.",
        table(
            rows,
            [
                "project",
                "model",
                "degraded",
                "concurrent",
                "retried",
                "synthetic",
                "tool_modes",
                "usage_source",
                "cost_status",
            ],
        ),
        "## TTFT p50 (ms)",
    ]
    maximum = max((r["ttft_ms_p50"] for r in principal), default=1) or 1
    sections.append(
        "```\n"
        + "\n".join(
            f"{r['project']} / {r['model']} {'#' * max(1, round(r['ttft_ms_p50'] / maximum * 30))} {r['ttft_ms_p50']:.1f}"
            for r in principal
        )
        + "\n```"
    )
    warmups = [c for c in calls if c.get("warmup")]
    sections.append(
        f"Calentamientos registrados y excluidos: {len(warmups)}. Costo conocido: {cell(total_known(c.get('cost_usd') for c in warmups))} USD."
    )
    write_private(run_dir / "REPORT.md", "\n\n".join(sections), plain=True)
    return rows


def compare(rows, baseline):
    keys = [
        "project",
        "scenario",
        "mode",
        "effective_modes",
        "dedup",
        "segment",
        "degraded",
        "concurrent",
        "synthetic",
        "retried",
        "tool_modes",
    ]
    lookup = {tuple(r[k] for k in keys): r for r in rows if r["model"] == baseline}
    compared = []
    for row in rows:
        base = lookup.get(tuple(row[k] for k in keys))
        if not base or row["model"] == baseline:
            continue
        item = {
            "project": row["project"],
            "scenario": row["scenario"],
            "model": row["model"],
            "mode": row["mode"],
        }
        for metric in ("ttft_ms_p50", "first_text_ms_p50", "cost_per_conversation_usd"):
            b, v = base[metric], row[metric]
            item[metric + "_delta_pct"] = (v / b - 1) * 100 if b and v is not None else None
        compared.append(item)
    return compared
