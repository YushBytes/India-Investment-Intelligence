"""Yahoo Finance chart endpoint. Unofficial, best effort, and useful anyway.

Verified against the live endpoint on 13 November 2026, one symbol at a time:

    SBIN.NS        200, currency INR, exchangeName NSI, fullExchangeName NSE
    M%26M.NS       200. The ampersand must be percent encoded or the request
                   truncates at it, which is what symbols.yahoo_url is for
    INFY.BO        200, a separate instrument from INFY.NS
    %5ENSEI        200, instrumentType INDEX, currency INR
    NIFTYBEES.NS   200, instrumentType EQUITY even though it is an ETF
    ZZZZNOPE.NS    404 with {"chart":{"result":null,"error":{"code":
                   "Not Found","description":"No data found, symbol may be
                   delisted"}}}
    500112.BO      404. A BSE scrip code is not a Yahoo symbol, and phase 2
                   refuses to request one rather than sending it and failing

What this provider is not: authoritative, or real time. The chart meta
carries currency, gmtoffset, exchangeTimezoneName and regularMarketTime, and
no delay field of any kind. There is no published service level, no terms
that cover programmatic use for this path, and no guarantee the endpoint will
answer tomorrow. So `official` is false, `realtime` is false and
`declared_delay_seconds` is None, which together mean marketdata.quality can
never classify anything from here as LIVE. Its ceiling is DELAYED.

Two host names answer the same path, query1 and query2. Both are declared, and
the second is used only when the first fails, which is failover inside one
provider and not a second source: the same operator, the same data.
"""

import datetime
from typing import Any, List, Optional

from .. import calendar_in as cal
from .. import market as mkt
from .. import rate_limit
from .. import retry as retry_mod
from ..marketdata import models as m
from ..marketdata import normalize, quality
from ..symbols import BSE, NSE, IndexSymbol, Symbol
from . import base, transport as tr

HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
CHART_PATH = "/v8/finance/chart/"

# Ranges Yahoo accepts for a daily series. Checked in meta.validRanges.
DAILY_RANGES = ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y",
                "ytd", "max")

# Intraday depth, observed rather than documented: a 1 day 5 minute request
# returns 75 bars for a full NSE session, and a 1 month request for the same
# interval is accepted. Yahoo does not publish how far back intraday goes, so
# this is declared as a limit this provider will not promise beyond.
INTRADAY_MAX_DAYS = 30

TERMS = "https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html"


def capabilities():
    # type: () -> m.Capabilities
    return m.Capabilities(
        provider="yahoo",
        exchanges=("NSE", "BSE"),
        instrument_types=(m.EQUITY, m.INDEX, m.UNKNOWN_TYPE),
        kinds=(m.QUOTE, m.BARS, m.INDEX_QUOTE),
        intervals=(m.DAY_1, m.MIN_1, m.MIN_5, m.MIN_15, m.MIN_30, m.MIN_60),
        official=False,
        operational=True,
        realtime=False,
        declared_delay_seconds=None,
        max_history_days=None,
        auth_required=False,
        terms_url=TERMS,
        notes=("Unofficial. An undocumented endpoint on a public host, with "
               "no published delay, no service level and no guarantee of "
               "continued access. Verified working for NSE, BSE and Indian "
               "index symbols. Treat as best effort and never as the record "
               "of a price. Yahoo reports Indian ETFs as instrumentType "
               "EQUITY, so ETF classification is not available from here."))


class YahooProvider(base.Provider):
    """Quotes, daily bars and intraday bars for NSE, BSE and Indian indices."""

    name = "yahoo"

    def __init__(self, transport=None, limiter=None, policy=None, clock=None,
                 market=None, rules=None, hosts=HOSTS):
        # type: (Any, Any, Any, Any, Any, Any, Any) -> None
        base.Provider.__init__(
            self,
            transport or tr.HttpTransport(allowed_hosts=hosts),
            capabilities(),
            # One request a second by default. Yahoo publishes no rate limit
            # for this path, so the floor is set by courtesy rather than by a
            # documented number.
            limiter or rate_limit.RateLimiter("yahoo", min_interval=1.0, burst=2),
            policy or retry_mod.RetryPolicy(attempts=3, base_delay=0.5),
            clock)
        self.hosts = tuple(hosts)
        self.rules = rules or quality.DEFAULT_RULES
        self._markets = {"NSE": mkt.nse_market(), "BSE": mkt.bse_market()}
        if market is not None:
            self._markets[market.code] = market

    # --------------------------------------------------------------- urls

    def chart_url(self, symbol, host=None, **params):
        # type: (Any, Optional[str], **Any) -> str
        """Build a chart URL with the symbol percent encoded by phase 2.

        symbol.yahoo_url() is what keeps M&M.NS intact as M%26M.NS and what
        refuses to send a BSE scrip code that is known to 404.
        """
        query = "&".join("%s=%s" % (k, v) for k, v in sorted(params.items())
                         if v is not None)
        return "https://%s%s%s%s" % (host or self.hosts[0], CHART_PATH,
                                     symbol.yahoo_url(),
                                     "?" + query if query else "")

    def _market_for(self, symbol):
        # type: (Any) -> Any
        if isinstance(symbol, IndexSymbol):
            # Indian index levels are published by the exchange that computes
            # them. NSE hours are used for ^NSEI and friends; the Sensex is
            # computed by BSE, whose equity session keeps the same clock.
            return self._markets["BSE" if symbol.symbol == "^BSESN" else "NSE"]
        return self._markets[symbol.exchange.code]

    def _market_status(self, symbol, now):
        # type: (Any, datetime.datetime) -> str
        try:
            return self._market_for(symbol).state_at(now).state
        except Exception:
            # An unverified calendar year must not stop a price being
            # reported. It becomes an unknown market state, which the
            # freshness rules then treat as unknown rather than open.
            return "unknown"

    def _get_chart(self, symbol, **params):
        # type: (Any, **Any) -> dict
        """Fetch one chart payload, failing over to the second Yahoo host."""
        errors = []  # type: List[str]
        for index, host in enumerate(self.hosts):
            url = self.chart_url(symbol, host=host, **params)

            def call(target=url):
                return self.transport.get_json(target)

            try:
                return self.fetch(call, "yahoo chart for %s" % symbol.yahoo)
            except tr.HttpError as exc:
                if exc.status in (404, 410):
                    raise base.SymbolNotFound(
                        "yahoo has no data for %s (HTTP %s). A valid NSE "
                        "symbol carries .NS and a BSE one .BO; a BSE scrip "
                        "code is not a Yahoo symbol"
                        % (symbol.yahoo, exc.status))
                errors.append("%s: %s" % (host, exc))
                if index == len(self.hosts) - 1:
                    raise base.ProviderUnavailable(
                        "every yahoo host failed: %s" % "; ".join(errors))
            except (tr.TransportError, retry_mod.RetryExhausted) as exc:
                errors.append("%s: %s" % (host, retry_mod._safe(exc)))
                if index == len(self.hosts) - 1:
                    raise base.ProviderUnavailable(
                        "every yahoo host failed: %s" % "; ".join(errors))
        raise base.ProviderUnavailable("no yahoo host was tried")

    # -------------------------------------------------------------- quotes

    def get_quote(self, symbol):
        # type: (Any) -> m.Quote
        if isinstance(symbol, IndexSymbol):
            return self.get_index_quote(symbol)
        self.require(m.QUOTE, symbol.exchange)
        # Five daily bars, so the previous close comes from the bar before
        # the newest rather than from a meta field that means something else.
        payload = self._get_chart(symbol, range="5d", interval=m.DAY_1)
        return self._quote_from(payload, symbol)

    def get_index_quote(self, index_symbol):
        # type: (Any) -> m.Quote
        if not isinstance(index_symbol, IndexSymbol):
            raise base.UnsupportedRequest(
                "get_index_quote needs an IndexSymbol such as "
                "IndexSymbol('^NSEI'), got %r" % (index_symbol,))
        self.require(m.INDEX_QUOTE)
        payload = self._get_chart(index_symbol, range="5d", interval=m.DAY_1)
        return self._quote_from(payload, index_symbol)

    def _quote_from(self, payload, symbol):
        # type: (dict, Any) -> m.Quote
        now = self.now()
        status = self._market_status(symbol, now)
        market = self._market_for(symbol)

        def provenance_for(source_time):
            return self.provenance(
                source="%s%s" % (self.hosts[0], CHART_PATH),
                source_time=source_time, retrieved_at=now)

        try:
            quote, _notes = normalize.yahoo_quote(
                payload, symbol, provenance_for, now, market_status=status)
        except normalize.NormalizeError as exc:
            raise base.BadProviderData("yahoo sent something unusable: %s" % exc)
        if quote is None:
            raise base.BadProviderData(
                "yahoo returned a chart for %s with no last traded price"
                % symbol.yahoo)
        freshness, reason = quality.classify_quote(
            quote.provenance.source_time, now, market, self.capabilities,
            self.rules)
        quote.provenance = quote.provenance.but(freshness=freshness,
                                                freshness_reason=reason)
        return quote

    # ---------------------------------------------------------------- bars

    def get_historical_bars(self, symbol, interval=m.DAY_1, start=None,
                            end=None):
        # type: (Any, str, Any, Any) -> Any
        exchange = None if isinstance(symbol, IndexSymbol) else symbol.exchange
        self.require(m.BARS, exchange, interval)
        params = {"interval": interval}
        if start is None and end is None:
            params["range"] = "1d" if m.is_intraday(interval) else "1mo"
        else:
            if start is None or end is None:
                raise base.UnsupportedRequest(
                    "pass both start and end, or neither. A half open range "
                    "would have to guess the other end")
            if end < start:
                raise base.UnsupportedRequest(
                    "end %s is before start %s" % (end, start))
            if m.is_intraday(interval) \
                    and (end - start).days > INTRADAY_MAX_DAYS:
                raise base.UnsupportedRequest(
                    "yahoo does not publish how far back %s bars go. This "
                    "provider will not promise more than %d days of intraday "
                    "history" % (interval, INTRADAY_MAX_DAYS))
            params["period1"] = _epoch(start, 0)
            params["period2"] = _epoch(end, 1)

        payload = self._get_chart(symbol, **params)
        now = self.now()
        market = self._market_for(symbol)

        def provenance_for(source_time):
            return self.provenance(
                source="%s%s" % (self.hosts[0], CHART_PATH),
                source_time=source_time, retrieved_at=now)

        try:
            bars, notes = normalize.yahoo_bars(
                payload, symbol, interval, provenance_for, now)
        except normalize.NormalizeError as exc:
            raise base.BadProviderData("yahoo sent something unusable: %s" % exc)

        if not bars:
            return base.EmptyButValid(
                self.name,
                "yahoo answered with a chart and no bars for this range, "
                "which is what it does for a window holding no session",
                notes)

        freshness, reason = quality.classify_bars(
            bars, now, market, self.capabilities, interval, self.rules)
        for bar in bars:
            bar.provenance = bar.provenance.but(freshness=freshness,
                                                freshness_reason=reason)
        return bars


def _epoch(day, add_days):
    # type: (Any, int) -> int
    """A date to an IST epoch second, inclusive of the end day."""
    if isinstance(day, datetime.datetime):
        moment = cal.to_ist(day)
    else:
        moment = datetime.datetime.combine(
            day + datetime.timedelta(days=add_days), datetime.time(0, 0),
            tzinfo=cal.ist_timezone())
    return int(moment.timestamp())
