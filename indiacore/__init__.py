"""indiacore: the India-first foundation for the trader skills.

Phase 2 built the market primitives: the trading calendar, the NSE and BSE
symbol encoding, INR money, the market session model and the data provenance
guard.

Phase 3 builds the market data layer on top of them, so that a future
research, portfolio, scanner or decision module asks one object for a price
instead of writing its own HTTP request:

    data_service   MarketDataService, the one entry point
    providers      Yahoo, the NSE and BSE bhavcopy files, the registry, and
                   the two exchange quote APIs that could not be used
    marketdata     the normalised models, validation, freshness and the cache
    rate_limit     request spacing and rate limit cooldowns
    retry          what is worth retrying, and what is just the answer

The rule the data layer is built around: a missing or stale quote is never
presented as a current market price. Every record carries where it came from
and how old it is. See DATA.md for the provider capability matrix, the
freshness rules and the known limitations.

The eleven skills are still not re-pointed at any of this. They run their own
US calendars and their own requests until the re-pointing phase, and nothing
in this package changes how they behave.

Standard library only, Python 3.8 compatible, to match the rest of the repo.
Only providers/transport.py and providers/live_probe.py touch the network.
"""

__version__ = "0.2.0"

PHASE = 3

__all__ = [
    # phase 2
    "calendar_in",
    "config_in",
    "market",
    "money",
    "provenance",
    "symbols",
    # phase 3
    "data_service",
    "marketdata",
    "providers",
    "rate_limit",
    "retry",
]
