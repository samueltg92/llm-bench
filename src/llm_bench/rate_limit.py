"""Conservative local pacing; account-wide traffic remains provider-controlled."""

import math
import time
from collections import deque


class RateLimitCapacityError(ValueError):
    pass


class RequestPacer:
    def __init__(self, tokens_per_minute, requests_per_second, input_margin=1.2,
                 include_output_tokens=True):
        if (
            not all(math.isfinite(v) for v in (tokens_per_minute, requests_per_second, input_margin))
            or tokens_per_minute <= 0
            or requests_per_second <= 0
            or input_margin < 1
        ):
            raise ValueError("Invalid request pacing limits")
        self.capacity = tokens_per_minute
        self.interval = 1 / requests_per_second
        self.margin = input_margin
        self.include_output = include_output_tokens
        self.window_s = 61.0
        self.requests = deque()
        self.last_start = None

    def acquire(self, input_tokens, output_tokens):
        # Reserve all output even if generation later stops early. Never discount cache.
        required = math.ceil(input_tokens * self.margin) + (
            output_tokens if self.include_output else 0
        )
        if required > self.capacity:
            raise RateLimitCapacityError("Complete request exceeds configured token throughput")
        while True:
            now = time.monotonic()
            while self.requests and now - self.requests[0][0] >= self.window_s:
                self.requests.popleft()
            wait = (
                max(0, self.interval - (now - self.last_start))
                if self.last_start is not None else 0
            )
            used = sum(tokens for _, tokens in self.requests)
            if used + required > self.capacity:
                wait = max(wait, self.window_s - (now - self.requests[0][0]))
            if wait <= 0:
                self.requests.append((now, required))
                self.last_start = now
                return
            time.sleep(min(wait, 30))
