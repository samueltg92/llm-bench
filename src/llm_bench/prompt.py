import copy
import json

from .bundle import Bundle
from .extract import dedup_blocks, route_tool
from .privacy import fingerprint
from .scenario import Scenario, render


def build(
    bundle: Bundle, scenario: Scenario, active: str, mode: str, variables: dict, dedup: bool = False
) -> tuple[str, list[dict], str]:
    node = bundle.node(active)
    effective = "single_node" if bundle.composition == "single_node" else mode
    if effective in ("single_node", "active_node"):
        selected = [node]
    elif effective == "subset":
        ids = {bundle.node(r).id for r in scenario.nodes_subset}
        if not ids or node.id not in ids:
            raise ValueError("Active node is not available in subset")
        selected = [n for n in bundle.nodes if n.id in ids]
    else:
        selected = bundle.nodes
    english = bundle.orchestration_language == "en"
    route = bundle.routing
    parts = [bundle.global_system]
    if effective in ("single_node", "active_node"):
        parts.append(node.prompt)
    else:
        common, unique = dedup_blocks(selected) if dedup else ([], {})
        if common:
            parts.append(
                ("# SHARED RULES\n" if english else "# REGLAS COMPARTIDAS POR TODOS LOS NODOS SELECCIONADOS\n") + "\n\n".join(common)
            )
        parts.append(("# NODE MAP\nInitial node: " if english else "# MAPA DE NODOS\nNodo inicial: ") + bundle.node(bundle.start_node).name)
        for n in selected:
            parts.append(
                (f"## NODE: {n.name}\nTransitions: {', '.join(n.transitions) or 'none'}\n" if english else f"## NODO: {n.name}\nTransiciones: {', '.join(n.transitions) or 'ninguna'}\n")
                + unique.get(n.id, n.prompt)
            )
    if effective != "single_node":
        if english:
            parts.append(
                f"# FLOW STATE\nActive node: {node.name}\n"
                "Follow only the active node's rules.\n"
                + (f"Route with {route.tool_name} using argument {route.argument_name}.\n" if node.transitions else "")
                +
                f"Allowed destinations: {', '.join(node.transitions) or 'none'}"
            )
        else:
            parts.append(
                f"# ESTADO DEL FLUJO\nNodo activo: {node.name}\n"
                "Solo ejecuta las reglas del nodo activo.\n"
                + (f"Cambia de nodo mediante {route.tool_name}.\n" if node.transitions else "")
                +
                f"Destinos permitidos: {', '.join(node.transitions) or 'ninguno'}"
            )
        if route.transport_note:
            parts.append(route.transport_note)
        if node.tool_transitions:
            parts.append(("# TRANSITIONS AFTER SUCCESSFUL SIMULATED TOOLS\n" if english else
                          "# TRANSICIONES TRAS HERRAMIENTAS SIMULADAS EXITOSAS\n")
                         + json.dumps({name: bundle.node(target).name
                                       for name, target in node.tool_transitions.items()}, ensure_ascii=False))
    aliases = [f"{t.original_name} => {t.name}" for t in bundle.tools if t.original_name != t.name]
    if aliases:
        parts.append(("# API TOOL NAMES\n" if english else "# NOMBRES DE HERRAMIENTAS ACEPTADOS POR LA API\n") + "\n".join(aliases))
    if variables:
        parts.append(
            ("# SIMULATION DATA AND STATE\n" if english else "# DATOS Y ESTADO DISPONIBLES EN ESTA SIMULACIÓN\n")
            + json.dumps(variables, ensure_ascii=False, sort_keys=True)
        )
    system = render("\n\n".join(p for p in parts if p), variables)
    names = {name for n in selected for name in n.tool_names}
    tools = [
        copy.deepcopy(t.api()) for t in bundle.tools if t.name in names and t.name != route.tool_name
    ]
    selected_ids = {n.id for n in selected}
    for tool in scenario.platform_tools:
        if any(bundle.node(ref).id in selected_ids for ref in tool.nodes):
            tools.append(tool.tool().api())
    if node.transitions and effective != "single_node":
        # Even full mode only authorizes transitions from the actual active node.
        tools.append(route_tool(list(node.transitions), route, bundle.orchestration_language).api())
    return system, tools, effective


def prepare(system: str, history: list[dict], tools: list[dict], model) -> tuple[list, list, dict]:
    messages = copy.deepcopy(history)
    tool_mode = "native" if model.supports_tools else "text_protocol"
    canonical_hash = fingerprint({"system": system, "tools": tools})
    wire_tools = copy.deepcopy(tools)
    if tool_mode == "text_protocol":
        system += (
            "\n# TEXT TOOL PROTOCOL\nTo invoke a function, emit only "
            '<<TOOL_CALL>{"name":"function_name","arguments":{}}<END>.\n'
            + json.dumps(tools, ensure_ascii=False)
        )
        wire_tools = []
        adapted = []
        for message in messages:
            if message["role"] == "tool":
                adapted.append({"role": "user", "content": "[TOOL_RESULT] " + message["content"]})
            elif message.get("tool_calls"):
                text = message.get("content") or ""
                for call in message["tool_calls"]:
                    text += (
                        "<<TOOL_CALL>"
                        + json.dumps(
                            {
                                "name": call["function"]["name"],
                                "arguments": json.loads(call["function"]["arguments"]),
                            },
                            ensure_ascii=False,
                        )
                        + "<END>"
                    )
                adapted.append({"role": "assistant", "content": text})
            else:
                adapted.append(message)
        messages = adapted
    messages.insert(
        0,
        {
            "role": "system" if model.supports_system else "user",
            "content": system if model.supports_system else "[SYSTEM]\n" + system,
        },
    )
    return (
        messages,
        wire_tools,
        {
            "system_sha256": fingerprint(system),
            "canonical_input_sha256": canonical_hash,
            "request_sha256": fingerprint({"messages": messages, "tools": wire_tools}),
            "request_hash_basis": "normalized_messages_and_tools_before_sdk_serialization",
            "tool_mode": tool_mode,
        },
    )
