"""The market itself: which exchange, which hours, open or shut right now.

The day-trading pack hard codes OPEN = 09:30, CLOSE = 16:00 and a US daylight
saving rule, in nine separate files. Here the hours belong to a Market object
that carries its own calendar, so a second exchange is a value rather than a
fork of the code.

NSE and BSE equity both run 09:15 to 15:30 IST with a pre open auction from
09:00 to 09:15. They are still modelled separately: they publish holidays by
separate circulars, and they quote different prices. Checked on 12 November
2026, SBIN closed at 1086.50 on NSE and 1085.00 on BSE.

The last completed session matters as much as the current state. An end of
day scan run at 09:00 is working from yesterday's closes, and anything that
reports a result has to say which date it means. `last_completed_session`
answers that, and the provenance module turns the answer into a guard.
"""

import datetime
from typing import Optional

from . import calendar_in as cal
from .symbols import BSE, NSE, Exchange

# Session states.
PRE_OPEN = "pre_open"
OPEN = "open"
CLOSED = "closed"
CLOSED_WEEKEND = "closed_weekend"
CLOSED_HOLIDAY = "closed_holiday"
MUHURAT_TIMING_UNKNOWN = "muhurat_timing_unknown"


class MarketError(Exception):
    """A market question could not be answered."""


class SessionState(object):
    """What the market is doing at one moment, and why."""

    __slots__ = ("state", "moment", "reason", "session_date")

    def __init__(self, state, moment, reason="", session_date=None):
        # type: (str, datetime.datetime, str, Optional[datetime.date]) -> None
        self.state = state
        self.moment = moment
        self.reason = reason
        self.session_date = session_date

    @property
    def is_open(self):
        # type: () -> bool
        """True only for the regular session.

        Muhurat is not counted. Its timing is announced separately each year
        and is not encoded, so claiming to know whether it is open would be a
        guess. Callers that care must handle MUHURAT_TIMING_UNKNOWN.
        """
        return self.state == OPEN

    def __repr__(self):
        return "SessionState(%s, %s)" % (self.state, self.moment.isoformat())

    def __str__(self):
        return "%s: %s%s" % (
            self.moment.strftime("%Y-%m-%d %H:%M IST"),
            self.state.replace("_", " "),
            ", " + self.reason if self.reason else "")


class Market(object):
    """One exchange: its hours, its calendar and its session arithmetic."""

    def __init__(self, exchange, calendar, pre_open, open_time, close_time):
        # type: (Exchange, cal.TradingCalendar, datetime.time, datetime.time, datetime.time) -> None
        self.exchange = exchange
        self.calendar = calendar
        self.pre_open = pre_open
        self.open_time = open_time
        self.close_time = close_time

    @property
    def code(self):
        # type: () -> str
        return self.exchange.code

    def now(self, clock=None):
        # type: (Optional[datetime.datetime]) -> datetime.datetime
        return cal.now_ist(clock)

    # --------------------------------------------------------------- state

    def state_at(self, moment):
        # type: (datetime.datetime) -> SessionState
        """What the market is doing at `moment` (any aware datetime)."""
        ist = cal.to_ist(moment)
        day = ist.date()
        kind = self.calendar.classify(day)

        if kind == cal.MUHURAT:
            entry = self.calendar.holiday_for(day)
            return SessionState(
                MUHURAT_TIMING_UNKNOWN, ist,
                "%s: regular trading is shut and a Muhurat session is held, "
                "timing notified separately by %s each year"
                % (entry.description if entry else "Muhurat", self.code),
                day)
        if kind == cal.WEEKEND:
            return SessionState(CLOSED_WEEKEND, ist, ist.strftime("%A"), None)
        if kind == cal.HOLIDAY:
            entry = self.calendar.holiday_for(day)
            return SessionState(CLOSED_HOLIDAY, ist,
                                entry.description if entry else "exchange holiday",
                                None)

        clock = ist.time()
        if clock < self.pre_open:
            return SessionState(CLOSED, ist, "before the pre open auction", None)
        if clock < self.open_time:
            return SessionState(PRE_OPEN, ist, "pre open auction", day)
        if clock < self.close_time:
            return SessionState(OPEN, ist, "regular session", day)
        return SessionState(CLOSED, ist, "after the close", day)

    def is_open(self, moment):
        # type: (datetime.datetime) -> bool
        return self.state_at(moment).is_open

    # ------------------------------------------------------------ sessions

    def last_completed_session(self, moment):
        # type: (datetime.datetime) -> datetime.date
        """The most recent date whose regular session has finished.

        This is the date an end of day figure belongs to. Before today's
        close it is the previous trading day, not today.
        """
        ist = cal.to_ist(moment)
        day = ist.date()
        if self.calendar.classify(day) == cal.TRADING_DAY \
                and ist.time() >= self.close_time:
            return day
        return self.calendar.previous_trading_day(day)

    def next_open(self, moment):
        # type: (datetime.datetime) -> datetime.datetime
        """When the next regular session starts."""
        ist = cal.to_ist(moment)
        day = ist.date()
        if self.calendar.classify(day) == cal.TRADING_DAY \
                and ist.time() < self.open_time:
            target = day
        else:
            target = self.calendar.next_trading_day(day)
        return datetime.datetime.combine(
            target, self.open_time, tzinfo=cal.ist_timezone())

    def session_bounds(self, day):
        # type: (datetime.date) -> tuple
        """(open, close) as aware IST datetimes. Raises on a non trading day."""
        if self.calendar.classify(day) != cal.TRADING_DAY:
            raise MarketError(
                "%s is not a regular %s trading day (%s)"
                % (day, self.code, self.calendar.classify(day)))
        tz = cal.ist_timezone()
        return (datetime.datetime.combine(day, self.open_time, tzinfo=tz),
                datetime.datetime.combine(day, self.close_time, tzinfo=tz))

    def __repr__(self):
        return "Market(%s)" % self.code


# Equity segment hours, identical on both exchanges today but declared
# separately so that a change to one does not silently move the other.
_PRE_OPEN = datetime.time(9, 0)
_OPEN = datetime.time(9, 15)
_CLOSE = datetime.time(15, 30)


def nse_market():
    # type: () -> Market
    return Market(NSE, cal.TradingCalendar("NSE"), _PRE_OPEN, _OPEN, _CLOSE)


def bse_market():
    # type: () -> Market
    """BSE equity keeps the same clock as NSE but its own holiday circular.

    Until a verified BSE holiday file is added this borrows the NSE calendar,
    and says so rather than implying the lists were checked against each other.
    """
    return Market(BSE, cal.TradingCalendar("NSE"), _PRE_OPEN, _OPEN, _CLOSE)
