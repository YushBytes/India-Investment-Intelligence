"""One door for market data, and nine outcomes a caller can tell apart.

Everything a future module should need goes through here: pick a provider,
fetch, normalise, validate, date, classify freshness, cache, answer. The
answer is a MarketDataResult, which wraps phase 2's provenance.DataResult so
that `require_usable()` still means what it meant in phase 2, and adds the
one thing a caller has to be able to see at a glance: which of these
happened.

    FRESH          fetched now, validated, and recent enough to be a price
    CACHED         served from the cache, with its age attached
    DELAYED        fetched now, and the provider does not promise real time
    END_OF_DAY     the last completed session's figures, correct and not a
                   current price
    HISTORICAL     older sessions, correct and not a current price
    EMPTY_VALID    the provider answered properly and the range holds
                   nothing. Not a failure
    PROVIDER_ERROR every provider failed. Not an empty result
    INVALID_DATA   something came back and validation refused it
    STALE          the figures are real and too old for what was asked
    UNKNOWN_AGE    figures arrived and their age could not be established,
                   which is not the same as knowing they are old
    UNSUPPORTED    nothing registered does this

Three rules this module will not bend.

A stale cache entry never silently covers for a failed request. When a fetch
fails, the default is to report the failure. A caller may pass
`allow_stale_cache=True` and then gets the cached record with outcome CACHED,
freshness STALE and a reason that says the live request failed. That is a
decision the caller makes in writing.

Fallback never mixes providers. If NSE's file fails and Yahoo answers, the
result carries Yahoo's rows and Yahoo's provenance, and `fallback_used` says
what happened. Two providers' prices are never combined into one record, and
`attempts` keeps the per provider history.

A record that fails validation is never cached and never returned as data.
"""

import datetime
from typing import Any, List, Optional

from . import calendar_in as cal
from . import market as mkt
from . import provenance as prov
from .marketdata import cache as cache_mod
from .marketdata import models as m
from .marketdata import quality, validation
from .providers import base, registry as registry_mod
from .symbols import BSE, NSE, IndexSymbol, Symbol

FRESH = "fresh"
CACHED = "cached"
DELAYED = "delayed"
END_OF_DAY = "end_of_day"
HISTORICAL = "historical"
EMPTY_VALID = "empty_valid"
PROVIDER_ERROR = "provider_error"
INVALID_DATA = "invalid_data"
STALE = "stale"
UNKNOWN_AGE = "unknown_age"
UNSUPPORTED = "unsupported"

OUTCOMES = (FRESH, CACHED, DELAYED, END_OF_DAY, HISTORICAL, EMPTY_VALID,
            PROVIDER_ERROR, INVALID_DATA, STALE, UNKNOWN_AGE, UNSUPPORTED)

_GOOD = (FRESH, CACHED, DELAYED, END_OF_DAY, HISTORICAL, EMPTY_VALID)


class MarketDataResult(object):
    """What came back, where from, and whether it can be used."""

    __slots__ = ("outcome", "rows", "result", "provider", "attempts",
                 "fallback_used", "fallback_from", "quality", "notes",
                 "cached", "cache_age_seconds", "errors")

    def __init__(self, outcome, result, rows=None, provider=None, attempts=None,
                 fallback_used=False, fallback_from=None, quality=None,
                 notes=None, cached=False, cache_age_seconds=None):
        # type: (str, prov.DataResult, Any, Optional[str], Any, bool, Any, Any, Any, bool, Any) -> None
        if outcome not in OUTCOMES:
            raise ValueError("unknown outcome %r" % (outcome,))
        self.outcome = outcome
        self.result = result
        self.rows = list(rows or [])
        self.provider = provider
        self.attempts = list(attempts or [])
        self.fallback_used = bool(fallback_used)
        self.fallback_from = fallback_from
        self.quality = quality
        self.notes = list(notes or [])
        self.cached = bool(cached)
        self.cache_age_seconds = cache_age_seconds
        self.errors = list(result.errors)

    # ------------------------------------------------------------ verdicts

    @property
    def ok(self):
        # type: () -> bool
        """The request was answered properly. Includes a real empty answer."""
        return self.outcome in _GOOD

    @property
    def usable(self):
        # type: () -> bool
        """Safe to base a decision on. Phase 2's rule, unchanged."""
        return self.ok and self.result.is_usable

    @property
    def is_current_price(self):
        # type: () -> bool
        """Whether these figures may be shown as the price right now."""
        if not self.usable:
            return False
        return quality.is_current_price(self.result.freshness)

    @property
    def first(self):
        # type: () -> Any
        return self.rows[0] if self.rows else None

    def require_usable(self):
        # type: () -> MarketDataResult
        """Raise unless this is data a decision can rest on."""
        if self.outcome == UNSUPPORTED:
            raise base.UnsupportedRequest(
                "; ".join(self.errors) or "unsupported request")
        if self.outcome == INVALID_DATA:
            raise prov.DataError(
                "data arrived and failed validation: %s"
                % "; ".join(self.errors) or "no reason recorded")
        self.result.require_usable()
        return self

    def to_dict(self):
        # type: () -> dict
        return {
            "outcome": self.outcome,
            "provider": self.provider,
            "usable": self.usable,
            "is_current_price": self.is_current_price,
            "cached": self.cached,
            "cache_age_seconds": self.cache_age_seconds,
            "fallback_used": self.fallback_used,
            "fallback_from": self.fallback_from,
            "rows": len(self.rows),
            "quality": self.quality.to_dict() if self.quality else None,
            "provenance": self.result.to_dict(),
            "attempts": list(self.attempts),
            "notes": list(self.notes),
        }

    def __repr__(self):
        return "MarketDataResult(%s, %s, %d rows)" % (
            self.outcome, self.provider or "no provider", len(self.rows))


class MarketDataService(object):
    """The shared entry point. Build one and pass it around."""

    def __init__(self, registry=None, cache=None, rules=None, thresholds=None,
                 clock=None, transport=None, calendar=None):
        # type: (Any, Any, Any, Any, Any, Any, Any) -> None
        self.registry = registry or registry_mod.default_registry(
            transport=transport, clock=clock, rules=rules)
        self.cache = cache if cache is not None else cache_mod.Cache()
        self.rules = rules or quality.DEFAULT_RULES
        self.thresholds = thresholds or validation.DEFAULT_THRESHOLDS
        self._clock = clock or (lambda: datetime.datetime.now(
            datetime.timezone.utc))
        self.markets = {"NSE": mkt.nse_market(), "BSE": mkt.bse_market()}
        self.calendar = calendar

    def now(self):
        # type: () -> datetime.datetime
        return cal.to_ist(self._clock())

    def market_for(self, symbol):
        # type: (Any) -> Any
        if isinstance(symbol, IndexSymbol):
            return self.markets["BSE" if symbol.symbol == "^BSESN" else "NSE"]
        return self.markets[symbol.exchange.code]

    # -------------------------------------------------------------- quotes

    def get_quote(self, symbol, provider=None, allow_cache=True,
                  allow_fallback=True, allow_stale_cache=False):
        # type: (Any, Optional[str], bool, bool, bool) -> MarketDataResult
        """The latest figures for one instrument."""
        if isinstance(symbol, IndexSymbol):
            return self.get_index_quote(symbol, provider, allow_cache,
                                        allow_fallback, allow_stale_cache)
        if not isinstance(symbol, Symbol):
            return self._unsupported(
                "get_quote needs an indiacore.symbols Symbol, got %r. A bare "
                "string does not say which exchange" % (symbol,))
        return self._serve(
            kind=m.QUOTE, symbol=symbol, exchange=symbol.exchange,
            interval=None, provider=provider, allow_cache=allow_cache,
            allow_fallback=allow_fallback, allow_stale_cache=allow_stale_cache,
            call=lambda p: p.get_quote(symbol),
            cache_parts={"ticker": symbol.ticker,
                         "exchange": symbol.exchange.code})

    def get_index_quote(self, index_symbol, provider=None, allow_cache=True,
                        allow_fallback=True, allow_stale_cache=False):
        # type: (Any, Optional[str], bool, bool, bool) -> MarketDataResult
        if not isinstance(index_symbol, IndexSymbol):
            return self._unsupported(
                "get_index_quote needs an IndexSymbol such as "
                "IndexSymbol('^NSEI'), got %r" % (index_symbol,))
        return self._serve(
            kind=m.INDEX_QUOTE, symbol=index_symbol, exchange=None,
            interval=None, provider=provider, allow_cache=allow_cache,
            allow_fallback=allow_fallback, allow_stale_cache=allow_stale_cache,
            call=lambda p: p.get_index_quote(index_symbol),
            cache_parts={"ticker": index_symbol.symbol, "exchange": None})

    # ---------------------------------------------------------------- bars

    def get_daily_bars(self, symbol, start=None, end=None, provider=None,
                       allow_cache=True, allow_fallback=True,
                       allow_stale_cache=False):
        # type: (Any, Any, Any, Optional[str], bool, bool, bool) -> MarketDataResult
        return self.get_bars(symbol, m.DAY_1, start, end, provider,
                             allow_cache, allow_fallback, allow_stale_cache)

    def get_bars(self, symbol, interval=m.DAY_1, start=None, end=None,
                 provider=None, allow_cache=True, allow_fallback=True,
                 allow_stale_cache=False):
        # type: (Any, str, Any, Any, Optional[str], bool, bool, bool) -> MarketDataResult
        """A series of bars for one instrument at one interval."""
        try:
            m.check_interval(interval)
        except m.ModelError as exc:
            return self._unsupported(str(exc))
        exchange = None if isinstance(symbol, IndexSymbol) else getattr(
            symbol, "exchange", None)
        if exchange is None and not isinstance(symbol, IndexSymbol):
            return self._unsupported(
                "get_bars needs an indiacore.symbols Symbol or IndexSymbol, "
                "got %r" % (symbol,))
        return self._serve(
            kind=m.BARS, symbol=symbol, exchange=exchange, interval=interval,
            provider=provider, allow_cache=allow_cache,
            allow_fallback=allow_fallback, allow_stale_cache=allow_stale_cache,
            call=lambda p: p.get_historical_bars(symbol, interval, start, end),
            cache_parts={"ticker": getattr(symbol, "ticker",
                                           getattr(symbol, "symbol", "")),
                         "exchange": getattr(exchange, "code", None),
                         "start": start, "end": end})

    # ------------------------------------------------------------- statuses

    def provider_status(self, name=None):
        # type: (Optional[str]) -> Any
        if name is not None:
            return self.registry.get(name).get_provider_status()
        return [p.get_provider_status() for p in self.registry.all()]

    def capability_matrix(self):
        # type: () -> List[dict]
        return self.registry.matrix()

    # ----------------------------------------------------------- the engine

    def _unsupported(self, message):
        # type: (str) -> MarketDataResult
        return MarketDataResult(
            UNSUPPORTED,
            prov.DataResult([], source="indiacore.data_service",
                            freshness=prov.UNAVAILABLE, requested=1, failed=1,
                            errors=[message]))

    def _cache_kind(self, kind, interval):
        # type: (str, Optional[str]) -> str
        if kind in (m.QUOTE, m.INDEX_QUOTE):
            return kind
        return "bars_intraday" if interval and m.is_intraday(interval) \
            else "bars_daily"

    def _serve(self, kind, symbol, exchange, interval, provider, allow_cache,
               allow_fallback, allow_stale_cache, call, cache_parts):
        # type: (str, Any, Any, Optional[str], Optional[str], bool, bool, bool, Any, dict) -> MarketDataResult
        now = self.now()
        try:
            chosen = self.registry.choose(kind, exchange, interval, provider)
        except (base.UnsupportedRequest, registry_mod.RegistryError) as exc:
            return self._unsupported(str(exc))

        chain = [chosen]
        if allow_fallback and provider is None:
            for other in self.registry.candidates(kind, exchange, interval):
                if other.name != chosen.name:
                    chain.append(other)

        cache_kind = self._cache_kind(kind, interval)
        attempts = []  # type: List[dict]
        errors = []    # type: List[str]
        first_cached = None  # type: Any

        for position, candidate in enumerate(chain):
            key = cache_mod.make_key(
                candidate.name, cache_kind, interval=interval,
                **cache_parts)
            exchange_code = cache_parts.get("exchange")

            if allow_cache:
                entry = self.cache.get(key, cache_kind, candidate.name,
                                       exchange_code)
                if entry is not None:
                    built = self._from_cache(entry, kind, symbol, candidate,
                                             interval, now)
                    if built is not None:
                        built.attempts = attempts + [
                            {"provider": candidate.name, "outcome": CACHED,
                             "age_seconds": round(entry.age_seconds, 1)}]
                        built.fallback_used = position > 0
                        built.fallback_from = chain[0].name if position else None
                        return built

            try:
                payload = call(candidate)
            except base.UnsupportedRequest as exc:
                attempts.append({"provider": candidate.name,
                                 "outcome": UNSUPPORTED, "error": str(exc)})
                errors.append("%s: %s" % (candidate.name, exc))
                continue
            except base.ProviderError as exc:
                attempts.append({"provider": candidate.name,
                                 "outcome": PROVIDER_ERROR, "error": str(exc)})
                errors.append("%s: %s" % (candidate.name, exc))
                # Hold the first provider's stale cache aside. It is only
                # used if the caller explicitly allowed it and nothing else
                # answers.
                if first_cached is None and allow_stale_cache:
                    stale = self.cache.get(key, cache_kind, candidate.name,
                                           exchange_code, allow_expired=True)
                    if stale is not None:
                        first_cached = (stale, candidate)
                continue
            except Exception as exc:  # a provider bug must not look like data
                attempts.append({"provider": candidate.name,
                                 "outcome": PROVIDER_ERROR,
                                 "error": "%s: %s" % (type(exc).__name__, exc)})
                errors.append("%s raised %s: %s"
                              % (candidate.name, type(exc).__name__, exc))
                continue

            built = self._build(payload, kind, symbol, candidate, interval,
                                now, key, cache_kind, exchange_code,
                                allow_cache)
            built.attempts = attempts + [{"provider": candidate.name,
                                          "outcome": built.outcome}]
            built.fallback_used = position > 0
            built.fallback_from = chain[0].name if position else None
            if built.outcome == INVALID_DATA and position < len(chain) - 1:
                # Refused data is not an answer. Try the next provider rather
                # than hand back something validation rejected.
                errors.extend(built.errors)
                attempts = built.attempts
                continue
            if built.fallback_used:
                built.notes.append(
                    "%s answered after %s failed. The rows are %s's alone and "
                    "are not combined with any other provider's"
                    % (candidate.name, chain[0].name, candidate.name))
            built.result.errors = list(built.result.errors) + errors
            built.errors = list(built.result.errors)
            return built

        if first_cached is not None:
            entry, candidate = first_cached
            built = self._from_cache(entry, kind, symbol, candidate, interval,
                                     now, force_stale=True)
            if built is not None:
                built.attempts = attempts
                built.notes.append(
                    "every provider failed and the caller allowed a stale "
                    "cache entry. This is %0.0f seconds old and is not a "
                    "current price" % entry.age_seconds)
                built.result.errors = list(built.result.errors) + errors
                built.errors = list(built.result.errors)
                return built

        return MarketDataResult(
            PROVIDER_ERROR,
            prov.DataResult([], source=chain[0].name if chain else "none",
                            freshness=prov.UNAVAILABLE, requested=1, failed=1,
                            errors=errors or ["no provider answered"]),
            provider=chain[0].name if chain else None, attempts=attempts)

    # --------------------------------------------------------- build result

    def _build(self, payload, kind, symbol, provider, interval, now, key,
               cache_kind, exchange_code, allow_cache):
        # type: (Any, str, Any, Any, Optional[str], datetime.datetime, str, str, Any, bool) -> MarketDataResult
        if isinstance(payload, base.EmptyButValid):
            # A real nothing. as_of is the last completed session, so phase 2
            # can call it usable: the feed worked and the filter is empty.
            market = self.market_for(symbol)
            try:
                as_of = market.last_completed_session(now)
            except Exception:
                as_of = None
            return MarketDataResult(
                EMPTY_VALID,
                prov.DataResult([], source=provider.name, as_of=as_of,
                                retrieved_at=now, freshness=prov.END_OF_DAY,
                                requested=1, fetched=1, failed=0),
                provider=provider.name, notes=[payload.reason] + payload.notes)

        if kind == m.BARS:
            bars = list(payload)
            verdict = validation.validate_series(
                bars, self.thresholds, self.calendar)
            rows = bars
            freshness = bars[0].provenance.freshness if bars else prov.UNKNOWN
            as_of = max(b.session_date for b in bars) if bars else None
            reason = bars[0].provenance.freshness_reason if bars else ""
        else:
            quote = payload
            verdict = validation.validate_quote(quote, self.thresholds)
            rows = [quote]
            freshness = quote.provenance.freshness
            reason = quote.provenance.freshness_reason
            as_of = (quote.provenance.source_time.date()
                     if quote.provenance.source_time else None)

        if verdict.verdict == validation.INVALID:
            return MarketDataResult(
                INVALID_DATA,
                prov.DataResult([], source=provider.name, retrieved_at=now,
                                freshness=prov.UNAVAILABLE, requested=1,
                                failed=1,
                                errors=["%s: %s" % (f.code, f.message)
                                        for f in verdict.findings
                                        if f.severity == validation.INVALID]),
                provider=provider.name, quality=verdict,
                notes=["validation refused this record, so it was neither "
                       "returned as data nor cached"])

        result = prov.DataResult(
            rows, source=provider.name, as_of=as_of, retrieved_at=now,
            freshness=freshness, requested=1, fetched=1, failed=0)

        if allow_cache and freshness in prov.USABLE_FRESHNESS:
            try:
                self.cache.put(
                    key, self._serialise(rows, kind), cache_kind,
                    provider.name, exchange_code,
                    meta={"kind": kind, "interval": interval,
                          "freshness": freshness,
                          "symbol": getattr(symbol, "yahoo", str(symbol))})
            except cache_mod.CacheError:
                # A cache that cannot be written must not fail a good request.
                pass

        return MarketDataResult(
            _outcome_for(freshness), result, rows=rows,
            provider=provider.name, quality=verdict,
            notes=[reason] if reason else [])

    def _serialise(self, rows, kind):
        # type: (Any, str) -> dict
        return {"kind": kind, "rows": [r.to_dict() for r in rows]}

    def _from_cache(self, entry, kind, symbol, provider, interval, now,
                    force_stale=False):
        # type: (Any, str, Any, Any, Optional[str], datetime.datetime, bool) -> Any
        """Rebuild a result from a cache entry, labelled as cached.

        Returns None when the entry cannot be rebuilt, which is treated as a
        miss. Freshness is recomputed from the stored source timestamps
        rather than trusted from the envelope: an entry written while the
        market was open is not still a quote an hour later.
        """
        body = entry.payload
        if not isinstance(body, dict) or body.get("kind") != kind:
            return None
        stored = body.get("rows") or []
        if not stored:
            return None

        market = self.market_for(symbol)
        source_times = []
        for row in stored:
            stamp = (row.get("provenance") or {}).get("source_time")
            if stamp:
                source_times.append(_parse_iso(stamp))
        newest = max([s for s in source_times if s], default=None)

        if kind == m.BARS:
            freshness, reason = (prov.STALE, "forced stale") if force_stale \
                else _bar_freshness_from_cache(stored, now, market,
                                               provider.capabilities,
                                               interval or m.DAY_1, self.rules)
            as_of = max(_parse_date(r.get("session_date")) for r in stored)
        else:
            freshness, reason = (prov.STALE, "forced stale") if force_stale \
                else quality.classify_quote(newest, now, market,
                                            provider.capabilities, self.rules)
            as_of = newest.date() if newest else None

        if force_stale:
            reason = ("served from cache after every provider failed. The "
                      "caller allowed this explicitly")

        result = prov.DataResult(
            stored, source=provider.name, as_of=as_of, retrieved_at=now,
            freshness=freshness, requested=1, fetched=1, failed=0)
        return MarketDataResult(
            CACHED, result, rows=stored, provider=provider.name,
            cached=True, cache_age_seconds=round(entry.age_seconds, 1),
            notes=["served from cache, %.0f seconds old. %s"
                   % (entry.age_seconds, reason)])


def _bar_freshness_from_cache(stored, now, market, capabilities, interval,
                              rules):
    # type: (Any, datetime.datetime, Any, Any, str, Any) -> Any
    """Freshness for cached bars, from their own session dates."""
    newest = max(_parse_date(r.get("session_date")) for r in stored)
    last_session = market.last_completed_session(now)
    if newest == last_session:
        return (prov.END_OF_DAY,
                "the newest cached bar is the last completed %s session, %s"
                % (market.code, last_session))
    if newest > last_session:
        return (prov.UNKNOWN,
                "the newest cached bar is dated %s, after the last completed "
                "session %s" % (newest, last_session))
    return quality._behind(newest, last_session, market, rules, capabilities,
                           "cached daily series")


def _outcome_for(freshness):
    # type: (str) -> str
    return {
        prov.LIVE: FRESH,
        prov.DELAYED: DELAYED,
        prov.END_OF_DAY: END_OF_DAY,
        prov.HISTORICAL: HISTORICAL,
        prov.STALE: STALE,
        prov.UNKNOWN: UNKNOWN_AGE,
        prov.UNAVAILABLE: PROVIDER_ERROR,
    }.get(freshness, STALE)


def _parse_iso(text):
    # type: (Optional[str]) -> Any
    if not text:
        return None
    try:
        cleaned = text.replace("Z", "+00:00")
        return datetime.datetime.strptime(cleaned, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M%z"):
        try:
            return datetime.datetime.strptime(text.replace("Z", "+00:00"), fmt)
        except ValueError:
            continue
    return None


def _parse_date(text):
    # type: (Optional[str]) -> Any
    if not text:
        return datetime.date.min
    try:
        return datetime.date(*[int(p) for p in text.split("-")])
    except Exception:
        return datetime.date.min
