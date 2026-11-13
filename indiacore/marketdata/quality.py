"""How old is this figure, and is that old enough to matter.

The rule the whole phase hangs on: a previous session's price must never be
presented as a current market price. That is not solved by a single age
threshold, because the same age means different things at different times.
At 09:00 on a Friday, yesterday's close is the newest figure that exists and
is perfectly good end of day data. At 11:00, with the market open, a figure
from yesterday's close is stale and must not be shown as a quote.

So freshness is decided from four things together: the source timestamp, the
market state from phase 2's Market, the interval, and what the provider
actually promises. The verdicts are phase 2's provenance vocabulary.

    LIVE         only when the provider contractually delivers real time.
                 No provider in this repo does, so nothing is classified
                 LIVE today. The branch exists so that adding a licensed
                 feed later is a capability flag, not a rewrite.
    DELAYED      the market is open and the figure is recent, but the
                 provider does not promise real time delivery.
    END_OF_DAY   the market is shut and the figure belongs to the last
                 completed session.
    HISTORICAL   the figure belongs to a session before the last completed
                 one. Correct and useful, and never a current price.
    STALE        the age is known and too great for what is being asked.
    UNKNOWN      the age cannot be established at all, usually because the
                 provider sent no timestamp.
"""

import datetime
from typing import Any, Optional, Tuple

from .. import calendar_in as cal
from .. import market as mkt
from .. import provenance as prov
from . import models as m

# Yahoo serves Indian quotes without declaring a delay anywhere in the
# payload. Checked against the live endpoint: the chart meta carries
# regularMarketTime, currency, exchangeName and gmtoffset, and no
# exchangeDataDelayedBy field of any kind. A quote whose source timestamp is
# within this window of now, during market hours, is therefore reported as
# DELAYED with an undeclared delay, never as LIVE.
DEFAULT_OPEN_TOLERANCE_SECONDS = 900

# After the close, a quote is expected to carry a timestamp from the session
# that just finished. This allows for a provider that stamps its end of day
# figure late.
DEFAULT_CLOSED_TOLERANCE_SECONDS = 6 * 3600


class FreshnessRules(object):
    """Configurable tolerances. One instance per profile, passed in.

    `max_quote_age_open_seconds` is the only knob most callers need: how old
    an intraday figure may be before it stops being a quote. The intraday bar
    tolerance is a multiple of the bar interval rather than a fixed number of
    seconds, because a 5 minute bar and a 60 minute bar age differently.
    """

    __slots__ = ("max_quote_age_open_seconds", "max_quote_age_closed_seconds",
                 "intraday_bar_stale_multiple", "max_eod_sessions_behind",
                 "allow_unknown_as_usable")

    def __init__(self, max_quote_age_open_seconds=DEFAULT_OPEN_TOLERANCE_SECONDS,
                 max_quote_age_closed_seconds=DEFAULT_CLOSED_TOLERANCE_SECONDS,
                 intraday_bar_stale_multiple=3.0,
                 max_eod_sessions_behind=1,
                 allow_unknown_as_usable=False):
        self.max_quote_age_open_seconds = max_quote_age_open_seconds
        self.max_quote_age_closed_seconds = max_quote_age_closed_seconds
        self.intraday_bar_stale_multiple = intraday_bar_stale_multiple
        self.max_eod_sessions_behind = max_eod_sessions_behind
        # Deliberately false. An unknown age is not a usable age.
        self.allow_unknown_as_usable = allow_unknown_as_usable


DEFAULT_RULES = FreshnessRules()


def _age(now, then):
    # type: (datetime.datetime, datetime.datetime) -> float
    return (cal.to_ist(now) - cal.to_ist(then)).total_seconds()


def classify_quote(source_time, now, market, capabilities, rules=None):
    # type: (Optional[datetime.datetime], datetime.datetime, mkt.Market, m.Capabilities, Optional[FreshnessRules]) -> Tuple[str, str]
    """Return (freshness, reason) for a quote.

    `market` is a phase 2 Market, so the answer accounts for weekends,
    exchange holidays and Muhurat without this module knowing any dates.
    """
    rules = rules or DEFAULT_RULES
    state = market.state_at(now)

    if source_time is None:
        return (prov.UNKNOWN,
                "%s sent no source timestamp, so the age of this figure "
                "cannot be established" % capabilities.provider)

    age = _age(now, source_time)
    if age < -300:
        return (prov.UNKNOWN,
                "source timestamp %s is %.0f seconds in the future, so it "
                "cannot be trusted as an age"
                % (cal.to_ist(source_time).isoformat(), -age))

    if state.state == mkt.OPEN:
        if age <= rules.max_quote_age_open_seconds:
            if capabilities.realtime:
                return (prov.LIVE,
                        "%s delivers real time data and stamped this %.0f "
                        "seconds ago" % (capabilities.provider, age))
            declared = capabilities.declared_delay_seconds
            return (prov.DELAYED,
                    "market is open and the figure is %.0f seconds old. %s "
                    "%s, so this is not a real time price"
                    % (age, capabilities.provider,
                       "declares a %ds delay" % declared if declared is not None
                       else "declares no delay and promises no real time "
                            "delivery"))
        return (prov.STALE,
                "market is open and the newest figure %s has is %.0f seconds "
                "old, over the %ds tolerance. It is a past price, not a quote"
                % (capabilities.provider, age, rules.max_quote_age_open_seconds))

    # Market shut. The question becomes which session the figure belongs to.
    last_session = market.last_completed_session(now)
    stamp_date = cal.to_ist(source_time).date()

    if state.state == mkt.PRE_OPEN:
        if stamp_date == last_session:
            return (prov.END_OF_DAY,
                    "pre open auction. The figure is the %s close from %s"
                    % (market.code, last_session))
        return (_behind(stamp_date, last_session, market, rules,
                        capabilities, "pre open auction"))

    if stamp_date == last_session:
        return (prov.END_OF_DAY,
                "market is shut (%s) and the figure is the %s close from %s"
                % (state.state.replace("_", " "), market.code, last_session))

    if stamp_date > last_session:
        return (prov.UNKNOWN,
                "the figure is stamped %s but the last completed %s session "
                "is %s, so the two disagree"
                % (stamp_date, market.code, last_session))

    return _behind(stamp_date, last_session, market, rules, capabilities,
                   "market is shut")


def _behind(stamp_date, last_session, market, rules, capabilities, prefix):
    # type: (datetime.date, datetime.date, mkt.Market, FreshnessRules, m.Capabilities, str) -> Tuple[str, str]
    """A figure from before the last completed session."""
    try:
        sessions = market.calendar.trading_days_between(stamp_date, last_session)
        behind = max(len(sessions) - 1, 0)
    except Exception:
        behind = (last_session - stamp_date).days
    if behind <= rules.max_eod_sessions_behind:
        return (prov.HISTORICAL,
                "%s. The figure is from %s, %d completed session(s) before "
                "the last %s session on %s. Valid history, not a current price"
                % (prefix, stamp_date, behind, market.code, last_session))
    return (prov.STALE,
            "%s. The newest figure %s has is from %s, %d sessions behind the "
            "last %s session on %s"
            % (prefix, capabilities.provider, stamp_date, behind, market.code,
               last_session))


def classify_bars(bars, now, market, capabilities, interval=m.DAY_1,
                  rules=None):
    # type: (Any, datetime.datetime, mkt.Market, m.Capabilities, str, Optional[FreshnessRules]) -> Tuple[str, str]
    """Return (freshness, reason) for a series of bars.

    A completed daily bar is end of day data for its own session, and the
    newest bar decides the verdict for the series.
    """
    rules = rules or DEFAULT_RULES
    bars = list(bars)
    if not bars:
        return (prov.UNKNOWN, "no bars, so there is no age to establish")

    newest = max(bars, key=lambda b: b.start)
    last_session = market.last_completed_session(now)
    state = market.state_at(now)

    if m.is_intraday(interval):
        age = _age(now, newest.end)
        window = m.INTERVAL_SECONDS[interval] * rules.intraday_bar_stale_multiple
        if state.state == mkt.OPEN:
            if age <= window:
                return (prov.DELAYED,
                        "newest %s bar ended %.0f seconds ago, within %.0f "
                        "seconds of now while the market is open"
                        % (interval, age, window))
            return (prov.STALE,
                    "newest %s bar ended %.0f seconds ago, over the %.0f "
                    "second window, while the market is open"
                    % (interval, age, window))
        if newest.session_date == last_session:
            return (prov.END_OF_DAY,
                    "market is shut and the newest %s bar is from the last "
                    "completed session, %s" % (interval, last_session))
        return _behind(newest.session_date, last_session, market, rules,
                       capabilities, "market is shut")

    if newest.session_date == last_session:
        return (prov.END_OF_DAY,
                "the newest daily bar is the last completed %s session, %s"
                % (market.code, last_session))
    if newest.session_date > last_session:
        return (prov.UNKNOWN,
                "the newest daily bar is dated %s, after the last completed "
                "%s session on %s. A bar for a session that has not finished "
                "is not an end of day figure"
                % (newest.session_date, market.code, last_session))
    return _behind(newest.session_date, last_session, market, rules,
                   capabilities, "daily series")


def usable(freshness):
    # type: (str) -> bool
    """Whether this freshness may be used for a decision. Phase 2's rule."""
    return freshness in prov.USABLE_FRESHNESS


def is_current_price(freshness):
    # type: (str) -> bool
    """Whether a figure of this freshness may be shown as the market price now.

    END_OF_DAY and HISTORICAL are both valid data and both false here. That
    distinction is the point of the module.
    """
    return freshness in (prov.LIVE, prov.DELAYED)
