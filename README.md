# India Investment Intelligence

An India-first investment intelligence system for Indian equity research:
market data, portfolio analysis, risk management and daily decision support,
built for NSE and BSE rather than adapted from a US market tool.

The project is under active development. Three of fifteen planned phases are
complete. This README describes only what is built and tested today, and
[docs/PROJECT_ROADMAP.md](docs/PROJECT_ROADMAP.md) describes the rest as
planned work.

## Project Vision

Most retail investment tooling is built for US markets and then pointed at
Indian symbols, which fails in ways that are quiet rather than loud: a
dollar-denominated limit applied to rupee amounts, a NYSE calendar applied to
an exchange that does not observe daylight saving, a symbol encoder that turns
`SBIN.NS` into `SBIN-NS` and reports "no data" instead of an error.

The goal is a research and decision-support system that treats the Indian
market as the primary case: NSE and BSE as separate instruments, INR as a
first-class currency, the exchange's own published holiday circulars as the
calendar, and the exchange's own end-of-day files as the authoritative price.
Above all, it is built so that **a missing or stale quote is never presented
as a current market price**.

It is a research tool. It does not place orders. See [Safety](#safety).

## Current Features

Implemented, tested, and in use by the data layer:

| Area | What works today |
| --- | --- |
| **Trading calendar** | IST as a fixed UTC+05:30 with no daylight saving. NSE 2026 holidays loaded from the exchange's own holiday master (circular NSE/CMTR/71775). An unverified year raises rather than guessing. Muhurat trading is modelled as a third state, neither open nor a plain holiday |
| **Symbol normalisation** | NSE `.NS` and BSE `.BO` suffixes kept in the type, not in a string. Correct percent-encoding, so `M&M.NS` travels as `M%26M.NS`. Indices (`^NSEI`, `^BSESN`) are a separate type with no exchange suffix. BSE scrip codes are refused rather than sent to an endpoint that 404s them |
| **INR money model** | Amounts stored as integer paise, carrying their currency. Adding rupees to dollars raises. No implicit FX conversion and no built-in rate table. Lakh and crore digit grouping |
| **Market sessions** | NSE and BSE equity sessions (09:15 to 15:30 IST, 09:00 pre-open) modelled separately, plus `last_completed_session`, the date an end-of-day figure actually belongs to |
| **Market data service** | One entry point (`MarketDataService`) for quotes, daily bars, intraday bars and index levels, returning eleven distinguishable outcomes so a caller can tell fresh from cached, delayed from end-of-day, a genuinely empty result from a dead feed |
| **Data providers** | Three working providers (Yahoo, NSE bhavcopy, BSE bhavcopy) behind one interface, each declaring exactly which exchanges, instruments, data kinds and intervals it serves |
| **Data validation** | Five verdicts (valid, warning, invalid, incomplete, stale). Structurally impossible records are rejected; a circuit-limit move or a split-sized gap is flagged and kept, not discarded |
| **Provenance** | Every record carries its provider, source, retrieval time, source timestamp and freshness. A record cannot be constructed without them |
| **Freshness classification** | Decided from the source timestamp, the market state, the interval and what the provider actually promises, so the same figure is valid end-of-day data at 09:00 and stale at 11:00 |
| **Caching** | File cache with deterministic exchange-aware keys, atomic writes, checksum-based corruption detection, per-kind TTLs. Failures are never cached, and a stale entry never silently replaces a failed request |
| **Retry and rate limiting** | Retry classification by HTTP status (a 404 is the answer, not a fault), exponential backoff with jitter, `Retry-After` support, and per-provider cooldowns that fail fast instead of blocking |
| **Config migration** | A USD configuration profile migrates to INR only under an explicit policy, because 50 dollars and 50 rupees are different limits. Keeps a byte-for-byte backup and reverts exactly |

**792 automated tests pass.** The entire test suite runs offline against
recorded fixtures.

### Also in this repository

This project began as a migration of an upstream US day-trading skill pack
(see [Attribution](#attribution)). The eleven original `trader-*` skill
folders are still present and still work as they did upstream, which means
they are **US-oriented**: they use NYSE hours, SEC EDGAR, Nasdaq Trader symbol
lists and USD amounts, and they do not use any of the India code above. They
have not been re-pointed yet; that is a later phase.

## Architecture

```
indiacore/                   the India-first shared library
  calendar_in.py             IST, NSE holiday circulars, Muhurat as a third state
  symbols.py                 NSE/BSE symbols, suffixes, URL encoding, indices
  money.py                   INR in paise, currency-carrying, no implicit FX
  market.py                  NSE and BSE session model, last completed session
  provenance.py              freshness vocabulary, and empty vs broken
  config_in.py               INR config schema and the explicit USD migration
  data/                      the exchange's published holiday lists (data only)

  data_service.py            MarketDataService: the one entry point
  rate_limit.py              request spacing and rate-limit cooldowns
  retry.py                   what is worth retrying, and what is just the answer
  DATA.md                    full data-layer documentation

  providers/
    base.py                  the Provider interface and its error types
    transport.py             the only module that touches the network
    registry.py              provider selection and the capability matrix
    yahoo.py                 Yahoo chart endpoint
    nse.py                   NSE bhavcopy, and the refused NSE quote API
    bse.py                   BSE bhavcopy, and the refused BSE quote API
    live_probe.py            optional live checks, separate from the test suite

  marketdata/
    models.py                Quote, Bar, Provenance, Capabilities, ProviderStatus
    normalize.py             provider payloads to records
    validation.py            the five verdicts
    quality.py               the freshness rules
    cache.py                 the file cache

trader-*/                    eleven inherited US day-trading skills (not re-pointed)
tests/                       792 tests, all offline, with recorded fixtures
docs/                        roadmap and development workflow
```

Two design rules hold the data layer together:

1. **Only `providers/transport.py` makes network requests.** Every provider
   takes a transport, so the test suite passes one that reads a recorded
   file and nothing in a test can reach the network. A repository test
   enforces this.
2. **A record cannot exist without its provenance.** There is no constructor
   that accepts a bare number and calls it a market price.

Full detail, including the freshness rules and cache design, is in
[indiacore/DATA.md](indiacore/DATA.md).

## Supported Providers

Every claim here was verified against the live endpoint before the adapter
was written. `python -m indiacore.providers.live_probe` re-checks them on
demand.

| Provider | Official | Working | Quotes | Daily bars | Intraday | Index | Exchanges |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `yahoo` | no | yes | yes | yes | 1m to 60m | yes | NSE, BSE |
| `nse_bhavcopy` | **yes** | yes | no | yes | no | no | NSE |
| `bse_bhavcopy` | **yes** | yes | no | yes | no | no | BSE |
| `nse_quote_api` | yes | **no** | - | - | - | - | NSE |
| `bse_quote_api` | yes | **no** | - | - | - | - | BSE |

**Yahoo Finance chart endpoint.** Unofficial and best-effort. An undocumented
endpoint on a public host, with no published delay, no service level and no
guarantee of continued access. It declares no delay field of any kind, so
nothing from it is ever classified as a live price; its ceiling is "delayed".
Its BSE history is thinner than its NSE history: a five-day daily request for
`INFY.BO` returned one bar where `INFY.NS` returned five.

**NSE bhavcopy.** The exchange's own published end-of-day file for a
completed session, covering the whole cash market in one file. Authoritative,
and **end-of-day only**: the file does not exist until the session is over, so
this provider refuses any request whose end date is after the last completed
session. Published unadjusted, so splits and bonus issues appear as price
gaps. Efficient for a market-wide snapshot of one date, expensive for one
instrument across many sessions.

**BSE bhavcopy.** The same, in the identical column format, served as a plain
CSV. It also carries the BSE scrip code alongside the ticker. For
2026-11-12, SBIN closed at 1086.50 on NSE and at 1085.00 on BSE, on volumes an
order of magnitude apart. Nothing in this project averages the two or
substitutes one for the other.

**The NSE and BSE live quote APIs are not usable and are not implemented.**
Both answered HTTP 403 to a plain request. They respond only if browser
cookies and headers are primed and replayed, which is circumventing an access
control, so these adapters exist to document the endpoint and refuse. A
licensed exchange feed would be a separate provider with its own credentials.

## Completed Phases

| Phase | Name | Status | Commit |
| --- | --- | --- | --- |
| 1 | Repository audit | Complete | (audit, no code change) |
| 2 | India market foundation | Complete | [`137c19e`](https://github.com/YushBytes/India-Investment-Intelligence/commit/137c19e000908f225944b449ea061061d38aa970) |
| 3 | Indian market data infrastructure | Complete | [`a2f3cc2`](https://github.com/YushBytes/India-Investment-Intelligence/commit/a2f3cc29b3879d135d397cd764933b8d9855a400) |

## Roadmap

Planned, not built. Nothing below is implemented.

| Phase | Name | Status |
| --- | --- | --- |
| 4 | Fundamental data and financial metrics | Planned |
| 5 | Technical analysis indicators | Planned |
| 6 | Stock scanner and screener | Planned |
| 7 | Portfolio management | Planned |
| 8 | Risk and allocation | Planned |
| 9 | News and events intelligence | Planned |
| 10 | Daily decision engine | Planned |
| 11 | Re-point the inherited skills to India | Planned |
| 12 | Reporting and output | Planned |
| 13 | Backtesting and validation | Planned |
| 14 | Performance and reliability | Planned |
| 15 | Documentation and release | Planned |

See [docs/PROJECT_ROADMAP.md](docs/PROJECT_ROADMAP.md) for deliverables,
dependencies and remaining work per phase.

## Installation

Python 3.8 or newer. **No third-party dependencies**: everything uses the
standard library, so there is nothing to `pip install` to run the code.

### Windows (PowerShell)

```powershell
git clone https://github.com/YushBytes/India-Investment-Intelligence.git
cd India-Investment-Intelligence
python --version          # 3.8 or newer

# optional, for running the test suite
python -m pip install pytest
```

### macOS and Linux

```bash
git clone https://github.com/YushBytes/India-Investment-Intelligence.git
cd India-Investment-Intelligence
python3 --version
python3 -m pip install pytest   # optional, for the test suite
```

### Optional configuration

```powershell
# where the market-data cache is written; defaults to a system temp directory
$env:INDIACORE_CACHE_DIR = "C:\data\indiacore-cache"
```

No credentials are needed, because no implemented provider requires
authentication. Copy `indiacore/config.india.example.json` and edit it to set
your own INR limits.

### Quick check

```python
from indiacore import symbols
from indiacore.data_service import MarketDataService

service = MarketDataService()
result = service.get_quote(symbols.nse("SBIN"))

print(result.outcome)                  # delayed, end_of_day, stale, ...
if result.usable:
    print(result.first.last_price.format())
    if not result.is_current_price:
        print("not a live price:", result.notes[0])
```

## Testing

The full suite is offline and deterministic. From the repository root:

```powershell
python -m pytest -q
```

Expected: **792 passed**, in roughly 30 seconds. No network access is used or
required.

Optional live checks against the real endpoints, kept deliberately separate
from the test suite so that an outage elsewhere never fails CI:

```powershell
python -m indiacore.providers.live_probe
```

## Data Limitations

Stated plainly, because the value of this project depends on them being
honest:

- **There is no verified real-time quote provider.** No implemented provider
  promises real-time delivery, so a "live" classification is unreachable by
  design. The code has the branch for a licensed feed; no such feed is wired
  up.
- **Yahoo Finance is unofficial.** An undocumented endpoint with no published
  delay, no service level and no guarantee it will answer tomorrow. Treat it
  as best-effort and never as the record of a price.
- **NSE and BSE bhavcopy data is end-of-day only.** A bhavcopy is published
  after the session closes. It cannot answer "what is the price now".
- **Provider coverage varies.** Intraday bars come from Yahoo alone. Index
  levels come from Yahoo alone, and are therefore unofficial. Yahoo's BSE
  history is thinner than its NSE history.
- **ETF classification is incomplete.** No verified provider distinguishes an
  Indian ETF from an equity: Yahoo reports `NIFTYBEES.NS` and `GOLDBEES.NS` as
  `EQUITY`, and the exchange bhavcopy carries only a generic stock type. ETFs
  are quotable; their instrument type is reported as equity or unknown, never
  guessed from the ticker.
- **Future holiday calendars may be unavailable.** Indian exchange holidays
  are announced by circular, not computed from rules. Only verified years are
  shipped (2026 at present), and an unverified year raises rather than
  guessing. NSE publishes the next year's circular in December.
- **Prices are unadjusted.** Both bhavcopies publish prices as traded.
  Validation flags a corporate-action-sized gap rather than silently
  adjusting for it.
- **The BSE calendar is borrowed.** The BSE market model currently uses the
  NSE holiday list and says so. The equity dates have matched in recent years
  but are published by separate circulars.

## Safety

**This is an investment research and decision-support tool. It is not an
automated trading system.**

- Nothing in the India data layer can place, modify or cancel an order. It
  reads public market data and nothing else.
- The INR configuration profile defaults to research only:
  `auto_execute: false`, `paper: true`, `broker: none-research-only`, and
  intraday, options, futures, margin, MTF and short selling all disabled.
- The inherited `trader-*` skills include an order-execution skill. It
  defaults to paper trading and cannot send an order without explicit human
  confirmation. It is US-oriented and is not part of the India system.
- Nothing here is financial advice. Output is research material for a person
  to evaluate, and any investment decision and its consequences are the
  user's own.

## Attribution

This project began as a migration of
[Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill),
an MIT-licensed pack of eleven US day-trading skills for Claude. That
repository's history is preserved here as the first commit, its eleven skills
are still present, and its MIT licence and copyright notice are retained
unchanged in [LICENSE](LICENSE).

The India-first work (`indiacore/`, its tests and its documentation) is new in
this repository.

## Licence

MIT. See [LICENSE](LICENSE). Copyright for the original day-trading skill pack
remains with its author, as the MIT licence requires.
