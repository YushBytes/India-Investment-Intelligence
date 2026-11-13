"""Which failures are worth trying again, and which are just the answer.

The distinction that matters: a 404 for SBIN.NS means the symbol is wrong,
and retrying it three times with backoff turns one wrong answer into three
requests and the same wrong answer. Checked against the live endpoint,
ZZZZNOPE.NS answers 404 with a JSON body reading "No data found, symbol may
be delisted". That is a final answer, so it is classified NOT_FOUND and
never retried.

    RETRYABLE   a timeout, a reset connection, a 500, a 502, a 503, a 504.
                Something that may work in a moment.
    RATE_LIMITED a 429, or a 503 carrying Retry-After. Retried, but only
                after telling the limiter to hold the whole provider back.
    NOT_FOUND   a 404 or a 410. The symbol or the file does not exist.
                Never retried.
    FATAL       a 400, a 401, a 403, a 422. The request is wrong, or access
                is refused. Retrying cannot fix either, and hammering a 403
                is exactly how an unofficial endpoint gets blocked harder.

Backoff is exponential with jitter, and the sleep is injected, so the tests
exercise the real delay arithmetic without waiting for it.
"""

import random
import time
from typing import Any, Callable, List, Optional

RETRYABLE = "retryable"
RATE_LIMITED = "rate_limited"
NOT_FOUND = "not_found"
FATAL = "fatal"

_RETRYABLE_STATUS = (408, 425, 500, 502, 503, 504, 507, 509)
_FATAL_STATUS = (400, 401, 402, 403, 405, 406, 409, 411, 413, 414, 415, 422,
                 451)


class RetryExhausted(Exception):
    """Every attempt failed. Carries the attempt history."""

    def __init__(self, message, attempts, last_error=None):
        # type: (str, List[dict], Optional[BaseException]) -> None
        Exception.__init__(self, message)
        self.attempts = attempts
        self.last_error = last_error


def classify_status(status):
    # type: (Optional[int]) -> str
    """Map an HTTP status onto a retry class."""
    if status is None:
        return RETRYABLE
    status = int(status)
    if status == 429:
        return RATE_LIMITED
    if status in (404, 410):
        return NOT_FOUND
    if status in _FATAL_STATUS:
        return FATAL
    if status in _RETRYABLE_STATUS:
        return RETRYABLE
    if 200 <= status < 300:
        return ""
    if 300 <= status < 400:
        return FATAL
    return RETRYABLE


class RetryPolicy(object):
    """How many times, how long between, and what counts as worth repeating."""

    def __init__(self, attempts=3, base_delay=0.5, max_delay=20.0,
                 jitter=0.25, sleeper=None, rng=None):
        # type: (int, float, float, float, Any, Any) -> None
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self.attempts = int(attempts)
        self.base_delay = float(base_delay)
        self.max_delay = float(max_delay)
        self.jitter = float(jitter)
        self._sleep = sleeper if sleeper is not None else time.sleep
        self._rng = rng or random.Random(0)

    def delay_for(self, attempt, retry_after=None):
        # type: (int, Any) -> float
        """Delay before `attempt` (1 based). Retry-After wins when supplied."""
        if retry_after is not None:
            try:
                return min(max(float(retry_after), 0.0), self.max_delay)
            except (TypeError, ValueError):
                pass
        raw = self.base_delay * (2 ** max(attempt - 1, 0))
        capped = min(raw, self.max_delay)
        if self.jitter:
            spread = capped * self.jitter
            capped = capped + self._rng.uniform(-spread, spread)
        return max(capped, 0.0)

    def run(self, call, on_rate_limited=None, describe="request"):
        # type: (Callable[[], Any], Any, str) -> Any
        """Call `call` until it works or there is nothing left to try.

        `call` must raise an exception carrying `retry_class`, and optionally
        `retry_after`, for this to classify it. Anything else is treated as
        retryable only if it is a timeout or a connection level error, which
        is what providers.transport tags.
        """
        history = []  # type: List[dict]
        last = None   # type: Optional[BaseException]
        for attempt in range(1, self.attempts + 1):
            try:
                result = call()
                if history:
                    history.append({"attempt": attempt, "outcome": "succeeded"})
                return result
            except Exception as exc:
                last = exc
                kind = getattr(exc, "retry_class", FATAL)
                retry_after = getattr(exc, "retry_after", None)
                history.append({"attempt": attempt, "outcome": kind,
                                "error": _safe(exc)})
                if kind == RATE_LIMITED and on_rate_limited is not None:
                    on_rate_limited(retry_after)
                if kind in (NOT_FOUND, FATAL):
                    # Final answers. Raised as they are, so the caller sees
                    # the provider's own error rather than a retry wrapper.
                    raise
                if attempt >= self.attempts:
                    break
                self._sleep(self.delay_for(attempt, retry_after))
        raise RetryExhausted(
            "%s failed after %d attempt(s): %s"
            % (describe, self.attempts, _safe(last) if last else "no error recorded"),
            history, last)


def _safe(exc):
    # type: (Optional[BaseException]) -> str
    """An error string with no request headers in it.

    Transport exceptions are built without headers in the first place; this
    is the second line of defence, since a retry history is often logged.
    """
    if exc is None:
        return ""
    text = str(exc)
    for marker in ("Authorization", "Cookie", "api_key", "apikey", "token="):
        if marker.lower() in text.lower():
            return "%s (message withheld: it mentioned %s)" % (
                type(exc).__name__, marker)
    return "%s: %s" % (type(exc).__name__, text[:300])
