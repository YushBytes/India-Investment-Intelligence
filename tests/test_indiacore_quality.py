import datetime
import unittest

import _india
from indiacore import market as mkt
from indiacore import provenance as prov
from indiacore.marketdata import models as m
from indiacore.marketdata import quality
from indiacore.symbols import nse

IST = _india.IST
NSE_MARKET = mkt.nse_market()


def caps(**kw):
    base = dict(provider="yahoo", exchanges=("NSE",), kinds=(m.QUOTE,),
                realtime=False)
    base.update(kw)
    return m.Capabilities(**base)


def at(day, hour, minute=0, second=0):
    return datetime.datetime(2026, 11, day, hour, minute, second, tzinfo=IST)


def bar(day, interval=m.DAY_1, hour=9, minute=15):
    return m.Bar(symbol=nse("SBIN"), interval=interval,
                 start=at(day, hour, minute), open="1062.00", high="1089.00",
                 low="1058.50", close="1086.50", volume=1,
                 provenance=m.Provenance("yahoo", "src", at(day, 16)))


class NothingIsEverCalledLive(unittest.TestCase):
    """The ceiling for a provider that promises nothing is DELAYED."""

    def test_a_recent_figure_during_the_session_is_delayed_not_live(self):
        freshness, reason = quality.classify_quote(
            at(13, 15, 28, 9), at(13, 15, 28, 30), NSE_MARKET, caps())
        self.assertEqual(freshness, prov.DELAYED)
        self.assertIn("not a real time price", reason)

    def test_the_live_branch_exists_for_a_provider_that_promises_it(self):
        freshness, _ = quality.classify_quote(
            at(13, 15, 28, 9), at(13, 15, 28, 30), NSE_MARKET,
            caps(realtime=True))
        self.assertEqual(freshness, prov.LIVE)

    def test_a_declared_delay_is_quoted_in_the_reason(self):
        _, reason = quality.classify_quote(
            at(13, 15, 28, 9), at(13, 15, 28, 30), NSE_MARKET,
            caps(declared_delay_seconds=900))
        self.assertIn("900s delay", reason)


class YesterdayIsNeverTodaysPrice(unittest.TestCase):
    """The rule the phase hangs on."""

    def test_a_previous_session_close_during_an_open_market_is_stale(self):
        freshness, reason = quality.classify_quote(
            at(12, 15, 30), at(13, 11, 0), NSE_MARKET, caps())
        self.assertEqual(freshness, prov.STALE)
        self.assertIn("not a quote", reason)
        self.assertFalse(quality.is_current_price(freshness))

    def test_the_same_figure_before_the_open_is_good_end_of_day_data(self):
        freshness, reason = quality.classify_quote(
            at(13, 15, 30), _india.NOW_NEXT_MORNING, NSE_MARKET, caps())
        self.assertEqual(freshness, prov.END_OF_DAY)
        self.assertIn("close from 2026-11-13", reason)

    def test_end_of_day_is_usable_and_still_not_a_current_price(self):
        self.assertTrue(quality.usable(prov.END_OF_DAY))
        self.assertFalse(quality.is_current_price(prov.END_OF_DAY))

    def test_historical_is_usable_and_still_not_a_current_price(self):
        self.assertTrue(quality.usable(prov.HISTORICAL))
        self.assertFalse(quality.is_current_price(prov.HISTORICAL))

    def test_only_live_and_delayed_count_as_a_current_price(self):
        self.assertTrue(quality.is_current_price(prov.LIVE))
        self.assertTrue(quality.is_current_price(prov.DELAYED))
        for f in (prov.STALE, prov.UNKNOWN, prov.UNAVAILABLE):
            self.assertFalse(quality.is_current_price(f), f)


class TheSameAgeMeansDifferentThings(unittest.TestCase):
    def test_after_the_close_the_days_own_figure_is_end_of_day(self):
        freshness, _ = quality.classify_quote(
            at(13, 15, 29), _india.NOW_CLOSED, NSE_MARKET, caps())
        self.assertEqual(freshness, prov.END_OF_DAY)

    def test_one_session_behind_is_historical(self):
        freshness, reason = quality.classify_quote(
            at(12, 15, 30), _india.NOW_CLOSED, NSE_MARKET, caps())
        self.assertEqual(freshness, prov.HISTORICAL)
        self.assertIn("not a current price", reason)

    def test_a_week_behind_is_stale(self):
        freshness, _ = quality.classify_quote(
            at(12, 15, 30), _india.NOW_WEEK_LATER, NSE_MARKET, caps())
        self.assertEqual(freshness, prov.STALE)

    def test_the_weekend_does_not_age_a_friday_close(self):
        # Monday morning: Friday's close is still the last completed session
        freshness, _ = quality.classify_quote(
            at(13, 15, 30), _india.NOW_NEXT_MORNING, NSE_MARKET, caps())
        self.assertEqual(freshness, prov.END_OF_DAY)

    def test_how_many_sessions_count_as_historical_is_configurable(self):
        # 2026-11-12 to 2026-11-20 is six completed sessions apart, so the
        # boundary falls exactly between these two settings.
        allowed = quality.FreshnessRules(max_eod_sessions_behind=6)
        refused = quality.FreshnessRules(max_eod_sessions_behind=5)
        self.assertEqual(
            quality.classify_quote(at(12, 15, 30), _india.NOW_WEEK_LATER,
                                   NSE_MARKET, caps(), allowed)[0],
            prov.HISTORICAL)
        self.assertEqual(
            quality.classify_quote(at(12, 15, 30), _india.NOW_WEEK_LATER,
                                   NSE_MARKET, caps(), refused)[0],
            prov.STALE)


class AnUnknownAgeIsNotAnOldAge(unittest.TestCase):
    def test_no_source_timestamp_is_unknown(self):
        freshness, reason = quality.classify_quote(
            None, _india.NOW_OPEN, NSE_MARKET, caps())
        self.assertEqual(freshness, prov.UNKNOWN)
        self.assertIn("cannot be established", reason)

    def test_unknown_is_not_usable(self):
        self.assertFalse(quality.usable(prov.UNKNOWN))

    def test_a_timestamp_from_the_future_is_unknown_not_fresh(self):
        freshness, reason = quality.classify_quote(
            at(13, 16, 30), _india.NOW_OPEN, NSE_MARKET, caps())
        self.assertEqual(freshness, prov.UNKNOWN)
        self.assertIn("future", reason)

    def test_a_stamp_after_the_last_completed_session_disagrees(self):
        freshness, reason = quality.classify_quote(
            at(16, 15, 30), at(16, 20, 0), NSE_MARKET, caps())
        self.assertEqual(freshness, prov.END_OF_DAY)
        # and on a day the market was shut, the two genuinely disagree
        freshness2, reason2 = quality.classify_quote(
            at(14, 12, 0), at(14, 18, 0), NSE_MARKET, caps())
        self.assertEqual(freshness2, prov.UNKNOWN)
        self.assertIn("disagree", reason2)


class TheToleranceIsConfigurable(unittest.TestCase):
    def test_the_default_window_is_fifteen_minutes(self):
        self.assertEqual(quality.DEFAULT_RULES.max_quote_age_open_seconds, 900)

    def test_a_figure_just_inside_the_window_is_delayed(self):
        freshness, _ = quality.classify_quote(
            at(13, 15, 20), at(13, 15, 28), NSE_MARKET, caps())
        self.assertEqual(freshness, prov.DELAYED)

    def test_a_figure_just_outside_it_is_stale(self):
        tight = quality.FreshnessRules(max_quote_age_open_seconds=60)
        freshness, _ = quality.classify_quote(
            at(13, 15, 20), at(13, 15, 28), NSE_MARKET, tight and caps(),
            tight)
        self.assertEqual(freshness, prov.STALE)


class Bars(unittest.TestCase):
    def test_a_daily_bar_for_the_last_completed_session_is_end_of_day(self):
        freshness, reason = quality.classify_bars(
            [bar(13)], _india.NOW_CLOSED, NSE_MARKET, caps())
        self.assertEqual(freshness, prov.END_OF_DAY)
        self.assertIn("2026-11-13", reason)

    def test_a_daily_bar_for_an_unfinished_session_is_not_end_of_day(self):
        freshness, reason = quality.classify_bars(
            [bar(13)], _india.NOW_OPEN, NSE_MARKET, caps())
        self.assertEqual(freshness, prov.UNKNOWN)
        self.assertIn("has not finished", reason)

    def test_an_older_daily_series_is_historical(self):
        freshness, _ = quality.classify_bars(
            [bar(11), bar(12)], _india.NOW_CLOSED, NSE_MARKET, caps())
        self.assertEqual(freshness, prov.HISTORICAL)

    def test_the_newest_bar_decides_the_series(self):
        freshness, _ = quality.classify_bars(
            [bar(2), bar(13)], _india.NOW_CLOSED, NSE_MARKET, caps())
        self.assertEqual(freshness, prov.END_OF_DAY)

    def test_no_bars_means_no_age(self):
        freshness, _ = quality.classify_bars([], _india.NOW_CLOSED,
                                             NSE_MARKET, caps())
        self.assertEqual(freshness, prov.UNKNOWN)

    def test_a_recent_intraday_bar_during_the_session_is_delayed(self):
        freshness, _ = quality.classify_bars(
            [bar(13, m.MIN_5, 15, 20)], at(13, 15, 28), NSE_MARKET, caps(),
            m.MIN_5)
        self.assertEqual(freshness, prov.DELAYED)

    def test_an_intraday_bar_from_hours_ago_is_stale_while_open(self):
        freshness, reason = quality.classify_bars(
            [bar(13, m.MIN_5, 10, 50)], at(13, 15, 28), NSE_MARKET, caps(),
            m.MIN_5)
        self.assertEqual(freshness, prov.STALE)
        self.assertIn("market is open", reason)

    def test_the_intraday_window_scales_with_the_interval(self):
        # the same bar age is stale at 5m and fine at 60m
        stale, _ = quality.classify_bars(
            [bar(13, m.MIN_5, 14, 30)], at(13, 15, 0), NSE_MARKET, caps(),
            m.MIN_5)
        fresh, _ = quality.classify_bars(
            [bar(13, m.MIN_60, 14, 30)], at(13, 15, 0), NSE_MARKET, caps(),
            m.MIN_60)
        self.assertEqual(stale, prov.STALE)
        self.assertEqual(fresh, prov.DELAYED)

    def test_after_the_close_an_intraday_series_is_end_of_day(self):
        freshness, _ = quality.classify_bars(
            [bar(13, m.MIN_5, 15, 25)], _india.NOW_CLOSED, NSE_MARKET,
            caps(), m.MIN_5)
        self.assertEqual(freshness, prov.END_OF_DAY)


class PhaseTwoVocabularyIsReused(unittest.TestCase):
    def test_the_values_are_phase_twos_own(self):
        for value in (prov.LIVE, prov.DELAYED, prov.END_OF_DAY,
                      prov.HISTORICAL, prov.STALE, prov.UNKNOWN):
            self.assertIsInstance(value, str)
        self.assertIn(prov.DELAYED, prov.USABLE_FRESHNESS)
        self.assertNotIn(prov.UNKNOWN, prov.USABLE_FRESHNESS)

    def test_usable_is_phase_twos_rule_and_not_a_second_opinion(self):
        for f in prov.USABLE_FRESHNESS:
            self.assertTrue(quality.usable(f), f)
        for f in (prov.STALE, prov.UNKNOWN, prov.UNAVAILABLE):
            self.assertFalse(quality.usable(f), f)


class AnUnverifiedCalendarDoesNotBecomeAnAssumption(unittest.TestCase):
    def test_a_2027_date_raises_rather_than_guessing_the_market_is_open(self):
        # phase 2 has no verified 2027 holiday list
        self.assertRaises(Exception, quality.classify_quote,
                          datetime.datetime(2027, 3, 4, 10, 0, tzinfo=IST),
                          datetime.datetime(2027, 3, 4, 11, 0, tzinfo=IST),
                          NSE_MARKET, caps())


if __name__ == "__main__":
    unittest.main()
