"""Every market figure carries where it came from and how old it is.

This exists because of a failure that is easy to reproduce. Point the shipped
scanner at Indian symbols and it says:

    Fetched 0 of 3 charts (3 failed or had no data). 0 scanned.
    No setups.

The JSON it writes is honest (failed: 3, as_of: null), but "No setups" is the
line a person reads, and a downstream skill that loads hits: [] cannot tell a
quiet market from a dead data feed unless it remembers to check two other
fields. For a system that decides where money goes, those two outcomes must
not share a representation.

DataResult makes the difference structural. An empty result is only valid if
something was actually scanned and the figures are fresh enough to use.
`require_usable` raises instead of returning an empty list.
"""

import datetime
from typing import Any, List, Optional

# How close to live a figure is.
LIVE = "live"
DELAYED = "delayed"
END_OF_DAY = "end_of_day"
HISTORICAL = "historical"
STALE = "stale"
UNAVAILABLE = "unavailable"

USABLE_FRESHNESS = (LIVE, DELAYED, END_OF_DAY, HISTORICAL)


class DataError(Exception):
    """Base class for data quality problems."""


class StaleDataError(DataError):
    """The figures are too old, or were never retrieved."""


class EmptyResultError(DataError):
    """Nothing came back, and nothing was successfully scanned either."""


class DataResult(object):
    """Rows plus the provenance needed to decide whether to trust them."""

    __slots__ = ("rows", "source", "as_of", "retrieved_at", "freshness",
                 "requested", "fetched", "failed", "errors")

    def __init__(self, rows, source, as_of=None, retrieved_at=None,
                 freshness=UNAVAILABLE, requested=0, fetched=0, failed=0,
                 errors=None):
        # type: (List[Any], str, Optional[datetime.date], Optional[datetime.datetime], str, int, int, int, Optional[List[str]]) -> None
        if not source:
            raise DataError("every result must name its source")
        self.rows = list(rows or [])
        self.source = source
        self.as_of = as_of
        self.retrieved_at = retrieved_at
        self.freshness = freshness
        self.requested = int(requested)
        self.fetched = int(fetched)
        self.failed = int(failed)
        self.errors = list(errors or [])

    # ------------------------------------------------------------ verdicts

    @property
    def complete(self):
        # type: () -> bool
        """Every requested item came back."""
        return self.requested > 0 and self.failed == 0 and self.fetched == self.requested

    @property
    def is_usable(self):
        # type: () -> bool
        """Safe to base a decision on.

        Requires a known as_of date, a usable freshness, at least one item
        actually fetched, and no failures. A partial fetch is not usable: a
        scan that reached 3 of 100 names is not a view of the market.
        """
        return (self.as_of is not None
                and self.freshness in USABLE_FRESHNESS
                and self.fetched > 0
                and self.failed == 0)

    @property
    def is_empty_and_valid(self):
        # type: () -> bool
        """A real, trustworthy "nothing matched".

        True only when the data was good and the filter simply matched
        nothing. This is the state the shipped scanner conflates with total
        fetch failure.
        """
        return self.is_usable and not self.rows

    def require_usable(self):
        # type: () -> DataResult
        """Return self, or raise explaining exactly what is wrong."""
        if self.freshness == UNAVAILABLE:
            raise StaleDataError(
                "%s returned nothing usable: freshness is %s"
                % (self.source, self.freshness))
        if self.as_of is None:
            raise StaleDataError(
                "%s returned rows with no as_of date, so their age is unknown"
                % self.source)
        if self.freshness == STALE:
            raise StaleDataError(
                "%s data for %s is marked stale" % (self.source, self.as_of))
        if self.fetched == 0:
            raise EmptyResultError(
                "%s fetched 0 of %d items (%d failed). This is a data failure, "
                "not an empty result. First errors: %s"
                % (self.source, self.requested, self.failed,
                   "; ".join(self.errors[:3]) or "none recorded"))
        if self.failed:
            raise EmptyResultError(
                "%s fetched %d of %d items but %d failed, so the picture is "
                "incomplete. First errors: %s"
                % (self.source, self.fetched, self.requested, self.failed,
                   "; ".join(self.errors[:3]) or "none recorded"))
        return self

    def staleness_days(self, today):
        # type: (datetime.date) -> Optional[int]
        if self.as_of is None:
            return None
        return (today - self.as_of).days

    # -------------------------------------------------------------- render

    def summary(self):
        # type: () -> str
        return ("%s: %d rows, %d/%d fetched, %d failed, as_of %s, %s"
                % (self.source, len(self.rows), self.fetched, self.requested,
                   self.failed, self.as_of.isoformat() if self.as_of else "unknown",
                   self.freshness))

    def to_dict(self):
        # type: () -> dict
        return {
            "source": self.source,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "freshness": self.freshness,
            "requested": self.requested,
            "fetched": self.fetched,
            "failed": self.failed,
            "usable": self.is_usable,
            "empty_but_valid": self.is_empty_and_valid,
            "rows": len(self.rows),
            "errors": self.errors[:10],
        }

    def __repr__(self):
        return "DataResult(%s)" % self.summary()


def unavailable(source, requested=0, errors=None):
    # type: (str, int, Optional[List[str]]) -> DataResult
    """A result that can never be mistaken for a valid empty answer."""
    return DataResult([], source, requested=requested, failed=requested,
                      freshness=UNAVAILABLE, errors=errors)
