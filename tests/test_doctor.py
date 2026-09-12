import json

import pytest
from typer.testing import CliRunner

from llm_bench.cli import app
from llm_bench.providers.base import StreamEvent


@pytest.mark.parametrize(
    "finish,text,expected",
    [("stop", "Hola", "ok"), ("length", "", "output_limit"), ("length", "Ho", "output_limit")],
)
def test_doctor_reasoning_budget_and_truncation(tmp_path, monkeypatch, finish, text, expected):
    (tmp_path / "models.yaml").write_text(
        "models:\n  sample:\n    provider: openai_compat\n    model_id: sample\n"
        "    api_key_env: TEST_BENCH_KEY\n"
    )
    (tmp_path / "pricing.yaml").write_text(
        "models:\n  sample:\n    input: 1\n    output: 1\n    last_verified: synthetic\n"
    )
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"limit_usd": 1, "reserved_usd": 0, "max_operation_usd": 1}))
    monkeypatch.setenv("TEST_BENCH_KEY", "synthetic-placeholder")
    captured = {}

    class Provider:
        def stream_chat(self, **kwargs):
            captured.update(kwargs)
            yield StreamEvent(kind="reasoning", text="synthetic")
            yield StreamEvent(kind="text", text=text)
            yield StreamEvent(kind="done", finish_reason=finish)

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr("llm_bench.providers.registry.create", lambda model: Provider())
    result = CliRunner().invoke(
        app, ["doctor", "--config-dir", str(tmp_path), "--online", "--budget-file", str(budget)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["online_status"] == expected
    assert captured["max_output_tokens"] == 512
    assert captured["closed"]
    assert "synthetic-placeholder" not in result.output
    assert float(json.loads(budget.read_text())["reserved_usd"]) > 0
