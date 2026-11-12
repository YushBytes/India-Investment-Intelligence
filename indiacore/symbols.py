"""NSE and BSE symbols, and the encoding that the US pack got wrong.

The day-trading pack encodes a Yahoo symbol like this:

    def to_yahoo(sym):
        return sym.upper().replace(".", "-")

That is right for US share classes, where Yahoo writes BRK.B as BRK-B. It is
destructive for India, where the exchange lives in a dotted suffix: SBIN.NS
becomes SBIN-NS, which Yahoo answers with a 404. Every NSE and BSE symbol in
the system fails this way, silently, as a "no data" result.

This module keeps the exchange in the type instead of in a string, so the
suffix is generated rather than typed by hand, and round-trips.

Three things here are not guesses about Yahoo's format but observed facts:

* The exchange suffix differs: .NS for NSE, .BO for BSE. The same company has
  two different symbols, and they are not interchangeable.
* Indian tickers contain characters that are not safe in a URL path. M&M is a
  real NSE ticker, and a request for "M&M.NS" truncates at the ampersand.
  `yahoo` and `yahoo_url` are therefore separate: one is the symbol, the other
  is the percent encoded path segment.
* Indices do not take a suffix at all. The Nifty 50 is ^NSEI, the Sensex is
  ^BSESN. They are a different shape and are modelled as a different type.

Also handled: tickers that open with a digit (3MINDIA, 20MICRONS), tickers
with a hyphen (BAJAJ-AUTO), and the NSE series suffix (SBIN-EQ) that appears
in exchange feeds but is not part of a Yahoo symbol.
"""

import re
from typing import Optional

try:  # pragma: no cover - trivial import shim for the 3.8 floor
    from urllib.parse import quote
except ImportError:  # pragma: no cover
    from urllib import quote  # type: ignore


class SymbolError(ValueError):
    """A symbol could not be built or parsed."""


class Exchange(object):
    """One Indian exchange and how Yahoo spells its symbols."""

    __slots__ = ("code", "name", "yahoo_suffix", "mic")

    def __init__(self, code, name, yahoo_suffix, mic):
        # type: (str, str, str, str) -> None
        self.code = code
        self.name = name
        self.yahoo_suffix = yahoo_suffix
        self.mic = mic

    def __repr__(self):
        return "Exchange(%r)" % self.code

    def __eq__(self, other):
        return isinstance(other, Exchange) and other.code == self.code

    def __hash__(self):
        return hash(self.code)


NSE = Exchange("NSE", "National Stock Exchange of India", ".NS", "XNSE")
BSE = Exchange("BSE", "BSE Limited", ".BO", "XBOM")

EXCHANGES = {"NSE": NSE, "BSE": BSE}
_BY_SUFFIX = {".NS": NSE, ".BO": BSE}

# NSE and BSE tickers: letters, digits, & - and a dot that is part of the name
# (as in "J.K.CEMENT"). Must start with a letter or a digit.
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9&.\-]{0,24}$")

# Series codes seen in NSE feeds: EQ (rolling), BE (trade to trade), BZ, SM,
# ST, IL, GB and others. Not part of a Yahoo symbol.
_SERIES = re.compile(r"^(?P<ticker>.+)-(?P<series>EQ|BE|BZ|SM|ST|IL|GB|MF|IV)$")

# Yahoo index symbols for India. These take no exchange suffix.
KNOWN_INDICES = {
    "^NSEI": "NIFTY 50",
    "^NSEBANK": "NIFTY BANK",
    "^BSESN": "S&P BSE SENSEX",
    "^INDIAVIX": "INDIA VIX",
    "^CNXIT": "NIFTY IT",
    "^CNXPHARMA": "NIFTY PHARMA",
    "^CNXAUTO": "NIFTY AUTO",
    "^CRSLDX": "NIFTY 500",
}


class Symbol(object):
    """A tradable instrument on one named Indian exchange."""

    __slots__ = ("ticker", "exchange", "series", "scrip_code")

    def __init__(self, ticker, exchange, series=None, scrip_code=None):
        # type: (str, Exchange, Optional[str], Optional[str]) -> None
        if not isinstance(exchange, Exchange):
            raise SymbolError("exchange must be NSE or BSE, got %r" % (exchange,))
        clean = (ticker or "").strip().upper()
        if not clean:
            raise SymbolError("a symbol needs a ticker")
        if clean.startswith("^"):
            raise SymbolError(
                "%s looks like an index. Use IndexSymbol, which takes no "
                "exchange suffix" % clean)
        match = _SERIES.match(clean)
        if match and series is None:
            series = match.group("series")
            clean = match.group("ticker")
        if not _TICKER.match(clean):
            raise SymbolError("%r is not a valid NSE or BSE ticker" % ticker)
        if scrip_code is not None:
            scrip_code = str(scrip_code).strip()
            if not scrip_code.isdigit():
                raise SymbolError("a BSE scrip code is numeric, got %r" % scrip_code)
            if exchange is NSE:
                raise SymbolError("a scrip code belongs to BSE, not NSE")
        self.ticker = clean
        self.exchange = exchange
        self.series = series
        self.scrip_code = scrip_code

    # --------------------------------------------------------------- yahoo

    @property
    def yahoo(self):
        # type: () -> str
        """The Yahoo symbol, dots intact. SBIN on NSE is SBIN.NS."""
        return self.ticker + self.exchange.yahoo_suffix

    @property
    def yahoo_addressable(self):
        # type: () -> bool
        """Whether Yahoo will actually answer for this symbol.

        A BSE scrip code is a BSE identifier, not a Yahoo symbol. Checked
        against the live endpoint: SBIN.BO answers 200, 500112.BO answers
        404. An all digit BSE ticker is therefore a scrip code that has been
        put in the ticker slot.
        """
        return not (self.exchange is BSE and self.ticker.isdigit())

    def yahoo_url(self):
        # type: () -> str
        """The Yahoo symbol, percent encoded for a URL path segment.

        M&M.NS becomes M%26M.NS. Passing the raw symbol into a URL loses
        everything after the ampersand.
        """
        if not self.yahoo_addressable:
            raise SymbolError(
                "%s is a BSE scrip code, not a Yahoo symbol. Yahoo answers 404 "
                "for it. Use the alphabetic ticker instead, for example "
                "bse('SBIN', scrip_code='%s')" % (self.yahoo, self.ticker))
        return quote(self.yahoo, safe=".-^")

    @classmethod
    def parse_yahoo(cls, text):
        # type: (str) -> Symbol
        """SBIN.NS -> Symbol(SBIN, NSE). Round-trips with `yahoo`."""
        raw = (text or "").strip().upper()
        if not raw:
            raise SymbolError("empty symbol")
        if raw.startswith("^"):
            raise SymbolError(
                "%s is an index symbol. Use IndexSymbol.parse_yahoo" % raw)
        for suffix, exchange in _BY_SUFFIX.items():
            if raw.endswith(suffix):
                return cls(raw[:-len(suffix)], exchange)
        raise SymbolError(
            "%r has no Indian exchange suffix. Expected one of %s. A bare "
            "ticker is ambiguous between NSE and BSE, so it is refused rather "
            "than assumed" % (text, ", ".join(sorted(_BY_SUFFIX))))

    # --------------------------------------------------------------- other

    def on(self, exchange):
        # type: (Exchange) -> Symbol
        """The same company on the other exchange. The ticker usually matches,
        but this is not guaranteed and should be verified per name."""
        return Symbol(self.ticker, exchange,
                      scrip_code=self.scrip_code if exchange is BSE else None)

    def __eq__(self, other):
        return (isinstance(other, Symbol)
                and other.ticker == self.ticker
                and other.exchange == self.exchange)

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self):
        return hash((self.ticker, self.exchange.code))

    def __str__(self):
        return "%s:%s" % (self.exchange.code, self.ticker)

    def __repr__(self):
        return "Symbol(%r, %s)" % (self.ticker, self.exchange.code)


class IndexSymbol(object):
    """An index. Carries no exchange suffix and is not tradable directly."""

    __slots__ = ("symbol", "name")

    def __init__(self, symbol, name=None):
        # type: (str, Optional[str]) -> None
        raw = (symbol or "").strip().upper()
        if not raw.startswith("^"):
            raise SymbolError("an index symbol starts with ^, got %r" % symbol)
        if len(raw) < 2:
            raise SymbolError("an index symbol needs a body after ^")
        self.symbol = raw
        self.name = name or KNOWN_INDICES.get(raw)

    @property
    def yahoo(self):
        # type: () -> str
        return self.symbol

    def yahoo_url(self):
        # type: () -> str
        """^NSEI becomes %5ENSEI in a path segment."""
        return quote(self.symbol, safe=".-")

    @classmethod
    def parse_yahoo(cls, text):
        # type: (str) -> IndexSymbol
        return cls(text)

    def __eq__(self, other):
        return isinstance(other, IndexSymbol) and other.symbol == self.symbol

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self):
        return hash(self.symbol)

    def __repr__(self):
        return "IndexSymbol(%r)" % self.symbol


def parse(text):
    # type: (str) -> object
    """Parse either shape. Indices start with ^, instruments carry a suffix."""
    raw = (text or "").strip().upper()
    if raw.startswith("^"):
        return IndexSymbol.parse_yahoo(raw)
    return Symbol.parse_yahoo(raw)


def nse(ticker, **kw):
    # type: (str, **object) -> Symbol
    return Symbol(ticker, NSE, **kw)  # type: ignore[arg-type]


def bse(ticker, scrip_code=None):
    # type: (str, Optional[str]) -> Symbol
    return Symbol(ticker, BSE, scrip_code=scrip_code)
