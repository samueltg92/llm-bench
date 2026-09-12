import pytest

from llm_bench.rate_limit import RateLimitCapacityError, RequestPacer


def fake_clock(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("llm_bench.rate_limit.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "llm_bench.rate_limit.time.sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    return clock


def test_paces_rolling_tokens_and_counts_full_output(monkeypatch):
    clock = fake_clock(monkeypatch)
    pacer = RequestPacer(1000, 2, input_margin=1.2)
    pacer.acquire(300, 100)  # 460
    pacer.acquire(300, 100)  # 920; spaced by RPS.
    assert clock[0] == 100.5
    pacer.acquire(300, 100)  # First request must expire, not just a calendar minute.
    assert clock[0] == 161.0
    assert sum(n for _, n in pacer.requests) <= 1000


def test_oversized_request_rejected_without_wait_or_partial_reservation(monkeypatch):
    clock = fake_clock(monkeypatch)
    pacer = RequestPacer(1000, 1)
    with pytest.raises(RateLimitCapacityError):
        pacer.acquire(800, 100)
    assert clock[0] == 100 and not pacer.requests


def test_request_spacing_when_clock_starts_at_zero(monkeypatch):
    clock = fake_clock(monkeypatch)
    clock[0] = 0
    pacer = RequestPacer(1000, 2)
    pacer.acquire(10, 10)
    pacer.acquire(10, 10)
    assert clock[0] == 0.5


def test_pacing_wait_is_excluded_from_request_latency(monkeypatch, scenario, scripted):
    from llm_bench.runner import measure
    from llm_bench.tokens import request_tokens

    clock = fake_clock(monkeypatch)
    monkeypatch.setattr("llm_bench.runner.time.perf_counter_ns", lambda: int(clock[0] * 1e9))
    messages = [{"role": "user", "content": "Hola"}]
    required = request_tokens(messages, []) + scenario.max_output_tokens
    scripted.request_pacer = RequestPacer(required, 1, input_margin=1)
    first, _, _ = measure(scripted, messages, [], scenario, None)
    second, _, _ = measure(scripted, messages, [], scenario, None)
    assert first["rate_limit_wait_ms"] == 0
    assert second["rate_limit_wait_ms"] == 61000
    assert second["ttft_ms"] == second["total_latency_ms"] == 0


@pytest.mark.parametrize("values", [(0, 1, 1), (1000, 0, 1), (1000, 1, 0.9), (float("inf"), 1, 1)])
def test_invalid_limits(values):
    with pytest.raises(ValueError):
        RequestPacer(*values)
