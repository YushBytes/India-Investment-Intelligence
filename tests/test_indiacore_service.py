"""The service: one door, and outcomes a caller can act on.

The question every test here asks is the one the phase exists for: can the
caller tell what it is holding. Fresh from stale, cached from fetched, a real
empty answer from a dead feed, and above all a current price from a previous
session's close.
"""

import datetime
import shutil
import tempfile
import unittest

import _india
from indiacore import data_service as svc
from indiacore import provenance as prov
from indiacore.marketdata import cache as cache_mod
from indiacore.marketdata import models as m
from indiacore.marketdata import quality, validation
from indiacore.providers import base, registry as registry_mod
from indiacore.providers import transport as tr
from indiacore.symbols import IndexSymbol, bse, nse

SESSION = _india.PREVIOUS_SESSION


class Clock(object):
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


class Quotes(unittest.TestCase):
    def test_a_quote_during_the_session_is_delayed_and_a_current_price(self):
        result = _india.service().get_quote(nse("SBIN"))
        self.assertEqual(result.outcome, svc.DELAYED)
        self.assertTrue(result.usable)
        self.assertTrue(result.is_current_price)
        self.assertEqual(str(result.first.last_price.as_rupees), "1080.00")

    def test_fresh_is_unreachable_because_no_provider_promises_real_time(self):
        result = _india.service().get_quote(nse("SBIN"))
        self.assertNotEqual(result.outcome, svc.FRESH)
        for row in _india.service().capability_matrix():
            self.assertFalse(row["realtime"], row["provider"])

    def test_the_same_payload_after_the_close_is_not_a_current_price(self):
        result = _india.service(_india.NOW_CLOSED).get_quote(nse("SBIN"))
        self.assertEqual(result.outcome, svc.END_OF_DAY)
        self.assertTrue(result.usable, "end of day data is still usable")
        self.assertFalse(result.is_current_price)

    def test_a_previous_session_figure_is_never_a_current_price(self):
        result = _india.service(_india.NOW_NEXT_MORNING).get_quote(nse("SBIN"))
        self.assertIn(result.outcome, (svc.END_OF_DAY, svc.HISTORICAL))
        self.assertFalse(result.is_current_price)

    def test_a_week_old_figure_is_stale_and_unusable(self):
        result = _india.service(_india.NOW_WEEK_LATER).get_quote(nse("SBIN"))
        self.assertEqual(result.outcome, svc.STALE)
        self.assertFalse(result.usable)
        self.assertFalse(result.is_current_price)
        self.assertRaises(prov.DataError, result.require_usable)

    def test_an_index_level_comes_back(self):
        result = _india.service().get_index_quote(IndexSymbol("^NSEI"))
        self.assertTrue(result.ok)
        self.assertEqual(result.first.instrument_type, m.INDEX)

    def test_a_bse_quote_is_served_from_the_bse_payload(self):
        result = _india.service(_india.NOW_CLOSED).get_quote(bse("INFY"))
        self.assertTrue(result.ok)
        self.assertEqual(result.first.exchange, "BSE")

    def test_a_bare_string_is_an_unsupported_request_not_a_crash(self):
        result = _india.service().get_quote("SBIN.NS")
        self.assertEqual(result.outcome, svc.UNSUPPORTED)
        self.assertIn("does not say which exchange", result.errors[0])
        self.assertRaises(base.UnsupportedRequest, result.require_usable)

    def test_a_quote_carries_its_validation_verdict(self):
        result = _india.service().get_quote(nse("SBIN"))
        self.assertEqual(result.quality.verdict, validation.VALID)


class Bars(unittest.TestCase):
    def test_daily_bars_prefer_the_exchanges_own_file(self):
        result = _india.service().get_daily_bars(nse("SBIN"), SESSION, SESSION)
        self.assertEqual(result.provider, "nse_bhavcopy")
        self.assertEqual(result.outcome, svc.END_OF_DAY)
        self.assertTrue(result.rows[0].provenance.official)

    def test_an_official_close_matches_the_exchange_file(self):
        result = _india.service().get_daily_bars(nse("SBIN"), SESSION, SESSION)
        self.assertEqual(str(result.first.close.as_rupees), "1086.50")

    def test_the_two_exchanges_are_never_the_same_record(self):
        service = _india.service()
        on_nse = service.get_daily_bars(nse("SBIN"), SESSION, SESSION)
        on_bse = service.get_daily_bars(bse("SBIN"), SESSION, SESSION)
        self.assertEqual(on_nse.provider, "nse_bhavcopy")
        self.assertEqual(on_bse.provider, "bse_bhavcopy")
        self.assertNotEqual(str(on_nse.first.close.as_rupees),
                            str(on_bse.first.close.as_rupees))

    def test_daily_bars_are_not_a_current_price(self):
        result = _india.service().get_daily_bars(nse("SBIN"), SESSION, SESSION)
        self.assertFalse(result.is_current_price)

    def test_intraday_bars_come_from_yahoo_because_nothing_else_has_them(self):
        result = _india.service().get_bars(nse("SBIN"), m.MIN_5)
        self.assertEqual(result.provider, "yahoo")
        self.assertGreater(len(result.rows), 1)

    def test_an_unknown_interval_is_unsupported(self):
        result = _india.service().get_bars(nse("SBIN"), "7m")
        self.assertEqual(result.outcome, svc.UNSUPPORTED)

    def test_a_series_carries_one_verdict_for_the_whole_thing(self):
        result = _india.service().get_bars(nse("SBIN"), m.DAY_1,
                                           provider="yahoo")
        self.assertIsNotNone(result.quality)
        self.assertIn(result.quality.verdict,
                      (validation.VALID, validation.WARNING,
                       validation.INCOMPLETE))


class ARealNothingIsNotAFailure(unittest.TestCase):
    def test_an_empty_range_is_a_valid_empty_result(self):
        service = _india.service(**{"SBIN.NS?interval=1d": _india.fx(
            "yahoo-SBIN.NS-empty-holiday-range.json")})
        result = service.get_bars(nse("SBIN"), m.DAY_1,
                                  datetime.date(2026, 11, 8),
                                  datetime.date(2026, 11, 8),
                                  provider="yahoo")
        self.assertEqual(result.outcome, svc.EMPTY_VALID)
        self.assertTrue(result.ok)
        self.assertEqual(result.rows, [])
        self.assertTrue(result.result.is_empty_and_valid)
        result.require_usable()  # must not raise

    def test_a_total_failure_is_not_an_empty_result(self):
        service = _india.service(transport_=tr.FixtureTransport({}))
        result = service.get_quote(nse("SBIN"))
        self.assertEqual(result.outcome, svc.PROVIDER_ERROR)
        self.assertFalse(result.ok)
        self.assertEqual(result.rows, [])
        self.assertFalse(result.result.is_empty_and_valid)
        self.assertRaises(prov.DataError, result.require_usable)

    def test_the_two_are_told_apart_by_more_than_the_row_count(self):
        empty = _india.service(**{"SBIN.NS?interval=1d": _india.fx(
            "yahoo-SBIN.NS-empty-holiday-range.json")}).get_bars(
                nse("SBIN"), m.DAY_1, datetime.date(2026, 11, 8),
                datetime.date(2026, 11, 8), provider="yahoo")
        broken = _india.service(
            transport_=tr.FixtureTransport({})).get_bars(
                nse("SBIN"), m.DAY_1, provider="yahoo")
        self.assertEqual(len(empty.rows), len(broken.rows))
        self.assertNotEqual(empty.ok, broken.ok)
        self.assertNotEqual(empty.outcome, broken.outcome)


class InvalidDataIsNotAnAnswer(unittest.TestCase):
    def test_a_high_below_its_low_is_refused(self):
        service = _india.service(
            **{"SBIN.NS?interval=1d":
               _india.fx("yahoo-SBIN.NS-invalid-ohlc.json")})
        result = service.get_bars(nse("SBIN"), m.DAY_1, provider="yahoo")
        self.assertEqual(result.outcome, svc.INVALID_DATA)
        self.assertEqual(result.rows, [])
        self.assertFalse(result.ok)
        self.assertRaises(prov.DataError, result.require_usable)

    def test_the_reason_names_the_problem(self):
        service = _india.service(
            **{"SBIN.NS?interval=1d":
               _india.fx("yahoo-SBIN.NS-invalid-ohlc.json")})
        result = service.get_bars(nse("SBIN"), m.DAY_1, provider="yahoo")
        self.assertTrue(any("high" in e for e in result.errors), result.errors)

    def test_a_negative_price_is_refused(self):
        service = _india.service(
            **{"SBIN.NS?interval=1d":
               _india.fx("yahoo-SBIN.NS-negative-price.json")})
        result = service.get_quote(nse("SBIN"), provider="yahoo")
        self.assertEqual(result.outcome, svc.INVALID_DATA)

    def test_refused_data_is_never_cached(self):
        folder = tempfile.mkdtemp(prefix="indiacore-invalid-")
        try:
            cache = cache_mod.Cache(directory=folder)
            service = _india.service(
                cache=cache,
                **{"SBIN.NS?interval=1d":
                   _india.fx("yahoo-SBIN.NS-invalid-ohlc.json")})
            service.get_bars(nse("SBIN"), m.DAY_1, provider="yahoo")
            self.assertEqual(cache.writes, 0)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_a_circuit_sized_move_is_kept_with_a_warning(self):
        """A violent market is not corrupt data."""
        service = _india.service(
            _india.NOW_CLOSED,
            **{"SBIN.NS?interval=1d":
               _india.fx("yahoo-SBIN.NS-circuit-move.json")})
        result = service.get_bars(nse("SBIN"), m.DAY_1, provider="yahoo")
        self.assertTrue(result.ok)
        self.assertEqual(result.quality.verdict, validation.WARNING)
        self.assertTrue(any(c in result.quality.codes() for c in
                            ("circuit_sized_move", "extreme_move")),
                        result.quality.codes())
        self.assertEqual(len(result.rows), 5, "nothing was discarded")


class Caching(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="indiacore-service-cache-")
        self.clock = Clock()
        self.cache = cache_mod.Cache(directory=self.dir, clock=self.clock)
        self.transport = _india.transport()
        self.service = _india.service(cache=self.cache,
                                      transport_=self.transport)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_first_call_fetches_and_the_second_is_served_from_cache(self):
        first = self.service.get_quote(nse("SBIN"))
        self.assertEqual(first.outcome, svc.DELAYED)
        self.assertFalse(first.cached)
        calls = len(self.transport.calls)

        second = self.service.get_quote(nse("SBIN"))
        self.assertEqual(second.outcome, svc.CACHED)
        self.assertTrue(second.cached)
        self.assertEqual(len(self.transport.calls), calls,
                         "a cache hit must not make a request")

    def test_a_cache_hit_reports_its_age(self):
        self.service.get_quote(nse("SBIN"))
        self.clock.tick(30)
        hit = self.service.get_quote(nse("SBIN"))
        self.assertEqual(hit.cache_age_seconds, 30.0)
        self.assertIn("30 seconds old", hit.notes[0])

    def test_an_expired_entry_is_fetched_again(self):
        self.service.get_quote(nse("SBIN"))
        calls = len(self.transport.calls)
        self.clock.tick(cache_mod.DEFAULT_TTL["quote"] + 1)
        again = self.service.get_quote(nse("SBIN"))
        self.assertFalse(again.cached)
        self.assertGreater(len(self.transport.calls), calls)

    def test_caching_can_be_switched_off_per_call(self):
        self.service.get_quote(nse("SBIN"))
        calls = len(self.transport.calls)
        fresh = self.service.get_quote(nse("SBIN"), allow_cache=False)
        self.assertFalse(fresh.cached)
        self.assertGreater(len(self.transport.calls), calls)

    def test_nse_and_bse_never_share_a_cached_record(self):
        service = _india.service(_india.NOW_CLOSED, cache=self.cache,
                                 transport_=self.transport)
        on_nse = service.get_quote(nse("SBIN"))
        on_bse = service.get_quote(bse("INFY"))
        self.assertEqual(on_nse.first.exchange, "NSE")
        self.assertEqual(on_bse.first.exchange, "BSE")
        self.assertFalse(on_bse.cached)

    def test_a_cached_quote_does_not_stay_a_quote_forever(self):
        """Freshness is recomputed from the stored timestamps, not trusted."""
        cache = cache_mod.Cache(directory=self.dir, clock=self.clock,
                                ttl={"quote": 10 ** 6})
        during = _india.service(cache=cache, transport_=self.transport)
        self.assertEqual(during.get_quote(nse("SBIN")).outcome, svc.DELAYED)

        later = _india.service(_india.NOW_WEEK_LATER, cache=cache,
                               transport_=self.transport)
        hit = later.get_quote(nse("SBIN"))
        self.assertEqual(hit.outcome, svc.CACHED)
        self.assertTrue(hit.cached)
        self.assertEqual(hit.result.freshness, prov.STALE)
        self.assertFalse(hit.usable)
        self.assertFalse(hit.is_current_price)

    def test_a_corrupt_entry_is_a_miss_and_the_request_still_succeeds(self):
        self.service.get_quote(nse("SBIN"))
        for base_dir, _dirs, files in __import__("os").walk(self.dir):
            for name in files:
                path = __import__("os").path.join(base_dir, name)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("corrupted")
        recovered = self.service.get_quote(nse("SBIN"))
        self.assertTrue(recovered.ok)
        self.assertFalse(recovered.cached)
        self.assertEqual(len(self.cache.corruptions), 1)

    def test_a_stale_entry_does_not_silently_replace_a_failed_request(self):
        self.service.get_quote(nse("SBIN"))
        self.clock.tick(10 ** 5)
        dead = _india.service(cache=self.cache,
                              transport_=tr.FixtureTransport({}))
        result = dead.get_quote(nse("SBIN"))
        self.assertEqual(result.outcome, svc.PROVIDER_ERROR,
                         "a dead feed must not be covered by an old cache")
        self.assertEqual(result.rows, [])

    def test_a_caller_can_ask_for_the_stale_entry_in_writing(self):
        self.service.get_quote(nse("SBIN"))
        self.clock.tick(10 ** 5)
        dead = _india.service(cache=self.cache,
                              transport_=tr.FixtureTransport({}))
        result = dead.get_quote(nse("SBIN"), allow_stale_cache=True)
        self.assertEqual(result.outcome, svc.CACHED)
        self.assertTrue(result.cached)
        self.assertEqual(result.result.freshness, prov.STALE)
        self.assertFalse(result.is_current_price)
        self.assertTrue(any("every provider failed" in n for n in result.notes),
                        result.notes)

    def test_daily_bars_are_cached_too(self):
        first = self.service.get_daily_bars(nse("SBIN"), SESSION, SESSION)
        self.assertFalse(first.cached)
        second = self.service.get_daily_bars(nse("SBIN"), SESSION, SESSION)
        self.assertEqual(second.outcome, svc.CACHED)
        self.assertEqual(len(second.rows), 1)

    def test_a_different_date_range_is_a_different_entry(self):
        self.service.get_daily_bars(nse("SBIN"), SESSION, SESSION)
        calls = len(self.transport.calls)
        self.service.get_daily_bars(nse("SBIN"), datetime.date(2026, 11, 11),
                                    SESSION)
        self.assertGreater(len(self.transport.calls), calls)


class Fallback(unittest.TestCase):
    def test_yahoo_answers_when_the_exchange_file_fails(self):
        transport = _india.transport(
            BhavCopy_NSE_CM_0_0_0_20261112=tr.HttpError(503, "Unavailable"))
        service = _india.service(_india.NOW_CLOSED, transport_=transport)
        result = service.get_daily_bars(nse("SBIN"), SESSION, SESSION)
        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "yahoo")
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.fallback_from, "nse_bhavcopy")

    def test_the_fallback_result_is_one_providers_rows_alone(self):
        transport = _india.transport(
            BhavCopy_NSE_CM_0_0_0_20261112=tr.HttpError(503, "Unavailable"))
        service = _india.service(_india.NOW_CLOSED, transport_=transport)
        result = service.get_daily_bars(nse("SBIN"), SESSION, SESSION)
        providers = set(b.provenance.provider for b in result.rows)
        self.assertEqual(providers, {"yahoo"})

    def test_the_fallback_is_explained_in_the_result(self):
        transport = _india.transport(
            BhavCopy_NSE_CM_0_0_0_20261112=tr.HttpError(503, "Unavailable"))
        service = _india.service(_india.NOW_CLOSED, transport_=transport)
        result = service.get_daily_bars(nse("SBIN"), SESSION, SESSION)
        self.assertTrue(any("not combined" in n for n in result.notes),
                        result.notes)
        self.assertTrue(any(a["provider"] == "nse_bhavcopy"
                            and a["outcome"] == svc.PROVIDER_ERROR
                            for a in result.attempts), result.attempts)

    def test_every_attempt_is_recorded(self):
        service = _india.service(transport_=tr.FixtureTransport({}))
        result = service.get_daily_bars(nse("SBIN"), SESSION, SESSION)
        self.assertEqual(result.outcome, svc.PROVIDER_ERROR)
        names = [a["provider"] for a in result.attempts]
        self.assertIn("nse_bhavcopy", names)
        self.assertIn("yahoo", names)

    def test_naming_a_provider_disables_the_fallback(self):
        transport = _india.transport(
            BhavCopy_NSE_CM_0_0_0_20261112=tr.HttpError(503, "Unavailable"))
        service = _india.service(_india.NOW_CLOSED, transport_=transport)
        result = service.get_daily_bars(nse("SBIN"), SESSION, SESSION,
                                        provider="nse_bhavcopy")
        self.assertEqual(result.outcome, svc.PROVIDER_ERROR)
        self.assertFalse(result.fallback_used)

    def test_the_fallback_can_be_switched_off(self):
        transport = _india.transport(
            BhavCopy_NSE_CM_0_0_0_20261112=tr.HttpError(503, "Unavailable"))
        service = _india.service(_india.NOW_CLOSED, transport_=transport)
        result = service.get_daily_bars(nse("SBIN"), SESSION, SESSION,
                                        allow_fallback=False)
        self.assertEqual(result.outcome, svc.PROVIDER_ERROR)

    def test_a_provider_that_raises_something_unexpected_is_contained(self):
        class Exploding(object):
            def __init__(self):
                self.calls = []
                self.allowed_hosts = ("*",)

            def check_url(self, url):
                return url

            def get_bytes(self, url, headers=None):
                raise ZeroDivisionError("a bug in a provider")

            def get_json(self, url, headers=None):
                raise ZeroDivisionError("a bug in a provider")

        service = _india.service(transport_=Exploding())
        result = service.get_quote(nse("SBIN"))
        self.assertEqual(result.outcome, svc.PROVIDER_ERROR)
        self.assertTrue(any("ZeroDivisionError" in e for e in result.errors),
                        result.errors)


class Statuses(unittest.TestCase):
    def test_every_provider_reports_a_status(self):
        statuses = _india.service().provider_status()
        self.assertEqual(len(statuses), 5)
        names = set(s.provider for s in statuses)
        self.assertIn("nse_quote_api", names)

    def test_one_provider_can_be_asked(self):
        status = _india.service().provider_status("yahoo")
        self.assertEqual(status.provider, "yahoo")
        self.assertTrue(status.available)

    def test_a_failure_is_recorded_on_the_provider(self):
        service = _india.service(transport_=tr.FixtureTransport({}))
        service.get_quote(nse("SBIN"))
        status = service.provider_status("yahoo")
        self.assertGreater(status.failures, 0)
        self.assertIsNotNone(status.last_failure)

    def test_the_capability_matrix_is_available_from_the_service(self):
        rows = {r["provider"]: r for r in _india.service().capability_matrix()}
        self.assertTrue(rows["nse_bhavcopy"]["official"])
        self.assertFalse(rows["bse_quote_api"]["operational"])


class TheOutcomesAreDistinct(unittest.TestCase):
    def test_every_outcome_is_reachable_or_documented(self):
        self.assertEqual(len(set(svc.OUTCOMES)), len(svc.OUTCOMES))
        for name in ("fresh", "cached", "delayed", "end_of_day", "historical",
                     "empty_valid", "provider_error", "invalid_data", "stale",
                     "unknown_age", "unsupported"):
            self.assertIn(name, svc.OUTCOMES)

    def test_an_unknown_outcome_cannot_be_constructed(self):
        self.assertRaises(ValueError, svc.MarketDataResult, "probably fine",
                          prov.unavailable("x"))

    def test_an_unknown_age_is_not_reported_as_merely_stale(self):
        service = _india.service(
            **{"SBIN.NS?interval=1d":
               _india.fx("yahoo-SBIN.NS-no-timestamp.json")})
        result = service.get_quote(nse("SBIN"), provider="yahoo")
        self.assertEqual(result.outcome, svc.UNKNOWN_AGE)
        self.assertEqual(result.result.freshness, prov.UNKNOWN)
        self.assertFalse(result.usable)

    def test_a_daily_bar_for_an_unfinished_session_is_not_end_of_day(self):
        """At 15:28 the session is still running, so today's daily bar is
        not yet an end of day figure, and the service says so rather than
        calling it one."""
        result = _india.service().get_bars(nse("SBIN"), m.DAY_1,
                                           provider="yahoo")
        self.assertEqual(result.outcome, svc.UNKNOWN_AGE)
        self.assertFalse(result.usable)
        self.assertEqual(len(result.rows), 5, "the bars are still carried")

    def test_a_result_serialises_for_a_caller_to_log(self):
        d = _india.service().get_quote(nse("SBIN")).to_dict()
        self.assertEqual(d["outcome"], svc.DELAYED)
        self.assertEqual(d["provider"], "yahoo")
        self.assertTrue(d["is_current_price"])
        self.assertIn("provenance", d)
        self.assertIn("attempts", d)

    def test_phase_twos_require_usable_still_means_what_it_meant(self):
        good = _india.service().get_quote(nse("SBIN"))
        self.assertIs(good.require_usable(), good)
        bad = _india.service(_india.NOW_WEEK_LATER).get_quote(nse("SBIN"))
        self.assertRaises(prov.StaleDataError, bad.require_usable)


class TheRulesAreConfigurable(unittest.TestCase):
    def test_a_tighter_freshness_window_makes_the_same_quote_stale(self):
        rules = quality.FreshnessRules(max_quote_age_open_seconds=5)
        service = _india.service(rules=rules)
        relaxed = _india.service()
        self.assertEqual(relaxed.get_quote(nse("SBIN")).outcome, svc.DELAYED)
        self.assertEqual(service.get_quote(nse("SBIN")).outcome, svc.STALE)

    def test_tighter_thresholds_make_the_same_series_a_warning(self):
        from indiacore import data_service

        tight = data_service.MarketDataService(
            registry=_india.registry(), cache=_india.no_cache(),
            clock=_india.clock(), thresholds=validation.Thresholds(
                circuit_pct=0.5))
        result = tight.get_bars(nse("SBIN"), m.DAY_1, provider="yahoo")
        self.assertEqual(result.quality.verdict, validation.WARNING)

    def test_the_calendar_can_be_handed_in_for_session_gap_checks(self):
        from indiacore import calendar_in as cal
        from indiacore import data_service

        service = data_service.MarketDataService(
            registry=_india.registry(_india.NOW_CLOSED),
            cache=_india.no_cache(),
            clock=_india.clock(_india.NOW_CLOSED),
            calendar=cal.nse_calendar())
        result = service.get_bars(nse("SBIN"), m.DAY_1, provider="yahoo")
        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, svc.END_OF_DAY)


class NothingReachesTheNetwork(unittest.TestCase):
    def test_the_whole_suite_runs_through_a_fixture_transport(self):
        transport = _india.transport()
        service = _india.service(transport_=transport)
        service.get_quote(nse("SBIN"))
        service.get_daily_bars(nse("SBIN"), SESSION, SESSION)
        self.assertTrue(transport.calls)
        for url in transport.calls:
            self.assertTrue(url.startswith("https://"), url)

    def test_a_default_service_can_be_built_without_making_a_request(self):
        service = svc.MarketDataService(cache=_india.no_cache())
        self.assertEqual(len(service.registry), 5)


if __name__ == "__main__":
    unittest.main()
