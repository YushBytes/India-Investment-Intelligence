import datetime
import unittest

import _load  # noqa: F401
from indiacore import calendar_in as cal


class Timezone(unittest.TestCase):
    def test_ist_is_a_flat_five_thirty_all_year(self):
        tz = cal.ist_timezone()
        for month in range(1, 13):
            moment = datetime.datetime(2026, month, 15, tzinfo=tz)
            self.assertEqual(moment.utcoffset(), datetime.timedelta(hours=5, minutes=30),
                             "month %d" % month)

    def test_no_daylight_saving_anywhere(self):
        """The US calendar shifts in March and November. IST never does."""
        tz = cal.ist_timezone()
        march = datetime.datetime(2026, 3, 8, 12, tzinfo=tz).utcoffset()
        november = datetime.datetime(2026, 11, 1, 12, tzinfo=tz).utcoffset()
        self.assertEqual(march, november)

    def test_utc_converts_into_ist(self):
        utc = datetime.datetime(2026, 11, 12, 4, 0, tzinfo=datetime.timezone.utc)
        ist = cal.to_ist(utc)
        self.assertEqual((ist.hour, ist.minute), (9, 30))

    def test_a_naive_datetime_is_refused(self):
        self.assertRaises(cal.CalendarError, cal.now_ist,
                          datetime.datetime(2026, 11, 12, 9, 30))


class Holidays2026(unittest.TestCase):
    """Checked against NSE's own holiday master, circular NSE/CMTR/71775."""

    def setUp(self):
        self.cal = cal.nse_calendar()

    def test_the_published_list_loads(self):
        rows = self.cal.holidays(2026)
        self.assertEqual(len(rows), 20)

    def test_sixteen_closures_fall_on_weekdays(self):
        self.assertEqual(len(self.cal.holidays(2026, weekdays_only=True)), 16)

    def test_known_weekday_closures(self):
        for day, name in [((2026, 1, 26), "Republic Day"),
                          ((2026, 3, 3), "Holi"),
                          ((2026, 4, 3), "Good Friday"),
                          ((2026, 5, 1), "Maharashtra Day"),
                          ((2026, 10, 2), "Mahatma Gandhi Jayanti"),
                          ((2026, 12, 25), "Christmas")]:
            d = datetime.date(*day)
            self.assertFalse(self.cal.is_trading_day(d), name)
            self.assertEqual(self.cal.classify(d), cal.HOLIDAY, name)
            self.assertIn(name.split()[0], self.cal.holiday_for(d).description)

    def test_the_election_holiday_the_secondary_sources_missed(self):
        """15 Jan 2026 is in NSE's list but absent from popular summaries."""
        d = datetime.date(2026, 1, 15)
        self.assertEqual(self.cal.classify(d), cal.HOLIDAY)
        self.assertIn("Election", self.cal.holiday_for(d).description)

    def test_ordinary_days_trade(self):
        for day in [(2026, 11, 12), (2026, 1, 2), (2026, 6, 1)]:
            self.assertTrue(self.cal.is_trading_day(datetime.date(*day)), str(day))

    def test_weekends_are_closed_and_named_as_weekends(self):
        self.assertEqual(self.cal.classify(datetime.date(2026, 11, 14)), cal.WEEKEND)
        self.assertEqual(self.cal.classify(datetime.date(2026, 11, 15)), cal.WEEKEND)

    def test_a_holiday_on_a_saturday_is_still_a_weekend(self):
        # 15 Aug 2026, Independence Day, falls on a Saturday
        self.assertEqual(self.cal.classify(datetime.date(2026, 8, 15)), cal.WEEKEND)

    def test_provenance_is_recorded(self):
        src = self.cal.source_for(2026)
        self.assertTrue(src["verified"])
        self.assertIn("CMTR71775", src["circular_url"])
        self.assertIn("nseindia.com", src["url"])


class Muhurat(unittest.TestCase):
    """Neither open nor shut, so it gets its own state."""

    def setUp(self):
        self.cal = cal.nse_calendar()
        self.day = datetime.date(2026, 11, 8)

    def test_diwali_is_classified_as_muhurat_not_holiday_or_weekend(self):
        self.assertEqual(self.cal.classify(self.day), cal.MUHURAT)
        self.assertTrue(self.cal.is_muhurat(self.day))

    def test_muhurat_is_not_a_regular_trading_day(self):
        self.assertFalse(self.cal.is_trading_day(self.day))

    def test_muhurat_beats_the_weekend_test(self):
        """8 Nov 2026 was a Sunday, and there was still a session."""
        self.assertEqual(self.day.weekday(), cal.SUNDAY)
        self.assertNotEqual(self.cal.classify(self.day), cal.WEEKEND)

    def test_the_timing_is_deliberately_not_encoded(self):
        entry = self.cal.holiday_for(self.day)
        self.assertTrue(entry.muhurat_marked)
        for field in ("start", "end", "open", "close"):
            self.assertFalse(hasattr(entry, field))


class UnverifiedYears(unittest.TestCase):
    """The list is announced, not computed, so an unknown year must raise."""

    def setUp(self):
        self.cal = cal.nse_calendar()

    def test_2027_raises_rather_than_guessing(self):
        # NSE publishes the next year by circular in December.
        self.assertRaises(cal.CalendarDataUnavailable,
                          self.cal.is_trading_day, datetime.date(2027, 1, 4))

    def test_the_error_says_what_is_available(self):
        try:
            self.cal.holidays(2027)
        except cal.CalendarDataUnavailable as e:
            self.assertIn("2026", str(e))
            self.assertIn("will not guess", str(e))
        else:
            self.fail("expected CalendarDataUnavailable")

    def test_no_silent_fallback_to_another_year(self):
        self.assertRaises(cal.CalendarDataUnavailable,
                          self.cal.classify, datetime.date(2025, 6, 2))

    def test_available_years_is_honest(self):
        self.assertEqual(self.cal.available_years(), [2026])


class Stepping(unittest.TestCase):
    def setUp(self):
        self.cal = cal.nse_calendar()

    def test_next_trading_day_skips_the_weekend(self):
        # Friday 13 Nov 2026 -> Monday 16 Nov
        self.assertEqual(self.cal.next_trading_day(datetime.date(2026, 11, 13)),
                         datetime.date(2026, 11, 16))

    def test_next_trading_day_skips_a_holiday(self):
        # Monday 23 Nov -> Tue 24 Nov is Guru Nanak Jayanti -> Wed 25 Nov
        self.assertEqual(self.cal.next_trading_day(datetime.date(2026, 11, 23)),
                         datetime.date(2026, 11, 25))

    def test_previous_trading_day_skips_backwards(self):
        self.assertEqual(self.cal.previous_trading_day(datetime.date(2026, 11, 16)),
                         datetime.date(2026, 11, 13))

    def test_previous_trading_day_steps_over_muhurat_and_the_weekend(self):
        # Mon 9 Nov back over Sun 8 (Muhurat) and Sat 7 to Fri 6 Nov
        self.assertEqual(self.cal.previous_trading_day(datetime.date(2026, 11, 9)),
                         datetime.date(2026, 11, 6))

    def test_trading_days_between_counts_a_real_week(self):
        # Mon 9 to Fri 13 Nov 2026 looks like five sessions but Tuesday the
        # 10th is Diwali Balipratipada, so it is four.
        days = self.cal.trading_days_between(datetime.date(2026, 11, 9),
                                             datetime.date(2026, 11, 13))
        self.assertEqual(len(days), 4)
        self.assertNotIn(datetime.date(2026, 11, 10), days)

    def test_a_clean_week_really_is_five_sessions(self):
        days = self.cal.trading_days_between(datetime.date(2026, 11, 16),
                                             datetime.date(2026, 11, 20))
        self.assertEqual(len(days), 5)

    def test_trading_days_between_drops_the_diwali_week_holiday(self):
        # 10 Nov 2026, Diwali Balipratipada, is a Tuesday closure
        days = self.cal.trading_days_between(datetime.date(2026, 11, 2),
                                             datetime.date(2026, 11, 13))
        self.assertNotIn(datetime.date(2026, 11, 10), days)
        self.assertNotIn(datetime.date(2026, 11, 8), days)

    def test_backwards_range_is_refused(self):
        self.assertRaises(cal.CalendarError, self.cal.trading_days_between,
                          datetime.date(2026, 11, 13), datetime.date(2026, 11, 9))


if __name__ == "__main__":
    unittest.main()
