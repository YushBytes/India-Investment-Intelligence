"""Provider payloads in, normalised records out, nothing invented in between.

Each function here is written against a payload that was fetched and read, not
against documentation. The three shapes that drove the code:

1. A Yahoo chart response for SBIN.NS carries meta.currency INR,
   meta.regularMarketTime, meta.gmtoffset 19800, a timestamp array and
   indicators.quote[0] with high, close, open, low and volume arrays.

2. A range that contains no session at all, such as the one day window around
   the 2026 Muhurat Sunday, comes back 200 with meta present,
   indicators.quote[0] equal to {} and no timestamp key at all. That is a
   valid empty answer, not a failure, and the two must not be confused. The
   caller gets zero bars and `fetched` of 1, so provenance.DataResult can
   report a real empty result.

3. An intraday series is padded with nulls. A 1 day 5 minute request for
   SBIN.NS returned 75 timestamps with a null close among them. Those bars
   are dropped and counted, never carried forward or interpolated. A bar this
   module did not receive is a bar it does not report.

The bhavcopy readers parse the unified CSV both exchanges publish. The header
is identical on NSE and BSE: TradDt, BizDt, Sgmt, Src, FinInstrmTp,
FinInstrmId, ISIN, TckrSymb, SctySrs, then OpnPric, HghPric, LwPric, ClsPric,
LastPric, PrvsClsgPric and TtlTradgVol. Only the equity cash segment is read:
rows with FinInstrmTp other than STK are skipped rather than guessed at.
"""

import csv
import datetime
import io
from typing import Any, Dict, List, Optional, Tuple

from .. import calendar_in as cal
from ..symbols import BSE, NSE, IndexSymbol, Symbol
from . import models as m

# The equity cash rows in the unified bhavcopy. Everything else in the file
# (government bonds, ETFs flagged the same way, and so on) carries other
# instrument type codes and is not read here.
BHAVCOPY_STOCK = "STK"

BHAVCOPY_COLUMNS = ("TradDt", "TckrSymb", "SctySrs", "FinInstrmTp",
                    "OpnPric", "HghPric", "LwPric", "ClsPric", "TtlTradgVol")


class NormalizeError(ValueError):
    """The payload did not have the shape this reader was written against."""


def _ist(epoch):
    # type: (Any) -> datetime.datetime
    return datetime.datetime.fromtimestamp(int(epoch), cal.ist_timezone())


def _instrument_type(yahoo_type):
    # type: (Optional[str]) -> str
    """Yahoo's instrumentType, mapped honestly.

    EQUITY is mapped to EQUITY even though Yahoo also answers EQUITY for
    Indian ETFs (checked: NIFTYBEES.NS and GOLDBEES.NS both come back
    EQUITY). The mapping is not corrected by guessing from the ticker, so an
    ETF arrives labelled equity and the limitation is documented rather than
    papered over.
    """
    raw = (yahoo_type or "").upper()
    if raw == "INDEX":
        return m.INDEX
    if raw == "ETF":
        return m.ETF
    if raw == "EQUITY":
        return m.EQUITY
    return m.UNKNOWN_TYPE


def _require_chart(payload, symbol_text):
    # type: (Any, str) -> dict
    if not isinstance(payload, dict):
        raise NormalizeError("expected a JSON object, got %s"
                             % type(payload).__name__)
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise NormalizeError("no chart object in the response for %s"
                             % symbol_text)
    error = chart.get("error")
    if error:
        code = error.get("code") if isinstance(error, dict) else error
        desc = error.get("description") if isinstance(error, dict) else ""
        raise NormalizeError("%s: %s %s" % (symbol_text, code, desc))
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        raise NormalizeError("no result for %s" % symbol_text)
    first = results[0]
    if not isinstance(first, dict) or not isinstance(first.get("meta"), dict):
        raise NormalizeError("result for %s carries no meta block"
                             % symbol_text)
    return first


def _check_symbol_matches(meta, symbol):
    # type: (dict, Any) -> None
    """The payload must be about the instrument that was asked for.

    Cheap, and it catches the single worst failure mode in a cache or a
    fallback chain: an NSE price filed under a BSE symbol. SBIN closed at
    1086.50 on NSE and 1085.00 on BSE on 12 November 2026, so the two are not
    interchangeable at any tolerance.
    """
    served = (meta.get("symbol") or "").upper()
    asked = symbol.yahoo.upper()
    if served and served != asked:
        raise NormalizeError(
            "asked for %s and the payload is for %s. Refusing to file one "
            "instrument's prices under another" % (asked, served))


def yahoo_bars(payload, symbol, interval, provenance_for, retrieved_at):
    # type: (Any, Any, str, Any, datetime.datetime) -> Tuple[List[m.Bar], List[str]]
    """Yahoo chart JSON to bars. Returns (bars, notes).

    `provenance_for` is called with the bar's source time and must return a
    models.Provenance. Freshness is not decided here; marketdata.quality does
    that once, for the series.
    """
    m.check_interval(interval)
    result = _require_chart(payload, getattr(symbol, "yahoo", str(symbol)))
    meta = result["meta"]
    _check_symbol_matches(meta, symbol)

    currency = (meta.get("currency") or "INR").upper()
    if currency != "INR":
        raise NormalizeError(
            "%s is quoted in %s, not INR. This system does not convert "
            "silently" % (meta.get("symbol"), currency))

    granularity = meta.get("dataGranularity")
    if granularity and granularity != interval:
        raise NormalizeError(
            "asked for %s bars and the payload says dataGranularity %s"
            % (interval, granularity))

    notes = []  # type: List[str]
    stamps = result.get("timestamp")
    if not stamps:
        # Verified shape: meta present, indicators.quote[0] == {}, no
        # timestamp key. A real empty range.
        notes.append("the provider returned no bars for this range, and no "
                     "error. Treated as a valid empty result")
        return ([], notes)

    indicators = result.get("indicators") or {}
    quotes = indicators.get("quote") or [{}]
    series = quotes[0] if isinstance(quotes[0], dict) else {}
    adj_block = (indicators.get("adjclose") or [{}])[0]
    adj = adj_block.get("adjclose") if isinstance(adj_block, dict) else None

    opens, highs = series.get("open"), series.get("high")
    lows, closes = series.get("low"), series.get("close")
    volumes = series.get("volume")
    if not all(isinstance(x, list) for x in (opens, highs, lows, closes)):
        raise NormalizeError(
            "the quote block for %s is missing one of open, high, low or "
            "close" % meta.get("symbol"))

    exchange = getattr(symbol, "exchange", None)
    bars = []  # type: List[m.Bar]
    dropped = 0
    for i, stamp in enumerate(stamps):
        row = [_at(opens, i), _at(highs, i), _at(lows, i), _at(closes, i)]
        if any(v is None for v in row) or stamp is None:
            dropped += 1
            continue
        start = _ist(stamp)
        bars.append(m.Bar(
            symbol=symbol, interval=interval, start=start,
            open=row[0], high=row[1], low=row[2], close=row[3],
            volume=_at(volumes, i), adj_close=_at(adj, i),
            exchange=exchange, currency="INR",
            # Yahoo serves an adjclose array alongside an unadjusted close.
            # The close carried here is the unadjusted one, which is what it
            # says.
            adjustment=m.UNADJUSTED,
            session_date=start.date(),
            provenance=provenance_for(start)))
    if dropped:
        notes.append("dropped %d of %d bar(s) that arrived with a null price. "
                     "Missing bars are not filled in" % (dropped, len(stamps)))
    return (bars, notes)


def _at(seq, i):
    # type: (Any, int) -> Any
    if not isinstance(seq, list) or i >= len(seq):
        return None
    return seq[i]


def yahoo_quote(payload, symbol, provenance_for, retrieved_at,
                market_status="unknown"):
    # type: (Any, Any, Any, datetime.datetime, str) -> Tuple[Optional[m.Quote], List[str]]
    """Yahoo chart JSON to one quote. Returns (quote, notes).

    The previous close is taken from the bar before the newest one in the
    series, not from meta.chartPreviousClose, because that field is the close
    before the start of the requested range and is only the previous session's
    close when the range happens to begin there. When the series has one bar
    the previous close is left as None and marked missing, which validation
    reports as INCOMPLETE.
    """
    result = _require_chart(payload, getattr(symbol, "yahoo", str(symbol)))
    meta = result["meta"]
    _check_symbol_matches(meta, symbol)

    currency = (meta.get("currency") or "INR").upper()
    if currency != "INR":
        raise NormalizeError("%s is quoted in %s, not INR"
                             % (meta.get("symbol"), currency))

    last = meta.get("regularMarketPrice")
    if last is None:
        return (None, ["no regularMarketPrice in the payload, so there is no "
                       "last traded price to report"])

    notes = []  # type: List[str]
    stamp = meta.get("regularMarketTime")
    source_time = _ist(stamp) if stamp else None
    if source_time is None:
        notes.append("no regularMarketTime, so the age of this price cannot "
                     "be established from the payload")

    previous_close = None
    derived = False
    closes = (((result.get("indicators") or {}).get("quote") or [{}])[0]
              or {}).get("close")
    stamps = result.get("timestamp") or []
    if isinstance(closes, list) and len(closes) >= 2 and stamps:
        earlier = [c for c in closes[:-1] if c is not None]
        if earlier:
            previous_close = earlier[-1]
            derived = True
            notes.append("previous close taken from the preceding bar in the "
                         "series, not from meta.chartPreviousClose")
    if previous_close is None:
        notes.append("no previous close available from this payload")

    is_index = isinstance(symbol, IndexSymbol)
    quote = m.Quote(
        symbol=symbol, last_price=last,
        provenance=provenance_for(source_time),
        exchange=None if is_index else symbol.exchange,
        instrument_type=(m.INDEX if is_index
                         else _instrument_type(meta.get("instrumentType"))),
        previous_close=previous_close,
        open=_first_not_none(meta.get("regularMarketOpen"), _series_open(result)),
        high=meta.get("regularMarketDayHigh"),
        low=meta.get("regularMarketDayLow"),
        volume=meta.get("regularMarketVolume"),
        currency="INR", market_status=market_status,
        notes=notes, previous_close_derived=derived)
    return (quote, notes)


def _first_not_none(*values):
    # type: (*Any) -> Any
    for v in values:
        if v is not None:
            return v
    return None


def _series_open(result):
    # type: (dict) -> Any
    opens = (((result.get("indicators") or {}).get("quote") or [{}])[0]
             or {}).get("open")
    if isinstance(opens, list):
        for v in reversed(opens):
            if v is not None:
                return v
    return None


def yahoo_index_quote(payload, index_symbol, provenance_for, retrieved_at,
                      market_status="unknown"):
    # type: (Any, IndexSymbol, Any, datetime.datetime, str) -> Tuple[Optional[m.Quote], List[str]]
    """An index level. Carries no exchange and no volume worth reporting."""
    quote, notes = yahoo_quote(payload, index_symbol, provenance_for,
                               retrieved_at, market_status)
    return (quote, notes)


# ------------------------------------------------------------------ bhavcopy

def _bhavcopy_rows(text):
    # type: (str) -> List[dict]
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise NormalizeError("the bhavcopy is empty")
    missing = [c for c in BHAVCOPY_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise NormalizeError(
            "this file is not the unified bhavcopy both exchanges publish: "
            "missing column(s) %s" % ", ".join(missing))
    return [row for row in reader]


def bhavcopy_bars(text, exchange, provenance_for, wanted=None, session_date=None):
    # type: (str, Any, Any, Any, Optional[datetime.date]) -> Tuple[List[m.Bar], List[str], Dict[str, str]]
    """Unified bhavcopy CSV to daily bars.

    Returns (bars, notes, scrip_codes). `wanted` is an optional set of
    tickers; when given, only those rows are built, which is what makes a
    whole market file usable for one instrument.

    `scrip_codes` maps ticker to FinInstrmId for BSE rows. That is the
    exchange's own identifier published in its own file, so a Symbol built
    from a BSE bhavcopy row carries a verified scrip code rather than an
    invented mapping.
    """
    rows = _bhavcopy_rows(text)
    notes = []  # type: List[str]
    bars = []  # type: List[m.Bar]
    codes = {}  # type: Dict[str, str]
    wanted_set = set(t.upper() for t in wanted) if wanted else None
    skipped_type = 0
    seen = set()

    for row in rows:
        if (row.get("FinInstrmTp") or "").strip().upper() != BHAVCOPY_STOCK:
            skipped_type += 1
            continue
        ticker = (row.get("TckrSymb") or "").strip().upper()
        if not ticker or (wanted_set is not None and ticker not in wanted_set):
            continue
        trade_date = (row.get("TradDt") or "").strip()
        try:
            day = datetime.date(*[int(p) for p in trade_date.split("-")])
        except Exception:
            raise NormalizeError("unreadable TradDt %r for %s"
                                 % (trade_date, ticker))
        if session_date is not None and day != session_date:
            raise NormalizeError(
                "this file is for %s but %s was requested. A bhavcopy is one "
                "session, and the two must not be mixed" % (day, session_date))
        series = (row.get("SctySrs") or "").strip().upper() or None
        code = (row.get("FinInstrmId") or "").strip()
        if exchange is BSE and code.isdigit():
            codes[ticker] = code
        key = (ticker, series)
        if key in seen:
            notes.append("%s appears more than once in series %s"
                         % (ticker, series))
        seen.add(key)
        try:
            symbol = Symbol(ticker, exchange,
                            series=series if exchange is NSE else None,
                            scrip_code=code if (exchange is BSE and code.isdigit())
                            else None)
        except Exception as exc:
            notes.append("skipped %s: %s" % (ticker, exc))
            continue
        try:
            o, h, l, c = (row["OpnPric"], row["HghPric"], row["LwPric"],
                          row["ClsPric"])
            volume = row.get("TtlTradgVol")
            # The session is one day; a daily bar starts when the session does.
            start = datetime.datetime.combine(
                day, datetime.time(9, 15), tzinfo=cal.ist_timezone())
            bars.append(m.Bar(
                symbol=symbol, interval=m.DAY_1, start=start,
                open=o, high=h, low=l, close=c,
                volume=int(float(volume)) if volume not in (None, "") else None,
                exchange=exchange, currency="INR",
                # The exchange publishes the prices as traded. No adjustment
                # for splits or bonuses is applied by either exchange in this
                # file.
                adjustment=m.UNADJUSTED, session_date=day,
                provenance=provenance_for(start)))
        except Exception as exc:
            notes.append("skipped %s: %s" % (ticker, exc))
            continue

    if skipped_type:
        notes.append("skipped %d non equity row(s) (FinInstrmTp other than %s)"
                     % (skipped_type, BHAVCOPY_STOCK))
    if wanted_set is not None:
        found = set(b.symbol.split(":")[-1] for b in bars)
        absent = sorted(wanted_set - found)
        if absent:
            notes.append("not present in this bhavcopy: %s"
                         % ", ".join(absent))
    return (bars, notes, codes)
