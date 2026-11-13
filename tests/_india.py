"""Shared setup for the indiacore market data tests. Offline, always.

Every test builds its providers around a FixtureTransport, so nothing in the
suite reaches the network and nothing depends on what the market did today.
The clock is pinned too: NOW_OPEN and NOW_CLOSED are real moments on a real
NSE trading day, 13 November 2026, which is a Friday.
"""

import datetime
import os

import _load  # noqa: F401
from indiacore import rate_limit, retry
from indiacore.marketdata import cache as cache_mod
from indiacore.providers import registry as registry_mod
from indiacore.providers import transport as tr

FIX = os.path.join(_load.FIX, "indiacore")

IST = datetime.timezone(datetime.timedelta(minutes=330), "IST")

# The session the recorded fixtures came from.
SESSION = datetime.date(2026, 11, 13)
PREVIOUS_SESSION = datetime.date(2026, 11, 12)

# Mid session, a few seconds after the SBIN fixture's own regularMarketTime
# of 15:28:09.
NOW_OPEN = datetime.datetime(2026, 11, 13, 15, 28, 30, tzinfo=IST)
# After the 15:30 close, same day.
NOW_CLOSED = datetime.datetime(2026, 11, 13, 16, 10, 0, tzinfo=IST)
# Before the pre open, the next trading day.
NOW_NEXT_MORNING = datetime.datetime(2026, 11, 16, 8, 30, 0, tzinfo=IST)
# A week later, so the same figures are far behind.
NOW_WEEK_LATER = datetime.datetime(2026, 11, 20, 16, 10, 0, tzinfo=IST)


def fx(name):
    # type: (str) -> str
    return os.path.join(FIX, name)


def routes(**overrides):
    """The default fixture routing, keyed by a fragment of the URL."""
    table = {
        "SBIN.NS?interval=1d": fx("yahoo-SBIN.NS-1d-5d.json"),
        "M%26M.NS": fx("yahoo-MandM.NS-1d-5d.json"),
        "%5ENSEI": fx("yahoo-NSEI-1d-5d.json"),
        "INFY.BO": fx("yahoo-INFY.BO-1d-5d.json"),
        "NIFTYBEES.NS": fx("yahoo-NIFTYBEES.NS-1d-5d.json"),
        "interval=5m": fx("yahoo-SBIN.NS-5m-1d.json"),
        "BhavCopy_NSE_CM_0_0_0_20261112":
            fx("nse-bhavcopy-20261112-excerpt.csv.zip"),
        "BhavCopy_BSE_CM_0_0_0_20261112":
            fx("bse-bhavcopy-20261112-excerpt.csv"),
    }
    table.update(overrides)
    return table


def transport(**overrides):
    return tr.FixtureTransport(routes(**overrides))


def clock(moment=NOW_OPEN):
    return lambda: moment


def open_limiter(name):
    """A limiter that spaces nothing, so no test waits on one."""
    return rate_limit.RateLimiter(name, min_interval=0.0)


def quick_policy():
    """Two attempts and a sleep that does not sleep.

    The retry arithmetic is tested directly in test_indiacore_ratelimit; here
    it only has to run, not wait.
    """
    return retry.RetryPolicy(attempts=2, sleeper=lambda seconds: None)


def registry(moment=NOW_OPEN, transport_=None, rules=None, **overrides):
    return registry_mod.default_registry(
        transport=transport_ or transport(**overrides), clock=clock(moment),
        rules=rules, limiter_factory=open_limiter, policy=quick_policy())


def no_cache():
    return cache_mod.Cache(enabled=False)


def service(moment=NOW_OPEN, cache=None, transport_=None, rules=None,
            thresholds=None, calendar=None, **overrides):
    """A MarketDataService wired to fixtures and a pinned clock.

    Any remaining keyword is a fixture route override, keyed by a fragment of
    the URL, so a test can swap one payload without rebuilding the registry.
    """
    from indiacore import data_service

    return data_service.MarketDataService(
        registry=registry(moment, transport_, rules, **overrides),
        cache=cache if cache is not None else no_cache(),
        clock=clock(moment), rules=rules, thresholds=thresholds,
        calendar=calendar)
