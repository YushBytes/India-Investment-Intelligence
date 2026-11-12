import json
import os
import shutil
import tempfile
import unittest

from _load import ROOT
from indiacore import config_in as cfgmod

LEGACY = {
    "budget": 10000,
    "cash_buffer_pct": 10,
    "max_position_pct": 60,
    "max_order_usd": 6000,
    "min_order_usd": 5,
    "max_loss_per_trade_usd": 600,
    "max_daily_loss_usd": 600,
    "max_trades_per_day": 2,
    "min_reward_risk": 1.0,
    "require_stop": True,
    "no_entries_after": "15:30",
    "flat_by": "15:55",
    "fractional": False,
    "pdt_guard": False,
    "paper": True,
    "allow_margin": False,
}


class RefusesToGuess(unittest.TestCase):
    """The whole point: 50 dollars and 50 rupees are not the same limit."""

    def test_a_dollar_config_without_a_policy_raises(self):
        self.assertRaises(cfgmod.MigrationPolicyRequired, cfgmod.migrate, LEGACY)

    def test_the_error_lists_the_offending_keys_and_the_choices(self):
        try:
            cfgmod.migrate(LEGACY)
        except cfgmod.MigrationPolicyRequired as e:
            text = str(e)
            self.assertIn("max_loss_per_trade_usd", text)
            for word in ("RESET", "CONVERT", "RELABEL"):
                self.assertIn(word, text)
        else:
            self.fail("expected MigrationPolicyRequired")

    def test_an_unknown_policy_is_refused(self):
        self.assertRaises(cfgmod.ConfigError, cfgmod.migrate, LEGACY, "whatever")

    def test_convert_without_a_rate_is_refused(self):
        self.assertRaises(cfgmod.ConfigError, cfgmod.migrate, LEGACY, cfgmod.CONVERT)

    def test_relabel_must_be_acknowledged_in_so_many_words(self):
        self.assertRaises(cfgmod.ConfigError, cfgmod.migrate, LEGACY, cfgmod.RELABEL)
        new, _ = cfgmod.migrate(LEGACY, cfgmod.RELABEL, acknowledge_relabel=True)
        self.assertEqual(new["max_loss_per_trade_inr"], 600)

    def test_a_fresh_config_needs_no_policy(self):
        new, record = cfgmod.migrate({})
        self.assertEqual(new["currency"], "INR")
        self.assertEqual(record["policy"], "fresh")


class Policies(unittest.TestCase):
    def test_reset_discards_the_old_numbers(self):
        new, record = cfgmod.migrate(LEGACY, cfgmod.RESET)
        self.assertEqual(new["max_loss_per_trade_inr"], 0)
        self.assertEqual(new["budget_inr"], 0)
        self.assertEqual(record["changes"]["max_loss_per_trade_usd"]["action"], "reset")

    def test_convert_applies_the_rate_you_supply(self):
        new, record = cfgmod.migrate(LEGACY, cfgmod.CONVERT, fx_rate=94.04)
        self.assertAlmostEqual(new["max_loss_per_trade_inr"], 600 * 94.04, places=2)
        self.assertAlmostEqual(new["budget_inr"], 10000 * 94.04, places=2)
        self.assertEqual(record["fx_rate_usd_inr"], 94.04)

    def test_relabel_keeps_the_number_exactly(self):
        new, _ = cfgmod.migrate(LEGACY, cfgmod.RELABEL, acknowledge_relabel=True)
        self.assertEqual(new["budget_inr"], 10000)
        self.assertEqual(new["max_order_inr"], 6000)

    def test_the_three_policies_disagree_which_is_the_point(self):
        reset, _ = cfgmod.migrate(LEGACY, cfgmod.RESET)
        conv, _ = cfgmod.migrate(LEGACY, cfgmod.CONVERT, fx_rate=94.04)
        rel, _ = cfgmod.migrate(LEGACY, cfgmod.RELABEL, acknowledge_relabel=True)
        values = {reset["budget_inr"], conv["budget_inr"], rel["budget_inr"]}
        self.assertEqual(len(values), 3)

    def test_no_usd_key_survives(self):
        new, _ = cfgmod.migrate(LEGACY, cfgmod.RESET)
        for key in new:
            self.assertFalse(key.endswith("_usd"), key)


class DroppedAndRecorded(unittest.TestCase):
    def test_us_only_settings_are_dropped_with_a_reason(self):
        new, record = cfgmod.migrate(LEGACY, cfgmod.RESET)
        for key in ("pdt_guard", "fractional"):
            self.assertNotIn(key, new)
            self.assertIn(key, record["changes"])
            self.assertIn("dropped", record["changes"][key]["action"])

    def test_intraday_settings_are_recorded_not_carried(self):
        new, record = cfgmod.migrate(LEGACY, cfgmod.RESET)
        for key in ("flat_by", "no_entries_after", "require_stop"):
            self.assertNotIn(key, new)
            self.assertIn("delivery only", record["changes"][key]["action"])

    def test_the_india_profile_bans_what_the_mandate_bans(self):
        new, _ = cfgmod.migrate(LEGACY, cfgmod.RESET)
        for key in ("allow_intraday", "allow_options", "allow_futures",
                    "allow_margin", "allow_mtf", "allow_short",
                    "allow_penny_stocks", "auto_execute"):
            self.assertIs(new[key], False, key)
        self.assertIs(new["delivery_only"], True)
        self.assertIs(new["whole_shares_only"], True)

    def test_the_original_is_kept_inside_the_record(self):
        new, _ = cfgmod.migrate(LEGACY, cfgmod.RESET)
        self.assertEqual(new["migrated_from"]["max_loss_per_trade_usd"], 600)


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "config.json")
        with open(self.path, "w") as fh:
            json.dump(LEGACY, fh, indent=2)
        with open(self.path) as fh:
            self.original_text = fh.read()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_migration_writes_config_backup_and_record(self):
        record = cfgmod.migrate_file(self.path, cfgmod.RESET)
        self.assertTrue(os.path.exists(cfgmod.backup_path(self.path)))
        self.assertTrue(os.path.exists(cfgmod.record_path(self.path)))
        self.assertTrue(record["reversible"])

    def test_the_backup_is_byte_for_byte(self):
        cfgmod.migrate_file(self.path, cfgmod.RESET)
        with open(cfgmod.backup_path(self.path)) as fh:
            self.assertEqual(fh.read(), self.original_text)

    def test_revert_restores_the_original_exactly(self):
        cfgmod.migrate_file(self.path, cfgmod.CONVERT, fx_rate=94.04)
        with open(self.path) as fh:
            self.assertIn("budget_inr", fh.read())
        cfgmod.revert_file(self.path)
        with open(self.path) as fh:
            self.assertEqual(fh.read(), self.original_text)

    def test_revert_cleans_up_after_itself(self):
        cfgmod.migrate_file(self.path, cfgmod.RESET)
        cfgmod.revert_file(self.path)
        self.assertFalse(os.path.exists(cfgmod.backup_path(self.path)))
        self.assertFalse(os.path.exists(cfgmod.record_path(self.path)))

    def test_revert_without_a_backup_is_refused(self):
        self.assertRaises(cfgmod.ConfigError, cfgmod.revert_file, self.path)

    def test_a_second_migration_does_not_clobber_the_first_original(self):
        cfgmod.migrate_file(self.path, cfgmod.RESET)
        cfgmod.migrate_file(self.path, cfgmod.RESET)
        with open(cfgmod.backup_path(self.path)) as fh:
            self.assertEqual(fh.read(), self.original_text)

    def test_load_refuses_a_dollar_config(self):
        self.assertRaises(cfgmod.MigrationPolicyRequired, cfgmod.load, self.path)

    def test_load_accepts_a_migrated_config(self):
        cfgmod.migrate_file(self.path, cfgmod.RESET)
        self.assertEqual(cfgmod.load(self.path)["currency"], "INR")


class ShippedExample(unittest.TestCase):
    def test_the_india_example_is_valid_and_inr(self):
        path = os.path.join(ROOT, "indiacore", "config.india.example.json")
        cfg = cfgmod.load(path)
        self.assertEqual(cfg["currency"], "INR")
        self.assertEqual(cfg["primary_exchange"], "NSE")
        self.assertIs(cfg["delivery_only"], True)
        self.assertIs(cfg["auto_execute"], False)

    def test_the_original_usd_example_is_untouched(self):
        """Phase 2 does not re-point the skills, so their config still stands."""
        path = os.path.join(ROOT, "trader-budget", "config.example.json")
        with open(path) as fh:
            legacy = json.load(fh)
        self.assertIn("max_loss_per_trade_usd", legacy)
        self.assertIs(legacy["paper"], True)


if __name__ == "__main__":
    unittest.main()
