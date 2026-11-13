"""The normalised shapes every provider must answer in.

One model per thing, and every one of them carries its own provenance. A
price that has travelled from a provider into this system cannot be held
without saying which provider, which exchange, when it was retrieved, what
the source said its own timestamp was, and how fresh that makes it. There is
no constructor here that lets a caller hold a bare number and call it a
market price.

Three decisions worth stating.

Prices are Money, from phase 2, so a figure carries INR with it and cannot be
added to a dollar. Money stores paise as an integer, which also disposes of
the float noise in provider payloads: Yahoo returns 1077.949951171875 for a
close the exchange published as 1077.95.

Timestamps are aware datetimes in IST. A naive datetime is refused rather
than assumed, the same rule phase 2 set in calendar_in.now_ist.

Instrument type is allowed to be UNKNOWN_TYPE, and that is used. No verified
provider classifies Indian ETFs: Yahoo answers instrumentType EQUITY for
NIFTYBEES.NS and GOLDBEES.NS, both of which are ETFs, and the exchange
bhavcopy carries only FinInstrmTp STK for every equity row. Those instruments
are tradable and quotable through this system; their type is not knowable
from the sources, so it is not invented.
"""

import datetime
from typing import Any, List, Optional

from .. import calendar_in as cal
from ..money import INR, Money
from ..symbols import Exchange, IndexSymbol, Symbol

# ------------------------------------------------------------------ intervals

DAY_1 = "1d"
MIN_1 = "1m"
MIN_5 = "5m"
MIN_15 = "15m"
MIN_30 = "30m"
MIN_60 = "60m"

INTRADAY_INTERVALS = (MIN_1, MIN_5, MIN_15, MIN_30, MIN_60)
ALL_INTERVALS = (DAY_1,) + INTRADAY_INTERVALS

INTERVAL_SECONDS = {
    MIN_1: 60, MIN_5: 300, MIN_15: 900, MIN_30: 1800, MIN_60: 3600,
    DAY_1: 86400,
}


class ModelError(ValueError):
    """A record could not be built from what the provider sent."""


def is_intraday(interval):
    # type: (str) -> bool
    return interval in INTRADAY_INTERVALS


def check_interval(interval):
    # type: (str) -> str
    if interval not in ALL_INTERVALS:
        raise ModelError("unknown interval %r, expected one of %s"
                         % (interval, ", ".join(ALL_INTERVALS)))
    return interval


# ----------------------------------------------------------- instrument types

EQUITY = "equity"
ETF = "etf"
INDEX = "index"
# Used, not a placeholder. See the module docstring.
UNKNOWN_TYPE = "unknown"

INSTRUMENT_TYPES = (EQUITY, ETF, INDEX, UNKNOWN_TYPE)

# --------------------------------------------------------------- data kinds

QUOTE = "quote"
BARS = "bars"
INDEX_QUOTE = "index_quote"
DATA_KINDS = (QUOTE, BARS, INDEX_QUOTE)

# --------------------------------------------------------- adjustment status

UNADJUSTED = "unadjusted"
ADJUSTED = "adjusted"
ADJUSTMENT_UNKNOWN = "adjustment_unknown"
ADJUSTMENT_STATES = (UNADJUSTED, ADJUSTED, ADJUSTMENT_UNKNOWN)


def _aware_ist(moment, field):
    # type: (Optional[datetime.datetime], str) -> Optional[datetime.datetime]
    if moment is None:
        return None
    if not isinstance(moment, datetime.datetime):
        raise ModelError("%s must be a datetime, got %r" % (field, moment))
    if moment.tzinfo is None:
        raise ModelError(
            "%s is naive. Pass an aware datetime, otherwise the zone is a "
            "guess and an IST figure can be read as UTC" % field)
    return cal.to_ist(moment)


def _money(value, field, currency=INR, required=False):
    # type: (Any, str, str, bool) -> Optional[Money]
    if value is None:
        if required:
            raise ModelError("%s is required" % field)
        return None
    if isinstance(value, Money):
        if value.currency != currency:
            raise ModelError("%s is in %s but the record is in %s"
                             % (field, value.currency, currency))
        return value
    return Money.rupees(value, currency=currency)


def _price_out(amount):
    # type: (Optional[Money]) -> Optional[str]
    """Prices leave as exact decimal strings, never as floats.

    Serialising to a float would put the rounding this model removed straight
    back into the file.
    """
    return None if amount is None else str(amount.as_rupees)


def _canonical(symbol):
    # type: (Any) -> str
    if isinstance(symbol, Symbol):
        return str(symbol)
    if isinstance(symbol, IndexSymbol):
        return symbol.symbol
    raise ModelError(
        "symbol must be an indiacore.symbols Symbol or IndexSymbol, got %r. "
        "A bare string is refused because it does not say which exchange"
        % (symbol,))


class Provenance(object):
    """Where one record came from, and how old it is.

    Carried by every Quote and every Bar. The freshness values are phase 2's
    (indiacore.provenance), so a record and a DataResult speak one language.
    """

    __slots__ = ("provider", "source", "retrieved_at", "source_time",
                 "freshness", "freshness_reason", "cached", "cache_age_seconds",
                 "official")

    def __init__(self, provider, source, retrieved_at, source_time=None,
                 freshness="unknown", freshness_reason="", cached=False,
                 cache_age_seconds=None, official=False):
        # type: (str, str, datetime.datetime, Optional[datetime.datetime], str, str, bool, Optional[float], bool) -> None
        if not provider:
            raise ModelError("every record must name the provider that served it")
        if not source:
            raise ModelError("every record must name its source")
        self.provider = provider
        self.source = source
        self.retrieved_at = _aware_ist(retrieved_at, "retrieved_at")
        if self.retrieved_at is None:
            raise ModelError("retrieved_at is required")
        self.source_time = _aware_ist(source_time, "source_time")
        self.freshness = freshness
        self.freshness_reason = freshness_reason
        self.cached = bool(cached)
        self.cache_age_seconds = cache_age_seconds
        self.official = bool(official)

    def but(self, **kw):
        # type: (**Any) -> Provenance
        """A copy with fields replaced. Provenance is never edited in place."""
        fields = dict((name, getattr(self, name)) for name in self.__slots__)
        fields.update(kw)
        return Provenance(**fields)

    def to_dict(self):
        # type: () -> dict
        return {
            "provider": self.provider,
            "source": self.source,
            "official": self.official,
            "retrieved_at": self.retrieved_at.isoformat(),
            "source_time": self.source_time.isoformat() if self.source_time else None,
            "freshness": self.freshness,
            "freshness_reason": self.freshness_reason,
            "cached": self.cached,
            "cache_age_seconds": self.cache_age_seconds,
        }

    def __repr__(self):
        return "Provenance(%s, %s%s)" % (
            self.provider, self.freshness, ", cached" if self.cached else "")


class Quality(object):
    """A validation verdict and the reasons behind it.

    Built by marketdata.validation. Held on the record, so a figure and the
    opinion of it cannot be separated by passing one around without the other.
    """

    __slots__ = ("verdict", "findings")

    def __init__(self, verdict, findings=None):
        # type: (str, Optional[List[Any]]) -> None
        self.verdict = verdict
        self.findings = list(findings or [])

    @property
    def ok(self):
        # type: () -> bool
        """VALID or WARNING. Anything else must not reach a decision."""
        return self.verdict in ("valid", "warning")

    def codes(self):
        # type: () -> List[str]
        return [f.code for f in self.findings]

    def to_dict(self):
        # type: () -> dict
        return {"verdict": self.verdict,
                "findings": [f.to_dict() for f in self.findings]}

    def __repr__(self):
        return "Quality(%s, %d findings)" % (self.verdict, len(self.findings))


class Quote(object):
    """One instrument's latest figures from one provider at one moment."""

    __slots__ = ("symbol", "exchange", "instrument_type", "last_price",
                 "previous_close", "open", "high", "low", "volume", "currency",
                 "market_status", "provenance", "quality", "notes",
                 "previous_close_derived")

    def __init__(self, symbol, last_price, provenance, exchange=None,
                 instrument_type=UNKNOWN_TYPE, previous_close=None, open=None,
                 high=None, low=None, volume=None, currency=INR,
                 market_status="unknown", quality=None, notes=None,
                 previous_close_derived=False):
        # type: (Any, Any, Provenance, Any, str, Any, Any, Any, Any, Optional[int], str, str, Optional[Quality], Optional[List[str]], bool) -> None
        self.symbol = _canonical(symbol)
        if exchange is None:
            exchange = getattr(symbol, "exchange", None)
        self.exchange = exchange.code if isinstance(exchange, Exchange) else exchange
        if instrument_type not in INSTRUMENT_TYPES:
            raise ModelError("unknown instrument type %r" % (instrument_type,))
        self.instrument_type = instrument_type
        self.currency = currency
        self.last_price = _money(last_price, "last_price", currency, required=True)
        self.previous_close = _money(previous_close, "previous_close", currency)
        self.open = _money(open, "open", currency)
        self.high = _money(high, "high", currency)
        self.low = _money(low, "low", currency)
        if volume is not None:
            volume = int(volume)
        self.volume = volume
        self.market_status = market_status
        if not isinstance(provenance, Provenance):
            raise ModelError("a quote needs a Provenance, got %r" % (provenance,))
        self.provenance = provenance
        self.quality = quality
        self.notes = list(notes or [])
        self.previous_close_derived = bool(previous_close_derived)

    @property
    def change(self):
        # type: () -> Optional[Money]
        if self.previous_close is None:
            return None
        return self.last_price - self.previous_close

    @property
    def change_pct(self):
        # type: () -> Optional[float]
        if self.previous_close is None or not self.previous_close.as_paise:
            return None
        return round(100.0 * (self.last_price.as_paise - self.previous_close.as_paise)
                     / self.previous_close.as_paise, 4)

    def with_quality(self, quality):
        # type: (Quality) -> Quote
        self.quality = quality
        return self

    def to_dict(self):
        # type: () -> dict
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "instrument_type": self.instrument_type,
            "last_price": _price_out(self.last_price),
            "previous_close": _price_out(self.previous_close),
            "previous_close_derived": self.previous_close_derived,
            "open": _price_out(self.open),
            "high": _price_out(self.high),
            "low": _price_out(self.low),
            "volume": self.volume,
            "change_pct": self.change_pct,
            "currency": self.currency,
            "market_status": self.market_status,
            "provenance": self.provenance.to_dict(),
            "quality": self.quality.to_dict() if self.quality else None,
            "notes": list(self.notes),
        }

    def __repr__(self):
        return "Quote(%s, %s, %s)" % (self.symbol, self.last_price.format(),
                                      self.provenance.freshness)


class Bar(object):
    """One OHLCV bar for one instrument at one interval."""

    __slots__ = ("symbol", "exchange", "interval", "start", "session_date",
                 "open", "high", "low", "close", "volume", "adj_close",
                 "currency", "provenance", "adjustment", "quality")

    def __init__(self, symbol, interval, start, open, high, low, close,
                 provenance, volume=None, adj_close=None, exchange=None,
                 currency=INR, adjustment=ADJUSTMENT_UNKNOWN, session_date=None,
                 quality=None):
        # type: (Any, str, datetime.datetime, Any, Any, Any, Any, Provenance, Optional[int], Any, Any, str, str, Optional[datetime.date], Optional[Quality]) -> None
        self.symbol = _canonical(symbol)
        if exchange is None:
            exchange = getattr(symbol, "exchange", None)
        self.exchange = exchange.code if isinstance(exchange, Exchange) else exchange
        self.interval = check_interval(interval)
        self.start = _aware_ist(start, "start")
        if self.start is None:
            raise ModelError("a bar needs a start time")
        self.session_date = session_date or self.start.date()
        self.currency = currency
        self.open = _money(open, "open", currency, required=True)
        self.high = _money(high, "high", currency, required=True)
        self.low = _money(low, "low", currency, required=True)
        self.close = _money(close, "close", currency, required=True)
        self.adj_close = _money(adj_close, "adj_close", currency)
        if volume is not None:
            volume = int(volume)
        self.volume = volume
        if adjustment not in ADJUSTMENT_STATES:
            raise ModelError("unknown adjustment state %r" % (adjustment,))
        self.adjustment = adjustment
        if not isinstance(provenance, Provenance):
            raise ModelError("a bar needs a Provenance, got %r" % (provenance,))
        self.provenance = provenance
        self.quality = quality

    @property
    def end(self):
        # type: () -> datetime.datetime
        return self.start + datetime.timedelta(
            seconds=INTERVAL_SECONDS[self.interval])

    def to_dict(self):
        # type: () -> dict
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "interval": self.interval,
            "start": self.start.isoformat(),
            "session_date": self.session_date.isoformat(),
            "open": _price_out(self.open),
            "high": _price_out(self.high),
            "low": _price_out(self.low),
            "close": _price_out(self.close),
            "adj_close": _price_out(self.adj_close),
            "volume": self.volume,
            "currency": self.currency,
            "adjustment": self.adjustment,
            "provenance": self.provenance.to_dict(),
            "quality": self.quality.to_dict() if self.quality else None,
        }

    def __repr__(self):
        return "Bar(%s %s %s c=%s)" % (self.symbol, self.interval,
                                       self.start.isoformat(),
                                       self.close.format(symbol=False))


class Capabilities(object):
    """What a provider actually does, declared rather than assumed.

    `operational` is the field that keeps this honest. An adapter can exist,
    be registered and describe its endpoint while answering no requests at
    all, which is the state of the two exchange quote APIs: both answer 403 to
    a plain request and need a primed browser session this system will not
    fake.
    """

    __slots__ = ("provider", "exchanges", "instrument_types", "kinds",
                 "intervals", "official", "operational", "realtime",
                 "declared_delay_seconds", "max_history_days", "auth_required",
                 "notes", "terms_url")

    def __init__(self, provider, exchanges=(), instrument_types=(), kinds=(),
                 intervals=(), official=False, operational=True,
                 realtime=False, declared_delay_seconds=None,
                 max_history_days=None, auth_required=False, notes="",
                 terms_url=""):
        self.provider = provider
        self.exchanges = tuple(exchanges)
        self.instrument_types = tuple(instrument_types)
        self.kinds = tuple(kinds)
        self.intervals = tuple(intervals)
        self.official = bool(official)
        self.operational = bool(operational)
        # Never set true for a provider that does not contractually promise
        # real time delivery. quality.py refuses to classify anything LIVE
        # unless this is set.
        self.realtime = bool(realtime)
        self.declared_delay_seconds = declared_delay_seconds
        self.max_history_days = max_history_days
        self.auth_required = bool(auth_required)
        self.notes = notes
        self.terms_url = terms_url

    def supports(self, kind, exchange=None, interval=None):
        # type: (str, Any, Optional[str]) -> bool
        if not self.operational or kind not in self.kinds:
            return False
        if exchange is not None:
            code = exchange.code if isinstance(exchange, Exchange) else exchange
            if code not in self.exchanges:
                return False
        if interval is not None and interval not in self.intervals:
            return False
        return True

    def to_dict(self):
        # type: () -> dict
        return {
            "provider": self.provider,
            "exchanges": list(self.exchanges),
            "instrument_types": list(self.instrument_types),
            "kinds": list(self.kinds),
            "intervals": list(self.intervals),
            "official": self.official,
            "operational": self.operational,
            "realtime": self.realtime,
            "declared_delay_seconds": self.declared_delay_seconds,
            "max_history_days": self.max_history_days,
            "auth_required": self.auth_required,
            "terms_url": self.terms_url,
            "notes": self.notes,
        }

    def __repr__(self):
        return "Capabilities(%s, %s)" % (
            self.provider,
            "operational" if self.operational else "not operational")


class ProviderStatus(object):
    """Health of one provider, as observed by this process."""

    __slots__ = ("provider", "available", "reason", "last_success",
                 "last_failure", "failure_reason", "rate_limited",
                 "retry_after_seconds", "capabilities", "requests", "failures")

    def __init__(self, provider, available, reason="", last_success=None,
                 last_failure=None, failure_reason="", rate_limited=False,
                 retry_after_seconds=None, capabilities=None, requests=0,
                 failures=0):
        self.provider = provider
        self.available = bool(available)
        self.reason = reason
        self.last_success = _aware_ist(last_success, "last_success")
        self.last_failure = _aware_ist(last_failure, "last_failure")
        self.failure_reason = failure_reason
        self.rate_limited = bool(rate_limited)
        self.retry_after_seconds = retry_after_seconds
        self.capabilities = capabilities
        self.requests = int(requests)
        self.failures = int(failures)

    def to_dict(self):
        # type: () -> dict
        return {
            "provider": self.provider,
            "available": self.available,
            "reason": self.reason,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_failure": self.last_failure.isoformat() if self.last_failure else None,
            "failure_reason": self.failure_reason,
            "rate_limited": self.rate_limited,
            "retry_after_seconds": self.retry_after_seconds,
            "requests": self.requests,
            "failures": self.failures,
            "capabilities": self.capabilities.to_dict() if self.capabilities else None,
        }

    def __repr__(self):
        return "ProviderStatus(%s, %s)" % (
            self.provider, "available" if self.available else "unavailable")
