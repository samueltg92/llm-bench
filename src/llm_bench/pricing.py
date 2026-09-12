from dataclasses import asdict

from .providers.base import Usage


def calculate(usage: Usage, price: dict | None) -> dict:
    if not price or price.get("input") is None or price.get("output") is None:
        return {"cost_usd": None, "cost_status": "unpriced"}
    if usage.prompt_tokens is None or usage.completion_tokens is None:
        return {"cost_usd": None, "cost_status": "unknown_usage"}
    p, c = usage.prompt_tokens, usage.cached_prompt_tokens
    if min(p, c, usage.completion_tokens, usage.reasoning_tokens) < 0 or c > p:
        raise ValueError("Invalid token accounting")
    if any(price.get(k) is not None and price[k] < 0 for k in ("input", "output", "cached_input")):
        raise ValueError("Negative price")
    out = usage.completion_tokens + (0 if usage.reasoning_included else usage.reasoning_tokens)
    cached_rate = price.get("cached_input")
    cost = (
        (p - c) * price["input"]
        + c * (price["input"] if cached_rate is None else cached_rate)
        + out * price["output"]
    ) / 1e6
    verified = price.get("last_verified") not in (None, "PENDING", "PENDIENTE")
    status = "verified" if verified else "unverified"
    if usage.source != "provider" and verified:
        status = usage.source
    return {"cost_usd": cost, "cost_status": status}


def usage_record(usage: Usage) -> dict:
    fields = asdict(usage)
    fields["usage_source"] = fields.pop("source")
    return fields
