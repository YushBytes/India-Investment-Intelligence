import unittest
from decimal import Decimal

import _load  # noqa: F401  (puts ROOT on sys.path)
from indiacore.money import (INR, USD, CurrencyError, Money, MoneyError,
                             _group_indian)


class Building(unittest.TestCase):
    def test_rupees_from_every_sane_input(self):
        for value in ("10000.00", 10000.00, Decimal("10000.00")):
            self.assertEqual(Money.rupees(value).as_paise, 1000000, repr(value))

    def test_whole_rupees_and_paise(self):
        self.assertEqual(Money.rupees(100).as_paise, 10000)
        self.assertEqual(Money.paise(1000000).as_rupees, Decimal("10000.00"))

    def test_strips_indian_currency_decoration(self):
        for text in ("Rs 10,000.00", "Rs.10000.00", "INR 10000.00",
                     "10,000.00"):
            self.assertEqual(Money.rupees(text).as_paise, 1000000, text)

    def test_rejects_nonsense(self):
        for bad in ("", "abc", None, True, [], {}):
            self.assertRaises(MoneyError, Money.rupees, bad)

    def test_float_noise_does_not_leak(self):
        total = Money.rupees(0.1) + Money.rupees(0.2)
        self.assertEqual(total, Money.rupees("0.30"))
        self.assertEqual(total.as_paise, 30)

    def test_paise_must_be_whole(self):
        self.assertRaises(MoneyError, Money, 1.5)


class Arithmetic(unittest.TestCase):
    def test_add_subtract(self):
        self.assertEqual(Money.rupees(10) + Money.rupees(5), Money.rupees(15))
        self.assertEqual(Money.rupees(10) - Money.rupees(5), Money.rupees(5))

    def test_multiply_by_quantity(self):
        self.assertEqual(Money.rupees("35.76") * 147, Money.rupees("5256.72"))
        self.assertEqual(147 * Money.rupees("35.76"), Money.rupees("5256.72"))

    def test_money_times_money_is_refused(self):
        self.assertRaises(MoneyError, lambda: Money.rupees(2) * Money.rupees(3))

    def test_how_many_whole_shares_fit(self):
        cash = Money.rupees("10000.00")
        # Per share prices are the real 12 November 2026 closes. The
        # budget is a placeholder, so the counts are computed from it.
        self.assertEqual(cash.how_many_fit(Money.rupees("35.76")), 279)
        self.assertEqual(cash.how_many_fit(Money.rupees("1151.20")), 8)
        self.assertEqual(cash.how_many_fit(Money.rupees("2442.40")), 4)
        self.assertEqual(cash.how_many_fit(Money.rupees("1086.50")), 9)

    def test_how_many_fit_floors_and_never_rounds_up(self):
        # 2 x 5000.01 is 10000.02, over a 10000.00 budget, so only 1 fits
        self.assertEqual(Money.rupees("10000.00").how_many_fit(
            Money.rupees("5000.01")), 1)

    def test_divide_and_negate(self):
        self.assertEqual(Money.rupees(100).divide_by(4), Money.rupees(25))
        self.assertRaises(MoneyError, Money.rupees(100).divide_by, 0)
        self.assertEqual(-Money.rupees(5), Money.rupees(-5))
        self.assertEqual(abs(Money.rupees(-5)), Money.rupees(5))


class CurrencySafety(unittest.TestCase):
    """The defect this type exists to prevent."""

    def test_rupees_and_dollars_never_mix(self):
        rupees = Money.rupees(100, INR)
        dollars = Money.rupees(100, USD)
        self.assertRaises(CurrencyError, lambda: rupees + dollars)
        self.assertRaises(CurrencyError, lambda: rupees - dollars)
        self.assertRaises(CurrencyError, lambda: rupees < dollars)

    def test_same_number_different_currency_is_not_equal(self):
        self.assertNotEqual(Money.rupees(100, INR), Money.rupees(100, USD))

    def test_conversion_is_explicit_and_needs_a_rate(self):
        dollars = Money.rupees(100, USD)
        self.assertRaises(TypeError, dollars.convert, INR)
        converted = dollars.convert(INR, 94.04)
        self.assertEqual(converted.currency, INR)
        self.assertEqual(converted.as_rupees, Decimal("9404.00"))

    def test_conversion_rate_must_be_positive(self):
        self.assertRaises(MoneyError, Money.rupees(1, USD).convert, INR, 0)

    def test_no_hidden_rate_table_exists(self):
        self.assertFalse(hasattr(Money, "RATES"))
        self.assertFalse(hasattr(Money, "fx"))

    def test_comparing_to_a_bare_number_is_refused(self):
        self.assertRaises(CurrencyError, lambda: Money.rupees(5) > 3)


class Formatting(unittest.TestCase):
    def test_indian_digit_grouping(self):
        self.assertEqual(_group_indian("123"), "123")
        self.assertEqual(_group_indian("1234"), "1,234")
        self.assertEqual(_group_indian("123456"), "1,23,456")
        self.assertEqual(_group_indian("1234567"), "12,34,567")
        self.assertEqual(_group_indian("12345678"), "1,23,45,678")

    def test_format_uses_lakh_and_crore_grouping(self):
        self.assertEqual(Money.rupees("10000.00").format(), "Rs 10,000.00")
        self.assertEqual(Money.rupees("1234567.89").format(), "Rs 12,34,567.89")
        self.assertEqual(Money.rupees("-10000.00").format(), "Rs -10,000.00")
        self.assertEqual(Money.rupees("10000.00").format(symbol=False),
                         "10,000.00")

    def test_dollars_render_with_a_dollar_sign(self):
        self.assertEqual(Money.rupees(100, USD).format(), "$100.00")


if __name__ == "__main__":
    unittest.main()
