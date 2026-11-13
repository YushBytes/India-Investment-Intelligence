# Project roadmap

Fifteen phases from a US day-trading skill pack to an India-first investment
intelligence system. Three are complete.

This document is the honest state of the project. A phase is marked complete
only when its code is committed, its tests pass and its limitations are
documented. Everything else is planned, which means designed at most and
usually not that.

**Last updated:** 13 November 2026, at commit `3974b87`.

## Status at a glance

| Phase | Name | Status | Commit |
| --- | --- | --- | --- |
| 1 | Repository audit | **Complete** | audit only, no code change |
| 2 | India market foundation | **Complete** | [`df3eb2d`](https://github.com/YushBytes/India-Investment-Intelligence/commit/df3eb2d89fdf5a3585831a90ccbcbdb8a97cdc79) |
| 3 | Indian market data infrastructure | **Complete** | [`c391136`](https://github.com/YushBytes/India-Investment-Intelligence/commit/c391136b52ee49c82e1d97a8ae112e110b84cce7) |
| 4 | Fundamental data and financial metrics | Planned | |
| 5 | Technical analysis indicators | Planned | |
| 6 | Stock scanner and screener | Planned | |
| 7 | Portfolio management | Planned | |
| 8 | Risk and allocation | Planned | |
| 9 | News and events intelligence | Planned | |
| 10 | Daily decision engine | Planned | |
| 11 | Re-point the inherited skills to India | Planned | |
| 12 | Reporting and output | Planned | |
| 13 | Backtesting and validation | Planned | |
| 14 | Performance and reliability | Planned | |
| 15 | Documentation and release | Planned | |

**Test status:** 795 passing, all offline. Phase 2 added 158, phase 3 added
319, the README commit added 3.

---

## Phase 1: Repository audit

**Status:** Complete. No code change; the audit's findings are what phases 2
and 3 were built to fix.

**What it found.** The inherited pack is US-only in ways that fail quietly on
Indian symbols rather than loudly:

- `to_yahoo()` did `sym.replace(".", "-")`, turning `SBIN.NS` into `SBIN-NS`,
  which Yahoo answers with a 404. Every NSE and BSE symbol failed as a "no
  data" result.
- NYSE hours and a US daylight-saving rule were hard-coded in nine files.
- Config keys were named `max_order_usd`, `max_loss_per_trade_usd` and so on,
  so rupee amounts produced correct arithmetic with a false unit.
- The scanner printed "No setups" when every fetch had failed, so a dead feed
  and a quiet market shared one representation.

---

## Phase 2: India market foundation

**Status:** Complete. Commit `df3eb2d`. 473 tests passing at the time.

**Deliverables.** `indiacore/`, six modules, no new dependencies:

- `calendar_in.py`: IST as a fixed UTC+05:30 with no daylight saving; NSE 2026
  holidays from the exchange's holiday master (circular NSE/CMTR/71775);
  Muhurat modelled as a third state; an unverified year raises.
- `symbols.py`: NSE `.NS` and BSE `.BO` in the type rather than in a string;
  correct percent-encoding (`M&M.NS` travels as `M%26M.NS`); indices as a
  separate type; BSE scrip codes refused rather than requested.
- `money.py`: INR in integer paise, carrying its currency; no implicit FX;
  lakh and crore grouping.
- `market.py`: NSE and BSE session models and `last_completed_session`.
- `provenance.py`: the freshness vocabulary, and the structural distinction
  between a real empty result and a dead feed.
- `config_in.py`: the INR schema and a USD migration that requires an
  explicit RESET, CONVERT or RELABEL policy, with a byte-for-byte backup and
  an exact revert.

**Dependencies:** none. Standard library only, Python 3.8 floor.

**Remaining work:** 2027 holidays (not yet published by NSE); a verified BSE
holiday list of its own (the BSE model currently borrows the NSE one and says
so); Muhurat session timings, which are notified separately each year.

---

## Phase 3: Indian market data infrastructure

**Status:** Complete. Commit `c391136`. 792 tests passing.

**Deliverables.** The data layer, built around one rule: a missing or stale
quote is never presented as a current market price.

- `data_service.py`: `MarketDataService`, one entry point, eleven
  distinguishable outcomes (fresh, delayed, end_of_day, historical, cached,
  empty_valid, provider_error, invalid_data, stale, unknown_age,
  unsupported).
- `providers/`: a declared-capability provider interface; the Yahoo chart
  adapter; the NSE and BSE bhavcopy adapters; the registry and capability
  matrix; a transport that is the only code touching the network; an optional
  live probe.
- `marketdata/`: the normalised models carrying provenance, the normalisers,
  five-verdict validation, the freshness rules, and the file cache.
- `rate_limit.py` and `retry.py`: injectable clock and sleep, so the suite
  tests the delay arithmetic without waiting.
- `DATA.md`: the full data-layer documentation and capability matrix.

**Providers delivered:** three working (`yahoo` unofficial; `nse_bhavcopy`
and `bse_bhavcopy` both official, end-of-day only) and two documented as
unusable (`nse_quote_api` and `bse_quote_api`, both HTTP 403 to a plain
request).

**Also fixed:** a phase 2 defect where `DataResult.is_usable` and
`require_usable()` could disagree about an unrecognised freshness value.

**Dependencies:** phase 2. **Dependents:** phases 4 to 11 all consume this
layer rather than making their own requests.

**Remaining work:** no real-time provider; no official intraday source; no
official index source; ETF classification unavailable from any verified
provider; prices unadjusted for corporate actions.

---

## Phase 4: Fundamental data and financial metrics

**Status:** Planned.

**Intended deliverables:** Indian company financial data models; profitability
and growth metrics; banking-specific ratios (NIM, CASA, GNPA and NNPA, which
make bank statements non-comparable with those of other companies); a peer
comparison engine; sector classification.

**Dependencies:** phase 3 for data access; a source for Indian company
financials, which is the open question. The inherited `trader-analyst` skill
reads SEC EDGAR, which has no Indian equivalent: Indian filings go to BSE and
NSE corporate announcements and to the MCA, and none of those is a documented
API. **This source question must be answered before the phase starts**, the
same way phase 3 verified every endpoint before writing an adapter.

**Open risks:** if no permitted programmatic source exists, the phase delivers
the models and a manual or file-based ingestion path, and says so, rather than
scraping.

---

## Phase 5: Technical analysis indicators

**Status:** Planned.

**Intended deliverables:** EMA and MACD; Bollinger Bands and ATR; RSI;
support and resistance analysis; volume analysis. All computed over the
validated `Bar` series from phase 3, with results that carry the provenance
and adjustment status of the bars they came from.

**Dependencies:** phase 3.

**Open risks:** indicators over unadjusted series are wrong across a split.
The validation layer already flags a corporate-action-sized gap; phase 5 has
to decide what an indicator does when it sees that flag, rather than
computing through it silently.

---

## Phase 6: Stock scanner and screener

**Status:** Planned.

**Intended deliverables:** a scanner over the whole NSE and BSE cash market,
using the bhavcopy whole-market snapshot; fundamental and technical filters;
liquidity and price filters; result ranking.

**Dependencies:** phases 3, 4, 5.

**Note:** `nse_bhavcopy.get_session(day)` already returns a whole session in
one request, which is the shape this phase needs.

---

## Phase 7: Portfolio management

**Status:** Planned.

**Intended deliverables:** holdings model in INR; cost basis and realised
versus unrealised gains; position weights; sector and concentration exposure;
transaction history.

**Dependencies:** phases 3 and 4.

**Open risks:** this phase will hold the user's real holdings, which is
private financial data. It must be designed so that nothing it stores is
committed to version control, by default and not by convention.

---

## Phase 8: Risk and allocation

**Status:** Planned.

**Intended deliverables:** position sizing against INR limits; concentration
and sector caps; drawdown measurement; volatility measures; allocation
proposals.

**Dependencies:** phases 3, 5, 7. The INR limit schema already exists in
`config_in.py`.

---

## Phase 9: News and events intelligence

**Status:** Planned.

**Intended deliverables:** corporate announcements; results calendar;
dividend, split and bonus events; event tagging and relevance to held or
watched instruments.

**Dependencies:** phase 3; a permitted source for Indian corporate
announcements, which has the same open question as phase 4.

---

## Phase 10: Daily decision engine

**Status:** Planned.

**Intended deliverables:** a daily research brief combining the scanner,
fundamentals, technicals, portfolio state and risk limits into
decision-support output with an explicit thesis and invalidation condition
per idea.

**Dependencies:** phases 4 to 9.

**Constraint, not negotiable:** output is research for a person to evaluate.
This phase does not place orders, and the configuration profile keeps
`auto_execute: false`.

---

## Phase 11: Re-point the inherited skills to India

**Status:** Planned.

**Intended deliverables:** the eleven `trader-*` skills consuming `indiacore`
instead of their own US calendars and HTTP requests. Currently none of them
imports it.

**Dependencies:** phases 3 to 8.

**Open decisions:** `trader-pelosi` tracks US congressional disclosures under
the STOCK Act and has no Indian analogue; it is a scope decision, not a port.
`trader-broker` targets Alpaca and would need an Indian broker equivalent, or
to be dropped in favour of research-only output.

**Recommended first slice:** `trader-scanner`, because its "No setups" bug is
what motivated the provenance guard in the first place.

---

## Phases 12 to 15

| Phase | Name | Intended deliverables | Dependencies |
| --- | --- | --- | --- |
| 12 | Reporting and output | Research reports, watchlist and portfolio summaries, export formats | 4 to 10 |
| 13 | Backtesting and validation | Historical replay of the scanner and decision rules, honest performance measurement | 3 to 10 |
| 14 | Performance and reliability | Bulk data handling, cache efficiency, provider failover under load | 3 to 12 |
| 15 | Documentation and release | End-user documentation, worked examples, versioned release | all |

---

## Cross-phase constraints

These hold for every phase and are enforced by `tests/test_repo.py`:

1. **No third-party dependencies.** Standard library only, Python 3.8 floor.
2. **Only `providers/transport.py` touches the network.**
3. **The test suite stays offline and deterministic.** Live checks live in
   `live_probe.py`, separate from the suite.
4. **No provider claims real-time delivery it does not have**, and a provider
   that needs credentials it does not have is marked not operational rather
   than left looking finished.
5. **Nothing writes into the repository at runtime.** The cache defaults
   outside it.
6. **No secrets committed, ever.**
