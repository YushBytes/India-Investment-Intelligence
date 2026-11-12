"""INR money, stored in paise, that refuses to be confused with dollars.

The day-trading pack kept amounts as bare floats and named its config keys
max_order_usd, max_loss_per_trade_usd and so on. Feeding rupee numbers into
those keys produced output like "max loss $325" for what was really 325
rupees: the arithmetic was right and the unit was a lie.

Money fixes that by carrying the currency with the number. Every amount is
an integer count of paise, so 0.1 + 0.2 behaves. Mixing currencies raises
instead of guessing, and there is no implicit conversion anywhere: a rate
has to be passed in by hand.

    >>> Money.rupees("10000.00")
    Money.rupees('10000.00')
    >>> Money.rupees("10000.00").format()
    'Rs 10,000.00'
    >>> Money.rupees(1234567.89).format()
    'Rs 12,34,567.89'
"""

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Union

INR = "INR"
USD = "USD"

Number = Union[int, float, str, Decimal]


class CurrencyError(ValueError):
    """Two amounts in different currencies were combined."""


class MoneyError(ValueError):
    """An amount could not be built."""


def _to_decimal(value):
    # type: (Number) -> Decimal
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int subclass, and never an amount
        raise MoneyError("a boolean is not an amount")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # repr keeps the shortest round-trip form, so 0.1 stays 0.1
        return Decimal(repr(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        for junk in ("Rs.", "Rs", "INR", "₹"):
            if text.startswith(junk):
                text = text[len(junk):].strip()
        if not text:
            raise MoneyError("empty string is not an amount")
        try:
            return Decimal(text)
        except Exception:
            raise MoneyError("%r is not an amount" % (value,))
    raise MoneyError("%r is not an amount" % (value,))


def _group_indian(digits):
    # type: (str) -> str
    """1234567 -> 12,34,567. Last three, then pairs (lakh, crore)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


class Money(object):
    """An exact amount of money. Immutable. Paise under the hood."""

    __slots__ = ("_paise", "_currency")

    def __init__(self, paise, currency=INR):
        # type: (int, str) -> None
        if not isinstance(paise, int) or isinstance(paise, bool):
            raise MoneyError("paise must be a whole number, got %r" % (paise,))
        if not currency:
            raise CurrencyError("an amount needs a currency")
        object.__setattr__(self, "_paise", paise)
        object.__setattr__(self, "_currency", currency)

    # ---------------------------------------------------------------- build

    @classmethod
    def rupees(cls, value, currency=INR):
        # type: (Number, str) -> Money
        """Build from rupees. Fractions of a paise round half up."""
        amount = _to_decimal(value) * 100
        return cls(int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), currency)

    @classmethod
    def paise(cls, value, currency=INR):
        # type: (int, str) -> Money
        return cls(int(value), currency)

    @classmethod
    def zero(cls, currency=INR):
        # type: (str) -> Money
        return cls(0, currency)

    # ------------------------------------------------------------- inspect

    @property
    def currency(self):
        # type: () -> str
        return self._currency

    @property
    def as_paise(self):
        # type: () -> int
        return self._paise

    @property
    def as_rupees(self):
        # type: () -> Decimal
        return (Decimal(self._paise) / 100).quantize(Decimal("0.01"))

    def is_inr(self):
        # type: () -> bool
        return self._currency == INR

    # ------------------------------------------------------------ currency

    def _same(self, other):
        # type: (Any) -> Money
        if not isinstance(other, Money):
            raise CurrencyError("%r is not an amount of money" % (other,))
        if other._currency != self._currency:
            raise CurrencyError(
                "refusing to mix %s with %s. Convert on purpose with "
                "Money.convert(rate=...), never by accident"
                % (self._currency, other._currency))
        return other

    def convert(self, to, rate):
        # type: (str, Number) -> Money
        """Convert at a rate you supply. There is no built in rate table."""
        factor = _to_decimal(rate)
        if factor <= 0:
            raise MoneyError("a conversion rate must be positive")
        if to == self._currency:
            raise CurrencyError("already in %s" % to)
        converted = (Decimal(self._paise) * factor).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP)
        return Money(int(converted), to)

    # ------------------------------------------------------------ maths

    def __add__(self, other):
        return Money(self._paise + self._same(other)._paise, self._currency)

    def __sub__(self, other):
        return Money(self._paise - self._same(other)._paise, self._currency)

    def __mul__(self, factor):
        if isinstance(factor, Money):
            raise MoneyError("money times money is not money")
        scaled = (Decimal(self._paise) * _to_decimal(factor)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP)
        return Money(int(scaled), self._currency)

    __rmul__ = __mul__

    def __neg__(self):
        return Money(-self._paise, self._currency)

    def __abs__(self):
        return Money(abs(self._paise), self._currency)

    def divide_by(self, divisor):
        # type: (Number) -> Money
        d = _to_decimal(divisor)
        if d == 0:
            raise MoneyError("cannot divide an amount by zero")
        return Money(int((Decimal(self._paise) / d).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP)), self._currency)

    def how_many_fit(self, unit):
        # type: (Money) -> int
        """Whole units of `unit` that fit inside this amount.

        Indian delivery equity trades in whole shares, so this floors.
        """
        u = self._same(unit)
        if u._paise <= 0:
            raise MoneyError("a unit price must be positive")
        return int((Decimal(self._paise) / Decimal(u._paise)).to_integral_value(
            rounding=ROUND_DOWN))

    # ------------------------------------------------------------ compare

    def __eq__(self, other):
        if not isinstance(other, Money):
            return NotImplemented
        return (self._paise, self._currency) == (other._paise, other._currency)

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __lt__(self, other):
        return self._paise < self._same(other)._paise

    def __le__(self, other):
        return self._paise <= self._same(other)._paise

    def __gt__(self, other):
        return self._paise > self._same(other)._paise

    def __ge__(self, other):
        return self._paise >= self._same(other)._paise

    def __hash__(self):
        return hash((self._paise, self._currency))

    def __bool__(self):
        return self._paise != 0

    # ------------------------------------------------------------- render

    def format(self, symbol=True):
        # type: (bool) -> str
        """Indian digit grouping: 12,34,567.89."""
        sign = "-" if self._paise < 0 else ""
        whole, frac = divmod(abs(self._paise), 100)
        body = "%s%s.%02d" % (sign, _group_indian(str(whole)), frac)
        if not symbol:
            return body
        prefix = {INR: "Rs ", USD: "$"}.get(self._currency, self._currency + " ")
        return prefix + body

    def __str__(self):
        return self.format()

    def __repr__(self):
        return "Money.rupees('%s')" % self.as_rupees if self._currency == INR \
            else "Money(%d, %r)" % (self._paise, self._currency)
