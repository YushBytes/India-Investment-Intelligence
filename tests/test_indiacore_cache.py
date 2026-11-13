import json
import os
import shutil
import tempfile
import unittest

import _india  # noqa: F401
from indiacore.marketdata import cache as cache_mod


class Clock(object):
    """A clock that only moves when a test moves it."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


class CacheCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="indiacore-cache-test-")
        self.clock = Clock()
        self.cache = cache_mod.Cache(directory=self.dir, clock=self.clock)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def put(self, payload=None, kind="quote", provider="yahoo",
            exchange="NSE", key="k1", **kw):
        return self.cache.put(key, payload or {"last": "1080.00"}, kind,
                              provider, exchange, **kw)

    def get(self, kind="quote", provider="yahoo", exchange="NSE", key="k1",
            **kw):
        return self.cache.get(key, kind, provider, exchange, **kw)


class KeysAreDeterministicAndExchangeAware(CacheCase):
    def test_the_same_request_makes_the_same_key(self):
        a = cache_mod.make_key("yahoo", "quote", "NSE", "SBIN")
        b = cache_mod.make_key("yahoo", "quote", "NSE", "SBIN")
        self.assertEqual(a, b)

    def test_nse_and_bse_never_share_a_key(self):
        """SBIN closed at 1086.50 on NSE and 1085.00 on BSE the same day."""
        nse_key = cache_mod.make_key("yahoo", "quote", "NSE", "SBIN")
        bse_key = cache_mod.make_key("yahoo", "quote", "BSE", "SBIN")
        self.assertNotEqual(nse_key, bse_key)

    def test_every_part_of_the_request_changes_the_key(self):
        base = cache_mod.make_key("yahoo", "bars", "NSE", "SBIN", "1d",
                                  "2026-11-01", "2026-11-12")
        for changed in (
                cache_mod.make_key("nse_bhavcopy", "bars", "NSE", "SBIN",
                                   "1d", "2026-11-01", "2026-11-12"),
                cache_mod.make_key("yahoo", "quote", "NSE", "SBIN", "1d",
                                   "2026-11-01", "2026-11-12"),
                cache_mod.make_key("yahoo", "bars", "NSE", "RELIANCE", "1d",
                                   "2026-11-01", "2026-11-12"),
                cache_mod.make_key("yahoo", "bars", "NSE", "SBIN", "5m",
                                   "2026-11-01", "2026-11-12"),
                cache_mod.make_key("yahoo", "bars", "NSE", "SBIN", "1d",
                                   "2026-11-02", "2026-11-12")):
            self.assertNotEqual(base, changed)

    def test_the_key_is_filename_safe_even_for_an_ampersand_ticker(self):
        key = cache_mod.make_key("yahoo", "quote", "NSE", "M&M")
        self.assertNotIn("&", key)
        self.cache.put(key, {"x": 1}, "quote", "yahoo", "NSE")
        self.assertIsNotNone(self.cache.get(key, "quote", "yahoo", "NSE"))

    def test_an_exchange_gets_its_own_directory(self):
        self.put(exchange="NSE")
        self.put(exchange="BSE", payload={"last": "1085.00"})
        trees = []
        for base, dirs, files in os.walk(self.dir):
            for f in files:
                trees.append(os.path.join(base, f))
        self.assertTrue(any(os.sep + "NSE" + os.sep in p for p in trees))
        self.assertTrue(any(os.sep + "BSE" + os.sep in p for p in trees))

    def test_quotes_and_bars_live_in_separate_subtrees(self):
        self.put(kind="quote")
        self.put(kind="bars_daily", key="k2")
        found = []
        for base, dirs, files in os.walk(self.dir):
            found.extend(os.path.join(base, f) for f in files)
        self.assertTrue(any("quotes" in p for p in found))
        self.assertTrue(any("bars" in p for p in found))


class HitsCarryTheirAge(CacheCase):
    def test_a_fresh_entry_comes_back_with_its_age(self):
        self.put()
        self.clock.tick(5)
        entry = self.get()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.age_seconds, 5)
        self.assertEqual(entry.payload["last"], "1080.00")

    def test_a_miss_is_none_not_an_exception(self):
        self.assertIsNone(self.get(key="never-written"))

    def test_an_expired_quote_is_a_miss(self):
        self.put()
        self.clock.tick(cache_mod.DEFAULT_TTL["quote"] + 1)
        self.assertIsNone(self.get())

    def test_an_expired_entry_can_be_asked_for_on_purpose(self):
        self.put()
        self.clock.tick(5000)
        entry = self.get(allow_expired=True)
        self.assertIsNotNone(entry)
        self.assertTrue(entry.expired)
        self.assertEqual(entry.age_seconds, 5000)

    def test_a_daily_bar_outlives_a_quote(self):
        self.assertGreater(cache_mod.DEFAULT_TTL["bars_daily"],
                           cache_mod.DEFAULT_TTL["quote"])

    def test_the_ttl_is_configurable(self):
        cache = cache_mod.Cache(directory=self.dir, clock=self.clock,
                                ttl={"quote": 5})
        cache.put("k", {"a": 1}, "quote", "yahoo", "NSE")
        self.clock.tick(6)
        self.assertIsNone(cache.get("k", "quote", "yahoo", "NSE"))


class FailuresAreNeverStored(CacheCase):
    def test_a_null_payload_is_refused(self):
        self.assertRaises(cache_mod.CacheError, self.cache.put,
                          "k", None, "quote", "yahoo", "NSE")

    def test_nothing_is_written_when_the_payload_is_refused(self):
        try:
            self.cache.put("k", None, "quote", "yahoo", "NSE")
        except cache_mod.CacheError:
            pass
        self.assertIsNone(self.get(key="k"))


class WritesAreAtomic(CacheCase):
    def test_no_temporary_file_survives_a_write(self):
        self.put()
        leftovers = []
        for base, dirs, files in os.walk(self.dir):
            leftovers.extend(f for f in files if f.startswith(".tmp-"))
        self.assertEqual(leftovers, [])

    def test_a_rewrite_replaces_rather_than_appends(self):
        self.put(payload={"last": "1080.00"})
        self.put(payload={"last": "1085.00"})
        self.assertEqual(self.get().payload["last"], "1085.00")

    def test_one_file_per_key(self):
        self.put()
        self.put()
        count = sum(len(files) for _b, _d, files in os.walk(self.dir))
        self.assertEqual(count, 1)


class CorruptionIsDetectedNotInherited(CacheCase):
    def path_of(self, key="k1", kind="quote", provider="yahoo",
                exchange="NSE"):
        return self.cache._path(key, kind, provider, exchange)

    def test_unparseable_json_is_a_miss_with_a_reason(self):
        self.put()
        with open(self.path_of(), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertIsNone(self.get())
        self.assertEqual(len(self.cache.corruptions), 1)
        self.assertIn("unparseable", self.cache.corruptions[0]["reason"])

    def test_a_tampered_payload_fails_its_checksum(self):
        self.put()
        path = self.path_of()
        with open(path, encoding="utf-8") as fh:
            envelope = json.load(fh)
        envelope["payload"]["last"] = "9999.00"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh)
        self.assertIsNone(self.get())
        self.assertIn("checksum", self.cache.corruptions[0]["reason"])

    def test_an_envelope_from_another_exchange_is_refused(self):
        self.put(exchange="NSE")
        path = self.path_of()
        with open(path, encoding="utf-8") as fh:
            envelope = json.load(fh)
        envelope["exchange"] = "BSE"
        envelope["checksum"] = cache_mod._checksum(envelope["payload"])
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh)
        self.assertIsNone(self.get(exchange="NSE"))
        self.assertIn("not what was asked for",
                      self.cache.corruptions[0]["reason"])

    def test_an_old_envelope_version_is_refused(self):
        self.put()
        path = self.path_of()
        with open(path, encoding="utf-8") as fh:
            envelope = json.load(fh)
        envelope["v"] = 0
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh)
        self.assertIsNone(self.get())

    def test_a_corrupt_file_is_quarantined_rather_than_silently_deleted(self):
        self.put()
        with open(self.path_of(), "w", encoding="utf-8") as fh:
            fh.write("rubbish")
        self.get()
        self.assertTrue(os.path.exists(self.path_of() + ".corrupt"))

    def test_corruption_never_raises_into_the_data_path(self):
        self.put()
        with open(self.path_of(), "w", encoding="utf-8") as fh:
            fh.write("rubbish")
        self.assertIsNone(self.get())  # no exception


class Maintenance(CacheCase):
    def test_an_entry_can_be_invalidated(self):
        self.put()
        self.assertTrue(self.cache.invalidate("k1", "quote", "yahoo", "NSE"))
        self.assertIsNone(self.get())

    def test_invalidating_what_is_not_there_says_so(self):
        self.assertFalse(self.cache.invalidate("nope", "quote", "yahoo", "NSE"))

    def test_clearing_one_provider_leaves_the_others(self):
        self.put(provider="yahoo")
        self.put(provider="nse_bhavcopy", kind="bars_daily", key="k2")
        self.assertEqual(self.cache.clear("yahoo"), 1)
        self.assertIsNone(self.get(provider="yahoo"))
        self.assertIsNotNone(self.get(provider="nse_bhavcopy",
                                      kind="bars_daily", key="k2"))

    def test_stats_report_the_traffic(self):
        self.put()
        self.get()
        self.get(key="absent")
        stats = self.cache.stats()
        self.assertEqual(stats["writes"], 1)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)


class DisabledIsAWorkingNoOp(CacheCase):
    def test_nothing_is_written_or_returned(self):
        cache = cache_mod.Cache(directory=self.dir, enabled=False)
        self.assertEqual(cache.put("k", {"a": 1}, "quote", "yahoo", "NSE"), "")
        self.assertIsNone(cache.get("k", "quote", "yahoo", "NSE"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "yahoo")))


class TheDefaultDirectoryIsOutsideTheRepo(unittest.TestCase):
    def test_it_is_under_the_system_temporary_directory_by_default(self):
        saved = os.environ.pop("INDIACORE_CACHE_DIR", None)
        try:
            self.assertTrue(cache_mod.default_dir().startswith(
                tempfile.gettempdir()))
        finally:
            if saved is not None:
                os.environ["INDIACORE_CACHE_DIR"] = saved

    def test_an_environment_variable_overrides_it(self):
        saved = os.environ.get("INDIACORE_CACHE_DIR")
        os.environ["INDIACORE_CACHE_DIR"] = os.path.join("some", "where")
        try:
            self.assertEqual(cache_mod.default_dir(),
                             os.path.join("some", "where"))
        finally:
            if saved is None:
                del os.environ["INDIACORE_CACHE_DIR"]
            else:
                os.environ["INDIACORE_CACHE_DIR"] = saved

    def test_building_a_cache_creates_nothing_until_a_write(self):
        folder = os.path.join(tempfile.gettempdir(), "indiacore-not-created")
        shutil.rmtree(folder, ignore_errors=True)
        cache_mod.Cache(directory=folder)
        self.assertFalse(os.path.exists(folder))

    def test_no_repository_path_is_ever_the_default(self):
        self.assertNotIn("India-Investment-Intelligence",
                         cache_mod.default_dir())


if __name__ == "__main__":
    unittest.main()
