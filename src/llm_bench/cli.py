import json
import os
import re
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from . import config
from .budget import amount, call_bound, reconcile_usage, settle_completed
from .budget import reserve as reserve_budget
from .experiment import execute, inputs, plan
from .extract import extract as parse_export
from .extract import save_bundle
from .privacy import external_path, write_private
from .report import compare as comparisons
from .report import report as make_report

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Benchmark conversacional de LLM con datos privados fuera de Git.",
)


def csv_list(value):
    return [v.strip() for v in value.split(",") if v.strip()]


def emit(value):
    typer.echo(json.dumps(value, ensure_ascii=False, default=str, indent=2))


def diagnostic_error(exc):
    """Expose only numeric diagnostics; exception bodies can contain private data."""
    result = {}
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        result["http_status"] = status
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        details = body.get("error", body)
        if isinstance(details, dict) and re.fullmatch(r"[0-9]{1,6}", str(details.get("code", ""))):
            result["provider_error_code"] = str(details["code"])
    headers = getattr(getattr(exc, "response", None), "headers", {})
    allowed = (
        "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining",
        "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
        "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens",
    )
    numeric_headers = {
        name: str(headers[name]) for name in allowed
        if name in headers and re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,6})?", str(headers[name]))
    }
    if numeric_headers:
        result["rate_limit_headers"] = numeric_headers
    return result


@app.command()
def extract(
    input: Annotated[Path, typer.Option(exists=True)],
    project: Annotated[str, typer.Option(help="Alias genérico: project_1")],
    out: Annotated[Path, typer.Option()],
    composition: str | None = None,
):
    """Importa un JSON privado; nunca escribe datos en un working tree Git."""
    if composition not in (None, "all_nodes", "single_node"):
        raise typer.BadParameter("composition debe ser all_nodes o single_node")
    external_path(out)
    bundle = parse_export(input, project, composition)
    save_bundle(bundle, out)
    index_path = out / "_index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    index[project] = {
        "project": project,
        "composition": bundle.composition,
        **bundle.stats,
        "warnings_count": len(bundle.warnings),
    }
    write_private(index_path, index)
    emit(index[project])


@app.command()
def import_bundle(
    input: Annotated[Path, typer.Option(exists=True)],
    out: Annotated[Path, typer.Option()],
):
    """Import a neutral JSON bundle; no platform-specific export is required."""
    import hashlib

    from .bundle import Bundle
    from .extract import save_bundle

    external_path(out)
    raw = input.read_bytes()
    data = json.loads(raw)
    data["source_sha256"] = hashlib.sha256(raw).hexdigest()
    data.setdefault("display_name", "Project " + data["project"].split("_")[-1])
    data.setdefault("orchestration_language", "en")
    data.setdefault("allowed_languages", ["en"])
    bundle = Bundle.model_validate(data)
    save_bundle(bundle, out)
    emit({"project": bundle.project, "nodes": len(bundle.nodes), "tools": len(bundle.tools),
          "source_sha256": bundle.source_sha256})


@app.command()
def budget_init(
    budget_file: Annotated[Path, typer.Option()],
    total: Annotated[str, typer.Option(help="Total USD limit shared by all selected models")],
    model_limit: Annotated[list[str], typer.Option("--model-limit", help="Repeat KEY=USD for each model")],
    per_operation: Annotated[str | None, typer.Option(help="Maximum reservation per operation; defaults to total")] = None,
):
    """Create an external budget with global/model limits; refuses to overwrite."""
    from .budget import initialize, status

    limits = {}
    for item in model_limit:
        key, separator, value = item.partition("=")
        key = key.strip()
        if not separator or not key or key in limits:
            raise typer.BadParameter("Use unique model keys as --model-limit KEY=USD")
        limits[key] = value.strip()
    try:
        initialize(budget_file, total, limits, per_operation)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(status(budget_file))


@app.command()
def budget_status(budget_file: Annotated[Path, typer.Option(exists=True)]):
    """Show total/per-model limits, reservations and remaining local capacity."""
    from .budget import status

    emit(status(budget_file))


@app.command()
def doctor(
    config_dir: Path = Path("config"),
    env_file: Path | None = None,
    online: bool = False,
    max_output_tokens: Annotated[int, typer.Option(min=1)] = 512,
    budget_file: Path | None = None,
    out: Path | None = None,
):
    """Valida configuración; --online hace una inferencia breve con texto sintético."""
    if env_file:
        load_dotenv(external_path(env_file), override=False)
    models = config.models(config_dir / "models.yaml")
    prices = config.yaml_data(config_dir / "pricing.yaml")["models"]
    if online:
        if budget_file is None and env_file is None:
            raise typer.BadParameter("Online checks require --budget-file or --env-file")
        allocations = {
            k: call_bound(m, prices.get(k), max_output_tokens)
            for k, m in models.items()
            if m.enabled and os.environ.get(m.api_key_env)
        }
        if allocations:
            reserve_budget(
                budget_file or env_file.parent / "budget.json",
                sum(allocations.values()),
                "doctor",
                allocations=allocations,
            )
    rows = []
    for key, model in models.items():
        price = prices.get(key, {})
        row = {
            "model": key,
            "enabled": model.enabled,
            "credential_present": bool(os.environ.get(model.api_key_env)),
            "price_verified": price.get("last_verified") not in (None, "PENDING", "PENDIENTE"),
            "configuration_verified": model.last_verified not in ("PENDING", "PENDIENTE"),
            "online_status": "not_requested",
        }
        if online and model.enabled and row["credential_present"]:
            from .providers.registry import create

            provider = create(model)
            try:
                events = list(
                    provider.stream_chat(
                        messages=[{"role": "user", "content": "Di hola."}],
                        tools=[],
                        temperature=0.2,
                        max_output_tokens=max_output_tokens,
                    )
                )
                finish = next((e.finish_reason for e in reversed(events) if e.kind == "done"), None)
                row["finish_reason"] = finish
                if finish in {"length", "MAX_TOKENS", "FinishReason.MAX_TOKENS"}:
                    row["online_status"] = "output_limit"
                else:
                    row["online_status"] = (
                        "ok" if any(e.kind == "text" and e.text for e in events) else "empty"
                    )
            except Exception as exc:
                row["online_status"] = type(exc).__name__
                row.update(diagnostic_error(exc))
            finally:
                provider.close()
        rows.append(row)
    if out:
        write_private(out, rows)
    emit(rows)


@app.command()
def run(
    data_dir: Annotated[Path, typer.Option(exists=True)],
    config_dir: Path = Path("config"),
    env_file: Path | None = None,
    projects: str = "",
    models: str = "",
    prompt_mode: str = "active_node",
    repetitions: int | None = None,
    concurrency: int | None = None,
    all_segments: bool = False,
    dedup: bool = False,
    warmup: bool = True,
    on_context_overflow: str = "skip",
    dry_run: bool = False,
    out: Path | None = None,
    yes: bool = False,
    offline_demo: bool = False,
    budget_file: Path | None = None,
):
    """Simula conversaciones completas; active_node recorre el grafo conservando historial."""
    if env_file:
        load_dotenv(external_path(env_file), override=False)
    modes = csv_list(prompt_mode)
    if not modes or set(modes) - {"active_node", "full", "subset"}:
        raise typer.BadParameter("Modos: active_node,full,subset")
    if on_context_overflow not in ("skip", "subset", "fail"):
        raise typer.BadParameter("Overflow: skip,subset,fail")
    bench = config.yaml_data(config_dir / "bench.yaml")
    for key, value in (("repetitions", repetitions), ("concurrency", concurrency)):
        if value is not None:
            if value < 1:
                raise typer.BadParameter(f"{key} debe ser positivo")
            bench[key] = value
    bench.update(warmup=warmup, on_context_overflow=on_context_overflow)
    catalog = config.models(config_dir / "models.yaml")
    requested = csv_list(models)
    if set(requested) - set(catalog):
        raise typer.BadParameter("Modelo desconocido")
    selected = {k: m for k, m in catalog.items() if k in requested or (not requested and m.enabled)}
    prices = config.yaml_data(config_dir / "pricing.yaml")["models"]
    if offline_demo:
        selected = {
            "offline-fake": config.Model(
                provider="fake", model_id="offline-fake", last_verified="synthetic"
            )
        }
        prices["offline-fake"] = {"input": 0, "output": 0, "last_verified": "synthetic"}
    pairs = inputs(data_dir, csv_list(projects), all_segments)
    preview = plan(pairs, selected, prices, bench, modes, dedup)
    reserve = None
    allocations = {}
    if all(r["budget_reserve_usd"] is not None for r in preview):
        for row in preview:
            key = row["model"]
            allocations[key] = allocations.get(key, amount(0)) + amount(row["budget_reserve_usd"])
        if warmup and not offline_demo:
            for k, m in selected.items():
                allocations[k] = allocations.get(k, amount(0)) + (
                    call_bound(m, prices.get(k), 512) * bench["retries"]["max_attempts"]
                )
        reserve = sum(allocations.values())
    emit(
        {
            "dry_run": True,
            "network_inference_calls": 0,
            "combinations": preview,
            "total_budget_reserve_usd": reserve,
            "budget_reserve_by_model_usd": allocations,
            "includes_warmup_and_retries": True,
            "note": "Estimaciones con trayectoria e historial desconocidos; reserva conservadora con reintentos.",
        }
    )
    if dry_run:
        return
    if not selected:
        raise typer.BadParameter("Ningún modelo habilitado")
    if any(not m.enabled for m in selected.values()):
        raise typer.BadParameter(
            "Hay modelos deshabilitados. Revisa catálogo y condiciones antes de habilitarlos."
        )
    missing = [
        m.api_key_env
        for m in selected.values()
        if m.provider != "fake" and not os.environ.get(m.api_key_env)
    ]
    if missing:
        raise typer.BadParameter("Faltan variables: " + ", ".join(missing))
    if any(r["budget_reserve_usd"] is None for r in preview):
        raise typer.BadParameter(
            "Hay precios desconocidos: completa pricing.yaml antes de inferencia"
        )
    if reserve > bench["cost_guard_usd"] and not yes:
        typer.confirm(f"Reserva estimada conservadora: USD {reserve:.2f}. ¿Ejecutar?", abort=True)
    reservation = None
    ledger_path = budget_file or (env_file.parent if env_file else data_dir) / "budget.json"
    if not offline_demo:
        reservation = reserve_budget(
            ledger_path,
            reserve,
            "benchmark",
            allocations=allocations,
        )
    directory = execute(
        pairs, selected, prices, bench, modes, out or data_dir / "results", dedup, preview,
        reservation=reservation,
    )
    if reservation:
        settle_completed(ledger_path, directory)
        try:
            reconcile_usage(ledger_path, directory)
        except (ValueError, KeyError, OSError):
            emit({"usage_reconciliation": "incomplete_evidence_full_bounds_retained"})
    rows = make_report(directory)
    emit({"run_dir": str(directory), "report_rows": len(rows), "synthetic": offline_demo})


@app.command()
def audit_context(
    data_dir: Annotated[Path, typer.Option(exists=True)],
    out: Annotated[Path, typer.Option()],
    config_dir: Path = Path("config"),
    results_dir: Path | None = None,
    projects: str = "",
    models: str = "",
):
    """Audita contexto publicado, tamaño estimado y evidencia previa; sin inferencias."""
    from .context import audit

    catalog = config.models(config_dir / "models.yaml")
    requested = csv_list(models)
    if set(requested) - set(catalog):
        raise typer.BadParameter("Modelo desconocido")
    selected = {k: m for k, m in catalog.items() if k in requested or (not requested and m.enabled)}
    pairs = inputs(data_dir, csv_list(projects), all_segments=True)
    rows = audit(
        pairs,
        selected,
        config.yaml_data(config_dir / "pricing.yaml")["models"],
        config.yaml_data(config_dir / "bench.yaml"),
        out,
        results_dir,
    )
    emit({"network_calls": 0, "comparisons": len(rows), "report": str(out / "CONTEXT.md")})


@app.command()
def report(
    run_dir: Annotated[Path, typer.Option(exists=True)], format: str = "all", recursive: bool = False
):
    """Regenera CSV e informe Markdown privados."""
    if format not in ("all", "table", "csv", "md"):
        raise typer.BadParameter("Formato: all,table,csv,md")
    rows = make_report(run_dir, recursive=recursive)
    if format in ("all", "table"):
        emit(rows)


@app.command()
def compare(
    run_dir: Annotated[Path, typer.Option(exists=True)], baseline: str, recursive: bool = False
):
    """Deltas contra un baseline, solo para condiciones equivalentes."""
    rows = comparisons(make_report(run_dir, recursive=recursive), baseline)
    write_private(run_dir / "comparison.json", rows)
    emit(rows)
