# indiacore market data

Phase 3 of the India migration. A shared layer that retrieves, normalises,
validates, caches and classifies Indian market data, so that a future
research, portfolio, scanner or decision module asks one object for a price
instead of writing its own HTTP request.

The rule the whole layer is built around: **a missing or stale quote is never
presented as a current market price.** Every record carries where it came
from and how old it is, and the service tells you which of those two things
you are holding.

No new dependencies. Standard library only, Python 3.8 floor, the same as the
rest of the repo.

## Contents

- [Architecture](#architecture)
- [Providers and what they actually do](#providers-and-what-they-actually-do)
- [Capability matrix](#capability-matrix)
- [Data models](#data-models)
- [Validation](#validation)
- [Freshness](#freshness)
- [Caching](#caching)
- [Rate limiting and retries](#rate-limiting-and-retries)
- [Fallback](#fallback)
- [Configuration](#configuration)
- [Running the tests](#running-the-tests)
- [Known limitations](#known-limitations)

## Architecture

```
indiacore/
  data_service.py        the one entry point: MarketDataService
  rate_limit.py          request spacing and rate limit cooldowns
  retry.py               what is worth retrying, and what is just the answer
  providers/
    base.py              the Provider interface and its error types
    transport.py         the only code that touches the network
    registry.py          which providers exist and which one to ask
    yahoo.py             Yahoo chart endpoint (unofficial)
    nse.py               NSE bhavcopy (official) and the refused NSE quote API
    bse.py               BSE bhavcopy (official) and the refused BSE quote API
    live_probe.py        optional live checks, not part of the test suite
  marketdata/
    models.py            Quote, Bar, Provenance, Capabilities, ProviderStatus
    normalize.py         provider payloads to models
    validation.py        VALID, WARNING, INVALID, INCOMPLETE, STALE
    quality.py           the freshness rules
    cache.py             the file cache
```

Phase 2 is reused rather than restated: `symbols` for every symbol and its
URL form, `market` and `calendar_in` for session state and the last completed
session, `money` for every price, and `provenance` for the freshness
vocabulary and the usable or not verdict.

Two deviations from the structure originally proposed, both deliberate:

1. The data layer lives in `indiacore/marketdata/`, not `indiacore/data/`.
   `indiacore/data/` already exists and holds the verified NSE holiday JSON
   that `calendar_in.DATA_DIR` points at and that `tests/test_repo.py` checks
   for provenance. Turning a verified data directory into a code package
   would have mixed the two.
2. `transport.py` sits in `providers/` rather than at the package root, since
   nothing outside the provider layer makes a request.

Smallest useful example:

```python
from indiacore import symbols
from indiacore.data_service import MarketDataService

service = MarketDataService()
result = service.get_quote(symbols.nse("SBIN"))

if result.usable:
    quote = result.first
    print(quote.last_price.format(), result.result.freshness)
    if not result.is_current_price:
        print("this is not a live price:", result.notes[0])
else:
    print(result.outcome, result.errors)
```

`result.outcome` is one of eleven values, and they are the point of the API:

| Outcome | Meaning |
| --- | --- |
| `fresh` | fetched now from a provider that delivers real time. Unreachable today: no provider here promises it |
| `delayed` | fetched now, provider promises no real time delivery |
| `end_of_day` | the last completed session's figures. Correct, and not a current price |
| `historical` | an earlier session's figures. Correct, and not a current price |
| `cached` | served from cache, with `cache_age_seconds` attached |
| `empty_valid` | the provider answered properly and the range holds no session. Not a failure |
| `provider_error` | every provider failed. Not an empty result |
| `invalid_data` | something arrived and validation refused it |
| `stale` | the figures are real and too old for what was asked |
| `unknown_age` | figures arrived and their age could not be established |
| `unsupported` | nothing registered does this |

## Providers and what they actually do

Every claim below was checked against the live endpoint on 13 November 2026,
one request at a time. `indiacore/providers/live_probe.py` re-runs those
checks on demand.

### yahoo (unofficial, operational)

`query1.finance.yahoo.com/v8/finance/chart/` and the identical `query2` host.

- Verified answering 200 for `SBIN.NS`, `M%26M.NS`, `INFY.BO`, `%5ENSEI` and
  `NIFTYBEES.NS`, all in INR.
- `ZZZZNOPE.NS` answers 404 with
  `{"chart":{"result":null,"error":{"code":"Not Found", ...}}}`, so a bad
  symbol is a clean failure and never an empty success.
- `500112.BO`, a BSE scrip code, answers 404. Phase 2 refuses to send one, so
  this provider fails locally before making the request.
- Quotes, daily bars and intraday bars at 1m, 5m, 15m, 30m and 60m.
- **Delay: undeclared.** The chart meta carries `currency`,
  `regularMarketTime`, `gmtoffset` and `exchangeTimezoneName`, and no delay
  field of any kind. There is no published service level and no terms
  covering programmatic use of this path. Therefore `official=False`,
  `realtime=False`, and the freshness engine can never classify anything from
  here as `live`. Its ceiling is `delayed`.
- Rate limit: not published. The provider is spaced at one request per second
  with a burst of two, by courtesy rather than by a documented number.
- Cost: none. Authentication: none.
- Known limitation: a 5 day daily request for `INFY.BO` returned **one** bar
  where `INFY.NS` returned five, so Yahoo's BSE history is thinner than its
  NSE history. With one bar there is no preceding bar, so the previous close
  comes back as `None` and validation reports `previous_close_missing` rather
  than inventing one.

### nse_bhavcopy (official, operational)

`nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv.zip`

- The exchange's own unified end of day file for one completed session, on
  the same archive host the phase 2 holiday circular came from.
- Verified for 2026-11-12: HTTP 200, `application/zip`, 208,828 bytes, one
  CSV of 3,728 rows. The SBIN row reads close 1086.50 on volume 26,973,410.
- Daily bars only, and **never a quote**: the file does not exist until the
  session is over, so the provider refuses any request whose end date is
  after the last completed session.
- Published unadjusted, so splits and bonus issues appear as price gaps. Every
  bar is marked `unadjusted`.
- Covers the whole cash market in one request, which makes it cheap for a
  market wide snapshot of one date and expensive for one instrument over many
  sessions (that would be one download per session). `get_session(day)`
  exposes the shape it is good at.
- Cost: none. Authentication: none.

### bse_bhavcopy (official, operational)

`www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_<YYYYMMDD>_F_0000.CSV`

- BSE's own end of day file, identical column format to the NSE one, served
  as a plain CSV rather than a zip.
- Verified for 2026-11-12: HTTP 200, 888,444 bytes, 5,193 rows. The SBIN row
  reads close 1085.00 on volume 1,590,668.
- Put beside the NSE file for the same session, that is the whole case for
  modelling two exchanges: same company, same day, close 1086.50 against
  1085.00 and volumes an order of magnitude apart. Nothing in this package
  averages them or substitutes one for the other.
- Carries `FinInstrmId`, which is the BSE scrip code (500112 for SBIN), so a
  Symbol built from a BSE row carries a scrip code the exchange published
  rather than a mapping this system invented.
- Daily bars only, never a quote. Published unadjusted.

### nse_quote_api and bse_quote_api (official, NOT operational)

Registered so the matrix lists them, and they serve nothing.

- `www.nseindia.com/api/quote-equity?symbol=SBIN` returned **HTTP 403
  Forbidden** to a plain request. It is an internal endpoint of the website,
  not a published interface.
- `api.bseindia.com/BseIndiaAPI/api/StockReachGraph/w` returned **HTTP 403
  Forbidden** likewise.
- Both respond only if browser cookies and headers are primed and replayed.
  That is circumventing an access control on someone else's infrastructure,
  so these adapters refuse instead. Every data method raises
  `ProviderUnavailable` carrying the evidence above.
- `nseindia.com/robots.txt` allows crawling broadly and the endpoint is still
  protected. `bseindia.com/robots.txt` serves an HTML page rather than a
  robots policy, so there is no machine readable permission for that host at
  all.
- A licensed NSE or BSE feed would be a different provider with its own
  credentials, read from the environment. See [Configuration](#configuration).

### Sources evaluated and rejected

- **Stooq**: no NSE coverage found. `stooq.com/q/l/?s=sbin.in` answers 404,
  and the daily CSV path answers a JavaScript browser verification challenge
  rather than data. Not usable programmatically, so not implemented.
- **Legacy BSE bhavcopy** (`/download/BhavCopy/Equity/EQ<DDMMYY>_CSV.ZIP`):
  answers the website's HTML page, not a file. The unified path above is the
  live one.

## Capability matrix

Implemented and verified, against planned and unsupported. `registry.matrix()`
returns this at runtime, so it cannot drift from the code without the code
changing.

| Provider | Official | Operational | Quotes | Daily bars | Intraday | Index | Exchanges | Real time | Auth |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `yahoo` | no | **yes** | yes | yes | 1m to 60m | yes | NSE, BSE | no (undeclared delay) | none |
| `nse_bhavcopy` | **yes** | **yes** | no | yes | no | no | NSE | no (end of day only) | none |
| `bse_bhavcopy` | **yes** | **yes** | no | yes | no | no | BSE | no (end of day only) | none |
| `nse_quote_api` | yes | **no** | no | no | no | no | NSE | no | would need licensing |
| `bse_quote_api` | yes | **no** | no | no | no | no | BSE | no | would need licensing |

Data types with no verified source today:

| Want | State |
| --- | --- |
| Real time quotes | **Unsupported.** No provider here promises real time delivery. `fresh` is unreachable by design, not by accident |
| ETF classification | **Unsupported.** Yahoo answers `instrumentType: EQUITY` for `NIFTYBEES.NS` and `GOLDBEES.NS`, and the bhavcopy carries only `FinInstrmTp: STK`. ETFs are quotable and their type is reported `equity` or `unknown`, never guessed from the ticker |
| Official index levels | **Partial.** Index levels come from Yahoo only, which is unofficial. The exchanges publish indices through the endpoints that answer 403 |
| Split and bonus adjusted history | **Partial.** Yahoo serves an `adjclose` array, which is carried on the bar; both bhavcopies are unadjusted. Validation flags a corporate action sized gap rather than silently adjusting |
| Corporate actions, delivery volumes, bulk deals | **Not in phase 3.** Out of scope |

## Data models

Every record carries its own provenance, and prices are phase 2 `Money`, so
an INR figure cannot be added to a dollar and float noise is gone: Yahoo
sends `1077.949951171875` for a close the exchange published as `1077.95`.

**Quote**: canonical symbol, exchange, instrument type, last price, previous
close (and whether it was derived), open, high, low, volume, currency, market
status, provenance, quality, notes.

The previous close is taken from the bar before the newest in the series, not
from `meta.chartPreviousClose`, which is the close before the start of the
requested range and only coincides with the previous session's close when the
range begins there. With fewer than two bars it stays `None` and validation
reports it missing.

**Bar**: symbol, exchange, interval, start (aware IST), session date, open,
high, low, close, volume, adjusted close, currency, adjustment status,
provenance, quality.

**Provenance**: provider, source, official, retrieved at, source time,
freshness, freshness reason, cached, cache age.

**ProviderStatus**: provider, available, reason, last success, last failure,
failure reason, rate limited, retry after, request and failure counts,
capabilities. Built from what this process observed, and never makes a
request of its own.

Timestamps are aware IST datetimes. A naive datetime is refused, following
phase 2's `calendar_in.now_ist`.

## Validation

Five verdicts, worst wins: `valid`, `warning`, `incomplete`, `stale`,
`invalid`. Each carries findings with a code, a severity, a message and the
field.

`invalid` is reserved for the structurally impossible, because that is the
only class of problem the market cannot explain:

- a non positive price
- high below low
- high below open or close, low above open or close
- a last price outside the day's high and low
- negative volume
- a currency that is not INR, or an unknown exchange
- an implausible timestamp
- one series mixing symbols, intervals or providers

`warning` is for real but notable, and nothing here is discarded:

- a move of 20 percent or more, the size of the widest NSE circuit band,
  reported as `circuit_sized_move`
- beyond 25 percent, `extreme_move`
- beyond 45 percent, `possible_corporate_action`, because in an unadjusted
  series that is usually a split or a bonus issue rather than bad data
- zero volume, which is normal for an illiquid scrip, and
  `zero_volume_with_movement` when the price moved anyway
- a high more than 40 percent above the low in one bar
- a bar dated to a day the exchange calendar says was shut

`incomplete` is for a right shape with something absent: a missing volume or
previous close, bars dropped for null prices, unordered bars, trading days in
the range with no bar.

A record whose verdict is `invalid` is never returned as data and never
cached. The service reports `invalid_data` with the reasons, and tries the
next provider if there is one.

Thresholds are configurable:

```python
from indiacore.marketdata.validation import Thresholds
service = MarketDataService(thresholds=Thresholds(circuit_pct=10.0))
```

## Freshness

Decided from four things together, never from age alone: the source
timestamp, the market state from phase 2's `Market`, the interval, and what
the provider actually promises.

| Freshness | When |
| --- | --- |
| `live` | only when `capabilities.realtime` is true. No provider here sets it |
| `delayed` | market open, figure within tolerance (900s by default), provider promises no real time delivery |
| `end_of_day` | market shut and the figure belongs to the last completed session |
| `historical` | the figure belongs to an earlier session, within `max_eod_sessions_behind` |
| `stale` | the age is known and too great for what was asked |
| `unknown` | the age cannot be established, usually no source timestamp |

The same age means different things at different times, which is why the
market state is part of the rule. At 09:00 on a Friday, yesterday's close is
the newest figure that exists and is good `end_of_day` data. At 11:00 with
the market open, the same figure is `stale` and must not be shown as a quote.

`result.is_current_price` is true only for `live` and `delayed`.
`end_of_day` and `historical` are both perfectly usable data and both false
there. That distinction is the point.

Tolerances are configurable:

```python
from indiacore.marketdata.quality import FreshnessRules
service = MarketDataService(rules=FreshnessRules(
    max_quote_age_open_seconds=300,
    intraday_bar_stale_multiple=2.0,
    max_eod_sessions_behind=3))
```

## Caching

A file cache, standard library only, off the repository tree.

- **Keys** are deterministic: a readable prefix plus a sha256 of provider,
  kind, exchange, ticker, interval and date range. The exchange is in the key
  and in the directory path, so an NSE and a BSE entry for the same ticker
  cannot collide.
- **Separate subtrees** per provider, then `quotes` or `bars`, then exchange.
- **TTL** per kind: 60s for a quote, 300s for intraday bars, 86400s for daily
  bars, 7 days for a bhavcopy. Override with `Cache(ttl={...})`.
- **Atomic writes**: the envelope goes to a temporary file in the same
  directory, is fsynced, and is moved into place with `os.replace`. A process
  killed mid-write leaves the old entry or the new one, never half of either.
- **Corruption detection**: every envelope carries a sha256 of its own
  payload. A file that will not parse, does not match its checksum, is the
  wrong envelope version, or describes a different provider, kind or exchange
  is treated as a **miss**, quarantined to `<name>.corrupt`, and the reason
  recorded on `cache.corruptions`. A bad cache file never raises into the
  data path and never takes down a request that a provider could still serve.
- **Failures are never cached.** There is no code path that writes an
  envelope for an error, a timeout or an empty failure, so a dead feed cannot
  warm the cache and look like data an hour later. `put(None)` raises.
- **A cache hit is labelled.** The outcome is `cached`, with
  `cache_age_seconds`, and its freshness is **recomputed** from the stored
  source timestamps rather than trusted from the envelope. An entry written
  while the market was open is not still a quote an hour later.
- **A stale entry never silently replaces a failed request.** On provider
  failure the default is to report the failure. `allow_stale_cache=True`
  opts in, and then returns `cached` with freshness `stale` and a note saying
  every provider failed.

Location: `INDIACORE_CACHE_DIR` if set, otherwise a directory under the
system temporary directory. It is created on first write, not on import, so
nothing appears until something is cached, and it is outside version control.
No credential is ever part of a key or an envelope.

Turn it off entirely with `MarketDataService(cache=Cache(enabled=False))`.

## Rate limiting and retries

Both the clock and the sleep are injected, which is what lets the whole suite
test the delay arithmetic without waiting for it.

`RateLimiter` spaces normal traffic with a token bucket (`min_interval`,
`burst`), and handles being told off separately: `note_rate_limited()` puts
the whole provider in a cooldown until a deadline, obeying `Retry-After` when
it is supplied and defaulting to 60 seconds when it is not. While a cooldown
is in force, `acquire()` **refuses** rather than queueing, so a rate limited
provider becomes a fast failure the service can fall back from instead of a
parked thread.

`RetryPolicy` classifies by HTTP status:

| Class | Statuses | Retried |
| --- | --- | --- |
| `rate_limited` | 429 | yes, after putting the provider in cooldown |
| `retryable` | 408, 425, 500, 502, 503, 504, 507, 509, timeouts, connection errors | yes, exponential backoff with jitter |
| `not_found` | 404, 410 | **no.** A bad symbol is the answer, not a transient fault |
| `fatal` | 400, 401, 403, 405, 409, 422 and similar | **no.** Retrying cannot fix a wrong request, and hammering a 403 is how an unofficial endpoint gets blocked harder |

Retries cannot corrupt the cache: a write only happens after a successful
fetch has been normalised and validated, and the write itself is atomic.

## Fallback

`registry.candidates()` ranks providers that declare the request, official
sources first. The service tries them in order and records every attempt.

- A fallback result's rows are **one provider's alone**. Two providers' prices
  are never combined into one record, and `validate_series` fails a series
  that mixes providers.
- `fallback_used` and `fallback_from` say what happened, and a note spells it
  out in the result.
- Data that failed validation is not an answer: the service moves to the next
  provider rather than returning it.
- `provider="yahoo"` forces one provider and disables fallback, for debugging
  and for tests.

## Configuration

Nothing in phase 3 needs a credential, because no implemented provider has
authentication. The shape for one that does:

```bash
export INDIACORE_CACHE_DIR=/var/tmp/indiacore-cache
# For a future licensed feed. Never committed, never logged, never a cache key.
export INDIACORE_VENDOR_API_KEY=...
```

Credentials are read from the environment only. No key is written to a cache
envelope, a URL in an error message, or a retry history: `transport` strips
the query string from any URL it puts in an exception, never copies request
headers into an error, and `retry._safe()` withholds a message that mentions
an authorization header or a token.

See `indiacore/config.india.example.json` for the INR configuration profile
from phase 2, which carries no secrets.

## Running the tests

The mandatory suite is entirely offline and deterministic:

```bash
python -m pytest -q
```

Fixtures are real recorded payloads in `tests/fixtures/indiacore/`: Yahoo
chart JSON for NSE, BSE, an index and an ETF, the real 404 envelope, a real
empty range, a trimmed intraday series, and excerpts of both exchanges' real
bhavcopy files for 2026-11-12. Providers never reach the network in a test,
because every provider takes a transport and the suite passes a
`FixtureTransport` that reads a file. A URL that matches no fixture raises,
so a test cannot pass by accident when a provider changes the URL it builds.

The optional live checks are separate and assert nothing:

```bash
python -m indiacore.providers.live_probe
```

It verifies the claims this document makes: that an NSE quote arrives in INR,
that an ampersand ticker survives encoding, that an index is typed as one,
that NSE and BSE are different instruments, that an unknown symbol 404s, that
a scrip code is refused locally, that both bhavcopy files exist for the last
completed session, and that both exchange quote APIs still refuse a plain
request. Run it when a provider starts failing, before changing any parsing
code, so a change in someone else's endpoint is not diagnosed as a bug in
this one.

## Known limitations

1. **No real time data.** Nothing here is a real time feed, and `fresh` is
   unreachable with the providers implemented. A licensed feed would set
   `realtime=True` on its capabilities, and the freshness engine already has
   the branch for it.
2. **No official intraday source.** Intraday bars come from Yahoo only.
3. **Yahoo is unofficial and may stop answering.** It is an undocumented
   endpoint with no service level. The layer is built so that is survivable:
   two official end of day providers, declared capabilities, and a probe that
   tells you what broke.
4. **ETFs are not classified.** See the matrix above.
5. **BSE history on Yahoo is thin.** A 5 day daily request for `INFY.BO`
   returned one bar where `INFY.NS` returned five.
6. **The BSE calendar is borrowed.** Phase 2's `bse_market()` uses the NSE
   holiday list and says so. The equity dates have matched in recent years
   but are separate circulars, and this layer inherits that caveat.
7. **Holiday data stops at 2026.** `calendar_in` raises
   `CalendarDataUnavailable` for an unverified year rather than guessing, so
   a 2027 date range will raise until NSE publishes the circular. Freshness
   and validation degrade safely: an unavailable calendar becomes an unknown
   market state, not an assumption that the market is open.
8. **A bhavcopy is one session per request.** Fine for a market wide
   snapshot, expensive for one instrument over many sessions.
9. **No skill consumes this yet.** Phase 3 does not re-point the eleven
   trading skills. They still run their own US calendars and their own HTTP
   requests.
