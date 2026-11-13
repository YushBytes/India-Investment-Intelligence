"""Spacing requests out, and standing well back when told to.

Both the clock and the sleep are injected. That is not a testing
convenience: it is the only way to test a rate limiter without a test suite
that takes minutes. Every test in this repo runs offline and fast, and a
limiter with time.sleep baked in would have broken that.

Two mechanisms, because providers punish two different things:

    A token bucket spaces out normal traffic. `min_interval` is the floor
    between requests; `burst` allows a short run of them.

    A cooldown handles being told off. When a provider answers 429 or 503,
    note_rate_limited puts the whole provider on hold until a deadline,
    respecting Retry-After when it is supplied. While a cooldown is in force
    acquire refuses rather than queues, so a rate limited provider is a fast
    failure the service can fall back from instead of a thread parked for a
    minute.
"""

import time
from typing import Any, Optional


class RateLimited(Exception):
    """The provider is in a cooldown and must not be called yet."""

    def __init__(self, provider, retry_after):
        # type: (str, float) -> None
        Exception.__init__(
            self, "%s is rate limited for another %.1f seconds"
                  % (provider, retry_after))
        self.provider = provider
        self.retry_after = retry_after


class RateLimiter(object):
    """One limiter per provider.

    min_interval of 0 disables spacing, which is what the offline fixture
    transport uses.
    """

    def __init__(self, provider, min_interval=0.0, burst=1, clock=None,
                 sleeper=None):
        # type: (str, float, int, Any, Any) -> None
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self.provider = provider
        self.min_interval = float(min_interval)
        self.burst = int(burst)
        self._clock = clock or time.monotonic
        self._sleep = sleeper if sleeper is not None else time.sleep
        self._allowance = float(burst)
        self._last = None      # type: Optional[float]
        self._cooldown_until = None  # type: Optional[float]
        self.cooldown_reason = ""
        self.waits = 0
        self.total_waited = 0.0
        self.refusals = 0

    # ----------------------------------------------------------- cooldown

    @property
    def cooling_down(self):
        # type: () -> bool
        if self._cooldown_until is None:
            return False
        if self._clock() >= self._cooldown_until:
            self._cooldown_until = None
            self.cooldown_reason = ""
            return False
        return True

    def retry_after(self):
        # type: () -> float
        if not self.cooling_down:
            return 0.0
        return max(self._cooldown_until - self._clock(), 0.0)

    def note_rate_limited(self, retry_after=None, reason="", default=60.0):
        # type: (Any, str, float) -> float
        """Called when the provider says to stop. Returns the hold in seconds.

        A provider that sends Retry-After is obeyed to the second. One that
        does not gets `default`, which is deliberately long: guessing short
        and hammering a provider that just refused is the behaviour this
        exists to prevent.
        """
        hold = default
        if retry_after is not None:
            try:
                hold = max(float(retry_after), 0.0)
            except (TypeError, ValueError):
                hold = default
        self._cooldown_until = self._clock() + hold
        self.cooldown_reason = reason or "the provider asked for a pause"
        return hold

    def clear_cooldown(self):
        # type: () -> None
        self._cooldown_until = None
        self.cooldown_reason = ""

    # ------------------------------------------------------------ acquire

    def acquire(self, wait=True):
        # type: (bool) -> float
        """Take one slot. Returns the seconds waited.

        Raises RateLimited while a cooldown is in force, or when wait is
        false and a slot is not free.
        """
        if self.cooling_down:
            self.refusals += 1
            raise RateLimited(self.provider, self.retry_after())
        if self.min_interval <= 0:
            self._last = self._clock()
            return 0.0

        now = self._clock()
        if self._last is not None:
            self._allowance = min(
                float(self.burst),
                self._allowance + (now - self._last) / self.min_interval)
        self._last = now

        if self._allowance >= 1.0:
            self._allowance -= 1.0
            return 0.0

        needed = (1.0 - self._allowance) * self.min_interval
        if not wait:
            self.refusals += 1
            raise RateLimited(self.provider, needed)
        self._sleep(needed)
        self.waits += 1
        self.total_waited += needed
        self._last = self._clock()
        self._allowance = 0.0
        return needed

    def stats(self):
        # type: () -> dict
        return {"provider": self.provider, "min_interval": self.min_interval,
                "burst": self.burst, "waits": self.waits,
                "total_waited": round(self.total_waited, 3),
                "refusals": self.refusals,
                "cooling_down": self.cooling_down,
                "retry_after": round(self.retry_after(), 3),
                "cooldown_reason": self.cooldown_reason}

    def __repr__(self):
        return "RateLimiter(%s, %.2fs, burst %d%s)" % (
            self.provider, self.min_interval, self.burst,
            ", cooling down" if self.cooling_down else "")
