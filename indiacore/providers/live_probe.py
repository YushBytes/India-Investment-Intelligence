"""Optional live checks, deliberately separate from the test suite.

Nothing in the mandatory suite touches the network, because a test that fails
when Yahoo rate limits or when NSE moves a file is not a test of this code.
This module is the other half of that bargain: a way to ask the real
endpoints whether the facts the providers were written against still hold.

    python -m indiacore.providers.live_probe

It makes a handful of GETs, one symbol at a time, through the same transport
and rate limiter the providers use, and prints a line per check. It asserts
nothing and writes nothing. Use it when a provider starts failing, before
changing any parsing code, so that a change in someone else's endpoint is not
diagnosed as a bug in this one.

The checks are the claims the code depends on:

    a quote for an NSE symbol arrives and is INR
    an ampersand ticker survives percent encoding
    an index answers and is typed INDEX
    a BSE symbol answers and is a different instrument from its NSE twin
    an unknown symbol 404s rather than returning an empty success
    a BSE scrip code is refused locally, before a request is made
    the NSE and BSE bhavcopy files for the last completed session exist
    the two exchange quote APIs still refuse a plain request
"""

import datetime
import sys
from typing import Any, List

from .. import market as mkt
from ..marketdata import models as m
from ..symbols import IndexSymbol, SymbolError, bse, nse
from . import base, bse as bse_mod, nse as nse_mod, transport as tr, yahoo


def _line(name, ok, detail):
    # type: (str, Any, str) -> dict
    mark = "ok  " if ok else ("skip" if ok is None else "FAIL")
    print("%s %-34s %s" % (mark, name, detail))
    return {"check": name, "ok": ok, "detail": detail}


def probe(verbose=True):
    # type: (bool) -> List[dict]
    """Run every check. Returns a list of results, one per check."""
    out = []
    y = yahoo.YahooProvider()

    try:
        quote = y.get_quote(nse("SBIN"))
        out.append(_line("yahoo quote SBIN.NS", True,
                         "%s %s, freshness %s"
                         % (quote.last_price.format(), quote.currency,
                            quote.provenance.freshness)))
    except Exception as exc:
        out.append(_line("yahoo quote SBIN.NS", False, "%s: %s"
                         % (type(exc).__name__, exc)))

    try:
        quote = y.get_quote(nse("M&M"))
        out.append(_line("yahoo quote M&M.NS (encoding)", True,
                         "%s, url %s" % (quote.last_price.format(),
                                         nse("M&M").yahoo_url())))
    except Exception as exc:
        out.append(_line("yahoo quote M&M.NS (encoding)", False,
                         "%s: %s" % (type(exc).__name__, exc)))

    try:
        level = y.get_index_quote(IndexSymbol("^NSEI"))
        out.append(_line("yahoo index ^NSEI", level.instrument_type == m.INDEX,
                         "%s, typed %s" % (level.last_price.format(),
                                           level.instrument_type)))
    except Exception as exc:
        out.append(_line("yahoo index ^NSEI", False, "%s: %s"
                         % (type(exc).__name__, exc)))

    try:
        on_nse = y.get_quote(nse("INFY")).last_price
        on_bse = y.get_quote(bse("INFY")).last_price
        out.append(_line("NSE and BSE are separate", True,
                         "INFY.NS %s, INFY.BO %s%s"
                         % (on_nse.format(), on_bse.format(),
                            ", identical today" if on_nse == on_bse else "")))
    except Exception as exc:
        out.append(_line("NSE and BSE are separate", False, "%s: %s"
                         % (type(exc).__name__, exc)))

    try:
        y.get_quote(nse("ZZZZNOPE"))
        out.append(_line("unknown symbol 404s", False,
                         "an unknown symbol answered successfully, which "
                         "means a bad symbol can now look like data"))
    except base.SymbolNotFound as exc:
        out.append(_line("unknown symbol 404s", True, str(exc)[:70]))
    except Exception as exc:
        out.append(_line("unknown symbol 404s", False, "%s: %s"
                         % (type(exc).__name__, exc)))

    try:
        bse("500112", scrip_code=None).yahoo_url()
        out.append(_line("scrip code refused locally", False,
                         "a BSE scrip code was accepted as a Yahoo symbol"))
    except SymbolError:
        out.append(_line("scrip code refused locally", True,
                         "500112.BO is refused before any request is made"))

    market = mkt.nse_market()
    try:
        session = market.last_completed_session(
            datetime.datetime.now(datetime.timezone.utc))
    except Exception as exc:
        session = None
        out.append(_line("last completed session", False, str(exc)[:90]))

    if session is not None:
        for label, provider in (("NSE bhavcopy", nse_mod.NseBhavcopyProvider()),
                                ("BSE bhavcopy", bse_mod.BseBhavcopyProvider())):
            try:
                text = provider.fetch_csv(session)
                rows = text.count("\n")
                out.append(_line("%s for %s" % (label, session), True,
                                 "%d rows, %d bytes" % (rows, len(text))))
            except Exception as exc:
                out.append(_line("%s for %s" % (label, session), False,
                                 "%s: %s" % (type(exc).__name__, exc)))

    # The two refusals. A pass here means the endpoint is still closed, which
    # is the state the capability matrix claims.
    for label, host, path in (
            ("NSE quote api still 403", nse_mod.WEBSITE_HOST,
             "/api/quote-equity?symbol=SBIN"),
            ("BSE quote api still 403", bse_mod.API_HOST,
             "/BseIndiaAPI/api/StockReachGraph/w")):
        transport = tr.HttpTransport(allowed_hosts=(host,), timeout=20.0)
        try:
            transport.get_bytes("https://%s%s" % (host, path))
            out.append(_line(label, None,
                             "the endpoint answered a plain request. It is "
                             "still undocumented, so the adapter stays "
                             "unsupported until access is licensed"))
        except tr.HttpError as exc:
            out.append(_line(label, exc.status in (401, 403),
                             "HTTP %s, as recorded" % exc.status))
        except Exception as exc:
            out.append(_line(label, None, "%s: %s" % (type(exc).__name__, exc)))

    failures = [r for r in out if r["ok"] is False]
    print("\n%d check(s), %d failed, %d skipped"
          % (len(out), len(failures),
             len([r for r in out if r["ok"] is None])))
    return out


if __name__ == "__main__":
    results = probe()
    sys.exit(1 if any(r["ok"] is False for r in results) else 0)
