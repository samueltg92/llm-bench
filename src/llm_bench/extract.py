"""Generic graph-export parser. Source names and paths are never catalogued in code."""

import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .bundle import Bundle, Node, Routing, Tool
from .privacy import write_private
from .tokens import count


def normalized(name: str) -> str:
    return " ".join(name.lower().split())


def tool_name(name: str) -> str:
    clean = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    clean = re.sub(r"[^a-zA-Z0-9_-]", "_", clean).strip("_") or "tool"
    if clean != name or len(clean) > 64:
        clean = clean[:51] + "_" + hashlib.sha256(name.encode()).hexdigest()[:12]
    return clean


def extract(source: Path, project: str, composition: str | None = None) -> Bundle:
    raw = source.read_bytes()
    data = json.loads(raw)
    warnings = []
    defs = data.get("tool_definitions") or []
    tools: dict[str, Tool] = {}
    nodes = []
    for item in data.get("nodes", []):
        node = Node(
            id=item["source_node_id"], name=item["node_name"], prompt=item.get("prompt") or ""
        )
        node.est_tokens = count(node.prompt)
        if not node.prompt.strip():
            warnings.append(f"Empty prompt excluded from segment scenarios: {node.name}")
        elif len(node.prompt.strip()) <= 4:
            warnings.append(f"Placeholder prompt requires review: {node.name}")
        for fn in item.get("functions") or []:
            refs = [
                str(fn.get(k) or "") for k in ("tool_ref_id", "function_label", "tool_ref_name")
            ]
            if any(r.startswith("route_node") for r in refs):
                node.transitions.update(fn.get("transition_mappings") or {})
                if "route_node" not in node.tool_names:
                    node.tool_names.append("route_node")
                continue
            name = fn.get("tool_ref_name") or fn.get("function_label") or "unknown_tool"
            matches = [t for t in defs if t.get("name") == name]
            if not matches:
                matches = [t for t in defs if normalized(t.get("name", "")) == normalized(name)]
            if len(matches) > 1:
                raise ValueError("Ambiguous tool definition; fix the private export")
            if matches:
                definition = matches[0]
                name = definition["name"]
                tool = Tool(
                    name=tool_name(name),
                    original_name=name,
                    description=definition.get("description") or "",
                    parameters_schema=definition.get("parameters_schema")
                    or {"type": "object", "properties": {}},
                )
            else:
                warnings.append(f"Missing tool schema; synthetic stub: {name}")
                tool = Tool(name=tool_name(name), original_name=name, synthetic=True)
            if tool.name == "route_node":
                raise ValueError("Business tool conflicts with reserved route_node")
            tools[tool.name] = tool
            if tool.name not in node.tool_names:
                node.tool_names.append(tool.name)
        nodes.append(node)
    if not nodes:
        raise ValueError("Export has no nodes")
    if len({n.id for n in nodes}) != len(nodes) or len({n.name for n in nodes}) != len(nodes):
        raise ValueError("Node IDs and names must be unique")
    inferred = "all_nodes" if any(n.transitions for n in nodes) else "single_node"
    if composition and composition != inferred:
        warnings.append("Composition override differs from graph inference")
    composition = composition or inferred
    by_id = {n.id: n for n in nodes}
    for n in nodes:
        for name, target in n.transitions.items():
            if target not in by_id:
                raise ValueError("Broken transition mapping in private export")
            if by_id[target].name != name:
                warnings.append(f"Transition alias preserved: {name} -> {by_id[target].name}")
        if composition == "single_node":
            n.transitions = {}
            n.tool_names = [t for t in n.tool_names if t != "route_node"]
    if composition == "all_nodes":
        targets = list(dict.fromkeys(t for n in nodes for t in n.transitions))
        tools["route_node"] = route_tool(targets)
    llm = (data.get("config") or {}).get("llm") or {}
    pipeline = (data.get("config") or {}).get("pipeline_config") or {}
    system = "\n\n".join(
        filter(None, [llm.get("system_prompt"), llm.get("context_append_prompt")])
    ).strip()
    if not system:
        warnings.append("Empty global system")
    warnings.extend(
        f"{key} omitted ({len(data.get(key) or [])})" for key in ("knowledge_bases", "assets")
    )
    bundle = Bundle(
        project=project,
        display_name=f"Proyecto {project.split('_')[-1]}",
        composition=composition,
        global_system=system,
        start_node=data["bot"]["start_node_id"],
        nodes=nodes,
        tools=list(tools.values()),
        initial_assistant_message=pipeline.get("initial_audio_phrase") or "",
        end_phrases=pipeline.get("end_phrases") or [],
        source_sha256=hashlib.sha256(raw).hexdigest(),
        warnings=warnings,
    )
    bundle.node(bundle.start_node)
    bundle.stats = {
        "n_nodes": len(nodes),
        "n_tools": len(tools),
        "global_system_tokens": count(system),
        "node_prompt_tokens": sum(n.est_tokens for n in nodes),
        "tools_tokens": count([t.api() for t in tools.values()]),
        "token_basis": "o200k_base_cross_model_estimate",
    }
    return bundle


def route_tool(targets: list[str], routing: Routing | None = None, language="es") -> Tool:
    routing = routing or Routing()
    return Tool(
        name=routing.tool_name,
        original_name=routing.tool_name,
        synthetic=True,
        description=("Switch to the destination node when the active node's rules require it."
                     if language == "en" else
                     "Cambia al nodo destino cuando las reglas del nodo activo lo indiquen."),
        parameters_schema={
            "type": "object",
            "required": [routing.argument_name],
            "additionalProperties": False,
            "properties": {routing.argument_name: {"type": "string", "enum": targets}},
        },
    )


def dedup_blocks(nodes: list[Node]) -> tuple[list[str], dict[str, str]]:
    # Only exact paragraphs present ONCE in EVERY nonempty selected node qualify.
    # Default is off: moving even identical text can change model behavior.
    active = [n for n in nodes if n.prompt.strip()]
    blocks = {n.id: n.prompt.split("\n\n") for n in active}
    counters = {key: Counter(value) for key, value in blocks.items()}
    shared = []
    if len(active) >= 2:
        shared = list(
            dict.fromkeys(
                b
                for b in blocks[active[0].id]
                if b.strip() and all(c[b] == 1 for c in counters.values())
            )
        )
    shared_set = set(shared)
    unique = {
        n.id: "\n\n".join(b for b in n.prompt.split("\n\n") if b not in shared_set) for n in nodes
    }
    return shared, unique


def save_bundle(bundle: Bundle, out: Path):
    write_private(out / f"{bundle.project}.json", bundle.model_dump())
