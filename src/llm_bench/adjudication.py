"""Auditable interpretation of a silent terminal response; never edit raw evidence."""

import copy
import re


def silent_close(transcript, scenario, bundle, policy):
    """Apply the same reviewed source policy to every model, without new inference.

    Requires every scripted turn, a spoken farewell, a valid route into an approved
    terminal node, and a normally completed empty response. An empty answer elsewhere,
    missing turns, output truncation or transport errors cannot pass this rule.
    """
    if policy.get("source_sha256") != bundle.source_sha256:
        return None
    original = transcript["summary"]
    calls, turns = transcript.get("calls", []), transcript.get("turns", [])
    if original["status"] != "empty_response" or len(calls) < 2 or not turns:
        return None
    if len(turns) != min(len(scenario.turns), scenario.max_turns):
        return None
    last = calls[-1]
    if last["status"] != "empty_response" or last.get("finish_reason") not in {
        "stop", "STOP", "FinishReason.STOP",
    }:
        return None
    if any(c["status"] != "ok" for c in calls[:-1]) or last.get("tool_calls_count"):
        return None
    node = bundle.node(last["active_node_before"])
    if node.id not in policy.get("terminal_node_ids", []):
        return None
    # Some exports attach a shared all-node route map even to a logical terminal.
    # Accept that only when explicitly reviewed against the source prompt.
    if node.transitions and not policy.get("allow_shared_route_map", False):
        return None
    if not any(re.search(pattern, turns[-1]["text"], re.I)
               for pattern in policy.get("farewell_patterns", [])):
        return None
    routes = [tool for tool in turns[-1].get("tools", [])
              if tool.get("valid") and tool.get("name") == "route_node"
              and tool.get("active_node_after") == node.id]
    if not routes or turns[-1].get("completed"):
        return None
    result = copy.deepcopy(transcript)
    result["calls"][-1].update(status="ok", silent_terminal_adjudicated=True)
    result["turns"][-1]["completed"] = True
    summary = result["summary"]
    summary.update(status="ok", original_status="empty_response", silent_terminal_adjudicated=True)
    summary["turns_completed"] += 1
    summary["known_successful_cost_usd"] = sum(c.get("cost_usd") or 0 for c in result["calls"])
    result["adjudication"] = {
        "rule": "reviewed_terminal_silence_after_farewell",
        "policy_id": policy["id"], "source_sha256": bundle.source_sha256,
        "original_status": "empty_response", "interpreted_status": "ok",
        "raw_evidence_unchanged": True,
    }
    return result
