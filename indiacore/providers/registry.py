"""Which providers exist, which one to ask, and what each one admits to.

Selection is explicit about its preference rather than clever. For end of day
data the exchange's own file is asked first, because it is the exchange's own
number; for anything the exchanges do not publish, Yahoo is asked, with its
unofficial status attached to every record it serves. A provider that is not
operational is never selected for data, and is still listed by `matrix()`, so
"we evaluated this source and could not use it" is a visible state rather
than an absence.

Nothing here is a singleton. A registry is built and passed in, so a test
builds one with a fixture transport and the real code path runs unchanged.
"""

from typing import Any, Dict, List, Optional

from ..marketdata import models as m
from . import base


class RegistryError(Exception):
    """A provider was asked for that is not registered."""


class Registry(object):
    """A named set of providers, in preference order per data kind."""

    def __init__(self):
        self._providers = {}  # type: Dict[str, Any]
        self._order = []      # type: List[str]

    # ------------------------------------------------------------ building

    def add(self, provider, prefer=None):
        # type: (Any, Optional[int]) -> Any
        if provider.name in self._providers:
            raise RegistryError("%s is already registered" % provider.name)
        self._providers[provider.name] = provider
        if prefer is None:
            self._order.append(provider.name)
        else:
            self._order.insert(prefer, provider.name)
        return provider

    def get(self, name):
        # type: (str) -> Any
        if name not in self._providers:
            raise RegistryError(
                "no provider named %r. Registered: %s"
                % (name, ", ".join(self._order) or "none"))
        return self._providers[name]

    def names(self):
        # type: () -> List[str]
        return list(self._order)

    def all(self):
        # type: () -> List[Any]
        return [self._providers[n] for n in self._order]

    def __contains__(self, name):
        return name in self._providers

    def __len__(self):
        return len(self._providers)

    # ----------------------------------------------------------- selecting

    def candidates(self, kind, exchange=None, interval=None,
                   official_first=True):
        # type: (str, Any, Optional[str], bool) -> List[Any]
        """Every provider that declares this, best first.

        Official sources come first for end of day work. Ordering is stable
        within each group, so the same request always tries the same provider
        first and a fallback is reproducible.
        """
        able = [p for p in self.all()
                if p.capabilities.supports(kind, exchange, interval)]
        if official_first:
            able.sort(key=lambda p: (not p.capabilities.official,
                                     self._order.index(p.name)))
        return able

    def choose(self, kind, exchange=None, interval=None, name=None,
               official_first=True):
        # type: (str, Any, Optional[str], Optional[str], bool) -> Any
        """One provider. `name` forces a choice, for debugging and tests."""
        if name is not None:
            provider = self.get(name)
            if not provider.capabilities.supports(kind, exchange, interval):
                raise base.UnsupportedRequest(
                    "%s was asked for by name and does not serve %s%s%s"
                    % (name, kind,
                       " on " + str(getattr(exchange, "code", exchange))
                       if exchange is not None else "",
                       " at " + interval if interval else ""))
            return provider
        able = self.candidates(kind, exchange, interval, official_first)
        if not able:
            raise base.UnsupportedRequest(
                "no registered provider serves %s%s%s. Registered: %s"
                % (kind,
                   " on " + str(getattr(exchange, "code", exchange))
                   if exchange is not None else "",
                   " at " + interval if interval else "",
                   ", ".join(self._order) or "none"))
        return able[0]

    # ------------------------------------------------------------ reporting

    def matrix(self):
        # type: () -> List[dict]
        """The capability matrix, one row per provider, including the dead ones."""
        rows = []
        for provider in self.all():
            caps = provider.capabilities
            rows.append({
                "provider": provider.name,
                "official": caps.official,
                "operational": caps.operational,
                "realtime": caps.realtime,
                "exchanges": list(caps.exchanges),
                "kinds": list(caps.kinds),
                "intervals": list(caps.intervals),
                "auth_required": caps.auth_required,
                "declared_delay_seconds": caps.declared_delay_seconds,
                "terms_url": caps.terms_url,
                "notes": caps.notes,
            })
        return rows

    def statuses(self):
        # type: () -> List[dict]
        return [p.get_provider_status().to_dict() for p in self.all()]

    def __repr__(self):
        return "Registry(%s)" % ", ".join(self._order)


def default_registry(transport=None, clock=None, market=None, rules=None,
                     include_unsupported=True, limiter_factory=None,
                     policy=None):
    # type: (Any, Any, Any, Any, bool, Any, Any) -> Registry
    """The providers this phase verified, plus the ones it could not use.

    `transport` is passed to every network provider, which is how the whole
    suite runs offline: one FixtureTransport reaches all three.

    `limiter_factory` is called with a provider name and returns that
    provider's RateLimiter; `policy` replaces every retry policy. Both exist
    so a test can supply a limiter that does not space requests and a policy
    that does not sleep. Left out, each provider keeps the limiter and policy
    it declares for real use.
    """
    from . import bse as bse_mod, nse as nse_mod, yahoo as yahoo_mod

    def limiter_for(name):
        return limiter_factory(name) if limiter_factory else None

    reg = Registry()
    reg.add(nse_mod.NseBhavcopyProvider(transport=transport, clock=clock,
                                        rules=rules,
                                        limiter=limiter_for("nse_bhavcopy"),
                                        policy=policy))
    reg.add(bse_mod.BseBhavcopyProvider(transport=transport, clock=clock,
                                        rules=rules,
                                        limiter=limiter_for("bse_bhavcopy"),
                                        policy=policy))
    reg.add(yahoo_mod.YahooProvider(transport=transport, clock=clock,
                                    market=market, rules=rules,
                                    limiter=limiter_for("yahoo"),
                                    policy=policy))
    if include_unsupported:
        reg.add(nse_mod.NseQuoteApiProvider(clock=clock))
        reg.add(bse_mod.BseQuoteApiProvider(clock=clock))
    return reg
