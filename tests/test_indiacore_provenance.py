import datetime
import unittest

import _load  # noqa: F401
from indiacore import provenance as prov

DAY = datetime.date(2026, 11, 12)


def good(rows=None, **kw):
    base = dict(source="yahoo", as_of=DAY, freshness=prov.END_OF_DAY,
                requested=50, fetched=50, failed=0)
    base.update(kw)
    return prov.DataResult(rows or [], **base)


class EmptyVersusBroken(unittest.TestCase):
    """The distinction the shipped scanner loses."""

    def test_a_real_empty_scan_is_valid(self):
        result = good([])
        self.assertTrue(result.is_usable)
        self.assertTrue(result.is_empty_and_valid)
        self.assertEqual(result.require_usable(), result)

    def test_a_total_fetch_failure_is_not_an_empty_scan(self):
        # exactly what happened: 3 requested, 0 fetched, 3 failed
        broken = prov.DataResult([], source="yahoo", as_of=None,
                                 freshness=prov.UNAVAILABLE,
                                 requested=3, fetched=0, failed=3,
                                 errors=["SBIN.NS 404", "INFY.NS 404"])
        self.assertFalse(broken.is_usable)
        self.assertFalse(broken.is_empty_and_valid)
        self.assertRaises(prov.DataError, broken.require_usable)

    def test_both_have_zero_rows_yet_report_differently(self):
        self.assertEqual(len(good([]).rows), 0)
        self.assertEqual(len(prov.unavailable("yahoo", 3).rows), 0)
        self.assertNotEqual(good([]).is_usable,
                            prov.unavailable("yahoo", 3).is_usable)

    def test_the_failure_message_names_the_counts(self):
        try:
            prov.unavailable("yahoo", 3, ["SBIN.NS 404"]).require_usable()
        except prov.DataError as e:
            self.assertIn("yahoo", str(e))
        else:
            self.fail("expected a DataError")


class Usability(unittest.TestCase):
    def test_missing_as_of_is_unusable(self):
        self.assertFalse(good([1], as_of=None).is_usable)
        self.assertRaises(prov.StaleDataError, good([1], as_of=None).require_usable)

    def test_stale_is_unusable(self):
        self.assertFalse(good([1], freshness=prov.STALE).is_usable)
        self.assertRaises(prov.StaleDataError,
                          good([1], freshness=prov.STALE).require_usable)

    def test_a_partial_fetch_is_unusable(self):
        partial = good([1], requested=100, fetched=3, failed=97)
        self.assertFalse(partial.is_usable)
        self.assertRaises(prov.EmptyResultError, partial.require_usable)

    def test_every_live_freshness_is_accepted(self):
        for f in (prov.LIVE, prov.DELAYED, prov.END_OF_DAY, prov.HISTORICAL):
            self.assertTrue(good([1], freshness=f).is_usable, f)

    def test_complete_requires_everything_back(self):
        self.assertTrue(good([1]).complete)
        self.assertFalse(good([1], fetched=49, failed=1).complete)

    def test_a_source_is_mandatory(self):
        self.assertRaises(prov.DataError, prov.DataResult, [], "")


class Staleness(unittest.TestCase):
    def test_age_in_days(self):
        self.assertEqual(good([1]).staleness_days(DAY), 0)
        self.assertEqual(good([1]).staleness_days(datetime.date(2026, 11, 20)), 8)

    def test_unknown_age_when_as_of_is_missing(self):
        self.assertIsNone(good([1], as_of=None).staleness_days(DAY))


class Reporting(unittest.TestCase):
    def test_summary_mentions_the_counts_and_the_date(self):
        text = good([1, 2]).summary()
        self.assertIn("2026-11-12", text)
        self.assertIn("50/50", text)

    def test_summary_says_unknown_when_there_is_no_date(self):
        self.assertIn("unknown", good([1], as_of=None).summary())

    def test_to_dict_carries_the_verdict(self):
        d = good([]).to_dict()
        self.assertTrue(d["usable"])
        self.assertTrue(d["empty_but_valid"])
        self.assertEqual(d["as_of"], "2026-11-12")
        self.assertEqual(d["rows"], 0)

    def test_to_dict_of_a_failure_is_unmistakable(self):
        d = prov.unavailable("yahoo", 3).to_dict()
        self.assertFalse(d["usable"])
        self.assertFalse(d["empty_but_valid"])
        self.assertIsNone(d["as_of"])
        self.assertEqual(d["failed"], 3)


if __name__ == "__main__":
    unittest.main()
