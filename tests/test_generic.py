import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import ScriptProvider
from typer.testing import CliRunner

from llm_bench.bundle import Bundle
from llm_bench.cli import app
from llm_bench.config import Model, deployment_signature, profile_description
from llm_bench.evaluation import evaluate_turn, language_check
from llm_bench.experiment import jobs_for
from llm_bench.prompt import build
from llm_bench.providers.registry import create
from llm_bench.runner import conversation, resolve_tool
from llm_bench.scenario import load

FIXTURES = Path(__file__).parent / "fixtures"


def neutral():
    raw = json.loads((FIXTURES / "neutral_graph.json").read_text())
    return Bundle(**raw, source_sha256="synthetic"), load(FIXTURES / "neutral_scenario.yaml")


def test_neutral_import_and_custom_flow(model, bench, tmp_path):
    result = CliRunner().invoke(app, ["import-bundle", "--input", str(FIXTURES / "neutral_graph.json"),
                                    "--out", str(tmp_path / "data")])
    assert result.exit_code == 0, result.output
    bundle = Bundle.model_validate_json((tmp_path / "data/project_11.json").read_text())
    _, scenario = neutral()
    provider = ScriptProvider([
        [("search_catalog", {"title": "Moon Atlas"})],
        [("handoff_stage", {"destination": "Done"})],
        "The requested fictional book is available.",
    ])
    result = conversation(bundle, scenario, "arbitrary-model", model, provider, None, bench, tmp_path / "run")
    assert result["status"] == "ok"
    assert result["expected_path_match"] is True  # IDs resolve; intermediate milestones need not be listed.
    assert result["routing_path"] == ["Entry", "Review", "Done"]
    assert result["assertions_passed"] == result["assertions_total"]
    assert result["native_tool_calls"] == 2 and result["emulated_tool_calls"] == 0
    assert "Active node: Review" in provider.requests[1]["messages"][0]["content"]
    assert any(m["role"] == "tool" for m in provider.requests[2]["messages"])
    assert provider.requests[1]["tools"][0]["function"]["name"] == "handoff_stage"
    assert "destination" in provider.requests[1]["tools"][0]["function"]["parameters"]["properties"]
    assert "route_node_all" not in json.dumps(provider.requests)


def test_failed_mock_does_not_follow_fixed_tool_transition():
    bundle, scenario = neutral()
    scenario.tool_mocks["search_catalog"].error = True
    active, _, event = resolve_tool(
        {"function": {"name": "search_catalog", "arguments": '{"title":"Moon Atlas"}'}},
        bundle, scenario, "entry", {}, {"search_catalog"})
    assert active == "entry" and event["valid"] and event["mock_error"]


def test_same_scenario_id_in_two_projects_is_not_dropped():
    bundle, scenario = neutral()
    other = bundle.model_copy(update={"project": "project_12"})
    second = scenario.model_copy(update={"project": "project_12"})
    assert len(list(jobs_for([(bundle, scenario), (other, second)], ["active_node"]))) == 2


def test_independent_segment_has_no_route_contract():
    bundle, scenario = neutral()
    bundle.composition = "single_node"
    for node in bundle.nodes:
        node.transitions = {}
        node.tool_transitions = {}
    scenario.segment = "done"
    system, tools, mode = build(bundle, scenario, "done", "full", {})
    assert mode == "single_node" and tools == [] and "FLOW STATE" not in system


def test_language_policy_can_allow_a_different_language_or_disable_checks(monkeypatch):
    language = SimpleNamespace(iso_code_639_1=SimpleNamespace(name="EN"))
    detector = SimpleNamespace(
        detect_multiple_languages_of=lambda text: [SimpleNamespace(start_index=0, end_index=len(text), language=language)],
        compute_language_confidence=lambda text, lang: .99,
    )
    monkeypatch.setattr("llm_bench.evaluation.detector", lambda: detector)
    text = "This is an entirely fictional test response."
    assert language_check(text, ["en"])["status"] == "no_signal"
    assert language_check(text, ["es"])["status"] == "flagged"
    assert language_check(text, ["es", "en"])["status"] == "no_signal"
    assert language_check(text, [])["status"] == "disabled"


def test_text_protocol_cannot_pass_a_native_function_expectation():
    _, scenario = neutral()
    tool = {"name": "search_catalog", "arguments": {"title": "Moon Atlas"}, "valid": True,
            "tool_mode": "text_protocol"}
    result = evaluate_turn(scenario.turns[0], 0, "", [tool], "Done", [], None)
    assert result[0]["pass"] is False


def test_provider_extension_does_not_require_a_model_allowlist(monkeypatch):
    def factory(model, timeouts):
        return model.model_id, timeouts
    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kwargs: [SimpleNamespace(load=lambda: factory)])
    model = Model(provider="custom_adapter", model_id="new-model", display_name="New model")
    assert create(model, {"read": 10}) == ("new-model", {"read": 10})
    assert profile_description({"reasoning_effort": "low"}) == "effort low"
    assert profile_description({"extra_body": {"reasoning": {"effort": "low"}}}) == "effort low"
    assert "default" in profile_description({"extra_body": {"thinking": {"type": "enabled"}}})


def test_unreachable_flow_and_invalid_tool_schema_fail_before_transport():
    bundle, scenario = neutral()
    scenario.expected_path = ["done", "entry"]
    with pytest.raises(ValueError, match="impossible"):
        scenario.validate_bundle(bundle)
    raw = bundle.model_dump()
    raw["nodes"][0]["tool_transitions"] = {"missing_tool": "done"}
    with pytest.raises(ValueError, match="available"):
        Bundle.model_validate(raw)


def test_profiles_distinguish_endpoint_and_effort_but_not_display_labels():
    model = Model(provider="openai_compat", model_id="sample", base_url="https://example.invalid/v1")
    before = model.model_dump()
    assert deployment_signature(before) == deployment_signature({**before, "display_name": "Renamed"})
    assert deployment_signature(before) != deployment_signature({**before, "extra": {"reasoning_effort": "low"}})
    assert deployment_signature(before) != deployment_signature({**before, "base_url": "https://other.invalid/v1"})
