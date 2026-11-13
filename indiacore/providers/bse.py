"""BSE, the same split as NSE: a working archive and a refused API.

BseBhavcopyProvider is operational and official. BSE publishes its own
unified bhavcopy for each completed session, in the identical column format
NSE uses, as a plain CSV rather than a zip. Verified on 13 November 2026: the
file for 2026-11-12 answered 200, 888,444 bytes, 5,193 rows, and the SBIN row
reads a close of 1085.00 on a volume of 1,590,668.

Put that beside the NSE file for the same session, where SBIN closed at
1086.50 on 26,973,410 shares, and the reason phase 2 modelled the two
exchanges separately is visible in one line: same company, same day, two
different prices and volumes an order of magnitude apart. Nothing in this
package averages them, substitutes one for the other, or treats a BSE close
as a fallback for a missing NSE close.

The BSE file carries one thing the NSE file does not: FinInstrmId is the BSE
scrip code, 500112 for SBIN, published by the exchange next to the ticker. So
a Symbol built from a BSE bhavcopy row carries a verified scrip code rather
than a mapping this system invented. Phase 2 already refuses to send a scrip
code to Yahoo, which answers 404 for 500112.BO.

BseQuoteApiProvider is not operational, for the same reason as its NSE twin:
api.bseindia.com answers HTTP 403 to a plain request, is undocumented, and
needs browser headers replayed to respond. Also recorded: bseindia.com serves
an HTML page rather than a robots policy at /robots.txt, so there is no
machine readable crawl permission to rely on, which is another reason to
leave it alone.
"""

from typing import Any

from .. import market as mkt
from ..marketdata import models as m
from ..symbols import BSE
from . import base, nse

HOST = "www.bseindia.com"
API_HOST = "api.bseindia.com"

BHAVCOPY_URL = ("https://" + HOST
                + "/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_%s_F_0000.CSV")

TERMS = "https://www.bseindia.com/static/about/Terms_of_Usage.html"


def bhavcopy_capabilities():
    # type: () -> m.Capabilities
    return m.Capabilities(
        provider="bse_bhavcopy",
        exchanges=("BSE",),
        instrument_types=(m.EQUITY, m.UNKNOWN_TYPE),
        kinds=(m.BARS,),
        intervals=(m.DAY_1,),
        official=True,
        operational=True,
        realtime=False,
        declared_delay_seconds=None,
        max_history_days=None,
        auth_required=False,
        terms_url=TERMS,
        notes=("Official. BSE's own published end of day file for a "
               "completed session, in the same unified format as the NSE "
               "one but served as a plain CSV. Daily bars only, never a "
               "quote. Carries the BSE scrip code alongside the ticker. "
               "Published unadjusted."))


def quote_api_capabilities():
    # type: () -> m.Capabilities
    return m.Capabilities(
        provider="bse_quote_api",
        exchanges=("BSE",),
        instrument_types=(m.EQUITY, m.INDEX),
        kinds=(),
        intervals=(),
        official=True,
        operational=False,
        realtime=False,
        auth_required=True,
        terms_url=TERMS,
        notes=("Not operational. api.bseindia.com is an internal endpoint of "
               "the BSE website, undocumented, and answers HTTP 403 to a "
               "plain request. It responds only when browser headers and a "
               "Referer are replayed, which this system will not do. A "
               "licensed BSE feed would be a separate provider with its own "
               "credentials."))


QUOTE_API_EVIDENCE = (
    "Verified 13 November 2026: GET https://api.bseindia.com/BseIndiaAPI/api/"
    "StockReachGraph/w returned HTTP 403 Forbidden without browser headers. "
    "bseindia.com/robots.txt serves an HTML page rather than a robots policy, "
    "so there is no published crawl permission for this host either.")


class BseQuoteApiProvider(base.NotOperationalProvider):
    """Registered to be listed as unsupported. Serves nothing."""

    name = "bse_quote_api"

    def __init__(self, clock=None):
        # type: (Any) -> None
        base.NotOperationalProvider.__init__(
            self, quote_api_capabilities(), QUOTE_API_EVIDENCE, clock=clock)


class BseBhavcopyProvider(nse.BhavcopyProvider):
    """BSE's own published end of day file. Official, daily bars only."""

    name = "bse_bhavcopy"
    exchange = BSE
    url_template = BHAVCOPY_URL
    zipped = False

    def __init__(self, **kw):
        kw.setdefault("hosts", (HOST,))
        nse.BhavcopyProvider.__init__(self, **kw)

    def build_capabilities(self):
        # type: () -> m.Capabilities
        return bhavcopy_capabilities()

    def build_market(self):
        # type: () -> Any
        # Phase 2's bse_market borrows the NSE holiday list and says so. The
        # equity dates have matched in recent years but are separate
        # circulars, so this provider inherits that caveat rather than
        # claiming the lists were reconciled.
        return mkt.bse_market()
