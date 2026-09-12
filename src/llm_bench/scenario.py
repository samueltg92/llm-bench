import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .bundle import Bundle, Tool


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expectation(Strict):
    tool: str
    arguments: dict = Field(default_factory=dict)
    min_calls: int = Field(default=1, ge=0)
    max_calls: int | None = Field(default=None, ge=0)


class Rule(Strict):
    id: str
    kind: Literal["required_regex", "forbidden_regex", "max_words", "tool_before_text"]
    value: str | int
    turn: int | None = Field(default=None, ge=0)


class Turn(Strict):
    content: str
    expect_tools: list[Expectation] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    expected_node: str | None = None
    # Deterministic branch wording indexed by actual active node; no forced transitions.
    content_by_node: dict[str, str] = Field(default_factory=dict)


class MockResponse(Strict):
    response: Any = Field(default_factory=lambda: {"ok": True})
    state_updates: dict[str, Any] = Field(default_factory=dict)
    delay_ms: float = Field(default=0, ge=0, le=1000)
    error: bool = False


class PlatformTool(Strict):
    """Explicit simulated platform interface missing from a business export."""

    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str
    nodes: list[str] = Field(min_length=1)
    parameters_schema: dict

    def tool(self):
        return Tool(
            name=self.name, original_name=self.name, description=self.description,
            parameters_schema=self.parameters_schema, synthetic=True,
        )


class Scenario(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    project: str = Field(pattern=r"^project_[1-9][0-9]*$")
    description: str = ""
    segment: str | None = None
    reference: bool = True
    max_output_tokens: int = Field(default=300, ge=1)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_turns: int = Field(default=8, ge=1)
    variables: dict[str, Any] = Field(default_factory=dict)
    turns: list[Turn] = Field(min_length=1)
    nodes_subset: list[str] = Field(default_factory=list)
    tool_mocks: dict[str, MockResponse] = Field(default_factory=dict)
    tool_prerequisites: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tool_limits_per_turn: dict[str, int] = Field(default_factory=dict)
    terminal_nodes: list[str] = Field(default_factory=list)
    terminal_tools: list[str] = Field(default_factory=list)
    platform_tools: list[PlatformTool] = Field(default_factory=list)
    default_mock: MockResponse = Field(default_factory=MockResponse)
    rules: list[Rule] = Field(default_factory=list)
    expected_path: list[str] = Field(default_factory=list)
    stop_on_end_phrases: bool = True
    user_simulator: str | None = None

    @model_validator(mode="after")
    def validate_rules(self):
        if self.user_simulator:
            raise ValueError("Only deterministic scripted scenarios are implemented")
        for rule in self.rules:
            if rule.kind.endswith("regex"):
                re.compile(str(rule.value))
        return self

    def validate_bundle(self, bundle: Bundle):
        if bundle.project != self.project:
            raise ValueError("Scenario project does not match bundle")
        if bundle.composition == "single_node":
            if not self.segment or not bundle.node(self.segment).prompt.strip():
                raise ValueError("A nonempty segment is required")
            if self.nodes_subset:
                raise ValueError("A segment scenario cannot have nodes_subset")
        elif self.segment:
            raise ValueError("Graph scenarios cannot select a segment")
        for ref in [*self.nodes_subset, *self.expected_path, *self.terminal_nodes]:
            bundle.node(ref)
        if self.nodes_subset and bundle.start_node not in [
            bundle.node(r).id for r in self.nodes_subset
        ]:
            raise ValueError("Subset must include the starting node")
        for a, b in zip(self.expected_path, self.expected_path[1:]):
            if bundle.node(b).id not in bundle.node(a).transitions.values():
                raise ValueError("Expected path contains an impossible transition")
        names = {t.name for t in bundle.tools}
        for tool in self.platform_tools:
            if tool.name == "route_node" or tool.name in names:
                raise ValueError("Platform tool conflicts with an existing tool")
            for ref in tool.nodes:
                bundle.node(ref)
            if tool.name not in self.tool_mocks:
                raise ValueError("Platform tools require explicit mock responses")
            import jsonschema

            jsonschema.Draft202012Validator.check_schema(tool.parameters_schema)
            names.add(tool.name)
        for turn in self.turns:
            if turn.expected_node:
                bundle.node(turn.expected_node)
            for ref in turn.content_by_node:
                bundle.node(ref)
            for name in [*(e.tool for e in turn.expect_tools), *turn.forbidden_tools]:
                if name not in names:
                    raise ValueError("Scenario refers to an unknown tool")
        if (
            set(self.tool_mocks)
            | set(self.tool_prerequisites)
            | set(self.tool_limits_per_turn)
            | set(self.terminal_tools)
        ) - names:
            raise ValueError("Mock/prerequisite refers to an unknown tool")
        if any(value < 0 for value in self.tool_limits_per_turn.values()):
            raise ValueError("Tool call limits must be nonnegative")


def load(path: Path) -> Scenario:
    return Scenario.model_validate(yaml.safe_load(path.read_text()))


def render(text: str, variables: dict) -> str:
    return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", lambda m: str(variables.get(m[1], m[0])), text)
