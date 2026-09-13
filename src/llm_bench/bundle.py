from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Routing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str = Field(default="route_node", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    argument_name: str = Field(default="target_node", min_length=1)
    transport_note: str = ""


class Tool(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    original_name: str
    description: str = ""
    parameters_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    synthetic: bool = False

    def api(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


class Node(BaseModel):
    id: str
    name: str
    prompt: str = ""
    tool_names: list[str] = Field(default_factory=list)
    transitions: dict[str, str] = Field(default_factory=dict)
    tool_transitions: dict[str, str] = Field(default_factory=dict)
    est_tokens: int = 0


class Bundle(BaseModel):
    project: str = Field(pattern=r"^project_[1-9][0-9]*$")
    display_name: str
    composition: Literal["all_nodes", "single_node"]
    global_system: str = ""
    start_node: str
    initial_assistant_message: str = ""
    end_phrases: list[str] = Field(default_factory=list)
    nodes: list[Node]
    tools: list[Tool]
    source_sha256: str
    warnings: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    routing: Routing = Field(default_factory=Routing)
    allowed_languages: list[str] = Field(default_factory=lambda: ["es"])
    orchestration_language: Literal["en", "es"] = "es"

    @model_validator(mode="after")
    def validate_graph(self):
        import jsonschema

        ids = {n.id for n in self.nodes}
        names = {t.name for t in self.tools}
        if not ids or len(ids) != len(self.nodes) or len({n.name for n in self.nodes}) != len(self.nodes):
            raise ValueError("Node IDs and names must be unique and nonempty")
        if self.start_node not in ids or len(names) != len(self.tools):
            raise ValueError("Invalid start node or duplicate tool name")
        if any(not language.isalpha() or len(language) != 2 for language in self.allowed_languages):
            raise ValueError("allowed_languages must contain two-letter ISO 639-1 codes")
        self.allowed_languages = [language.lower() for language in self.allowed_languages]
        for tool in self.tools:
            jsonschema.Draft202012Validator.check_schema(tool.parameters_schema)
        for node in self.nodes:
            if (set(node.transitions.values()) | set(node.tool_transitions.values())) - ids:
                raise ValueError("Transition points to an unknown node")
            if set(node.tool_names) - names - {self.routing.tool_name}:
                raise ValueError("Node references an unknown tool")
            if set(node.tool_transitions) - set(node.tool_names):
                raise ValueError("Tool transitions must use tools available in their node")
            if self.routing.tool_name in node.tool_transitions:
                raise ValueError("A routing tool cannot also have a fixed tool transition")
            if self.composition == "single_node" and (node.transitions or node.tool_transitions):
                raise ValueError("Independent segments cannot declare graph transitions")
        return self

    def node(self, ref: str) -> Node:
        matches = [n for n in self.nodes if n.id == ref or n.name == ref]
        if not matches:
            targets = {n.transitions[ref] for n in self.nodes if ref in n.transitions}
            matches = [n for n in self.nodes if n.id in targets]
        if len(matches) != 1:
            raise ValueError("Unknown or ambiguous node reference")
        return matches[0]
