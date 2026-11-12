import datetime
import unittest

import _load  # noqa: F401
from indiacore import calendar_in as cal
from indiacore import market as mk


def ist(y, m, d, hh=0, mm=0):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=cal.ist_timezone())


class Hours(unittest.TestCase):
    def setUp(self):
        self.nse = mk.nse_market()

    def test_session_hours_are_indian(self):
        self.assertEqual(self.nse.open_time, datetime.time(9, 15))
        self.assertEqual(self.nse.close_time, datetime.time(15, 30))
        self.assertEqual(self.nse.pre_open, datetime.time(9, 0))

    def test_not_the_us_hours_the_pack_hard_codes(self):
        self.assertNotEqual(self.nse.open_time, datetime.time(9, 30))
        self.assertNotEqual(self.nse.close_time, datetime.time(16, 0))

    def test_states_through_one_ordinary_thursday(self):
        # 12 November 2026 was a normal trading day
        cases = [(8, 30, mk.CLOSED), (9, 5, mk.PRE_OPEN), (9, 15, mk.OPEN),
                 (12, 0, mk.OPEN), (15, 29, mk.OPEN), (15, 30, mk.CLOSED),
                 (18, 0, mk.CLOSED)]
        for hh, mm, expected in cases:
            state = self.nse.state_at(ist(2026, 11, 12, hh, mm))
            self.assertEqual(state.state, expected, "%02d:%02d" % (hh, mm))

    def test_is_open_only_during_the_regular_session(self):
        self.assertTrue(self.nse.is_open(ist(2026, 11, 12, 10, 0)))
        self.assertFalse(self.nse.is_open(ist(2026, 11, 12, 9, 5)))
        self.assertFalse(self.nse.is_open(ist(2026, 11, 12, 16, 0)))

    def test_a_utc_moment_is_converted_not_assumed(self):
        # 04:00 UTC is 09:30 IST, inside the session
        utc = datetime.datetime(2026, 11, 12, 4, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(self.nse.is_open(utc))

    def test_session_bounds(self):
        start, end = self.nse.session_bounds(datetime.date(2026, 11, 12))
        self.assertEqual(start.hour, 9)
        self.assertEqual(start.minute, 15)
        self.assertEqual(end.hour, 15)
        self.assertEqual(end.minute, 30)

    def test_session_bounds_refuse_a_closed_day(self):
        self.assertRaises(mk.MarketError, self.nse.session_bounds,
                          datetime.date(2026, 12, 25))


class ClosedDays(unittest.TestCase):
    def setUp(self):
        self.nse = mk.nse_market()

    def test_weekend_is_named_as_a_weekend(self):
        state = self.nse.state_at(ist(2026, 11, 14, 11, 0))
        self.assertEqual(state.state, mk.CLOSED_WEEKEND)
        self.assertIn("Saturday", state.reason)

    def test_holiday_gives_its_reason(self):
        state = self.nse.state_at(ist(2026, 12, 25, 11, 0))
        self.assertEqual(state.state, mk.CLOSED_HOLIDAY)
        self.assertIn("Christmas", state.reason)

    def test_a_closed_day_has_no_session_date(self):
        self.assertIsNone(self.nse.state_at(ist(2026, 12, 25, 11, 0)).session_date)


class MuhuratIsNeitherOpenNorShut(unittest.TestCase):
    def setUp(self):
        self.nse = mk.nse_market()
        self.moment = ist(2026, 11, 8, 18, 30)

    def test_it_has_its_own_state(self):
        self.assertEqual(self.nse.state_at(self.moment).state,
                         mk.MUHURAT_TIMING_UNKNOWN)

    def test_is_open_does_not_claim_to_know(self):
        self.assertFalse(self.nse.state_at(self.moment).is_open)

    def test_the_reason_says_the_timing_is_announced_separately(self):
        reason = self.nse.state_at(self.moment).reason
        self.assertIn("notified separately", reason)

    def test_it_is_not_reported_as_a_weekend_even_though_it_was_a_sunday(self):
        self.assertNotEqual(self.nse.state_at(self.moment).state,
                            mk.CLOSED_WEEKEND)


class LastCompletedSession(unittest.TestCase):
    """What date an end of day figure belongs to."""

    def setUp(self):
        self.nse = mk.nse_market()

    def test_before_the_close_it_is_the_previous_day(self):
        self.assertEqual(
            self.nse.last_completed_session(ist(2026, 11, 12, 9, 0)),
            datetime.date(2026, 11, 11))

    def test_after_the_close_it_is_today(self):
        self.assertEqual(
            self.nse.last_completed_session(ist(2026, 11, 12, 16, 0)),
            datetime.date(2026, 11, 12))

    def test_at_the_bell_the_session_counts_as_complete(self):
        self.assertEqual(
            self.nse.last_completed_session(ist(2026, 11, 12, 15, 30)),
            datetime.date(2026, 11, 12))

    def test_on_a_sunday_it_reaches_back_to_friday(self):
        self.assertEqual(
            self.nse.last_completed_session(ist(2026, 11, 15, 11, 0)),
            datetime.date(2026, 11, 13))

    def test_it_steps_over_a_holiday(self):
        # 25 Dec 2026 is Christmas, a Friday; the 24th was a Thursday
        self.assertEqual(
            self.nse.last_completed_session(ist(2026, 12, 25, 11, 0)),
            datetime.date(2026, 12, 24))


class NextOpen(unittest.TestCase):
    def setUp(self):
        self.nse = mk.nse_market()

    def test_before_the_bell_it_is_today(self):
        nxt = self.nse.next_open(ist(2026, 11, 12, 7, 0))
        self.assertEqual(nxt.date(), datetime.date(2026, 11, 12))
        self.assertEqual((nxt.hour, nxt.minute), (9, 15))

    def test_after_the_close_it_is_the_next_trading_day(self):
        nxt = self.nse.next_open(ist(2026, 11, 12, 16, 0))
        self.assertEqual(nxt.date(), datetime.date(2026, 11, 13))

    def test_on_a_friday_evening_it_is_monday(self):
        nxt = self.nse.next_open(ist(2026, 11, 13, 18, 0))
        self.assertEqual(nxt.date(), datetime.date(2026, 11, 16))


class BothExchanges(unittest.TestCase):
    def test_bse_exists_and_is_separate(self):
        nse_m, bse_m = mk.nse_market(), mk.bse_market()
        self.assertEqual(nse_m.code, "NSE")
        self.assertEqual(bse_m.code, "BSE")
        self.assertIsNot(nse_m.exchange, bse_m.exchange)

    def test_equity_hours_match_today(self):
        nse_m, bse_m = mk.nse_market(), mk.bse_market()
        self.assertEqual(nse_m.open_time, bse_m.open_time)
        self.assertEqual(nse_m.close_time, bse_m.close_time)

    def test_bse_is_open_on_an_ordinary_day(self):
        self.assertTrue(mk.bse_market().is_open(ist(2026, 11, 12, 10, 0)))


class UnverifiedYears(unittest.TestCase):
    def test_the_market_inherits_the_calendar_refusal(self):
        self.assertRaises(cal.CalendarDataUnavailable,
                          mk.nse_market().state_at, ist(2027, 3, 1, 10, 0))


if __name__ == "__main__":
    unittest.main()
