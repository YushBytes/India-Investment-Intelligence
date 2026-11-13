import datetime
import unittest

import _india
from indiacore import provenance as prov
from indiacore.marketdata import models as m
from indiacore.money import Money
from indiacore.symbols import BSE, NSE, IndexSymbol, bse, nse

IST = _india.IST
MOMENT = _india.NOW_OPEN


def provenance(**kw):
    base = dict(provider="yahoo", source="query1.finance.yahoo.com",
                retrieved_at=MOMENT)
    base.update(kw)
    return m.Provenance(**base)


def quote(**kw):
    base = dict(symbol=nse("SBIN"), last_price="1080.00",
                provenance=provenance(), previous_close="1086.50",
                open="1090.00", high="1090.70", low="1074.15",
                volume=15834714, instrument_type=m.EQUITY)
    base.update(kw)
    return m.Quote(**base)


def bar(**kw):
    base = dict(symbol=nse("SBIN"), interval=m.DAY_1,
                start=datetime.datetime(2026, 11, 12, 9, 15, tzinfo=IST),
                open="1062.00", high="1089.00", low="1058.50",
                close="1086.50", volume=26973410, provenance=provenance())
    base.update(kw)
    return m.Bar(**base)


class EveryRecordCarriesItsSource(unittest.TestCase):
    """The rule phase 3 is built on: no figure without its provenance."""

    def test_a_quote_without_provenance_is_refused(self):
        self.assertRaises(m.ModelError, m.Quote, nse("SBIN"), "1080.00", None)

    def test_a_bar_without_provenance_is_refused(self):
        self.assertRaises(
            m.ModelError, m.Bar, nse("SBIN"), m.DAY_1,
            datetime.datetime(2026, 11, 12, 9, 15, tzinfo=IST),
            "1", "1", "1", "1", None)

    def test_provenance_needs_a_provider_and_a_source(self):
        self.assertRaises(m.ModelError, m.Provenance, "", "src", MOMENT)
        self.assertRaises(m.ModelError, m.Provenance, "yahoo", "", MOMENT)

    def test_provenance_needs_a_retrieval_time(self):
        self.assertRaises(m.ModelError, m.Provenance, "yahoo", "src", None)

    def test_the_provider_survives_serialisation(self):
        d = quote().to_dict()
        self.assertEqual(d["provenance"]["provider"], "yahoo")
        self.assertIn("retrieved_at", d["provenance"])


class SymbolsNotStrings(unittest.TestCase):
    def test_a_bare_string_is_refused_because_it_omits_the_exchange(self):
        self.assertRaises(m.ModelError, quote, symbol="SBIN.NS")

    def test_the_canonical_form_names_the_exchange(self):
        self.assertEqual(quote().symbol, "NSE:SBIN")
        self.assertEqual(quote(symbol=bse("SBIN")).symbol, "BSE:SBIN")

    def test_the_exchange_comes_from_the_symbol(self):
        self.assertEqual(quote().exchange, "NSE")
        self.assertEqual(quote(symbol=bse("SBIN")).exchange, "BSE")

    def test_an_index_carries_no_exchange(self):
        q = quote(symbol=IndexSymbol("^NSEI"), instrument_type=m.INDEX)
        self.assertEqual(q.symbol, "^NSEI")
        self.assertIsNone(q.exchange)


class PricesAreMoney(unittest.TestCase):
    def test_a_price_carries_its_currency(self):
        self.assertEqual(quote().last_price.currency, "INR")

    def test_float_noise_is_rounded_to_paise(self):
        # Yahoo sends this for a close the exchange published as 1077.95
        self.assertEqual(bar(close=1077.949951171875).close,
                         Money.rupees("1077.95"))

    def test_a_dollar_amount_is_refused(self):
        self.assertRaises(m.ModelError, quote,
                          last_price=Money.rupees(5, currency="USD"))

    def test_prices_serialise_as_exact_strings_not_floats(self):
        d = bar(close=1077.949951171875).to_dict()
        self.assertEqual(d["close"], "1077.95")
        self.assertIsInstance(d["close"], str)

    def test_a_missing_required_price_is_refused(self):
        self.assertRaises(m.ModelError, bar, close=None)

    def test_the_change_is_computed_on_exact_paise(self):
        q = quote(last_price="1080.00", previous_close="1086.50")
        self.assertEqual(q.change, Money.rupees("-6.50"))
        self.assertAlmostEqual(q.change_pct, -0.5983, places=3)

    def test_no_previous_close_means_no_change_rather_than_zero(self):
        q = quote(previous_close=None)
        self.assertIsNone(q.change)
        self.assertIsNone(q.change_pct)


class TimestampsAreAware(unittest.TestCase):
    def test_a_naive_datetime_is_refused(self):
        self.assertRaises(m.ModelError, bar,
                          start=datetime.datetime(2026, 11, 12, 9, 15))

    def test_a_utc_moment_is_converted_to_ist(self):
        b = bar(start=datetime.datetime(2026, 11, 12, 3, 45,
                                        tzinfo=datetime.timezone.utc))
        self.assertEqual(b.start.hour, 9)
        self.assertEqual(b.start.minute, 15)

    def test_the_session_date_defaults_to_the_start_date(self):
        self.assertEqual(bar().session_date, datetime.date(2026, 11, 12))

    def test_a_bar_knows_when_it_ends(self):
        b = bar(interval=m.MIN_5,
                start=datetime.datetime(2026, 11, 13, 9, 15, tzinfo=IST))
        self.assertEqual(b.end.minute, 20)


class Intervals(unittest.TestCase):
    def test_an_unknown_interval_is_refused(self):
        self.assertRaises(m.ModelError, m.check_interval, "7m")
        self.assertRaises(m.ModelError, bar, interval="7m")

    def test_daily_is_not_intraday(self):
        self.assertFalse(m.is_intraday(m.DAY_1))
        self.assertTrue(m.is_intraday(m.MIN_5))


class InstrumentTypeMayBeUnknown(unittest.TestCase):
    """No provider classifies Indian ETFs, so UNKNOWN is a real state."""

    def test_unknown_is_accepted(self):
        self.assertEqual(quote(instrument_type=m.UNKNOWN_TYPE).instrument_type,
                         m.UNKNOWN_TYPE)

    def test_an_invented_type_is_refused(self):
        self.assertRaises(m.ModelError, quote, instrument_type="reit")


class AdjustmentIsStated(unittest.TestCase):
    def test_the_default_is_unknown_rather_than_adjusted(self):
        self.assertEqual(bar().adjustment, m.ADJUSTMENT_UNKNOWN)

    def test_an_invented_adjustment_state_is_refused(self):
        self.assertRaises(m.ModelError, bar, adjustment="sort of")


class CapabilitiesAreDeclared(unittest.TestCase):
    def test_a_provider_that_is_not_operational_supports_nothing(self):
        caps = m.Capabilities("dead", exchanges=("NSE",), kinds=(m.QUOTE,),
                              operational=False)
        self.assertFalse(caps.supports(m.QUOTE, NSE))

    def test_support_is_checked_per_exchange_and_interval(self):
        caps = m.Capabilities("x", exchanges=("NSE",), kinds=(m.BARS,),
                              intervals=(m.DAY_1,))
        self.assertTrue(caps.supports(m.BARS, NSE, m.DAY_1))
        self.assertFalse(caps.supports(m.BARS, BSE, m.DAY_1))
        self.assertFalse(caps.supports(m.BARS, NSE, m.MIN_5))
        self.assertFalse(caps.supports(m.QUOTE, NSE))

    def test_realtime_is_off_unless_declared(self):
        self.assertFalse(m.Capabilities("x").realtime)


class ProviderStatusSerialises(unittest.TestCase):
    def test_a_status_round_trips_to_a_dict(self):
        status = m.ProviderStatus("yahoo", True, last_success=MOMENT,
                                  requests=3, failures=1)
        d = status.to_dict()
        self.assertTrue(d["available"])
        self.assertEqual(d["requests"], 3)
        self.assertEqual(d["failures"], 1)
        self.assertIn("2026-11-13", d["last_success"])


class ProvenanceIsImmutableInPlace(unittest.TestCase):
    def test_but_returns_a_copy(self):
        first = provenance(freshness=prov.UNKNOWN)
        second = first.but(freshness=prov.DELAYED, freshness_reason="because")
        self.assertEqual(first.freshness, prov.UNKNOWN)
        self.assertEqual(second.freshness, prov.DELAYED)
        self.assertEqual(second.provider, "yahoo")

    def test_a_cached_record_says_so_and_carries_its_age(self):
        p = provenance(cached=True, cache_age_seconds=42.0)
        self.assertTrue(p.to_dict()["cached"])
        self.assertEqual(p.to_dict()["cache_age_seconds"], 42.0)


if __name__ == "__main__":
    unittest.main()
