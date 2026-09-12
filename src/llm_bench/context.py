"""Private context compatibility audit. No provider calls or prompt truncation."""

from pathlib import Path

from .experiment import plan
from .privacy import external_path, write_private
from .report import read_lines, table


def audit(pairs, models, prices, bench, out: Path, results_dir: Path | None = None):
    out = external_path(out)
    rows = plan(pairs, models, prices, bench, ["active_node", "full"])
    observed = {}
    if results_dir:
        results_dir = external_path(results_dir)
        for path in results_dir.rglob("calls.jsonl"):
            for call in read_lines(path):
                # A finished stream with provider usage is evidence of processing
                # this input length, even if visible output was empty/truncated.
                if (
                    call.get("usage_source") == "provider"
                    and call.get("finish_reason")
                    and call.get("prompt_tokens") is not None
                ):
                    key = call["model_key"]
                    observed[key] = max(observed.get(key, 0), call["prompt_tokens"])
    compatibility = []
    for row in rows:
        model = models[row["model"]]
        compatibility.append(
            {
                "project": row["project"],
                "scenario": row["scenario"],
                "model": row["model"],
                "mode": row["prompt_mode"],
                "documented_context_tokens": model.context_window,
                "source": model.source_url,
                "verified_on": model.last_verified,
                "largest_prompt_tokens_est": row["largest_prompt_tokens_est"],
                "output_budget_tokens": row["reserved_output_tokens"],
                "headroom_before_history_tokens_est": row["largest_prompt_headroom_tokens_est"],
                "preflight_status": row["largest_prompt_context_status"],
                "largest_provider_reported_input_seen": observed.get(row["model"]),
                "full_window_empirically_verified": False,
            }
        )
    fields = [
        "project",
        "model",
        "mode",
        "documented_context_tokens",
        "largest_prompt_tokens_est",
        "output_budget_tokens",
        "headroom_before_history_tokens_est",
        "preflight_status",
    ]
    # Collapse identical numeric comparisons while retaining every scenario in JSON.
    unique = {tuple(row[k] for k in fields): row for row in compatibility}
    write_private(out / "context-audit.json", {"network_calls": 0, "rows": compatibility})
    write_private(
        out / "CONTEXT.md",
        "\n\n".join(
            [
                "# Compatibilidad de contexto — privado",
                "Esta auditoría no hace inferencias ni modifica prompts. active_node conserva historial y recorre nodos; full es una prueba de estrés con todos los prompts juntos. Los segmentos independientes conservan un solo nodo.",
                table(list(unique.values()), fields),
                "## Evidencia observada",
                table(
                    [
                        {
                            "model": k,
                            "largest_reported_input_tokens": observed.get(k),
                            "full_window_verified": False,
                        }
                        for k in models
                    ],
                    ["model", "largest_reported_input_tokens", "full_window_verified"],
                ),
                "## Interpretación",
                "- El máximo de prompt incluye instrucciones globales, estado inicial, herramientas y formato del transporte. El margen descuenta también la salida reservada.\n"
                "- El historial futuro todavía no existe: se vuelve a comprobar cada petición después de añadir respuestas y resultados de tools.\n"
                "- o200k_base es una estimación entre modelos, no el tokenizer exacto de todos los proveedores. La cuota de la cuenta puede ser menor que la capacidad publicada.\n"
                "- Una petición aceptada demuestra únicamente esa longitud observada; no demuestra la ventana completa ni descarta truncamiento interno del proveedor.\n"
                "- La calidad se evalúa aparte con rutas, reglas, uso de tools e idioma, incluida información de turnos anteriores. No deducirla de la aceptación HTTP.\n"
                "- No recortar ni resumir prompts para hacerlos caber y presentar el resultado como equivalente. Mantener overflow en skip/fail; un subset explícito es otra condición experimental.",
            ]
        ),
        plain=True,
    )
    return compatibility
