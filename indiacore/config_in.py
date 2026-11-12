"""The INR configuration schema, and a migration that refuses to guess.

The shipped config names its limits in dollars: max_order_usd,
max_loss_per_trade_usd, max_daily_loss_usd, min_order_usd. A user who types
rupee amounts into those keys gets arithmetic that is right and a unit that
is wrong, which is exactly what happened in the preserved state file, where a
5,225 rupee order was recorded as "value_usd": 5225.0.

Renaming the key is the one thing this module will not do on its own. The
number 50 means something very different as fifty dollars and as fifty
rupees, and only the person who typed it knows which was meant. So migration
takes an explicit policy:

    RESET     throw the old numbers away and start from INR defaults
    CONVERT   treat them as dollars and convert at a rate you supply
    RELABEL   keep the numbers as they stand and call them rupees,
              which has to be acknowledged in so many words

Every migration writes the original alongside the new file and a record of
what was decided, so `revert` puts it back exactly.
"""

import copy
import datetime
import json
import os
from typing import Dict, Optional, Tuple

from .money import Money

SCHEMA_VERSION = 1

# Old dollar key -> new rupee key.
USD_TO_INR_KEYS = {
    "max_order_usd": "max_order_inr",
    "min_order_usd": "min_order_inr",
    "max_loss_per_trade_usd": "max_loss_per_trade_inr",
    "max_daily_loss_usd": "max_daily_loss_inr",
}

# US only settings with no Indian meaning.
DROPPED_KEYS = {
    "pdt_guard": ("FINRA's pattern day trader rule is US only and has no "
                  "Indian equivalent"),
    "fractional": ("Indian delivery equity trades in whole shares, so "
                   "fractional sizing cannot apply"),
}

# Migration policies.
RESET = "reset"
CONVERT = "convert"
RELABEL = "relabel"
POLICIES = (RESET, CONVERT, RELABEL)


class ConfigError(Exception):
    """The configuration could not be read or built."""


class MigrationPolicyRequired(ConfigError):
    """A dollar denominated config was found and no policy was given."""


def india_defaults():
    # type: () -> dict
    """A delivery first INR profile. Amounts are rupees."""
    return {
        "schema": "indiacore/config",
        "schema_version": SCHEMA_VERSION,
        "currency": "INR",
        "country": "IN",
        "primary_exchange": "NSE",
        "secondary_exchange": "BSE",
        "segment": "equity_delivery",

        "budget_inr": 0,
        "cash_buffer_pct": 10,
        "max_position_pct": 40,
        "max_order_inr": 0,
        "min_order_inr": 100,
        "max_loss_per_trade_inr": 0,
        "max_daily_loss_inr": 0,
        "max_sector_pct": 35,
        "min_reward_risk": 1.5,

        "horizon": "1_to_3_years",
        "delivery_only": True,
        "whole_shares_only": True,
        "staged_entry": True,
        "max_tranches": 3,

        "allow_intraday": False,
        "allow_options": False,
        "allow_futures": False,
        "allow_margin": False,
        "allow_mtf": False,
        "allow_short": False,
        "allow_penny_stocks": False,
        "min_price_inr": 50,

        "require_thesis": True,
        "require_invalidation": True,
        "auto_execute": False,
        "paper": True,
        "broker": "none-research-only",
        "blocked": [],
    }


def is_usd_config(cfg):
    # type: (dict) -> bool
    return any(k in cfg for k in USD_TO_INR_KEYS)


def _record(policy, changes, fx_rate=None):
    # type: (str, Dict[str, dict], Optional[float]) -> dict
    return {
        "migrated_by": "indiacore.config_in",
        "schema_version": SCHEMA_VERSION,
        "migrated_at": datetime.datetime.now(datetime.timezone.utc)
                               .replace(microsecond=0).isoformat(),
        "policy": policy,
        "fx_rate_usd_inr": fx_rate,
        "changes": changes,
        "reversible": True,
    }


def migrate(old, policy=None, fx_rate=None, acknowledge_relabel=False):
    # type: (dict, Optional[str], Optional[float], bool) -> Tuple[dict, dict]
    """Return (new_config, migration_record).

    Raises MigrationPolicyRequired if the old config names amounts in dollars
    and no policy says what those numbers meant.
    """
    old = copy.deepcopy(old or {})
    new = india_defaults()
    changes = {}  # type: Dict[str, dict]

    if is_usd_config(old):
        if policy is None:
            raise MigrationPolicyRequired(
                "this config names amounts in US dollars (%s). Choose a "
                "policy: RESET to start from INR defaults, CONVERT with "
                "fx_rate to treat them as dollars, or RELABEL with "
                "acknowledge_relabel=True to keep the numbers and call them "
                "rupees. Refusing to assume, because 50 dollars and 50 rupees "
                "are not the same limit."
                % ", ".join(sorted(k for k in USD_TO_INR_KEYS if k in old)))
        if policy not in POLICIES:
            raise ConfigError("unknown policy %r, expected one of %s"
                              % (policy, ", ".join(POLICIES)))
        if policy == CONVERT and not fx_rate:
            raise ConfigError("CONVERT needs fx_rate, the USD to INR rate to "
                              "use. There is no built in rate table")
        if policy == RELABEL and not acknowledge_relabel:
            raise ConfigError(
                "RELABEL keeps every number and simply calls dollars rupees. "
                "Pass acknowledge_relabel=True to confirm that is intended")

    # Amount keys.
    for usd_key, inr_key in USD_TO_INR_KEYS.items():
        if usd_key not in old:
            continue
        raw = old[usd_key]
        if policy == RESET:
            new[inr_key] = india_defaults()[inr_key]
            changes[usd_key] = {"to": inr_key, "from_value": raw,
                                "to_value": new[inr_key], "action": "reset"}
        elif policy == CONVERT:
            converted = Money.rupees(raw, currency="USD").convert("INR", fx_rate)
            new[inr_key] = float(converted.as_rupees)
            changes[usd_key] = {"to": inr_key, "from_value": raw,
                                "to_value": new[inr_key],
                                "action": "converted at %s" % fx_rate}
        else:  # RELABEL
            new[inr_key] = raw
            changes[usd_key] = {"to": inr_key, "from_value": raw,
                                "to_value": raw, "action": "relabelled"}

    # budget has no currency suffix in the old schema, so it gets the same
    # treatment rather than being copied across unexamined.
    if "budget" in old:
        raw = old["budget"]
        if policy == CONVERT:
            new["budget_inr"] = float(
                Money.rupees(raw, currency="USD").convert("INR", fx_rate).as_rupees)
        elif policy == RESET:
            new["budget_inr"] = 0
        else:
            new["budget_inr"] = raw
        changes["budget"] = {"to": "budget_inr", "from_value": raw,
                             "to_value": new["budget_inr"],
                             "action": policy or "copied"}

    # Settings that carry over unchanged when present.
    for key in ("cash_buffer_pct", "max_position_pct", "min_reward_risk",
                "blocked", "paper", "allow_options", "allow_margin",
                "allow_short"):
        if key in old:
            new[key] = old[key]

    # Settings that do not exist here.
    for key, why in DROPPED_KEYS.items():
        if key in old:
            changes[key] = {"to": None, "from_value": old[key],
                            "to_value": None, "action": "dropped: " + why}

    # Anything intraday is recorded, not carried, because the India profile is
    # delivery only.
    for key in ("no_entries_before", "no_entries_after", "flat_by",
                "max_trades_per_day", "require_stop"):
        if key in old:
            changes[key] = {
                "to": None, "from_value": old[key], "to_value": None,
                "action": "not carried: the India profile is delivery only, "
                          "so an intraday stop and a flat by time do not apply"}

    new["migrated_from"] = {k: old.get(k) for k in sorted(old)} if old else None
    return new, _record(policy or "fresh", changes, fx_rate)


# ------------------------------------------------------------------- files

def backup_path(path):
    # type: (str) -> str
    return path + ".pre-india.bak"


def record_path(path):
    # type: (str) -> str
    return path + ".migration.json"


def migrate_file(path, policy=None, fx_rate=None, acknowledge_relabel=False,
                 out_path=None):
    # type: (str, Optional[str], Optional[float], bool, Optional[str]) -> dict
    """Migrate a config file, preserving the original. Returns the record.

    Writes three things: the new config, a byte for byte backup of the
    original, and a record of what was decided. `revert_file` undoes it.
    """
    if not os.path.exists(path):
        raise ConfigError("no config at %s" % path)
    with open(path, encoding="utf-8") as fh:
        original_text = fh.read()
    old = json.loads(original_text)

    new, record = migrate(old, policy, fx_rate, acknowledge_relabel)

    target = out_path or path
    backup = backup_path(path)
    if not os.path.exists(backup):  # never overwrite the first original
        with open(backup, "w", encoding="utf-8") as fh:
            fh.write(original_text)
    record["original_backup"] = backup
    record["config_path"] = target

    with open(target, "w", encoding="utf-8") as fh:
        json.dump(new, fh, indent=2)
        fh.write("\n")
    with open(record_path(path), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
    return record


def revert_file(path):
    # type: (str) -> str
    """Restore the original config from its backup. Returns the path."""
    backup = backup_path(path)
    if not os.path.exists(backup):
        raise ConfigError("no backup at %s, nothing to revert" % backup)
    with open(backup, encoding="utf-8") as fh:
        text = fh.read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.remove(backup)
    if os.path.exists(record_path(path)):
        os.remove(record_path(path))
    return path


def load(path):
    # type: (str) -> dict
    """Load an INR config, refusing a dollar one."""
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if is_usd_config(cfg):
        raise MigrationPolicyRequired(
            "%s still names amounts in dollars. Run migrate_file first" % path)
    if cfg.get("currency") not in (None, "INR"):
        raise ConfigError("%s is denominated in %s, not INR"
                          % (path, cfg.get("currency")))
    return cfg
