"""
Generic async circuit breaker. Used to stop hammering a failing external
service (Jina Reader) and instead route immediately to a fallback for a
cooldown window.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(
        self,
        fail_threshold: int = 5,
        reset_timeout: float = 60.0,
        ignored_exceptions: tuple[type[Exception], ...] = (),
    ):
        self.fail_threshold = fail_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.state = CircuitState.CLOSED
        self.last_failure: float | None = None
        # Exceptions that mean "this particular input was bad" (dead site,
        # unrenderable page, 404/422) rather than "the upstream service
        # itself is unhealthy". Counting those toward the breaker means a
        # handful of companies with broken URLs - inevitable in any batch of
        # thousands - permanently trips the circuit OPEN even though the
        # service is working perfectly for every other URL, and it can't
        # self-heal because the single half-open probe that runs every
        # reset_timeout has a good chance of ALSO landing on a bad URL.
        self.ignored_exceptions = ignored_exceptions

    def _maybe_half_open(self) -> None:
        if self.state == CircuitState.OPEN and self.last_failure is not None:
            if time.monotonic() - self.last_failure > self.reset_timeout:
                self.state = CircuitState.HALF_OPEN

    async def call(
        self,
        func: Callable[..., Awaitable[T]],
        *args,
        fallback: Callable[..., Awaitable[T]] | None = None,
        **kwargs,
    ) -> T:
        self._maybe_half_open()

        if self.state == CircuitState.OPEN:
            if fallback is None:
                raise RuntimeError("Circuit is OPEN and no fallback was provided.")
            return await fallback(*args, **kwargs)

        try:
            result = await func(*args, **kwargs)
        except self.ignored_exceptions:
            # Bad input, not a service outage - route this one call to the
            # fallback without touching the breaker's health accounting at all.
            if fallback is not None:
                return await fallback(*args, **kwargs)
            raise
        except Exception:
            self.failures += 1
            self.last_failure = time.monotonic()
            if self.failures >= self.fail_threshold:
                self.state = CircuitState.OPEN
            if fallback is not None:
                return await fallback(*args, **kwargs)
            raise
        else:
            self.failures = 0
            self.state = CircuitState.CLOSED
            return result

    def status(self) -> dict:
        return {
            "state": self.state.value,
            "failures": self.failures,
            "reset_in_s": (
                max(0.0, self.reset_timeout - (time.monotonic() - self.last_failure))
                if self.state == CircuitState.OPEN and self.last_failure
                else 0.0
            ),
        }


class ContentUnavailable(Exception):
    """Raised when the primary function couldn't extract usable content for
    THIS specific input (dead site, 404/422, empty render) - as opposed to
    the upstream service itself being down. See `ignored_exceptions` above."""


# Module-level singleton shared by every coroutine in the worker process.
jina_breaker = CircuitBreaker(fail_threshold=5, reset_timeout=60.0, ignored_exceptions=(ContentUnavailable,))
