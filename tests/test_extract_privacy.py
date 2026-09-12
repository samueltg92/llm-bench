import importlib.util
import json
import os
from pathlib import Path

import pytest

from llm_bench.bundle import Node
from llm_bench.extract import dedup_blocks, extract, tool_name
from llm_bench.privacy import external_path, write_private
from llm_bench.providers.google_genai import sanitize_schema_for_gemini


def test_dedup_does_not_expand_rules_to_missing_nodes():
    nodes = [
        Node(
            id=str(i),
            name=str(i),
            prompt="Común.\n\n" + ("Solo cuatro." if i < 4 else "Otra regla."),
        )
        for i in range(5)
    ]
    shared, unique = dedup_blocks(nodes)
    assert shared == ["Común."]
    assert "Solo cuatro." in unique["0"] and "Solo cuatro." not in unique["4"]


def test_sanitization_is_stable_and_collision_resistant():
    assert tool_name("simple") == "simple"
    assert tool_name("a b") != tool_name("a_b")
    assert len(tool_name("x" * 100)) <= 64


def test_empty_nodes_and_dirty_tool_names(tmp_path):
    data = json.loads((Path(__file__).parent / "fixtures/mini_export.json").read_text())
    data["nodes"][0]["prompt"] = None
    data["nodes"][0]["functions"][0]["tool_ref_name"] = " VERIFICAR "
    file = tmp_path / "input.json"
    file.write_text(json.dumps(data))
    bundle = extract(file, "project_2")
    assert bundle.nodes[0].prompt == ""
    assert "verificar" in bundle.nodes[0].tool_names
    assert any("Empty prompt" in w for w in bundle.warnings)


def test_output_rejected_inside_git_and_symlink(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    link = tmp_path / "elsewhere"
    link.symlink_to(repo, target_is_directory=True)
    with pytest.raises(ValueError):
        external_path(repo / "results")
    with pytest.raises(ValueError):
        external_path(link / "results")


def test_artifacts_redact_secret_values_and_have_private_permissions(tmp_path, monkeypatch):
    secret = "unit-test-credential-" + "x" * 24
    monkeypatch.setenv("EXAMPLE_API_KEY", secret)
    path = tmp_path / "private" / "output.json"
    write_private(path, {"value": secret, "header": "Bearer abcdefg"})
    assert secret not in path.read_text()
    assert "abcdefg" not in path.read_text()
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_google_schema_keeps_properties_named_like_keywords():
    source = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"default": {"type": "string", "default": "x"}, "format": {"type": "string"}},
        "required": ["default"],
    }
    sanitized = sanitize_schema_for_gemini(source)
    assert "additionalProperties" not in sanitized
    assert sanitized["properties"]["default"] == {"type": "string"}
    assert "format" in sanitized["properties"]
    assert source["additionalProperties"] is False


def test_publication_guard_catches_name_prompt_and_populated_env():
    path = Path(__file__).parents[1] / "scripts/check_public.py"
    spec = importlib.util.spec_from_file_location("publication_guard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "private_name" in mod.violations(
        "README.md", b"Hidden Client", [b"hidden client"], set()
    )
    assert "private_corpus_overlap" in mod.violations(
        "src/x.py", b"private phrase", [], {b"private phrase"}
    )
    assert "populated_env_template" in mod.violations(
        ".env.example", b"API_KEY=something", [], set()
    )


@pytest.mark.parametrize(
    "name,content,blocked",
    [
        ("README.md", b"Client ZZ", True),
        ("docs/zz_report.md", b"Numbers only", True),
        ("README.md", b"client-zz-results", True),
        ("README.md", b"puzzle https://example.invalid", False),
    ],
)
def test_publication_guard_handles_private_acronyms(name, content, blocked):
    path = Path(__file__).parents[1] / "scripts/check_public.py"
    spec = importlib.util.spec_from_file_location("publication_guard_acronyms", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    reasons = mod.violations(name, content, [], set(), ["zz"])
    assert ("private_name" in reasons) is blocked
