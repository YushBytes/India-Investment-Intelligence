"""What a provider is, and what it is not allowed to pretend.

The interface is deliberately small: three data methods and a status method.
Every one of them either answers with normalised records or raises. None of
them returns None, an empty list for a failure, or a tuple whose second
element has to be checked. The phase 2 lesson stands: an empty answer and a
broken feed must not share a representation.

Two rules every subclass follows:

    A capability that is not declared raises UnsupportedRequest. Asking the
    NSE bhavcopy provider for a live quote is a programming question with a
    clear answer, not something to approximate with yesterday's close.

    A provider that cannot work says so in its own status. `operational` on
    the capabilities is false for the two exchange quote APIs, and every data
    method on them raises ProviderUnavailable with the reason. The adapters
    exist so the shape of the integration is written down. They do not exist
    to look finished.
"""

import datetime
from typing import Any, List, Optional

from .. import calendar_in as cal
from .. import rate_limit, retry as retry_mod
from ..marketdata import models as m


class ProviderError(Exception):
    """Base class for a provider failing to answer."""

    retry_class = retry_mod.FATAL


class ProviderUnavailable(ProviderError):
    """The provider cannot serve requests at all."""

    retry_class = retry_mod.RETRYABLE


class UnsupportedRequest(ProviderError):
    """The provider does not do this, and will not approximate it."""

    retry_class = retry_mod.FATAL


class SymbolNotFound(ProviderError):
    """The provider has no such instrument. Never retried."""

    retry_class = retry_mod.NOT_FOUND


class BadProviderData(ProviderError):
    """The response arrived and could not be trusted."""

    retry_class = retry_mod.FATAL


class EmptyButValid(object):
    """A real, trustworthy nothing.

    Returned by get_historical_bars when the provider answered correctly and
    the range genuinely holds no session, which is what the Yahoo chart
    endpoint does for a window containing only the 2026 Muhurat Sunday: HTTP
    200, meta present, no timestamp array, no error. Distinct from every
    failure above because it is not an exception.
    """

    __slots__ = ("provider", "reason", "notes")

    def __init__(self, provider, reason, notes=None):
        # type: (str, str, Optional[List[str]]) -> None
        self.provider = provider
        self.reason = reason
        self.notes = list(notes or [])

    def __repr__(self):
        return "EmptyButValid(%s, %r)" % (self.provider, self.reason)


class Provider(object):
    """Base class. Subclasses declare capabilities and implement what they do."""

    name = "provider"

    def __init__(self, transport, capabilities, limiter=None, policy=None,
                 clock=None):
        # type: (Any, m.Capabilities, Any, Any, Any) -> None
        self.transport = transport
        self.capabilities = capabilities
        self.limiter = limiter or rate_limit.RateLimiter(self.name)
        self.policy = policy or retry_mod.RetryPolicy()
        self._clock = clock or (lambda: datetime.datetime.now(
            datetime.timezone.utc))
        self.requests = 0
        self.failures = 0
        self.last_success = None   # type: Optional[datetime.datetime]
        self.last_failure = None   # type: Optional[datetime.datetime]
        self.last_failure_reason = ""

    # ------------------------------------------------------------ plumbing

    def now(self):
        # type: () -> datetime.datetime
        return cal.to_ist(self._clock())

    def supports(self, kind, exchange=None, interval=None):
        # type: (str, Any, Optional[str]) -> bool
        return self.capabilities.supports(kind, exchange, interval)

    def require(self, kind, exchange=None, interval=None):
        # type: (str, Any, Optional[str]) -> None
        """Raise UnsupportedRequest unless this is declared."""
        if not self.capabilities.operational:
            raise ProviderUnavailable(
                "%s is not operational: %s" % (self.name,
                                               self.capabilities.notes))
        if kind not in self.capabilities.kinds:
            raise UnsupportedRequest(
                "%s does not serve %s. It serves: %s"
                % (self.name, kind, ", ".join(self.capabilities.kinds) or "nothing"))
        if exchange is not None:
            code = getattr(exchange, "code", exchange)
            if code not in self.capabilities.exchanges:
                raise UnsupportedRequest(
                    "%s does not cover %s. It covers: %s"
                    % (self.name, code, ", ".join(self.capabilities.exchanges)))
        if interval is not None and interval not in self.capabilities.intervals:
            raise UnsupportedRequest(
                "%s does not serve %s bars. It serves: %s"
                % (self.name, interval,
                   ", ".join(self.capabilities.intervals) or "no bars"))

    def provenance(self, source, source_time=None, retrieved_at=None):
        # type: (str, Any, Any) -> m.Provenance
        return m.Provenance(
            provider=self.name, source=source,
            retrieved_at=retrieved_at or self.now(),
            source_time=source_time,
            official=self.capabilities.official)

    def fetch(self, call, describe):
        # type: (Any, str) -> Any
        """Run one call through the limiter and the retry policy."""
        self.requests += 1
        try:
            self.limiter.acquire()
        except rate_limit.RateLimited as exc:
            self.failures += 1
            self.last_failure = self.now()
            self.last_failure_reason = str(exc)
            raise ProviderUnavailable(str(exc))
        try:
            result = self.policy.run(
                call, on_rate_limited=self._on_rate_limited, describe=describe)
        except Exception as exc:
            self.failures += 1
            self.last_failure = self.now()
            self.last_failure_reason = retry_mod._safe(exc)
            raise
        self.last_success = self.now()
        return result

    def _on_rate_limited(self, retry_after):
        # type: (Any) -> None
        self.limiter.note_rate_limited(
            retry_after, reason="%s answered with a rate limit" % self.name)

    # -------------------------------------------------------------- the API

    def get_quote(self, symbol):
        # type: (Any) -> m.Quote
        """The latest figures for one instrument."""
        raise UnsupportedRequest("%s does not serve quotes" % self.name)

    def get_historical_bars(self, symbol, interval=m.DAY_1, start=None,
                            end=None):
        # type: (Any, str, Any, Any) -> Any
        """A list of Bar, or an EmptyButValid when the range holds no session."""
        raise UnsupportedRequest("%s does not serve bars" % self.name)

    def get_index_quote(self, index_symbol):
        # type: (Any) -> m.Quote
        """The latest level of one index."""
        raise UnsupportedRequest("%s does not serve index quotes" % self.name)

    def get_provider_status(self):
        # type: () -> m.ProviderStatus
        """Health as observed by this process. Never makes a request."""
        cooling = self.limiter.cooling_down
        available = self.capabilities.operational and not cooling
        reason = ""
        if not self.capabilities.operational:
            reason = self.capabilities.notes or "declared not operational"
        elif cooling:
            reason = "in a rate limit cooldown: %s" % self.limiter.cooldown_reason
        return m.ProviderStatus(
            provider=self.name, available=available, reason=reason,
            last_success=self.last_success, last_failure=self.last_failure,
            failure_reason=self.last_failure_reason, rate_limited=cooling,
            retry_after_seconds=round(self.limiter.retry_after(), 3) or None,
            capabilities=self.capabilities, requests=self.requests,
            failures=self.failures)

    def __repr__(self):
        return "%s(%s)" % (type(self).__name__, self.name)


class NotOperationalProvider(Provider):
    """An adapter whose access could not be obtained through a permitted route.

    Every data method raises ProviderUnavailable carrying the observed
    evidence. It is registered so that `registry.matrix()` lists it as
    unsupported, which is the honest form of "we looked at this one".
    """

    def __init__(self, capabilities, evidence, transport=None, clock=None):
        # type: (m.Capabilities, str, Any, Any) -> None
        Provider.__init__(self, transport, capabilities, clock=clock)
        self.evidence = evidence

    def _refuse(self, what):
        # type: (str) -> None
        raise ProviderUnavailable(
            "%s cannot serve %s. %s" % (self.name, what, self.evidence))

    def get_quote(self, symbol):
        self._refuse("quotes")

    def get_historical_bars(self, symbol, interval=m.DAY_1, start=None, end=None):
        self._refuse("bars")

    def get_index_quote(self, index_symbol):
        self._refuse("index quotes")
