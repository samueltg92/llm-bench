import json

import pytest

from llm_bench.evaluation import evaluate_turn, language_check
from llm_bench.report import aggregate, compare, report
from llm_bench.runner import conversation
from llm_bench.scenario import Rule, Turn


@pytest.mark.parametrize(
    "text",
    [
        "Thank you for calling our customer service. Please give me your full name and address.",
        "Merci de nous avoir contactés. Pouvez-vous me donner votre nom et votre adresse ?",
    ],
)
def test_foreign_language_is_flagged(text):
    assert language_check(text)["status"] == "flagged"


def test_spanish_and_short_unknown():
    assert (
        language_check(
            "Gracias por comunicarte con nosotros. ¿Puedes darme tu nombre y tu número de documento?"
        )["status"]
        == "no_signal"
    )
    assert language_check("Sí.")["status"] == "unknown"


def test_tool_expectations_and_rule_order():
    turn = Turn(
        content="Consulta",
        expect_tools=[{"tool": "buscar", "max_calls": 1}],
        forbidden_tools=["borrar"],
    )
    events = [{"name": "buscar", "arguments": {}, "valid": True, "executed_ns": 20}]
    rules = [Rule(id="before", kind="tool_before_text", value="buscar")]
    assertions = evaluate_turn(turn, 0, "Hola", events, "Inicio", rules, 10)
    assert [r["pass"] for r in assertions] == [True, True, False]


def test_report_excludes_synthetic_and_degraded(bundle, scenario, model, bench, scripted, tmp_path):
    summary = conversation(bundle, scenario, "fake", model, scripted, None, bench, tmp_path)
    rows = report(tmp_path)
    assert rows[0]["ranking_eligible"] is False
    assert (tmp_path / "REPORT.md").exists()
    assert (tmp_path / "summary.csv").exists()
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    summary["synthetic"] = False
    summary["degraded"] = True
    assert aggregate([summary], calls)[0]["ranking_eligible"] is False
    assert compare(rows, "absent") == []


def test_partial_error_keeps_transcript_and_failed_assertions(
    bundle, scenario, model, bench, tmp_path
):
    from conftest import ScriptProvider

    provider = ScriptProvider([RuntimeError("provider failure")])
    row = conversation(bundle, scenario, "fake", model, provider, None, bench, tmp_path)
    assert row["status"] == "error"
    assert row["rule_compliance"] == 0
    assert row["conversation_cost_usd"] is None
    assert list((tmp_path / "transcripts").glob("*.json"))


def test_private_conversation_review_preserves_dialogue_and_tools(tmp_path):
    import json

    from llm_bench.report import conversation_review

    folder = tmp_path / "transcripts"
    folder.mkdir()
    (folder / "sample.json").write_text(
        json.dumps(
            {
                "scenario": "example",
                "model_key": "sample",
                "history": [
                    {"role": "user", "content": "¿Puedes consultar?"},
                    {
                        "role": "assistant",
                        "content": "Un momento.",
                        "reasoning_content": "INTERNAL_ANALYSIS",
                        "tool_calls": [{"function": {"name": "consultar", "arguments": "{}"}}],
                    },
                    {"role": "tool", "content": '{"ok": true}'},
                    {"role": "assistant", "content": "<script>unsafe()</script>"},
                ],
                "summary": {"routing_path": ["Inicio", "Consulta"]},
            }
        )
    )
    conversation_review(tmp_path)
    output = (tmp_path / "CONVERSATIONS.md").read_text()
    assert (
        output.index("¿Puedes consultar?")
        < output.index("Un momento.")
        < output.index('&quot;name&quot;: &quot;consultar&quot;')
    )
    assert "consultar" in output and "Resultado simulado" in output
    assert "INTERNAL_ANALYSIS" not in output
    assert "<script>" not in output and "&lt;script&gt;" in output
    assert "routing_path" in output
