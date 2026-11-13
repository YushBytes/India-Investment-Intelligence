import ast
import json
import os
import re
import sys
import unittest

from _load import ROOT

# v2, the day-trading pack: the eleven folders the reel's repo card shows
SKILLS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
          "trader-risk", "trader-budget", "trader-broker", "trader-alerts", "trader-analyst",
          "trader-pelosi"]
ABOUT = ("11 Claude skills for day trading: scan thousands of charts, track breaking news, time every "
         "entry and exit, and replay every outcome before you risk a dollar.")
# skills on the way to an order carry the safety block
ORDER_PATH = ["trader-scanner", "trader-entry", "trader-exit", "trader-timemachine", "trader-budget",
              "trader-pelosi", "trader-risk", "trader-broker", "trader-alerts"]
V1_ONLY = ["trader-copycat", "trader-screener", "trader-tax", "trader-rebalance"]

# India migration, phase 2: the shared package the eleven skills will be
# re-pointed at. It is a package, not a skill, so it has no SKILL.md and is
# checked on its own terms below.
CORE = "indiacore"
CORE_MODULES = ["calendar_in", "config_in", "market", "money", "provenance", "symbols"]

# India migration, phase 3: the market data layer. Two subpackages and three
# modules at the package root, held to exactly the same rules as phase 2.
CORE_PACKAGES = ["marketdata", "providers"]
CORE_PHASE3_MODULES = ["data_service", "rate_limit", "retry"]
MARKETDATA_MODULES = ["cache", "models", "normalize", "quality", "validation"]
PROVIDER_MODULES = ["base", "bse", "live_probe", "nse", "registry",
                    "transport", "yahoo"]


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_core_python_file():
    """Every .py in the package, including the phase 3 subpackages.

    The original version listed only the top level, which would have let a
    subpackage import a dependency or use 3.9 only syntax unnoticed.
    """
    folder = os.path.join(ROOT, CORE)
    for base, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.relpath(os.path.join(base, name), ROOT)


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml")) or f == "LICENSE":
                yield os.path.join(base, f)


class Repo(unittest.TestCase):
    def test_eleven_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT)
                       if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_root_matches_the_reel_plus_the_india_core(self):
        # Dot entries are tool droppings (.git, .pytest_cache, .claude-plugin
        # excepted) rather than repo content. The original test filtered only
        # ".git", which made it pass or fail depending on whether pytest had
        # already written its cache.
        root = sorted(d for d in os.listdir(ROOT)
                      if d == ".claude-plugin" or not d.startswith("."))
        self.assertEqual(root, sorted([".claude-plugin", "LICENSE", "README.md",
                                       "tests", CORE] + SKILLS))

    def test_the_core_is_a_package_and_not_a_twelfth_skill(self):
        folder = os.path.join(ROOT, CORE)
        self.assertTrue(os.path.isdir(folder))
        self.assertTrue(os.path.exists(os.path.join(folder, "__init__.py")))
        self.assertFalse(os.path.exists(os.path.join(folder, "SKILL.md")),
                         "indiacore is shared code, not a skill")
        for mod in CORE_MODULES:
            self.assertTrue(os.path.exists(os.path.join(folder, mod + ".py")), mod)

    def test_the_core_adds_no_dependencies(self):
        """The pack promises nothing to pip install. That has to stay true."""
        allowed = set("""
            __future__ copy csv datetime decimal gzip hashlib importlib io json
            math os random re shutil socket statistics subprocess sys tempfile
            time typing unittest urllib xml zipfile indiacore
        """.split())
        # Parsed rather than matched by regex: a line of prose that happens
        # to begin with "from" is not an import, and the regex version read
        # one in a docstring as a dependency.
        for rel in every_core_python_file():
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), rel)
            for node in ast.walk(tree):
                tops = []
                if isinstance(node, ast.Import):
                    tops = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # a relative import, inside the package
                        continue
                    tops = [(node.module or "").split(".")[0]]
                for top in tops:
                    self.assertIn(top, allowed, "%s imports %s" % (rel, top))

    def test_the_data_layer_is_shaped_as_documented(self):
        folder = os.path.join(ROOT, CORE)
        for name in CORE_PHASE3_MODULES:
            self.assertTrue(os.path.exists(os.path.join(folder, name + ".py")),
                            name)
        for package, modules in ((CORE_PACKAGES[0], MARKETDATA_MODULES),
                                 (CORE_PACKAGES[1], PROVIDER_MODULES)):
            sub = os.path.join(folder, package)
            self.assertTrue(os.path.exists(os.path.join(sub, "__init__.py")),
                            package)
            for name in modules:
                self.assertTrue(
                    os.path.exists(os.path.join(sub, name + ".py")),
                    "%s/%s" % (package, name))
        self.assertTrue(os.path.exists(os.path.join(folder, "DATA.md")))

    def test_the_verified_holiday_directory_stayed_data_only(self):
        """indiacore/data holds the exchange's published lists, not code.

        Phase 3 put its modules in indiacore/marketdata for this reason: the
        data directory is what calendar_in reads and what the provenance test
        below checks.
        """
        data = os.path.join(ROOT, CORE, "data")
        for name in os.listdir(data):
            if name == "__pycache__":
                continue
            self.assertTrue(name.endswith(".json"),
                            "%s is not holiday data" % name)

    def test_only_the_transport_reaches_the_network(self):
        """One place makes requests, which is what keeps the suite offline."""
        allowed = (os.path.join(CORE, "providers", "transport.py"),
                   os.path.join(CORE, "providers", "live_probe.py"))
        for rel in every_core_python_file():
            if rel.endswith(allowed):
                continue
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                src = fh.read()
            for banned in ("urlopen(", "urllib.request", "http.client",
                           "socket.socket"):
                self.assertNotIn(banned, src,
                                 "%s reaches the network directly" % rel)

    def test_no_provider_claims_real_time_or_hides_a_refusal(self):
        """The honesty rules, asserted against the code and not the docs."""
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from indiacore.providers import registry as registry_mod

        rows = dict((r["provider"], r)
                    for r in registry_mod.default_registry().matrix())
        self.assertEqual(len(rows), 5)
        for name, row in rows.items():
            self.assertFalse(row["realtime"], "%s claims real time" % name)
            if row["auth_required"]:
                self.assertFalse(row["operational"],
                                 "%s needs auth and claims to work" % name)
            if not row["operational"]:
                self.assertEqual(row["kinds"], [],
                                 "%s is not operational and declares work"
                                 % name)
            self.assertTrue(row["notes"], "%s documents nothing" % name)
        self.assertTrue(rows["nse_bhavcopy"]["official"])
        self.assertTrue(rows["bse_bhavcopy"]["official"])
        self.assertFalse(rows["yahoo"]["official"])

    def test_the_cache_never_defaults_into_the_repository(self):
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        saved = os.environ.pop("INDIACORE_CACHE_DIR", None)
        try:
            from indiacore.marketdata import cache as cache_mod
            self.assertFalse(
                os.path.abspath(cache_mod.default_dir()).startswith(
                    os.path.abspath(ROOT)),
                "the cache would be written inside the repository")
        finally:
            if saved is not None:
                os.environ["INDIACORE_CACHE_DIR"] = saved

    def test_the_core_holiday_data_is_verified_and_sourced(self):
        """A calendar with no provenance is a guess with a filename."""
        data = os.path.join(ROOT, CORE, "data")
        files = [f for f in os.listdir(data) if f.endswith(".json")]
        self.assertTrue(files, "no holiday data shipped")
        for name in files:
            with open(os.path.join(data, name), encoding="utf-8") as fh:
                doc = json.load(fh)
            src = doc.get("source") or {}
            self.assertIs(src.get("verified"), True, name)
            for field in ("name", "url", "retrieved_at"):
                self.assertTrue(src.get(field), "%s is missing source.%s" % (name, field))
            self.assertTrue(doc.get("holidays"), name)

    def test_the_india_config_bans_what_the_mandate_bans(self):
        path = os.path.join(ROOT, CORE, "config.india.example.json")
        with open(path) as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["currency"], "INR")
        for key in ("allow_intraday", "allow_options", "allow_futures",
                    "allow_margin", "allow_mtf", "allow_short", "auto_execute"):
            self.assertIs(cfg[key], False, key)
        self.assertIs(cfg["delivery_only"], True)
        for key in cfg:
            self.assertFalse(key.endswith("_usd"), key)

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 200, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_order_path_skills_carry_the_safety_rule(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("yes", low, s)

    def test_plugin_manifest_points_at_the_root_skills(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            plugin = json.load(fh)
        self.assertEqual(plugin["name"], "day-trader-skill")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT)
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as fh:
            market = json.load(fh)
        self.assertEqual(market["name"], "day-trader-skill")
        self.assertEqual(market["plugins"][0]["name"], "day-trader-skill")

    def test_readme_carries_the_reel_copy(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        self.assertTrue(readme.startswith("# The Day Trader skill"))
        for line in (ABOUT,
                     "https://github.com/Jakeschincariol/day-trader-skill install skill",
                     "/plugin marketplace add Jakeschincariol/day-trader-skill",
                     "/plugin install day-trader-skill@day-trader-skill",
                     "## Fine print"):
            self.assertIn(line, readme)
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)

    def test_no_em_dashes_and_no_personal_email(self):
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, ROOT)
            if not rel.startswith(os.path.join("tests", "fixtures")):  # fixtures are real data, kept as published
                self.assertNotIn("\u2014", text, rel)
            self.assertNotIn("jakeschincariol" + "@" + "gmail.com", text, rel)  # built, so this file passes too

    def test_no_v1_only_skill_is_left_behind(self):
        for path in every_text_file():
            rel = os.path.relpath(path, ROOT)
            if rel.startswith("tests" + os.sep) or rel == "README.md":
                continue  # the README points v1 users to the v1 commit
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for old in V1_ONLY:
                self.assertNotIn(old, text, rel)

    def test_every_tool_runs_on_python_3_8(self):
        # no 3.9+ only modules or syntax in the tools (the README promises 3.8)
        # indiacore is held to the same floor as the skills it will serve
        files = []
        for s in SKILLS:
            folder = os.path.join(ROOT, s)
            files.extend(os.path.join(s, f) for f in os.listdir(folder)
                         if f.endswith(".py"))
        files.extend(every_core_python_file())  # the subpackages too
        for rel in files:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                src = fh.read()
            for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix(",
                        "match ", "list[", "dict[", "tuple["):
                if bad == "match ":
                    self.assertNotRegex(src, r"(?m)^\s+match \w+.*:$", rel)
                elif bad.endswith("["):
                    self.assertNotRegex(src, r"(?<![\w.])%s" % re.escape(bad), rel)
                else:
                    self.assertNotIn(bad, src, rel)

    def test_day_trading_limits_ship_in_the_example_config(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        for k in ("max_loss_per_trade_usd", "max_daily_loss_usd", "max_trades_per_day",
                  "min_reward_risk", "require_stop", "no_entries_after", "flat_by"):
            self.assertIn(k, cfg, k)
        self.assertIs(cfg["require_stop"], True)
        self.assertIs(cfg.get("allow_short", False), False)
        self.assertIs(cfg.get("allow_margin", False), False)

    def test_paper_trading_is_the_default(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        self.assertIs(cfg["paper"], True)
        self.assertIs(cfg.get("allow_options", False), False)


if __name__ == "__main__":
    unittest.main()
