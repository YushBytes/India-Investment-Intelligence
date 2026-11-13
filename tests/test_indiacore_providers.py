"""The providers, offline, against payloads recorded from the real endpoints.

Every fixture in tests/fixtures/indiacore is a response that actually came
back on 13 November 2026, or a deliberate mutation of one. Nothing here
reaches the network: each provider is built with a FixtureTransport, and a URL
that matches no fixture raises, so a test cannot pass by accident if a
provider changes the URL it builds.
"""

import datetime
import os
import re
import unittest

import _india
from indiacore import provenance as prov
from indiacore import retry as retry_mod
from indiacore.marketdata import models as m
from indiacore.marketdata import normalize
from indiacore.providers import base, bse as bse_mod, nse as nse_mod
from indiacore.providers import registry as registry_mod
from indiacore.providers import transport as tr
from indiacore.providers import yahoo as yahoo_mod
from indiacore.symbols import BSE, NSE, IndexSymbol, SymbolError, bse, nse

SESSION = _india.PREVIOUS_SESSION


def yahoo(moment=_india.NOW_OPEN, **overrides):
    return yahoo_mod.YahooProvider(transport=_india.transport(**overrides),
                                   clock=_india.clock(moment))


def nse_bhav(moment=_india.NOW_OPEN, **overrides):
    return nse_mod.NseBhavcopyProvider(transport=_india.transport(**overrides),
                                       clock=_india.clock(moment))


def bse_bhav(moment=_india.NOW_OPEN, **overrides):
    return bse_mod.BseBhavcopyProvider(transport=_india.transport(**overrides),
                                        clock=_india.clock(moment))


class TheUrlKeepsTheSymbolIntact(unittest.TestCase):
    """The phase 2 bug this whole migration started from."""

    def test_an_nse_symbol_keeps_its_dotted_suffix(self):
        url = yahoo().chart_url(nse("SBIN"))
        self.assertIn("/SBIN.NS", url)
        self.assertNotIn("SBIN-NS", url)

    def test_a_bse_symbol_keeps_its_own_suffix(self):
        self.assertIn("/INFY.BO", yahoo().chart_url(bse("INFY")))

    def test_an_ampersand_ticker_is_percent_encoded(self):
        url = yahoo().chart_url(nse("M&M"))
        self.assertIn("M%26M.NS", url)
        self.assertNotIn("M&M.NS", url)

    def test_an_index_is_encoded_and_carries_no_exchange_suffix(self):
        url = yahoo().chart_url(IndexSymbol("^NSEI"))
        self.assertIn("%5ENSEI", url)
        self.assertNotIn(".NS", url)

    def test_a_ticker_that_opens_with_a_digit_survives(self):
        self.assertIn("/3MINDIA.NS", yahoo().chart_url(nse("3MINDIA")))

    def test_a_hyphenated_ticker_survives(self):
        self.assertIn("/BAJAJ-AUTO.NS", yahoo().chart_url(nse("BAJAJ-AUTO")))

    def test_a_bse_scrip_code_is_refused_before_a_request_is_made(self):
        # 500112.BO answers 404 on the live endpoint, so phase 2 refuses it
        self.assertRaises(SymbolError, yahoo().chart_url, bse("500112"))

    def test_every_url_is_https(self):
        self.assertTrue(yahoo().chart_url(nse("SBIN")).startswith("https://"))


class YahooQuotes(unittest.TestCase):
    def test_a_real_payload_becomes_a_quote(self):
        quote = yahoo().get_quote(nse("SBIN"))
        self.assertEqual(quote.symbol, "NSE:SBIN")
        self.assertEqual(quote.exchange, "NSE")
        self.assertEqual(quote.currency, "INR")
        self.assertEqual(str(quote.last_price.as_rupees), "1080.00")
        self.assertEqual(quote.volume, 15834714)

    def test_the_previous_close_comes_from_the_preceding_bar(self):
        quote = yahoo().get_quote(nse("SBIN"))
        # the 12 November NSE close, which the exchange file also shows
        self.assertEqual(str(quote.previous_close.as_rupees), "1086.50")
        self.assertTrue(quote.previous_close_derived)

    def test_the_source_timestamp_is_carried_in_ist(self):
        quote = yahoo().get_quote(nse("SBIN"))
        stamp = quote.provenance.source_time
        self.assertEqual(stamp.hour, 15)
        self.assertEqual(stamp.minute, 28)
        self.assertEqual(stamp.utcoffset(), datetime.timedelta(minutes=330))

    def test_a_quote_during_the_session_is_delayed_never_live(self):
        quote = yahoo().get_quote(nse("SBIN"))
        self.assertEqual(quote.provenance.freshness, prov.DELAYED)

    def test_the_same_payload_after_the_close_is_end_of_day(self):
        quote = yahoo(_india.NOW_CLOSED).get_quote(nse("SBIN"))
        self.assertEqual(quote.provenance.freshness, prov.END_OF_DAY)

    def test_the_market_status_is_recorded_on_the_quote(self):
        self.assertEqual(yahoo().get_quote(nse("SBIN")).market_status, "open")
        self.assertEqual(
            yahoo(_india.NOW_CLOSED).get_quote(nse("SBIN")).market_status,
            "closed")

    def test_the_provider_is_named_and_marked_unofficial(self):
        quote = yahoo().get_quote(nse("SBIN"))
        self.assertEqual(quote.provenance.provider, "yahoo")
        self.assertFalse(quote.provenance.official)

    def test_an_index_level_is_typed_as_an_index(self):
        level = yahoo().get_index_quote(IndexSymbol("^NSEI"))
        self.assertEqual(level.instrument_type, m.INDEX)
        self.assertIsNone(level.exchange)
        self.assertEqual(str(level.last_price.as_rupees), "25144.30")

    def test_an_etf_is_served_but_not_classified_as_one(self):
        """Yahoo answers EQUITY for NIFTYBEES, so the type is not invented."""
        quote = yahoo().get_quote(nse("NIFTYBEES"))
        self.assertEqual(quote.instrument_type, m.EQUITY)
        self.assertGreater(quote.last_price.as_paise, 0)

    def test_asking_for_an_index_through_get_quote_is_routed_not_refused(self):
        level = yahoo().get_quote(IndexSymbol("^NSEI"))
        self.assertEqual(level.instrument_type, m.INDEX)

    def test_an_instrument_passed_to_get_index_quote_is_refused(self):
        self.assertRaises(base.UnsupportedRequest,
                          yahoo().get_index_quote, nse("SBIN"))


class YahooBars(unittest.TestCase):
    def test_daily_bars_come_back_in_order_with_prices_as_money(self):
        bars = yahoo().get_historical_bars(nse("SBIN"), m.DAY_1)
        self.assertEqual(len(bars), 5)
        self.assertEqual([b.interval for b in bars], [m.DAY_1] * 5)
        self.assertEqual(bars, sorted(bars, key=lambda b: b.start))
        self.assertEqual(str(bars[-2].close.as_rupees), "1086.50")

    def test_float_noise_is_rounded_to_paise(self):
        bars = yahoo().get_historical_bars(nse("SBIN"), m.DAY_1)
        # the raw payload carries 1077.949951171875 for one of these highs
        for bar in bars:
            self.assertEqual(bar.close.as_paise % 1, 0)
            self.assertLessEqual(len(str(bar.close.as_rupees).split(".")[1]), 2)

    def test_bars_are_marked_unadjusted(self):
        bars = yahoo().get_historical_bars(nse("SBIN"), m.DAY_1)
        self.assertEqual(bars[0].adjustment, m.UNADJUSTED)

    def test_the_adjusted_close_is_carried_when_the_payload_has_one(self):
        bars = yahoo().get_historical_bars(nse("SBIN"), m.DAY_1)
        self.assertIsNotNone(bars[0].adj_close)

    def test_a_clean_intraday_window_keeps_every_bar(self):
        bars = yahoo().get_historical_bars(nse("SBIN"), m.MIN_5)
        self.assertEqual(len(bars), 20)
        self.assertEqual(bars[0].interval, m.MIN_5)

    def test_intraday_null_padding_is_dropped_rather_than_filled(self):
        """Yahoo pads a 5m series with nulls. Those bars are not invented."""
        provider = yahoo(**{"interval=5m":
                            _india.fx("yahoo-SBIN.NS-5m-with-null.json")})
        bars = provider.get_historical_bars(nse("SBIN"), m.MIN_5)
        # the recorded window is 11 bars and one of them has a null close
        self.assertEqual(len(bars), 10)
        for bar in bars:
            self.assertGreater(bar.close.as_paise, 0)

    def test_a_range_holding_no_session_is_an_empty_result_not_a_failure(self):
        """Recorded from the live endpoint for the 2026 Muhurat Sunday.

        HTTP 200, meta present, indicators.quote[0] empty, no timestamp array
        and no error. A real nothing, which must not look like a failure.
        """
        provider = yahoo(**{"SBIN.NS?interval=1d": _india.fx(
            "yahoo-SBIN.NS-empty-holiday-range.json")})
        answer = provider.get_historical_bars(
            nse("SBIN"), m.DAY_1, datetime.date(2026, 11, 8),
            datetime.date(2026, 11, 8))
        self.assertIsInstance(answer, base.EmptyButValid)
        self.assertIn("no bars", answer.reason)

    def test_a_half_open_range_is_refused_rather_than_guessed(self):
        self.assertRaises(base.UnsupportedRequest,
                          yahoo().get_historical_bars, nse("SBIN"), m.DAY_1,
                          datetime.date(2026, 11, 1), None)

    def test_a_backwards_range_is_refused(self):
        self.assertRaises(base.UnsupportedRequest,
                          yahoo().get_historical_bars, nse("SBIN"), m.DAY_1,
                          datetime.date(2026, 11, 12),
                          datetime.date(2026, 11, 1))

    def test_an_intraday_range_beyond_the_declared_depth_is_refused(self):
        self.assertRaises(base.UnsupportedRequest,
                          yahoo().get_historical_bars, nse("SBIN"), m.MIN_5,
                          datetime.date(2026, 1, 1),
                          datetime.date(2026, 11, 12))

    def test_an_unknown_interval_is_refused(self):
        self.assertRaises(base.UnsupportedRequest,
                          yahoo().get_historical_bars, nse("SBIN"), "7m")


class YahooFailures(unittest.TestCase):
    def test_a_404_is_a_missing_symbol_and_is_not_retried(self):
        error = tr.HttpError(404, "Not Found",
                            url="https://query1.finance.yahoo.com/x")
        provider = yahoo_mod.YahooProvider(
            transport=tr.FixtureTransport({"chart": error}),
            clock=_india.clock())
        self.assertRaises(base.SymbolNotFound, provider.get_quote,
                          nse("ZZZZNOPE"))
        self.assertEqual(error.retry_class, retry_mod.NOT_FOUND)

    def test_a_timeout_exhausts_the_retries_and_fails_the_provider(self):
        provider = yahoo_mod.YahooProvider(
            transport=tr.FixtureTransport(
                {"chart": tr.TransportTimeout("timed out")}),
            limiter=None, clock=_india.clock(),
            policy=retry_mod.RetryPolicy(attempts=2, sleeper=lambda s: None))
        self.assertRaises(base.ProviderUnavailable, provider.get_quote,
                          nse("SBIN"))

    def test_both_hosts_are_tried_before_the_provider_gives_up(self):
        transport = tr.FixtureTransport(
            {"chart": tr.TransportError("connection reset")})
        provider = yahoo_mod.YahooProvider(
            transport=transport, clock=_india.clock(),
            policy=retry_mod.RetryPolicy(attempts=1, sleeper=lambda s: None))
        try:
            provider.get_quote(nse("SBIN"))
        except base.ProviderUnavailable as exc:
            self.assertIn("query1", str(exc))
            self.assertIn("query2", str(exc))
        else:
            self.fail("expected ProviderUnavailable")

    def test_malformed_json_is_a_bad_response_not_a_quote(self):
        provider = yahoo(**{"SBIN.NS?interval=1d":
                            _india.fx("yahoo-malformed.json")})
        self.assertRaises(base.ProviderUnavailable, provider.get_quote,
                          nse("SBIN"))

    def test_a_payload_for_another_instrument_is_refused(self):
        provider = yahoo(**{"SBIN.NS?interval=1d":
                            _india.fx("yahoo-wrong-symbol.json")})
        try:
            provider.get_quote(nse("SBIN"))
        except base.BadProviderData as exc:
            self.assertIn("RELIANCE.NS", str(exc))
        else:
            self.fail("a payload for the wrong symbol must be refused")

    def test_a_payload_with_no_close_array_is_refused(self):
        provider = yahoo(**{"SBIN.NS?interval=1d":
                            _india.fx("yahoo-SBIN.NS-missing-close.json")})
        self.assertRaises(base.BadProviderData,
                          provider.get_historical_bars, nse("SBIN"), m.DAY_1)

    def test_a_quote_with_no_timestamp_has_an_unknown_age(self):
        provider = yahoo(**{"SBIN.NS?interval=1d":
                            _india.fx("yahoo-SBIN.NS-no-timestamp.json")})
        quote = provider.get_quote(nse("SBIN"))
        self.assertEqual(quote.provenance.freshness, prov.UNKNOWN)
        self.assertIn("cannot be established", quote.provenance.freshness_reason)

    def test_a_rate_limited_provider_refuses_fast(self):
        provider = yahoo()
        provider.limiter.note_rate_limited(60, reason="429 from yahoo")
        self.assertRaises(base.ProviderUnavailable, provider.get_quote,
                          nse("SBIN"))
        status = provider.get_provider_status()
        self.assertTrue(status.rate_limited)
        self.assertFalse(status.available)


class TheExchangeFilesAreOfficial(unittest.TestCase):
    def test_the_nse_file_gives_the_exchanges_own_numbers(self):
        bars = nse_bhav().get_historical_bars(nse("SBIN"), m.DAY_1, SESSION,
                                               SESSION)
        self.assertEqual(len(bars), 1)
        bar = bars[0]
        self.assertEqual(str(bar.close.as_rupees), "1086.50")
        self.assertEqual(str(bar.open.as_rupees), "1062.00")
        self.assertEqual(str(bar.high.as_rupees), "1089.00")
        self.assertEqual(str(bar.low.as_rupees), "1058.50")
        self.assertEqual(bar.volume, 26973410)
        self.assertTrue(bar.provenance.official)

    def test_the_bse_file_is_a_different_instrument_on_the_same_day(self):
        on_nse = nse_bhav().get_historical_bars(nse("SBIN"), m.DAY_1, SESSION,
                                                 SESSION)[0]
        on_bse = bse_bhav().get_historical_bars(bse("SBIN"), m.DAY_1, SESSION,
                                                 SESSION)[0]
        self.assertEqual(str(on_nse.close.as_rupees), "1086.50")
        self.assertEqual(str(on_bse.close.as_rupees), "1085.00")
        self.assertNotEqual(on_nse.volume, on_bse.volume)
        self.assertEqual(on_nse.exchange, "NSE")
        self.assertEqual(on_bse.exchange, "BSE")

    def test_a_bhavcopy_bar_is_unadjusted_and_says_so(self):
        bar = nse_bhav().get_historical_bars(nse("SBIN"), m.DAY_1, SESSION,
                                              SESSION)[0]
        self.assertEqual(bar.adjustment, m.UNADJUSTED)

    def test_the_source_url_names_the_session(self):
        bar = nse_bhav().get_historical_bars(nse("SBIN"), m.DAY_1, SESSION,
                                              SESSION)[0]
        self.assertIn("20261112", bar.provenance.source)

    def test_a_whole_session_can_be_read_in_one_request(self):
        bars = nse_bhav().get_session(SESSION)
        self.assertGreater(len(bars), 5)
        tickers = set(b.symbol for b in bars)
        self.assertIn("NSE:SBIN", tickers)
        self.assertIn("NSE:RELIANCE", tickers)

    def test_an_instrument_absent_from_the_file_is_an_empty_result(self):
        answer = nse_bhav().get_historical_bars(nse("ZZZZNOPE"), m.DAY_1,
                                                 SESSION, SESSION)
        self.assertIsInstance(answer, base.EmptyButValid)
        self.assertIn("read and the instrument is not in it", answer.reason)

    def test_a_missing_file_is_a_failure_and_not_an_empty_result(self):
        provider = nse_mod.NseBhavcopyProvider(
            transport=tr.FixtureTransport({}), clock=_india.clock())
        self.assertRaises(base.ProviderError, provider.get_historical_bars,
                          nse("SBIN"), m.DAY_1, SESSION, SESSION)

    def test_a_file_that_is_not_the_unified_format_is_refused(self):
        provider = bse_mod.BseBhavcopyProvider(
            transport=tr.FixtureTransport(
                {"BhavCopy": _india.fx("bhavcopy-wrong-format.csv")}),
            clock=_india.clock())
        try:
            provider.get_historical_bars(bse("SBIN"), m.DAY_1, SESSION,
                                         SESSION)
        except base.BadProviderData as exc:
            self.assertIn("unified bhavcopy", str(exc))
        else:
            self.fail("expected BadProviderData")

    def test_a_404_for_a_session_is_a_clear_message(self):
        provider = nse_mod.NseBhavcopyProvider(
            transport=tr.FixtureTransport(
                {"BhavCopy": tr.HttpError(404, "Not Found")}),
            clock=_india.clock())
        try:
            provider.get_historical_bars(nse("SBIN"), m.DAY_1, SESSION,
                                         SESSION)
        except base.SymbolNotFound as exc:
            self.assertIn("not been published", str(exc))
        else:
            self.fail("expected SymbolNotFound")


class ABhavcopyIsNotAQuoteSource(unittest.TestCase):
    def test_asking_it_for_a_quote_is_refused_with_the_reason(self):
        try:
            nse_bhav().get_quote(nse("SBIN"))
        except base.UnsupportedRequest as exc:
            self.assertIn("published after the close", str(exc))
        else:
            self.fail("a bhavcopy must not serve a quote")

    def test_it_declares_no_quote_capability(self):
        caps = nse_bhav().capabilities
        self.assertEqual(caps.kinds, (m.BARS,))
        self.assertFalse(caps.supports(m.QUOTE, NSE))

    def test_todays_session_is_refused_while_the_market_is_open(self):
        try:
            nse_bhav().get_historical_bars(nse("SBIN"), m.DAY_1,
                                           _india.SESSION, _india.SESSION)
        except base.UnsupportedRequest as exc:
            self.assertIn("not a completed", str(exc))
        else:
            self.fail("an unfinished session has no bhavcopy")

    def test_it_serves_only_daily_bars(self):
        self.assertRaises(base.UnsupportedRequest,
                          nse_bhav().get_historical_bars, nse("SBIN"),
                          m.MIN_5, SESSION, SESSION)

    def test_each_exchange_file_refuses_the_others_instruments(self):
        self.assertRaises(base.UnsupportedRequest,
                          nse_bhav().get_historical_bars, bse("SBIN"),
                          m.DAY_1, SESSION, SESSION)
        self.assertRaises(base.UnsupportedRequest,
                          bse_bhav().get_historical_bars, nse("SBIN"),
                          m.DAY_1, SESSION, SESSION)

    def test_a_bse_row_carries_the_exchanges_own_scrip_code(self):
        text = bse_bhav().fetch_csv(SESSION)
        _bars, _notes, codes = normalize.bhavcopy_bars(
            text, BSE, lambda t: m.Provenance("bse_bhavcopy", "f",
                                              _india.NOW_OPEN),
            wanted=["SBIN"], session_date=SESSION)
        self.assertEqual(codes["SBIN"], "500112")


class TheRefusedEndpointsAreListedNotHidden(unittest.TestCase):
    def test_the_nse_quote_api_serves_nothing_and_says_why(self):
        provider = nse_mod.NseQuoteApiProvider(clock=_india.clock())
        self.assertFalse(provider.capabilities.operational)
        try:
            provider.get_quote(nse("SBIN"))
        except base.ProviderUnavailable as exc:
            self.assertIn("403", str(exc))
        else:
            self.fail("a refused endpoint must not answer")

    def test_the_bse_quote_api_serves_nothing_and_says_why(self):
        provider = bse_mod.BseQuoteApiProvider(clock=_india.clock())
        try:
            provider.get_historical_bars(bse("SBIN"))
        except base.ProviderUnavailable as exc:
            self.assertIn("403", str(exc))
        else:
            self.fail("a refused endpoint must not answer")

    def test_their_status_is_unavailable_with_the_evidence(self):
        status = nse_mod.NseQuoteApiProvider().get_provider_status()
        self.assertFalse(status.available)
        self.assertIn("not operational", status.reason.lower())

    def test_they_declare_no_capabilities_at_all(self):
        for provider in (nse_mod.NseQuoteApiProvider(),
                         bse_mod.BseQuoteApiProvider()):
            self.assertEqual(provider.capabilities.kinds, ())
            self.assertFalse(provider.capabilities.supports(m.QUOTE, NSE))

    def test_the_evidence_names_the_endpoint_that_was_checked(self):
        self.assertIn("quote-equity", nse_mod.QUOTE_API_EVIDENCE)
        self.assertIn("api.bseindia.com", bse_mod.QUOTE_API_EVIDENCE)


class TheRegistryRanksAndLists(unittest.TestCase):
    def setUp(self):
        self.reg = _india.registry()

    def test_the_official_file_is_preferred_for_daily_bars(self):
        chosen = self.reg.choose(m.BARS, NSE, m.DAY_1)
        self.assertEqual(chosen.name, "nse_bhavcopy")

    def test_the_bse_file_is_preferred_for_bse_daily_bars(self):
        self.assertEqual(self.reg.choose(m.BARS, BSE, m.DAY_1).name,
                         "bse_bhavcopy")

    def test_only_yahoo_serves_quotes_today(self):
        self.assertEqual(self.reg.choose(m.QUOTE, NSE).name, "yahoo")

    def test_only_yahoo_serves_intraday(self):
        self.assertEqual(self.reg.choose(m.BARS, NSE, m.MIN_5).name, "yahoo")

    def test_a_provider_can_be_demanded_by_name(self):
        self.assertEqual(self.reg.choose(m.BARS, NSE, m.DAY_1,
                                         name="yahoo").name, "yahoo")

    def test_demanding_a_provider_that_cannot_do_it_is_refused(self):
        self.assertRaises(base.UnsupportedRequest, self.reg.choose, m.QUOTE,
                          NSE, None, "nse_bhavcopy")

    def test_an_unknown_provider_name_is_refused_with_the_list(self):
        try:
            self.reg.get("bloomberg")
        except registry_mod.RegistryError as exc:
            self.assertIn("yahoo", str(exc))
        else:
            self.fail("expected RegistryError")

    def test_nothing_serves_a_kind_no_one_declared(self):
        # fundamentals belong to a later phase, and nothing pretends otherwise
        self.assertRaises(base.UnsupportedRequest, self.reg.choose,
                          "fundamentals", NSE, None)

    def test_candidates_include_a_fallback_for_daily_bars(self):
        names = [p.name for p in self.reg.candidates(m.BARS, NSE, m.DAY_1)]
        self.assertEqual(names[0], "nse_bhavcopy")
        self.assertIn("yahoo", names)

    def test_a_not_operational_provider_is_never_a_candidate(self):
        for kind in (m.QUOTE, m.BARS, m.INDEX_QUOTE):
            names = [p.name for p in self.reg.candidates(kind)]
            self.assertNotIn("nse_quote_api", names)
            self.assertNotIn("bse_quote_api", names)

    def test_the_matrix_lists_the_unsupported_ones_anyway(self):
        rows = {row["provider"]: row for row in self.reg.matrix()}
        self.assertEqual(len(rows), 5)
        self.assertFalse(rows["nse_quote_api"]["operational"])
        self.assertTrue(rows["nse_bhavcopy"]["official"])
        self.assertFalse(rows["yahoo"]["official"])

    def test_no_provider_claims_real_time(self):
        for row in self.reg.matrix():
            self.assertFalse(row["realtime"], row["provider"])

    def test_registering_a_name_twice_is_refused(self):
        self.assertRaises(registry_mod.RegistryError, self.reg.add,
                          yahoo_mod.YahooProvider(transport=_india.transport()))

    def test_every_provider_reports_a_status_without_making_a_request(self):
        transport = _india.transport()
        reg = _india.registry(transport_=transport)
        statuses = reg.statuses()
        self.assertEqual(len(statuses), 5)
        self.assertEqual(transport.calls, [])


class TheTransportIsTheOnlyWayOut(unittest.TestCase):
    def setUp(self):
        self.transport = tr.HttpTransport(
            allowed_hosts=("query1.finance.yahoo.com",))

    def test_a_non_https_url_is_refused(self):
        self.assertRaises(tr.UnsafeUrl, self.transport.check_url,
                          "http://query1.finance.yahoo.com/x")

    def test_a_host_the_provider_did_not_declare_is_refused(self):
        self.assertRaises(tr.UnsafeUrl, self.transport.check_url,
                          "https://evil.example.com/x")

    def test_a_url_carrying_credentials_is_refused(self):
        self.assertRaises(tr.UnsafeUrl, self.transport.check_url,
                          "https://user:pass@query1.finance.yahoo.com/x")

    def test_a_declared_host_is_allowed(self):
        url = "https://query1.finance.yahoo.com/v8/finance/chart/SBIN.NS"
        self.assertEqual(self.transport.check_url(url), url)

    def test_there_is_always_a_timeout(self):
        self.assertGreater(self.transport.timeout, 0)
        self.assertEqual(tr.DEFAULT_TIMEOUT, 15.0)

    def test_there_is_always_a_size_limit(self):
        self.assertGreater(self.transport.max_bytes, 0)

    def test_a_response_over_the_limit_is_refused(self):
        class Fat(object):
            def read(self, size):
                return b"x" * size

            def close(self):
                pass

        transport = tr.HttpTransport(
            allowed_hosts=("query1.finance.yahoo.com",), max_bytes=10,
            opener=lambda request, timeout: Fat())
        self.assertRaises(tr.ResponseTooLarge, transport.get_bytes,
                          "https://query1.finance.yahoo.com/x")

    def test_a_query_string_is_stripped_from_an_error_message(self):
        error = tr.TransportError(
            "failed", url="https://query1.finance.yahoo.com/x?token=sekret")
        self.assertNotIn("sekret", error.url)
        self.assertEqual(error.url,
                         "https://query1.finance.yahoo.com/x")

    def test_no_request_header_reaches_an_error_message(self):
        error = tr.HttpError(403, "Forbidden",
                            url="https://query1.finance.yahoo.com/x")
        for secret in ("Authorization", "Cookie", "User-Agent"):
            self.assertNotIn(secret, str(error))

    def test_an_http_error_carries_its_retry_class(self):
        self.assertEqual(tr.HttpError(503, "x").retry_class,
                         retry_mod.RETRYABLE)
        self.assertEqual(tr.HttpError(404, "x").retry_class,
                         retry_mod.NOT_FOUND)
        self.assertEqual(tr.HttpError(403, "x").retry_class, retry_mod.FATAL)
        self.assertEqual(tr.HttpError(429, "x").retry_class,
                         retry_mod.RATE_LIMITED)

    def test_the_user_agent_carries_no_credential(self):
        self.assertNotIn("key", tr.DEFAULT_USER_AGENT.lower())
        self.assertNotIn("token", tr.DEFAULT_USER_AGENT.lower())

    def test_a_body_that_is_not_json_is_a_bad_response(self):
        class Junk(object):
            def read(self, size):
                return b"<html>not json</html>"

            def close(self):
                pass

        transport = tr.HttpTransport(
            allowed_hosts=("query1.finance.yahoo.com",),
            opener=lambda request, timeout: Junk())
        self.assertRaises(tr.BadResponse, transport.get_json,
                          "https://query1.finance.yahoo.com/x")

    def test_json_that_is_not_an_object_is_a_bad_response(self):
        class Listy(object):
            def read(self, size):
                return b"[1, 2, 3]"

            def close(self):
                pass

        transport = tr.HttpTransport(
            allowed_hosts=("query1.finance.yahoo.com",),
            opener=lambda request, timeout: Listy())
        self.assertRaises(tr.BadResponse, transport.get_json,
                          "https://query1.finance.yahoo.com/x")

    def test_a_fixture_with_no_route_raises_rather_than_returning_nothing(self):
        self.assertRaises(tr.TransportError,
                          tr.FixtureTransport({}).get_json,
                          "https://query1.finance.yahoo.com/x")

    def test_the_providers_declare_their_hosts(self):
        self.assertEqual(yahoo_mod.HOSTS[0], "query1.finance.yahoo.com")
        self.assertIn("nsearchives", nse_mod.ARCHIVE_HOST)
        self.assertIn("bseindia", bse_mod.HOST)


class NoSecretsAnywhere(unittest.TestCase):
    def test_no_module_assigns_a_credential_literal(self):
        """A key in the source is the one leak no runtime guard can fix."""
        assigned = re.compile(
            r"(?i)(api_?key|secret|password|passwd|token|bearer)\s*[:=]\s*"
            r"[\"'][^\"']{6,}[\"']")
        for base_dir, dirs, files in os.walk(
                os.path.join(_load_root(), "indiacore")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(base_dir, name)
                with open(path, encoding="utf-8") as fh:
                    for number, line in enumerate(fh, 1):
                        self.assertIsNone(
                            assigned.search(line),
                            "%s line %d looks like an assigned credential"
                            % (name, number))

    def test_credentials_are_read_from_the_environment_only(self):
        """The documented route for a future licensed feed."""
        with open(os.path.join(_load_root(), "indiacore", "DATA.md"),
                  encoding="utf-8") as fh:
            doc = fh.read()
        self.assertIn("read from the environment only", doc)

    def test_no_provider_declares_an_auth_requirement_it_then_hides(self):
        for provider in _india.registry().all():
            caps = provider.capabilities
            if caps.auth_required:
                self.assertFalse(caps.operational,
                                 "%s claims to need auth and to work anyway"
                                 % provider.name)


def _load_root():
    import _load
    return _load.ROOT


if __name__ == "__main__":
    unittest.main()
