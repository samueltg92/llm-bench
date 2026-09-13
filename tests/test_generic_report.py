import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from llm_bench.config import Model
from llm_bench.experiment import execute, inputs, plan


@pytest.mark.parametrize("model_count,project_count", [(1, 2), (5, 8)])
def test_report_supports_new_models_and_multiple_projects(tmp_path, bench, model_count, project_count):
    pytest.importorskip("reportlab")
    PdfReader = pytest.importorskip("pypdf").PdfReader
    data = tmp_path / "data"
    (data / "bundles").mkdir(parents=True)
    (data / "scenarios").mkdir()
    for number in range(11, 11 + project_count):
        project = f"project_{number}"
        bundle = {"project": project, "display_name": f"Project {number}",
                  "composition": "single_node", "start_node": "sample", "source_sha256": project,
                  "allowed_languages": [], "orchestration_language": "en", "tools": [],
                  "nodes": [{"id": "sample", "name": "Sample", "prompt": "A fictional test."}]}
        (data / "bundles" / f"{project}.json").write_text(json.dumps(bundle))
        # Intentionally reuse the ID across projects.
        scenario = {"id": "example", "project": project, "segment": "sample",
                    "turns": [{"content": "Give a fictional test response."}]}
        (data / "scenarios" / f"{project}.yaml").write_text(yaml.safe_dump(scenario))
    models = {f"sample-{i}": Model(provider="fake", model_id=f"new-model-{i}",
                                  display_name=f"Sample model {i}", last_verified="synthetic")
              for i in range(model_count)}
    prices = {key: {"input": 0, "output": 0, "last_verified": "synthetic"} for key in models}
    pairs = inputs(data, [], True)
    preview = plan(pairs, models, prices, bench, ["active_node"])
    root = execute(pairs, models, prices, bench, ["active_node"], tmp_path / "runs", False, preview)
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "models.yaml").write_text(yaml.safe_dump({"models": {k: m.model_dump() for k, m in models.items()}}))
    (cfg / "pricing.yaml").write_text(yaml.safe_dump({"models": prices}))
    out = tmp_path / "deliverables"
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo / "src")}
    result = subprocess.run([sys.executable, "scripts/create_onepager.py", "--data-dir", str(data),
                             "--config-dir", str(cfg), "--run-root", str(root), "--out", str(out),
                             "--include-synthetic"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    summary = json.loads((out / "summary.json").read_text())
    assert summary["models"] == model_count
    assert summary["tested"] == project_count * model_count
    assert summary["synthetic"] is True
    assert len(PdfReader(out / "Model-consolidated.pdf").pages) == model_count
    if project_count == 8:
        assert len(PdfReader(out / "Benchmark-LLM.pdf").pages) > 1
    pdf_text = "".join(p.extract_text() for p in PdfReader(out / "Benchmark-LLM.pdf").pages)
    assert "SYNTHETIC" in pdf_text and "USD25" not in pdf_text
    html = (tmp_path / "conversation-review/Conversaciones.html").read_text()
    payload = json.loads(re.search(r'id="records">(.*?)</script>', html, re.S)[1])
    assert len(payload["records"]) == project_count * model_count
    assert {r["model"] for r in payload["records"]} == {m.display_name for m in models.values()}
