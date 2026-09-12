import math
import statistics


def percentile(values, p):
    values = sorted(v for v in values if v is not None and math.isfinite(v))
    if not values:
        return None
    position = (len(values) - 1) * p
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def describe(values):
    values = [v for v in values if v is not None and math.isfinite(v)]
    if not values:
        return {k: None for k in ("mean", "p50", "p95", "stdev", "min", "max")}
    return {
        "mean": statistics.mean(values),
        "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
        "stdev": statistics.stdev(values) if len(values) > 1 else None,
        "min": min(values),
        "max": max(values),
    }


def total_known(values):
    values = list(values)
    return sum(values) if values and all(v is not None for v in values) else None
