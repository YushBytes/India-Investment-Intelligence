# Development workflow

How work gets done, committed and published in this repository. The aim is a
history that reads as genuine development: small, logical, tested commits with
messages that say why.

## Repository and remotes

```
origin     https://github.com/YushBytes/India-Investment-Intelligence.git
upstream   https://github.com/Jakeschincariol/day-trader-skill.git  (fetch only)
```

`origin` is this project. `upstream` is the MIT-licensed US day-trading pack
this project was migrated from, kept so its history stays attributable and so
future upstream changes can be inspected.

**The upstream push URL is deliberately set to an invalid value**, so that a
mistyped `git push upstream` fails instead of reaching someone else's
repository. Leave it that way.

```bash
git remote -v                    # confirm before any push
git fetch upstream               # inspecting upstream is fine
```

Default branch: `india-migration`. The `main` branch holds the unmodified
upstream base commit as a reference point and is not developed on.

## Branch naming

One branch per phase, zero-padded, lower case, hyphenated:

```
phase-04-fundamentals
phase-05-technical-analysis
phase-06-stock-scanner
phase-07-portfolio-management
phase-08-risk-allocation
phase-09-news-intelligence
phase-10-daily-decision-engine
phase-11-repoint-skills
```

For work that is not a phase:

```
fix/<short-description>          a bug fix
docs/<short-description>         documentation only
chore/<short-description>        tooling, config, housekeeping
```

Branch from the latest approved commit on `india-migration`:

```bash
git checkout india-migration
git pull origin india-migration
git checkout -b phase-04-fundamentals
```

## Commit message convention

Conventional Commits:

```
<type>(<scope>): <summary in the imperative, lower case, no full stop>

<body: what changed, and why it was done this way. Wrapped at 72 columns.
State the facts that drove a decision, including anything verified against a
real source. Name what was deliberately not done.>
```

**Types**

| Type | Use for |
| --- | --- |
| `feat` | new capability |
| `fix` | a defect corrected |
| `test` | tests added or changed on their own |
| `docs` | documentation |
| `refactor` | behaviour-preserving restructuring |
| `chore` | tooling, config, dependencies, housekeeping |
| `perf` | a measured performance change |

**Scope** is the module or subsystem: `fundamentals`, `technicals`, `scanner`,
`portfolio`, `risk`, `news`, `decisions`, `providers`, `marketdata`,
`calendar`, `symbols`, `money`, `cache`, `repo`, `readme`.

**A phase is several commits, one per logical unit.** For example:

```
feat(fundamentals): add Indian company financial data models
feat(fundamentals): implement profitability and growth metrics
feat(fundamentals): add banking-specific financial ratios
feat(fundamentals): add peer comparison engine
test(fundamentals): add financial calculation fixtures
docs(fundamentals): document data sources and limitations
```

**Rules.**

- Every commit has a clear purpose, a descriptive message, its relevant
  tests, and no unrelated changes.
- Do not put a whole phase in one commit when it divides naturally.
- Do not split one logical change into meaningless one-line commits.
- Never commit to inflate a contribution graph. A fabricated history is
  worse than a short one.
- Every commit must leave the test suite green. If a change and its test
  update are one logical unit, they belong in one commit.
- Co-author trailers are kept where work was genuinely co-authored.

## Phase workflow

1. **Start from the latest approved commit.** Confirm the branch, the HEAD
   hash and a clean working tree before writing anything.
2. **Inspect before building.** Read the existing code rather than trusting a
   previous phase's report. If a previous phase has a defect, report it before
   making unrelated changes.
3. **Verify external facts first.** Before writing an adapter for any data
   source, check the real endpoint and record what it actually returns. A
   source that worked in an earlier session is not evidence that it works now.
4. **Create the phase branch.**
5. **Implement one logical feature at a time.**
6. **Run the relevant tests** for that feature.
7. **Commit the completed feature** with its tests.
8. **Push regularly**, so work in progress is backed up and visible:
   `git push -u origin phase-04-fundamentals`
9. **Update the documentation** in the same phase, not later:
   `docs/PROJECT_ROADMAP.md`, the module docs, and `README.md` when a feature
   becomes user-visible.
10. **Run the full suite** at the end of the phase: `python -m pytest -q`
11. **Push the final commit** and provide the commit links.
12. **Stop and wait for approval** before starting the next phase.

Merging a finished phase into `india-migration`:

```bash
git checkout india-migration
git merge --no-ff phase-04-fundamentals
python -m pytest -q
git push origin india-migration
```

`--no-ff` keeps the phase visible as a unit in the history.

## Testing before commits

```bash
python -m pytest -q                              # the whole suite
python -m pytest tests/test_indiacore_X.py -q    # one module
python -m indiacore.providers.live_probe         # optional, hits the network
```

Rules the suite is held to, enforced by `tests/test_repo.py`:

- **Offline and deterministic.** No test may reach the network. Providers take
  a transport, and tests pass a `FixtureTransport` that reads a recorded file.
  A URL matching no fixture raises, so a test cannot pass by accident when a
  provider changes the URL it builds.
- **No real waiting.** Clocks and sleeps are injected. A test must not sleep
  to exercise a timeout, a backoff or a rate limit.
- **No new dependencies.** Standard library only, Python 3.8 floor.
- **Fixtures are real.** Record an actual response and trim it, or mutate a
  real one deliberately for a failure case. Do not invent a payload shape.
- **Do not relax a test to make a change pass.** If a repository rule should
  change, change it on purpose, say so in the commit message, and make the
  replacement at least as strict.

Live checks stay out of the mandatory suite, so that someone else's outage
never fails this project's CI.

## Publishing to GitHub

```bash
git status                       # clean
python -m pytest -q              # green
git remote -v                    # origin is this project
git push origin <branch>
```

- **Never force-push.** Not to fix a message, not to tidy a history.
- **Never push to `upstream`.**
- Do not push unfinished, broken or untested code to `india-migration`. A
  phase branch is the place for work in progress.
- Verify after pushing, do not assume:

```bash
git ls-remote --heads origin
gh api repos/YushBytes/India-Investment-Intelligence/commits/<sha> \
  --jq '.sha + "  " + (.commit.message | split("\n")[0])'
```

## Secret management

The repository has no credentials in it, and no implemented provider needs
any. That is the state to preserve.

- **Credentials come from the environment only**, never from a committed
  file, a default argument or a cache key.
- `.gitignore` already covers `trader/` (runtime state), `indiacore-cache/`,
  `*.corrupt`, `__pycache__/`, `.pytest_cache/` and the config migration
  backups. Extend it before adding anything that writes to disk.
- The runtime cache defaults outside the repository, under the system
  temporary directory, overridable with `INDIACORE_CACHE_DIR`. A repository
  test asserts the default is never inside the repository.
- Errors must not leak secrets: the transport strips query strings from any
  URL it puts in an exception and never copies request headers into one, and
  the retry helper withholds a message mentioning an authorization header or
  a token.

**Before any push, scan the history and not just the working tree.** A secret
removed in a later commit is still in the history and still gets published:

```bash
git log --all --pretty=format: --name-only --diff-filter=A | sort -u
git grep -nI -e "BEGIN.*PRIVATE KEY" -e "api_key" -e "secret" $(git rev-list --all)
```

**If a secret is found in history, stop.** Adding it to `.gitignore` does not
remove it from history. Rotate the credential first, because it must be
treated as compromised the moment it was committed, then plan the history
rewrite separately and deliberately.

Private financial data is treated the same way as a credential. Real holdings,
transactions and account identifiers must never be committed, which is a
design constraint on phase 7 and not a convention to remember.

## Release procedure

1. Full suite green: `python -m pytest -q`.
2. Live probe run and its result recorded, including any check that failed.
3. `docs/PROJECT_ROADMAP.md` updated: phase status, commit hashes, remaining
   work, test count.
4. `README.md` updated: only features that are implemented and tested, and
   the data limitations still accurate.
5. Secret scan over the full history, clean.
6. Merge the phase branch into `india-migration` with `--no-ff` and push.
7. Tag the phase:

```bash
git tag -a phase-04 -m "Phase 4: fundamental data and financial metrics"
git push origin phase-04
```

8. Report the commit links and stop for review.

Versioning follows `indiacore.__version__`: the minor version moves with a
phase that adds capability, the patch version with a fix.
