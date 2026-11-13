"""NSE, split into the part that works and the part that does not.

Two adapters, because "NSE" is not one source.

NseBhavcopyProvider is operational and official. NSE publishes a unified
bhavcopy for each completed session as a zipped CSV on its own archive host,
the same host the phase 2 holiday circular came from. Verified on 13 November
2026: the file for 2026-11-12 answered 200 with content type application/zip,
208,828 bytes, containing one CSV of 3,728 rows, and the row for SBIN reads a
close of 1086.50 on a volume of 26,973,410. That is the exchange's own
published number for the session, which makes this the one provider in the
repo whose prices are authoritative.

What it is not is a quote source. A bhavcopy exists only after a session has
finished, so the ceiling on anything it serves is END_OF_DAY, and it is
declared that way. There is no path here that turns a published close into a
current price.

NseQuoteApiProvider is not operational, and is registered anyway so the
capability matrix says so out loud. The endpoint people mean by "the NSE API"
is www.nseindia.com/api/quote-equity, which is an internal endpoint of the
website: undocumented, not published as an interface, and answering HTTP 403
Forbidden to a plain request (verified, same session as above). It can be
made to answer by priming cookies from the homepage and replaying them with
browser headers, which is circumventing an access control on someone else's
infrastructure. This system does not do that. The adapter therefore holds the
endpoint, the evidence and a refusal, and no amount of calling it will
produce a price.

One practical note on cost of use. A bhavcopy is one session for the whole
market, so it is cheap for a market wide snapshot of one date and expensive
for one instrument over sixty sessions, which would be sixty downloads. The
service prefers Yahoo for long histories and this provider for official
figures and cross checks.
"""

import datetime
import io
import zipfile
from typing import Any

from .. import market as mkt
from .. import rate_limit
from .. import retry as retry_mod
from ..marketdata import models as m
from ..marketdata import normalize, quality
from ..symbols import NSE, Symbol
from . import base, transport as tr

ARCHIVE_HOST = "nsearchives.nseindia.com"
WEBSITE_HOST = "www.nseindia.com"

# The unified ("udiff") bhavcopy, which is the format NSE moved the cash
# market to. One file per session.
BHAVCOPY_URL = ("https://" + ARCHIVE_HOST
                + "/content/cm/BhavCopy_NSE_CM_0_0_0_%s_F_0000.csv.zip")

TERMS = "https://www.nseindia.com/terms-conditions"


def bhavcopy_capabilities():
    # type: () -> m.Capabilities
    return m.Capabilities(
        provider="nse_bhavcopy",
        exchanges=("NSE",),
        instrument_types=(m.EQUITY, m.UNKNOWN_TYPE),
        kinds=(m.BARS,),
        intervals=(m.DAY_1,),
        official=True,
        operational=True,
        realtime=False,
        declared_delay_seconds=None,
        max_history_days=None,
        auth_required=False,
        terms_url=TERMS,
        notes=("Official. The exchange's own published end of day file for a "
               "completed session, one file per trading day, covering the "
               "whole cash market. Daily bars only and never a quote: the "
               "file does not exist until the session is over. Published "
               "unadjusted, so splits and bonuses appear as gaps."))


def quote_api_capabilities():
    # type: () -> m.Capabilities
    return m.Capabilities(
        provider="nse_quote_api",
        exchanges=("NSE",),
        instrument_types=(m.EQUITY, m.INDEX),
        kinds=(),
        intervals=(),
        official=True,
        operational=False,
        realtime=False,
        auth_required=True,
        terms_url=TERMS,
        notes=("Not operational. www.nseindia.com/api/quote-equity is an "
               "internal endpoint of the NSE website, not a published "
               "interface, and answers HTTP 403 to a plain request. Reaching "
               "it requires replaying browser cookies and headers, which is "
               "circumventing an access control, so this adapter refuses "
               "instead. A licensed NSE data feed would be a different "
               "provider with its own credentials."))


QUOTE_API_EVIDENCE = (
    "Verified 13 November 2026: GET https://www.nseindia.com/api/"
    "quote-equity?symbol=SBIN returned HTTP 403 Forbidden without a browser "
    "session. The published robots.txt allows crawling broadly, but the "
    "endpoint is still protected and is not a documented interface, so it is "
    "treated as unavailable rather than worked around.")


class NseQuoteApiProvider(base.NotOperationalProvider):
    """Registered to be listed as unsupported. Serves nothing."""

    name = "nse_quote_api"

    def __init__(self, clock=None):
        # type: (Any) -> None
        base.NotOperationalProvider.__init__(
            self, quote_api_capabilities(), QUOTE_API_EVIDENCE, clock=clock)


class BhavcopyProvider(base.Provider):
    """Shared implementation. NSE ships a zip, BSE a plain CSV."""

    name = "bhavcopy"
    exchange = NSE
    url_template = BHAVCOPY_URL
    zipped = True

    def __init__(self, transport=None, limiter=None, policy=None, clock=None,
                 market=None, rules=None, hosts=(ARCHIVE_HOST,)):
        # type: (Any, Any, Any, Any, Any, Any, Any) -> None
        base.Provider.__init__(
            self,
            transport or tr.HttpTransport(allowed_hosts=hosts,
                                          # A whole market file, so a longer
                                          # timeout than a quote needs.
                                          timeout=45.0),
            self.build_capabilities(),
            # One file is one session for the whole market, so there is no
            # reason to ask quickly. Two seconds apart, no bursting.
            limiter or rate_limit.RateLimiter(self.name, min_interval=2.0),
            policy or retry_mod.RetryPolicy(attempts=3, base_delay=1.0),
            clock)
        self.market = market or self.build_market()
        self.rules = rules or quality.DEFAULT_RULES

    def build_capabilities(self):
        # type: () -> m.Capabilities
        return bhavcopy_capabilities()

    def build_market(self):
        # type: () -> Any
        return mkt.nse_market()

    # ---------------------------------------------------------------- fetch

    def url_for(self, day):
        # type: (datetime.date) -> str
        return self.url_template % day.strftime("%Y%m%d")

    def fetch_csv(self, day):
        # type: (datetime.date) -> str
        """The session's CSV text, unzipped when the exchange ships a zip."""
        url = self.url_for(day)

        def call():
            return self.transport.get_bytes(url)

        try:
            raw = self.fetch(call, "%s bhavcopy for %s" % (self.name, day))
        except tr.HttpError as exc:
            if exc.status in (404, 410):
                raise base.SymbolNotFound(
                    "%s publishes no bhavcopy at %s. Either the session has "
                    "not been published yet or %s was not a trading day"
                    % (self.exchange.code, day, day))
            raise base.ProviderUnavailable(
                "%s bhavcopy for %s failed: %s" % (self.name, day, exc))
        except (tr.TransportError, retry_mod.RetryExhausted) as exc:
            raise base.ProviderUnavailable(
                "%s bhavcopy for %s failed: %s"
                % (self.name, day, retry_mod._safe(exc)))

        if not self.zipped:
            return raw.decode("utf-8", "replace")
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise base.BadProviderData(
                    "the %s archive for %s holds no CSV: %s"
                    % (self.name, day, ", ".join(archive.namelist())))
            return archive.read(names[0]).decode("utf-8", "replace")
        except zipfile.BadZipFile as exc:
            raise base.BadProviderData(
                "the %s archive for %s is not a readable zip: %s"
                % (self.name, day, exc))

    # ----------------------------------------------------------------- bars

    def get_historical_bars(self, symbol, interval=m.DAY_1, start=None,
                            end=None):
        # type: (Any, str, Any, Any) -> Any
        self.require(m.BARS, getattr(symbol, "exchange", None), interval)
        if not isinstance(symbol, Symbol):
            raise base.UnsupportedRequest(
                "a bhavcopy carries exchange listed instruments, not %r"
                % (symbol,))
        if symbol.exchange is not self.exchange:
            raise base.UnsupportedRequest(
                "%s serves %s only, and %s is a %s instrument"
                % (self.name, self.exchange.code, symbol,
                   symbol.exchange.code))

        now = self.now()
        last_session = self.market.last_completed_session(now)
        if start is None and end is None:
            start = end = last_session
        elif start is None or end is None:
            raise base.UnsupportedRequest(
                "pass both start and end, or neither")
        if end < start:
            raise base.UnsupportedRequest("end %s is before start %s"
                                          % (end, start))
        if end > last_session:
            raise base.UnsupportedRequest(
                "%s is not a completed %s session (the last one is %s). A "
                "bhavcopy exists only after the close, so this is not a "
                "source for today's price while the market is open"
                % (end, self.exchange.code, last_session))

        try:
            days = self.market.calendar.trading_days_between(start, end)
        except Exception as exc:
            raise base.UnsupportedRequest(
                "the %s calendar cannot cover %s to %s: %s"
                % (self.exchange.code, start, end, exc))
        if not days:
            return base.EmptyButValid(
                self.name,
                "the %s calendar has no trading session between %s and %s, "
                "so there is no bhavcopy to read" % (self.exchange.code,
                                                     start, end),
                [])

        bars = []
        notes = []
        for day in days:
            text = self.fetch_csv(day)

            def provenance_for(source_time, when=day):
                return self.provenance(
                    source=self.url_for(when), source_time=source_time,
                    retrieved_at=now)

            try:
                day_bars, day_notes, _codes = normalize.bhavcopy_bars(
                    text, self.exchange, provenance_for,
                    wanted=[symbol.ticker], session_date=day)
            except normalize.NormalizeError as exc:
                raise base.BadProviderData(
                    "the %s bhavcopy for %s could not be read: %s"
                    % (self.exchange.code, day, exc))
            bars.extend(day_bars)
            notes.extend(day_notes)

        if not bars:
            return base.EmptyButValid(
                self.name,
                "%s does not appear in the %s bhavcopy for %s. The file was "
                "read and the instrument is not in it"
                % (symbol.ticker, self.exchange.code,
                   ", ".join(str(d) for d in days[:3])),
                notes)

        freshness, reason = quality.classify_bars(
            bars, now, self.market, self.capabilities, m.DAY_1, self.rules)
        for bar in bars:
            bar.provenance = bar.provenance.but(freshness=freshness,
                                                freshness_reason=reason)
        return bars

    def get_session(self, day, wanted=None):
        # type: (datetime.date, Any) -> Any
        """Every equity row for one session. The whole market in one request.

        This is what a bhavcopy is good at, and it is the shape a future
        scanner wants: one file, one date, thousands of instruments.
        """
        self.require(m.BARS, self.exchange, m.DAY_1)
        now = self.now()
        last_session = self.market.last_completed_session(now)
        if day > last_session:
            raise base.UnsupportedRequest(
                "%s is not a completed %s session (the last one is %s)"
                % (day, self.exchange.code, last_session))
        text = self.fetch_csv(day)

        def provenance_for(source_time):
            return self.provenance(source=self.url_for(day),
                                   source_time=source_time, retrieved_at=now)

        try:
            bars, notes, codes = normalize.bhavcopy_bars(
                text, self.exchange, provenance_for, wanted=wanted,
                session_date=day)
        except normalize.NormalizeError as exc:
            raise base.BadProviderData(
                "the %s bhavcopy for %s could not be read: %s"
                % (self.exchange.code, day, exc))
        if not bars:
            return base.EmptyButValid(
                self.name, "the %s bhavcopy for %s holds no matching equity "
                           "row" % (self.exchange.code, day), notes)
        freshness, reason = quality.classify_bars(
            bars, now, self.market, self.capabilities, m.DAY_1, self.rules)
        for bar in bars:
            bar.provenance = bar.provenance.but(freshness=freshness,
                                                freshness_reason=reason)
        return bars

    def get_quote(self, symbol):
        # type: (Any) -> m.Quote
        raise base.UnsupportedRequest(
            "%s has no quotes. A bhavcopy is published after the close, so "
            "the newest figure in it is a session close. Ask for daily bars, "
            "or ask a quote provider" % self.name)


class NseBhavcopyProvider(BhavcopyProvider):
    """NSE's own published end of day file. Official, daily bars only."""

    name = "nse_bhavcopy"
    exchange = NSE
    url_template = BHAVCOPY_URL
    zipped = True
