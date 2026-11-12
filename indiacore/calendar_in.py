"""The Indian trading calendar: IST, NSE holidays, and Muhurat kept separate.

Three things make this different from the NYSE calendar the day-trading pack
carries in nine copies:

1. India does not observe daylight saving. IST is a flat UTC+05:30 all year,
   so there is no equivalent of eastern_offset() and nothing to get wrong
   twice a year.
2. The holiday list is not derivable. US market holidays follow rules you can
   compute (the third Monday in January, and so on). Indian exchange holidays
   follow lunar festival dates that are announced by circular each year, so
   the only correct source is the exchange's own published list.
3. Muhurat trading sits outside the normal model. On Diwali the market is
   shut for regular trading and opens for one short ceremonial session whose
   timing NSE notifies separately each year. In 2026 that day, 8 November,
   was a Sunday. A calendar that answered a plain yes or no would be wrong
   either way, so this module answers with a third state.

Because the list is announced rather than computed, a year with no verified
data raises. It never falls back to last year's dates and never guesses from
a pattern. As of this writing NSE had published 2026 but not 2027: NSE issues
the next year's circular in December.
"""

import datetime
import json
import os
from typing import Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# India Standard Time. Fixed, no daylight saving, all year, every year.
IST_OFFSET_MINUTES = 330
IST_NAME = "IST"

SATURDAY = 5
SUNDAY = 6

# What a given date is for trading purposes.
TRADING_DAY = "trading_day"
WEEKEND = "weekend"
HOLIDAY = "holiday"
MUHURAT = "muhurat"


class CalendarError(Exception):
    """Base class for calendar problems."""


class CalendarDataUnavailable(CalendarError):
    """No verified exchange holiday list for that year.

    Raised rather than guessed. Indian exchange holidays are announced by
    circular, not computed from rules, so an unverified year has no safe
    default.
    """


def ist_timezone():
    # type: () -> datetime.timezone
    """IST as a fixed offset. No zoneinfo, so this works on Python 3.8."""
    return datetime.timezone(datetime.timedelta(minutes=IST_OFFSET_MINUTES), IST_NAME)


def now_ist(clock=None):
    # type: (Optional[datetime.datetime]) -> datetime.datetime
    """Current time in IST. Pass `clock` (an aware datetime) to pin it."""
    if clock is None:
        clock = datetime.datetime.now(datetime.timezone.utc)
    if clock.tzinfo is None:
        raise CalendarError("pass an aware datetime, otherwise the zone is a guess")
    return clock.astimezone(ist_timezone())


def to_ist(moment):
    # type: (datetime.datetime) -> datetime.datetime
    return now_ist(moment)


class Holiday(object):
    """One published closure."""

    __slots__ = ("date", "description", "weekday", "muhurat_marked")

    def __init__(self, date, description, weekday, muhurat_marked=False):
        # type: (datetime.date, str, str, bool) -> None
        self.date = date
        self.description = description
        self.weekday = weekday
        self.muhurat_marked = muhurat_marked

    def __repr__(self):
        return "Holiday(%s, %r)" % (self.date.isoformat(), self.description)


class TradingCalendar(object):
    """Published holidays for one exchange, loaded per year, with provenance.

    Years are loaded lazily and cached. A year with no data file raises
    CalendarDataUnavailable the first time it is needed.
    """

    def __init__(self, exchange="NSE", data_dir=DATA_DIR):
        # type: (str, str) -> None
        self.exchange = exchange.upper()
        self.data_dir = data_dir
        self._years = {}      # type: Dict[int, Dict[datetime.date, Holiday]]
        self._sources = {}    # type: Dict[int, dict]

    # ------------------------------------------------------------- loading

    def _path_for(self, year):
        # type: (int) -> str
        return os.path.join(self.data_dir, "%s_holidays_%d.json"
                            % (self.exchange.lower(), year))

    def available_years(self):
        # type: () -> List[int]
        years = []
        prefix = "%s_holidays_" % self.exchange.lower()
        if not os.path.isdir(self.data_dir):
            return years
        for name in os.listdir(self.data_dir):
            if name.startswith(prefix) and name.endswith(".json"):
                stem = name[len(prefix):-len(".json")]
                if stem.isdigit():
                    years.append(int(stem))
        return sorted(years)

    def _load(self, year):
        # type: (int) -> Dict[datetime.date, Holiday]
        if year in self._years:
            return self._years[year]
        path = self._path_for(year)
        if not os.path.exists(path):
            raise CalendarDataUnavailable(
                "no verified %s holiday list for %d (looked for %s). "
                "Indian exchange holidays are announced by circular, not "
                "computed, so this calendar will not guess. Years on hand: %s. "
                "%s publishes the next year's list by circular in December."
                % (self.exchange, year, os.path.basename(path),
                   self.available_years() or "none", self.exchange))
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        source = doc.get("source") or {}
        if not source.get("verified"):
            raise CalendarDataUnavailable(
                "the %s holiday list for %d is present but not marked verified; "
                "refusing to use it" % (self.exchange, year))
        table = {}
        for row in doc.get("holidays", []):
            d = datetime.date(*[int(p) for p in row["date"].split("-")])
            table[d] = Holiday(d, row.get("description", ""),
                               row.get("weekday", ""),
                               bool(row.get("muhurat_marked")))
        self._years[year] = table
        self._sources[year] = source
        return table

    def source_for(self, year):
        # type: (int) -> dict
        """Where that year's list came from. Loads the year if needed."""
        self._load(year)
        return dict(self._sources[year])

    # ------------------------------------------------------------ querying

    def classify(self, day):
        # type: (datetime.date) -> str
        """One of TRADING_DAY, WEEKEND, HOLIDAY, MUHURAT.

        MUHURAT means regular trading is shut and a special session is held
        whose timing is announced separately. It is deliberately not folded
        into TRADING_DAY or HOLIDAY, because it is neither.
        """
        table = self._load(day.year)
        entry = table.get(day)
        if entry is not None and entry.muhurat_marked:
            return MUHURAT
        if day.weekday() in (SATURDAY, SUNDAY):
            return WEEKEND
        if entry is not None:
            return HOLIDAY
        return TRADING_DAY

    def is_trading_day(self, day):
        # type: (datetime.date) -> bool
        """True only for a normal full session. Muhurat is not one."""
        return self.classify(day) == TRADING_DAY

    def holiday_for(self, day):
        # type: (datetime.date) -> Optional[Holiday]
        return self._load(day.year).get(day)

    def is_muhurat(self, day):
        # type: (datetime.date) -> bool
        return self.classify(day) == MUHURAT

    def holidays(self, year, weekdays_only=False):
        # type: (int, bool) -> List[Holiday]
        rows = sorted(self._load(year).values(), key=lambda h: h.date)
        if weekdays_only:
            rows = [h for h in rows if h.date.weekday() not in (SATURDAY, SUNDAY)]
        return rows

    # ------------------------------------------------------------ stepping

    def next_trading_day(self, day, limit=40):
        # type: (datetime.date, int) -> datetime.date
        step = day
        for _ in range(limit):
            step = step + datetime.timedelta(days=1)
            if self.is_trading_day(step):
                return step
        raise CalendarError("no trading day within %d days of %s" % (limit, day))

    def previous_trading_day(self, day, limit=40):
        # type: (datetime.date, int) -> datetime.date
        step = day
        for _ in range(limit):
            step = step - datetime.timedelta(days=1)
            if self.is_trading_day(step):
                return step
        raise CalendarError("no trading day within %d days before %s" % (limit, day))

    def trading_days_between(self, start, end):
        # type: (datetime.date, datetime.date) -> List[datetime.date]
        """Inclusive of both ends."""
        if end < start:
            raise CalendarError("end %s is before start %s" % (end, start))
        out = []
        step = start
        while step <= end:
            if self.is_trading_day(step):
                out.append(step)
            step = step + datetime.timedelta(days=1)
        return out


def nse_calendar():
    # type: () -> TradingCalendar
    return TradingCalendar("NSE")
