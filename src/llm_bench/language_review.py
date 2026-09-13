"""Review language-detector signals without turning unreviewed text into a pass."""

import hashlib


def review_signals(transcript, policy):
    records = []
    approved = policy.get("segments", {})
    for turn in transcript.get("turns", []):
        for span in turn.get("language", {}).get("segments", []):
            fragment = turn["text"][span["start"]:span["end"]]
            digest = hashlib.sha256(fragment.encode()).hexdigest()
            decision = approved.get(digest, {}).get("decision", "pending")
            if decision not in {"false_positive_spanish", "false_positive", "confirmed_foreign", "pending"}:
                raise ValueError("Unknown language review decision")
            records.append({"run_id": transcript["run_id"], "turn": turn["turn_index"],
                            "span_sha256": digest, "decision": decision})
    return records
