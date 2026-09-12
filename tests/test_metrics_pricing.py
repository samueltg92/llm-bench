import time

import httpx
import pytest

from llm_bench.metrics import describe, percentile
from llm_bench.pricing import calculate
from llm_bench.providers.base import StreamEvent, Usage
from llm_bench.runner import measure


def test_ttft_excludes_empty_reasoning_and_usage(scenario):
    class Delayed:
        def stream_chat(self, **kwargs):
            yield StreamEvent(kind="text", text="")
            yield StreamEvent(kind="reasoning", text="hidden")
            time.sleep(0.04)
            yield StreamEvent(kind="tool_call", name="consultar", arguments="{}")
            time.sleep(0.02)
            yield StreamEvent(kind="usage", usage=Usage(prompt_tokens=10, completion_tokens=5))
            yield StreamEvent(kind="done", finish_reason="stop")

    row, _, first_text = measure(Delayed(), [], [], scenario, None)
    assert 25 <= row["ttft_ms"] <= 55
    assert row["first_tool_ms"] == row["ttft_ms"]
    assert first_text is None
    assert row["generation_ms"] >= 15


def test_retries_record_partial_attempt_and_unknown_spend(scenario):
    class Retry:
        n = 0

        def stream_chat(self, **kwargs):
            self.n += 1
            if self.n == 1:
                yield StreamEvent(kind="text", text="parcial")
                raise httpx.ReadTimeout("simulated")
            yield StreamEvent(kind="text", text="Hola")
            yield StreamEvent(kind="usage", usage=Usage(prompt_tokens=5, completion_tokens=2))
            yield StreamEvent(kind="done", finish_reason="stop")

    row, _, _ = measure(Retry(), [], [], scenario, None, {"max_attempts": 2, "base_delay_s": 0})
    assert row["status"] == "ok" and row["retries"] == 1
    assert row["attempts"][0]["partial_text"] == "parcial"
    assert row["spend_complete"] is False


def test_incomplete_stream_has_no_fabricated_metrics(scenario):
    class Broken:
        def stream_chat(self, **kwargs):
            yield StreamEvent(kind="text", text="parcial")

    row, assistant, _ = measure(Broken(), [], [], scenario, None)
    assert row["status"] == "error"
    assert row["ttft_ms"] is None and row["cost_usd"] is None
    assert assistant["content"] == "parcial"


def test_token_cost_avoids_reasoning_double_charge():
    price = {"input": 1, "output": 2, "cached_input": 0.5, "last_verified": "2026-09-12"}
    usage = Usage(
        prompt_tokens=100, completion_tokens=30, cached_prompt_tokens=20, reasoning_tokens=10
    )
    assert calculate(usage, price)["cost_usd"] == pytest.approx(0.00015)
    usage.reasoning_included = False
    assert calculate(usage, price)["cost_usd"] == pytest.approx(0.00017)


@pytest.mark.parametrize(
    "price", [None, {}, {"input": None, "output": 1}, {"input": 0, "output": None}]
)
def test_unknown_price_never_zero(price):
    assert calculate(Usage(prompt_tokens=1, completion_tokens=1), price) == {
        "cost_usd": None,
        "cost_status": "unpriced",
    }


def test_percentiles_and_single_observation():
    assert percentile([10, 20, 30], 0.5) == 20
    assert percentile([], 0.95) is None
    assert describe([5])["stdev"] is None
