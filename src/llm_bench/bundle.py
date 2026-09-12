from typing import Any, Literal

from pydantic import BaseModel, Field


class Tool(BaseModel):
    name: str
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

    def node(self, ref: str) -> Node:
        matches = [n for n in self.nodes if n.id == ref or n.name == ref]
        if not matches:
            targets = {n.transitions[ref] for n in self.nodes if ref in n.transitions}
            matches = [n for n in self.nodes if n.id in targets]
        if len(matches) != 1:
            raise ValueError("Unknown or ambiguous node reference")
        return matches[0]
