import datetime
import unittest

import _india
from indiacore import calendar_in as cal
from indiacore.marketdata import models as m
from indiacore.marketdata import validation as v
from indiacore.symbols import IndexSymbol, bse, nse

IST = _india.IST
MOMENT = _india.NOW_OPEN


def provenance(provider="yahoo"):
    return m.Provenance(provider=provider, source="src", retrieved_at=MOMENT)


def bar(day=12, **kw):
    base = dict(symbol=nse("SBIN"), interval=m.DAY_1,
                start=datetime.datetime(2026, 11, day, 9, 15, tzinfo=IST),
                open="1062.00", high="1089.00", low="1058.50",
                close="1086.50", volume=26973410, provenance=provenance())
    base.update(kw)
    return m.Bar(**base)


def quote(**kw):
    base = dict(symbol=nse("SBIN"), last_price="1080.00",
                provenance=provenance(), previous_close="1086.50",
                open="1090.00", high="1090.70", low="1074.15",
                volume=15834714, instrument_type=m.EQUITY)
    base.update(kw)
    return m.Quote(**base)


class AGoodRecordPasses(unittest.TestCase):
    def test_a_real_bar_from_the_exchange_file_is_valid(self):
        q = v.validate_bar(bar())
        self.assertEqual(q.verdict, v.VALID, q.codes())

    def test_a_real_quote_is_valid(self):
        q = v.validate_quote(quote())
        self.assertEqual(q.verdict, v.VALID, q.codes())

    def test_an_index_quote_without_an_exchange_is_valid(self):
        q = v.validate_quote(quote(symbol=IndexSymbol("^NSEI"),
                                   instrument_type=m.INDEX, exchange=None,
                                   volume=None, last_price="25144.30",
                                   previous_close="25100.00",
                                   high="25200.00", low="25000.00"))
        self.assertEqual(q.verdict, v.VALID, q.codes())


class TheStructurallyImpossibleIsInvalid(unittest.TestCase):
    """No market produces these, so no market explains them away."""

    def test_high_below_low(self):
        q = v.validate_bar(bar(high="1000.00", low="1058.50"))
        self.assertEqual(q.verdict, v.INVALID)
        self.assertIn("high_below_low", q.codes())

    def test_high_below_the_close(self):
        q = v.validate_bar(bar(high="1070.00"))
        self.assertEqual(q.verdict, v.INVALID)
        self.assertIn("high_not_highest", q.codes())

    def test_low_above_the_open(self):
        q = v.validate_bar(bar(low="1070.00"))
        self.assertEqual(q.verdict, v.INVALID)
        self.assertIn("low_not_lowest", q.codes())

    def test_a_negative_price(self):
        q = v.validate_bar(bar(close="-5.00"))
        self.assertEqual(q.verdict, v.INVALID)
        self.assertIn("non_positive_price", q.codes())

    def test_a_zero_price(self):
        self.assertEqual(v.validate_bar(bar(low="0")).verdict, v.INVALID)

    def test_one_bad_price_does_not_produce_four_confusing_findings(self):
        codes = v.validate_bar(bar(close="-5.00")).codes()
        self.assertNotIn("high_not_highest", codes)

    def test_negative_volume(self):
        q = v.validate_bar(bar(volume=-1))
        self.assertEqual(q.verdict, v.INVALID)
        self.assertIn("negative_volume", q.codes())

    def test_a_last_price_outside_the_day_range(self):
        q = v.validate_quote(quote(last_price="1200.00"))
        self.assertEqual(q.verdict, v.INVALID)
        self.assertIn("last_outside_day_range", q.codes())

    def test_a_non_inr_record(self):
        # Set after construction, since Money already refuses to build a bar
        # whose prices and currency disagree. This is the second guard.
        b = bar()
        b.currency = "USD"
        q = v.validate_bar(b)
        self.assertEqual(q.verdict, v.INVALID)
        self.assertIn("unexpected_currency", q.codes())

    def test_an_unknown_exchange(self):
        b = bar()
        b.exchange = "LSE"
        self.assertIn("unknown_exchange", v.validate_bar(b).codes())


class AViolentMarketIsNotCorruptData(unittest.TestCase):
    """The instinct this module exists to resist."""

    def test_a_circuit_sized_move_is_a_warning_and_is_kept(self):
        series = [bar(11, close="1000.00", open="1000.00", high="1000.00",
                      low="1000.00"),
                  bar(12, close="1200.00", open="1190.00", high="1205.00",
                      low="1185.00")]
        q = v.validate_series(series)
        self.assertEqual(q.verdict, v.WARNING)
        self.assertIn("circuit_sized_move", q.codes())
        self.assertEqual(len(series), 2, "nothing was discarded")

    def test_a_split_sized_gap_is_named_as_a_corporate_action(self):
        series = [bar(11, close="1000.00", open="1000.00", high="1000.00",
                      low="1000.00"),
                  bar(12, close="500.00", open="505.00", high="510.00",
                      low="495.00")]
        q = v.validate_series(series)
        self.assertEqual(q.verdict, v.WARNING)
        self.assertIn("possible_corporate_action", q.codes())
        message = [f.message for f in q.findings
                   if f.code == "possible_corporate_action"][0]
        self.assertIn("split", message)

    def test_a_move_past_the_widest_band_is_extreme_but_still_a_warning(self):
        series = [bar(11, close="1000.00", open="1000.00", high="1000.00",
                      low="1000.00"),
                  bar(12, close="1300.00", open="1290.00", high="1305.00",
                      low="1285.00")]
        q = v.validate_series(series)
        self.assertEqual(q.verdict, v.WARNING)
        self.assertIn("extreme_move", q.codes())

    def test_zero_volume_on_an_illiquid_scrip_is_only_a_warning(self):
        q = v.validate_bar(bar(volume=0, open="1086.50", high="1086.50",
                               low="1086.50", close="1086.50"))
        self.assertEqual(q.verdict, v.WARNING)
        self.assertIn("zero_volume", q.codes())

    def test_zero_volume_with_a_price_move_is_called_out_separately(self):
        q = v.validate_bar(bar(volume=0))
        self.assertIn("zero_volume_with_movement", q.codes())

    def test_the_thresholds_are_configurable(self):
        tight = v.Thresholds(circuit_pct=5.0)
        series = [bar(11, close="1000.00", open="1000.00", high="1000.00",
                      low="1000.00"),
                  bar(12, close="1060.00", open="1055.00", high="1065.00",
                      low="1050.00")]
        self.assertEqual(v.validate_series(series).verdict, v.VALID)
        self.assertEqual(v.validate_series(series, tight).verdict, v.WARNING)


class MissingIsIncompleteNotInvalid(unittest.TestCase):
    def test_a_bar_with_no_volume(self):
        q = v.validate_bar(bar(volume=None))
        self.assertEqual(q.verdict, v.INCOMPLETE)
        self.assertIn("volume_missing", q.codes())

    def test_a_quote_with_no_previous_close(self):
        q = v.validate_quote(quote(previous_close=None))
        self.assertEqual(q.verdict, v.INCOMPLETE)
        self.assertIn("previous_close_missing", q.codes())

    def test_a_quote_with_no_day_range(self):
        q = v.validate_quote(quote(high=None, low=None, open=None))
        self.assertEqual(q.verdict, v.INCOMPLETE)
        self.assertIn("day_fields_missing", q.codes())

    def test_unordered_bars_are_incomplete_because_a_window_over_them_is_wrong(self):
        series = [bar(12), bar(11)]
        q = v.validate_series(series)
        self.assertIn("unordered", q.codes())

    def test_a_trading_day_with_no_bar_is_reported(self):
        series = [bar(10), bar(12)]
        q = v.validate_series(series, calendar=cal.nse_calendar())
        self.assertIn("missing_sessions", q.codes())
        self.assertIn("2026-11-11", str([f.message for f in q.findings]))


class OneSeriesIsOneThing(unittest.TestCase):
    def test_two_instruments_in_one_series_is_invalid(self):
        q = v.validate_series([bar(11), bar(12, symbol=bse("SBIN"),
                                            exchange=None)])
        self.assertEqual(q.verdict, v.INVALID)
        self.assertIn("mixed_symbols", q.codes())

    def test_two_providers_in_one_series_is_invalid(self):
        q = v.validate_series(
            [bar(11), bar(12, provenance=provenance("nse_bhavcopy"))])
        self.assertEqual(q.verdict, v.INVALID)
        self.assertIn("mixed_providers", q.codes())
        self.assertIn("must not be stitched",
                      [f.message for f in q.findings
                       if f.code == "mixed_providers"][0])

    def test_two_intervals_in_one_series_is_invalid(self):
        q = v.validate_series(
            [bar(11), bar(12, interval=m.MIN_5,
                          start=datetime.datetime(2026, 11, 12, 9, 15,
                                                  tzinfo=IST))])
        self.assertIn("mixed_intervals", q.codes())

    def test_the_same_bar_twice_is_a_warning(self):
        q = v.validate_series([bar(12), bar(12)])
        self.assertEqual(q.verdict, v.WARNING)
        self.assertIn("duplicate_timestamp", q.codes())

    def test_two_different_bars_at_the_same_time_is_invalid(self):
        q = v.validate_series([bar(12), bar(12, close="1090.00")])
        self.assertEqual(q.verdict, v.INVALID)
        self.assertIn("conflicting_duplicate", q.codes())


class TheCalendarIsConsulted(unittest.TestCase):
    def test_a_bar_dated_to_a_closed_day_is_a_warning_not_an_error(self):
        # 2026-11-08 was the Muhurat Sunday: shut for regular trading
        q = v.validate_bar(bar(8), calendar=cal.nse_calendar())
        self.assertEqual(q.verdict, v.WARNING)
        self.assertIn("bar_on_non_trading_day", q.codes())

    def test_an_unverified_calendar_year_does_not_become_a_data_verdict(self):
        # 2027 holidays are not published, so the calendar raises. That must
        # not turn into a verdict about the price.
        q = v.validate_bar(
            bar(start=datetime.datetime(2027, 3, 4, 9, 15, tzinfo=IST),
                session_date=datetime.date(2027, 3, 4)),
            calendar=cal.nse_calendar())
        self.assertEqual(q.verdict, v.VALID, q.codes())


class VerdictArithmetic(unittest.TestCase):
    def test_the_worst_verdict_wins(self):
        self.assertEqual(v.worst([v.VALID, v.WARNING, v.INVALID]), v.INVALID)
        self.assertEqual(v.worst([v.VALID, v.WARNING]), v.WARNING)
        self.assertEqual(v.worst([v.WARNING, v.INCOMPLETE]), v.INCOMPLETE)
        self.assertEqual(v.worst([]), v.VALID)

    def test_one_impossible_bar_decides_the_whole_series(self):
        q = v.validate_series([bar(11), bar(12, high="900.00")])
        self.assertEqual(q.verdict, v.INVALID)

    def test_warning_and_valid_are_both_usable(self):
        self.assertTrue(m.Quality(v.VALID).ok)
        self.assertTrue(m.Quality(v.WARNING).ok)
        self.assertFalse(m.Quality(v.INVALID).ok)
        self.assertFalse(m.Quality(v.INCOMPLETE).ok)

    def test_an_empty_series_is_not_a_verdict_on_its_own(self):
        self.assertEqual(v.validate_series([]).verdict, v.VALID)

    def test_findings_name_the_field_and_serialise(self):
        q = v.validate_bar(bar(high="900.00"))
        d = q.to_dict()
        self.assertEqual(d["verdict"], v.INVALID)
        self.assertEqual(d["findings"][0]["field"], "high")
        self.assertIn("severity", d["findings"][0])

    def test_validating_a_series_stamps_each_bar(self):
        series = [bar(11), bar(12)]
        v.validate_series(series)
        self.assertEqual(series[0].quality.verdict, v.VALID)


if __name__ == "__main__":
    unittest.main()
