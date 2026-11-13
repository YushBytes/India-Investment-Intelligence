"""Validation that tells a broken feed apart from a violent market.

The easy version of this module throws away anything surprising, and in
Indian equities that is the wrong instinct. A 20 percent move is not a
corrupt record: NSE runs 2, 5, 10 and 20 percent circuit bands, and a stock
that hits its upper band prints exactly the kind of jump a naive filter
discards. A 50 percent overnight gap is usually not corruption either; it is
a split or a bonus issue landing in an unadjusted series, which is a fact
about the series, not an error in it.

So the severities are split by what is actually being claimed:

    INVALID       structurally impossible. A high below its own low, a
                  negative price, a timestamp that is not a time. No market
                  produces these, so no market explains them away.
    WARNING       real but worth saying out loud. Circuit sized moves,
                  split sized gaps, zero volume, a bar on a day the
                  exchange calendar says was shut.
    INCOMPLETE    the shape is right and something is missing: no volume
                  field, bars dropped from the series, a gap in the
                  sequence.
    STALE         set by marketdata.quality, kept here so one verdict
                  vocabulary covers the whole pipeline.
    VALID         nothing to report.

Nothing is silently dropped or repaired. Every verdict carries findings, and
a finding names a code, a message and the field it came from, so a caller can
act on a specific problem rather than on a boolean.
"""

from typing import Any, List, Optional

from . import models as m

VALID = "valid"
WARNING = "warning"
INVALID = "invalid"
INCOMPLETE = "incomplete"
STALE = "stale"

# Worst wins, so a series with one impossible bar is never reported as merely
# incomplete.
SEVERITY_ORDER = (VALID, WARNING, INCOMPLETE, STALE, INVALID)


def worst(verdicts):
    # type: (Any) -> str
    out = VALID
    for v in verdicts:
        if SEVERITY_ORDER.index(v) > SEVERITY_ORDER.index(out):
            out = v
    return out


class Finding(object):
    """One specific thing noticed about one record."""

    __slots__ = ("code", "severity", "message", "field")

    def __init__(self, code, severity, message, field=None):
        # type: (str, str, str, Optional[str]) -> None
        self.code = code
        self.severity = severity
        self.message = message
        self.field = field

    def to_dict(self):
        # type: () -> dict
        return {"code": self.code, "severity": self.severity,
                "message": self.message, "field": self.field}

    def __repr__(self):
        return "Finding(%s, %s)" % (self.code, self.severity)


class Thresholds(object):
    """The numbers the warnings are measured against.

    Defaults are set from how the Indian cash market actually behaves rather
    than from a round number. The widest NSE circuit band is 20 percent, so a
    single session move inside that is reported as circuit sized and not as
    suspicious. Beyond `split_pct` the likeliest cause of an unadjusted series
    moving that far is a corporate action.
    """

    __slots__ = ("circuit_pct", "suspicious_pct", "split_pct",
                 "max_intraday_range_pct", "allow_zero_volume")

    def __init__(self, circuit_pct=20.0, suspicious_pct=25.0, split_pct=45.0,
                 max_intraday_range_pct=40.0, allow_zero_volume=True):
        self.circuit_pct = circuit_pct
        self.suspicious_pct = suspicious_pct
        self.split_pct = split_pct
        self.max_intraday_range_pct = max_intraday_range_pct
        self.allow_zero_volume = allow_zero_volume


DEFAULT_THRESHOLDS = Thresholds()


def _pct(a, b):
    # type: (Any, Any) -> float
    """Percent change from b to a, on exact paise, never on floats."""
    if not b:
        return 0.0
    return 100.0 * (a - b) / float(b)


# ------------------------------------------------------------------ one bar

def validate_bar(bar, thresholds=None, calendar=None):
    # type: (m.Bar, Optional[Thresholds], Any) -> m.Quality
    """Check one bar on its own terms."""
    th = thresholds or DEFAULT_THRESHOLDS
    found = []  # type: List[Finding]

    o, h, l, c = bar.open, bar.high, bar.low, bar.close
    prices = (("open", o), ("high", h), ("low", l), ("close", c))

    for name, price in prices:
        if price.as_paise <= 0:
            found.append(Finding(
                "non_positive_price", INVALID,
                "%s is %s. A traded price is positive"
                % (name, price.format(symbol=False)), name))

    # Only check the relationships if the numbers themselves are sane, so one
    # negative price does not generate four confusing findings.
    if not any(f.code == "non_positive_price" for f in found):
        if h.as_paise < l.as_paise:
            found.append(Finding(
                "high_below_low", INVALID,
                "high %s is below low %s" % (h.format(symbol=False),
                                             l.format(symbol=False)), "high"))
        else:
            if h.as_paise < o.as_paise or h.as_paise < c.as_paise:
                found.append(Finding(
                    "high_not_highest", INVALID,
                    "high %s is below open %s or close %s"
                    % (h.format(symbol=False), o.format(symbol=False),
                       c.format(symbol=False)), "high"))
            if l.as_paise > o.as_paise or l.as_paise > c.as_paise:
                found.append(Finding(
                    "low_not_lowest", INVALID,
                    "low %s is above open %s or close %s"
                    % (l.format(symbol=False), o.format(symbol=False),
                       c.format(symbol=False)), "low"))
            span = _pct(h.as_paise, l.as_paise)
            if span > th.max_intraday_range_pct:
                found.append(Finding(
                    "wide_range", WARNING,
                    "high is %.1f percent above low in one %s bar. Possible, "
                    "but worth checking against the exchange"
                    % (span, bar.interval), "high"))

    if bar.volume is None:
        found.append(Finding(
            "volume_missing", INCOMPLETE,
            "no volume on this bar, so liquidity cannot be judged", "volume"))
    elif bar.volume < 0:
        found.append(Finding(
            "negative_volume", INVALID,
            "volume is %d" % bar.volume, "volume"))
    elif bar.volume == 0:
        moved = o.as_paise != c.as_paise or h.as_paise != l.as_paise
        found.append(Finding(
            "zero_volume_with_movement" if moved else "zero_volume",
            WARNING if th.allow_zero_volume or moved else INVALID,
            ("zero volume but the price moved, which should not happen in a "
             "real session" if moved else
             "zero volume. Normal for an illiquid scrip on a quiet day"),
            "volume"))

    if bar.currency != "INR":
        found.append(Finding(
            "unexpected_currency", INVALID,
            "bar is denominated in %s, not INR" % bar.currency, "currency"))

    if bar.exchange not in ("NSE", "BSE", None):
        found.append(Finding(
            "unknown_exchange", INVALID,
            "%r is not an exchange this system knows" % (bar.exchange,),
            "exchange"))

    if bar.start.year < 1990 or bar.start > bar.start.replace(year=bar.start.year + 2):
        found.append(Finding("bad_timestamp", INVALID,
                             "implausible bar time %s" % bar.start.isoformat(),
                             "start"))

    # A daily bar dated on a day the exchange calendar says was shut is a
    # warning, not an error: the calendar only has verified years, and a
    # CalendarDataUnavailable must not turn into a data verdict.
    if calendar is not None and bar.interval == m.DAY_1:
        try:
            if not calendar.is_trading_day(bar.session_date):
                found.append(Finding(
                    "bar_on_non_trading_day", WARNING,
                    "%s is %s on the %s calendar, yet a bar is dated to it"
                    % (bar.session_date, calendar.classify(bar.session_date),
                       calendar.exchange), "session_date"))
        except Exception:
            pass

    return m.Quality(worst([f.severity for f in found]) if found else VALID, found)


# --------------------------------------------------------------- one series

def validate_series(bars, thresholds=None, calendar=None, expect_ordered=True):
    # type: (Any, Optional[Thresholds], Any, bool) -> m.Quality
    """Check a list of bars as a series: order, duplicates, gaps, jumps."""
    th = thresholds or DEFAULT_THRESHOLDS
    found = []  # type: List[Finding]
    bars = list(bars)

    if not bars:
        # An empty series is not a verdict on its own. provenance.DataResult
        # decides whether no rows means a quiet filter or a dead feed.
        return m.Quality(VALID, [])

    verdicts = []
    for bar in bars:
        q = validate_bar(bar, th, calendar)
        bar.quality = q
        verdicts.append(q.verdict)
        found.extend(q.findings)

    symbols = set(b.symbol for b in bars)
    if len(symbols) > 1:
        found.append(Finding(
            "mixed_symbols", INVALID,
            "one series carries more than one instrument: %s"
            % ", ".join(sorted(symbols)), "symbol"))
    intervals = set(b.interval for b in bars)
    if len(intervals) > 1:
        found.append(Finding(
            "mixed_intervals", INVALID,
            "one series mixes intervals: %s" % ", ".join(sorted(intervals)),
            "interval"))
    providers = set(b.provenance.provider for b in bars)
    if len(providers) > 1:
        found.append(Finding(
            "mixed_providers", INVALID,
            "one series mixes providers: %s. Prices from two sources must "
            "not be stitched into one series" % ", ".join(sorted(providers)),
            "provenance"))

    seen = {}
    for bar in bars:
        key = bar.start
        if key in seen:
            same = (seen[key].close.as_paise == bar.close.as_paise
                    and seen[key].volume == bar.volume)
            found.append(Finding(
                "duplicate_timestamp" if same else "conflicting_duplicate",
                WARNING if same else INVALID,
                ("the same bar appears twice at %s" if same else
                 "two different bars claim the same time %s")
                % key.isoformat(), "start"))
        seen[key] = bar

    starts = [b.start for b in bars]
    if expect_ordered and starts != sorted(starts):
        found.append(Finding(
            "unordered", INCOMPLETE,
            "bars are not in time order, so any window over them is wrong",
            "start"))

    ordered = sorted(bars, key=lambda b: b.start)
    for prev, cur in zip(ordered, ordered[1:]):
        move = _pct(cur.close.as_paise, prev.close.as_paise)
        size = abs(move)
        if size >= th.split_pct:
            found.append(Finding(
                "possible_corporate_action", WARNING,
                "close moved %.1f percent from %s to %s. In an unadjusted "
                "series a move this size is usually a split or a bonus issue, "
                "not bad data. Verify before treating it as a return"
                % (move, prev.session_date, cur.session_date), "close"))
        elif size >= th.suspicious_pct:
            found.append(Finding(
                "extreme_move", WARNING,
                "close moved %.1f percent from %s to %s, beyond the widest "
                "NSE circuit band" % (move, prev.session_date, cur.session_date),
                "close"))
        elif size >= th.circuit_pct:
            found.append(Finding(
                "circuit_sized_move", WARNING,
                "close moved %.1f percent from %s to %s, the size of a "
                "circuit limit day. Reported, not discarded"
                % (move, prev.session_date, cur.session_date), "close"))

    if calendar is not None and ordered and ordered[0].interval == m.DAY_1:
        try:
            expected = calendar.trading_days_between(
                ordered[0].session_date, ordered[-1].session_date)
            have = set(b.session_date for b in ordered)
            missing = [d for d in expected if d not in have]
            if missing:
                found.append(Finding(
                    "missing_sessions", INCOMPLETE,
                    "%d trading day(s) in the range have no bar: %s%s"
                    % (len(missing), ", ".join(str(d) for d in missing[:5]),
                       " and more" if len(missing) > 5 else ""),
                    "session_date"))
        except Exception:
            pass

    verdicts.extend(f.severity for f in found)
    return m.Quality(worst(verdicts) if verdicts else VALID, found)


# ---------------------------------------------------------------- one quote

def validate_quote(quote, thresholds=None):
    # type: (m.Quote, Optional[Thresholds]) -> m.Quality
    """Check one quote."""
    th = thresholds or DEFAULT_THRESHOLDS
    found = []  # type: List[Finding]

    if quote.last_price.as_paise <= 0:
        found.append(Finding("non_positive_price", INVALID,
                             "last price is %s" % quote.last_price.format(),
                             "last_price"))
    if quote.currency != "INR":
        found.append(Finding("unexpected_currency", INVALID,
                             "quote is in %s, not INR" % quote.currency,
                             "currency"))

    # An index carries no exchange of its own: the Nifty 50 is ^NSEI, with no
    # suffix, which is phase 2's reason for modelling it as a separate type. A
    # missing exchange on an index is correct; on an instrument it is not.
    is_index = quote.instrument_type == m.INDEX
    allowed = (None, "NSE", "BSE") if is_index else ("NSE", "BSE")
    if quote.exchange not in allowed:
        found.append(Finding("unknown_exchange", INVALID,
                             "%r is not an exchange this system knows"
                             % (quote.exchange,), "exchange"))

    day = [("high", quote.high), ("low", quote.low), ("open", quote.open)]
    missing = [name for name, value in day if value is None]
    if missing:
        found.append(Finding(
            "day_fields_missing", INCOMPLETE,
            "no %s on this quote" % ", ".join(missing), missing[0]))

    if quote.high is not None and quote.low is not None:
        if quote.high.as_paise < quote.low.as_paise:
            found.append(Finding("high_below_low", INVALID,
                                 "day high %s is below day low %s"
                                 % (quote.high.format(symbol=False),
                                    quote.low.format(symbol=False)), "high"))
        elif not (quote.low.as_paise <= quote.last_price.as_paise <= quote.high.as_paise):
            found.append(Finding(
                "last_outside_day_range", INVALID,
                "last price %s sits outside the day range %s to %s"
                % (quote.last_price.format(symbol=False),
                   quote.low.format(symbol=False),
                   quote.high.format(symbol=False)), "last_price"))

    if quote.volume is None:
        # Not reported for an index, which has no volume of its own.
        if not is_index:
            found.append(Finding("volume_missing", INCOMPLETE,
                                 "no volume on this quote", "volume"))
    elif quote.volume < 0:
        found.append(Finding("negative_volume", INVALID,
                             "volume is %d" % quote.volume, "volume"))

    if quote.previous_close is None:
        found.append(Finding(
            "previous_close_missing", INCOMPLETE,
            "no previous close, so the day's change cannot be stated",
            "previous_close"))
    else:
        move = quote.change_pct or 0.0
        size = abs(move)
        if size >= th.split_pct:
            found.append(Finding(
                "possible_corporate_action", WARNING,
                "%.1f percent against the previous close. Usually a split or "
                "a bonus issue rather than bad data" % move, "previous_close"))
        elif size >= th.suspicious_pct:
            found.append(Finding(
                "extreme_move", WARNING,
                "%.1f percent against the previous close, beyond the widest "
                "NSE circuit band" % move, "previous_close"))
        elif size >= th.circuit_pct:
            found.append(Finding(
                "circuit_sized_move", WARNING,
                "%.1f percent against the previous close, the size of a "
                "circuit limit day" % move, "previous_close"))

    return m.Quality(worst([f.severity for f in found]) if found else VALID,
                     found)
