import unittest

import _load  # noqa: F401
from indiacore.symbols import (BSE, NSE, IndexSymbol, Symbol, SymbolError,
                               bse, nse, parse)


def shipped_to_yahoo(sym):
    """The encoder trader-scanner/scan.py ships, reproduced for contrast."""
    return sym.upper().replace(".", "-")


class TheBugThisFixes(unittest.TestCase):
    def test_the_shipped_encoder_destroys_every_indian_symbol(self):
        for raw in ("SBIN.NS", "INFY.NS", "TCS.NS", "SBIN.BO"):
            self.assertEqual(shipped_to_yahoo(raw), raw.replace(".", "-"))
            self.assertNotIn(".", shipped_to_yahoo(raw))

    def test_indiacore_keeps_the_dot(self):
        for ticker in ("SBIN", "INFY", "TCS"):
            self.assertEqual(nse(ticker).yahoo, ticker + ".NS")
            self.assertIn(".", nse(ticker).yahoo)


class Encoding(unittest.TestCase):
    def test_nse_suffix(self):
        self.assertEqual(nse("SBIN").yahoo, "SBIN.NS")
        self.assertEqual(nse("INFY").yahoo, "INFY.NS")
        self.assertEqual(nse("TCS").yahoo, "TCS.NS")

    def test_bse_suffix_is_different(self):
        self.assertEqual(bse("SBIN").yahoo, "SBIN.BO")
        self.assertEqual(bse("INFY").yahoo, "INFY.BO")

    def test_lowercase_and_whitespace_are_normalised(self):
        self.assertEqual(nse("  sbin  ").yahoo, "SBIN.NS")

    def test_etfs_use_the_same_shape(self):
        self.assertEqual(nse("NIFTYBEES").yahoo, "NIFTYBEES.NS")
        self.assertEqual(nse("ITBEES").yahoo, "ITBEES.NS")

    def test_tickers_that_start_with_a_digit(self):
        self.assertEqual(nse("3MINDIA").yahoo, "3MINDIA.NS")
        self.assertEqual(nse("20MICRONS").yahoo, "20MICRONS.NS")

    def test_hyphenated_ticker(self):
        self.assertEqual(nse("BAJAJ-AUTO").yahoo, "BAJAJ-AUTO.NS")

    def test_empty_and_malformed_are_refused(self):
        for bad in ("", "   ", None, "!!!", "A" * 40):
            self.assertRaises(SymbolError, nse, bad)

    def test_exchange_must_be_a_real_exchange(self):
        self.assertRaises(SymbolError, Symbol, "SBIN", "NSE")


class UrlEncoding(unittest.TestCase):
    """A raw ampersand truncates the request, so the two forms differ."""

    def test_ampersand_is_percent_encoded(self):
        s = nse("M&M")
        self.assertEqual(s.yahoo, "M&M.NS")
        self.assertEqual(s.yahoo_url(), "M%26M.NS")

    def test_plain_tickers_are_unchanged_in_a_url(self):
        for ticker in ("SBIN", "INFY", "TCS", "BAJAJ-AUTO", "3MINDIA"):
            s = nse(ticker)
            self.assertEqual(s.yahoo_url(), s.yahoo, ticker)

    def test_index_caret_is_percent_encoded(self):
        self.assertEqual(IndexSymbol("^NSEI").yahoo_url(), "%5ENSEI")
        self.assertEqual(IndexSymbol("^BSESN").yahoo_url(), "%5EBSESN")


class RoundTrip(unittest.TestCase):
    CASES = ["SBIN.NS", "INFY.NS", "TCS.NS", "M&M.NS", "BAJAJ-AUTO.NS",
             "3MINDIA.NS", "NIFTYBEES.NS", "SBIN.BO", "INFY.BO"]

    def test_parse_then_encode_returns_the_original(self):
        for text in self.CASES:
            self.assertEqual(Symbol.parse_yahoo(text).yahoo, text, text)

    def test_encode_then_parse_returns_the_original(self):
        for text in self.CASES:
            s = Symbol.parse_yahoo(text)
            self.assertEqual(Symbol.parse_yahoo(s.yahoo), s, text)

    def test_the_exchange_survives_the_round_trip(self):
        self.assertIs(Symbol.parse_yahoo("SBIN.NS").exchange, NSE)
        self.assertIs(Symbol.parse_yahoo("SBIN.BO").exchange, BSE)

    def test_a_bare_ticker_is_refused_as_ambiguous(self):
        self.assertRaises(SymbolError, Symbol.parse_yahoo, "SBIN")

    def test_a_us_style_symbol_is_refused(self):
        self.assertRaises(SymbolError, Symbol.parse_yahoo, "AAPL")
        self.assertRaises(SymbolError, Symbol.parse_yahoo, "BRK-B")


class ExchangesAreDistinct(unittest.TestCase):
    """SBIN on NSE and SBIN on BSE are different instruments.

    Checked on 12 November 2026: SBIN.NS closed at 1086.50 and SBIN.BO at
    1085.00. Treating them as one symbol loses that.
    """

    def test_not_equal(self):
        self.assertNotEqual(nse("SBIN"), bse("SBIN"))

    def test_hash_differently_so_they_survive_a_dict(self):
        book = {nse("SBIN"): "nse", bse("SBIN"): "bse"}
        self.assertEqual(len(book), 2)

    def test_str_names_the_exchange(self):
        self.assertEqual(str(nse("SBIN")), "NSE:SBIN")
        self.assertEqual(str(bse("SBIN")), "BSE:SBIN")

    def test_on_moves_a_ticker_across(self):
        self.assertEqual(nse("SBIN").on(BSE), bse("SBIN"))


class BseScripCodes(unittest.TestCase):
    """A scrip code is a BSE identifier, not a Yahoo symbol."""

    def test_scrip_code_is_carried_as_metadata(self):
        s = bse("SBIN", scrip_code="500112")
        self.assertEqual(s.scrip_code, "500112")
        self.assertEqual(s.yahoo, "SBIN.BO")

    def test_scrip_code_must_be_numeric(self):
        self.assertRaises(SymbolError, bse, "SBIN", "five hundred")

    def test_nse_has_no_scrip_code(self):
        self.assertRaises(SymbolError, Symbol, "SBIN", NSE, None, "500112")

    def test_an_all_digit_bse_ticker_is_not_addressable(self):
        """Yahoo answers 404 for 500112.BO, so the URL builder refuses."""
        s = bse("500112")
        self.assertFalse(s.yahoo_addressable)
        self.assertRaises(SymbolError, s.yahoo_url)

    def test_the_refusal_points_at_the_fix(self):
        try:
            bse("500112").yahoo_url()
        except SymbolError as e:
            self.assertIn("scrip code", str(e))
            self.assertIn("SBIN", str(e))
        else:
            self.fail("expected SymbolError")

    def test_alphabetic_bse_tickers_are_addressable(self):
        self.assertTrue(bse("SBIN", scrip_code="500112").yahoo_addressable)


class Series(unittest.TestCase):
    """NSE feeds carry a series suffix that is not part of a Yahoo symbol."""

    def test_eq_series_is_split_off(self):
        s = nse("SBIN-EQ")
        self.assertEqual(s.ticker, "SBIN")
        self.assertEqual(s.series, "EQ")
        self.assertEqual(s.yahoo, "SBIN.NS")

    def test_be_series(self):
        self.assertEqual(nse("IDEA-BE").series, "BE")

    def test_a_hyphen_that_is_not_a_series_is_left_alone(self):
        s = nse("BAJAJ-AUTO")
        self.assertEqual(s.ticker, "BAJAJ-AUTO")
        self.assertIsNone(s.series)


class Indices(unittest.TestCase):
    """Indices are a different shape: a caret, and no exchange suffix."""

    def test_known_indices_resolve_their_names(self):
        self.assertEqual(IndexSymbol("^NSEI").name, "NIFTY 50")
        self.assertEqual(IndexSymbol("^BSESN").name, "S&P BSE SENSEX")
        self.assertEqual(IndexSymbol("^INDIAVIX").name, "INDIA VIX")

    def test_an_index_takes_no_suffix(self):
        self.assertEqual(IndexSymbol("^NSEI").yahoo, "^NSEI")

    def test_an_index_is_not_a_symbol(self):
        self.assertRaises(SymbolError, nse, "^NSEI")
        self.assertRaises(SymbolError, Symbol.parse_yahoo, "^NSEI")

    def test_a_symbol_is_not_an_index(self):
        self.assertRaises(SymbolError, IndexSymbol, "SBIN.NS")

    def test_an_unknown_index_still_parses(self):
        self.assertEqual(IndexSymbol("^CNXMETAL").yahoo, "^CNXMETAL")
        self.assertIsNone(IndexSymbol("^CNXMETAL").name)


class Dispatcher(unittest.TestCase):
    def test_parse_picks_the_right_type(self):
        self.assertIsInstance(parse("SBIN.NS"), Symbol)
        self.assertIsInstance(parse("^NSEI"), IndexSymbol)

    def test_parse_keeps_the_exchange(self):
        self.assertIs(parse("INFY.BO").exchange, BSE)


if __name__ == "__main__":
    unittest.main()
