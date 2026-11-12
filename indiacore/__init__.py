"""indiacore: the India-first foundation for the trader skills.

Phase 2 of the India migration. This package holds the market primitives the
eleven skills will be re-pointed at in a later phase: the trading calendar,
the NSE/BSE symbol encoding, INR money, the market session model and the
data-provenance guard.

Nothing in this package reaches the network and nothing here changes how the
existing skills behave. The skills still run their own US calendars until the
re-pointing phase.

Standard library only, Python 3.8 compatible, to match the rest of the repo.
"""

__version__ = "0.1.0"

PHASE = 2

__all__ = [
    "calendar_in",
    "config_in",
    "market",
    "money",
    "provenance",
    "symbols",
]
