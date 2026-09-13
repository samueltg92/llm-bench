import hashlib

from llm_bench.language_review import review_signals


def test_review_requires_exact_evidence_and_keeps_unknown_signals_pending():
    text = "Por favor"
    transcript = {"run_id": "a", "turns": [{"turn_index": 0, "text": text,
                  "language": {"segments": [{"start": 0, "end": len(text)}]}}]}
    assert review_signals(transcript, {})[0]["decision"] == "pending"
    policy = {"segments": {hashlib.sha256(text.encode()).hexdigest(): {
        "decision": "false_positive_spanish"}}}
    assert review_signals(transcript, policy)[0]["decision"] == "false_positive_spanish"
    transcript["turns"][0]["text"] = "Thank you"
    assert review_signals(transcript, policy)[0]["decision"] == "pending"
