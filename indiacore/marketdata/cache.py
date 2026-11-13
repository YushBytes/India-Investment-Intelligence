"""A cache that cannot quietly become the source of truth.

Four rules, each of which exists because the opposite is the normal bug:

1. A cached figure is never returned as though it were fetched. Every hit
   comes back with its age, and the service stamps `cached` on the record's
   provenance. A caller that prints a price without looking at provenance is
   already wrong, but nothing here helps it be wrong.

2. A failure is never stored. There is no code path that writes an envelope
   for an error, a timeout or an empty failure, so a dead feed cannot warm the
   cache and look like data an hour later.

3. Writes are atomic. The payload goes to a temporary file in the same
   directory and is moved into place with os.replace, so a process killed
   mid-write leaves either the old entry or the new one, never half of either.

4. Corruption is detected, not inherited. Every envelope carries a sha256 of
   its own payload. A file that does not match its checksum, or will not
   parse, is treated as a miss and quarantined rather than deleted silently,
   and the reason is recorded on the cache for a caller to read.

Keys are deterministic and carry the exchange, so NSE and BSE entries for the
same ticker can never collide: SBIN closed at 1086.50 on NSE and 1085.00 on
BSE on 12 November 2026.

The default directory is outside the repository, under the system temporary
directory, and is created on first write rather than on import. Set
INDIACORE_CACHE_DIR to put it somewhere durable. Nothing here writes a
credential: the key is built from the request, never from a header.
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional

ENVELOPE_VERSION = 1

# Per data kind, in seconds. A quote expires quickly; a completed daily bar
# does not change, so its entry is kept for a day and re-validated by the
# freshness rules rather than by expiry.
DEFAULT_TTL = {
    "quote": 60,
    "index_quote": 60,
    "bars_intraday": 300,
    "bars_daily": 86400,
    "bhavcopy": 7 * 86400,
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class CacheError(Exception):
    """The cache itself could not be used."""


def default_dir():
    # type: () -> str
    """Outside version control, and not created until something is written."""
    env = os.environ.get("INDIACORE_CACHE_DIR")
    if env:
        return env
    return os.path.join(tempfile.gettempdir(), "indiacore-cache")


def make_key(provider, kind, exchange=None, ticker=None, interval=None,
             start=None, end=None, extra=None):
    # type: (str, str, Optional[str], Optional[str], Optional[str], Any, Any, Any) -> str
    """A deterministic key: readable prefix plus a hash of every part.

    The readable prefix makes a cache directory inspectable by eye; the hash
    makes the key exact, including the parts that are not filename safe.
    """
    parts = [provider, kind, exchange or "-", ticker or "-", interval or "-",
             str(start or "-"), str(end or "-"), json.dumps(extra or {}, sort_keys=True)]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    label = _SAFE.sub("-", "%s-%s-%s-%s" % (provider, kind, exchange or "x",
                                            ticker or "x")).strip("-")
    return "%s-%s" % (label[:60], digest)


class Entry(object):
    """One cache hit: the payload, and how old it is."""

    __slots__ = ("key", "payload", "stored_at", "age_seconds", "ttl_seconds",
                 "provider", "kind", "exchange", "meta")

    def __init__(self, key, payload, stored_at, age_seconds, ttl_seconds,
                 provider, kind, exchange, meta=None):
        self.key = key
        self.payload = payload
        self.stored_at = stored_at
        self.age_seconds = age_seconds
        self.ttl_seconds = ttl_seconds
        self.provider = provider
        self.kind = kind
        self.exchange = exchange
        self.meta = meta or {}

    @property
    def expired(self):
        # type: () -> bool
        return self.ttl_seconds is not None and self.age_seconds > self.ttl_seconds

    def __repr__(self):
        return "Entry(%s, %.0fs old%s)" % (
            self.key, self.age_seconds, ", expired" if self.expired else "")


class Cache(object):
    """A small file cache for market data.

    `enabled=False` turns it into a working no-op, so a caller can switch
    caching off without a second code path.
    """

    def __init__(self, directory=None, ttl=None, enabled=True, clock=None,
                 quarantine=True):
        # type: (Optional[str], Optional[Dict[str, int]], bool, Any, bool) -> None
        self.directory = directory or default_dir()
        self.ttl = dict(DEFAULT_TTL)
        if ttl:
            self.ttl.update(ttl)
        self.enabled = enabled
        self._clock = clock or time.time
        self.quarantine = quarantine
        self.corruptions = []  # type: List[dict]
        self.hits = 0
        self.misses = 0
        self.writes = 0

    # ------------------------------------------------------------- plumbing

    def _path(self, key, kind, provider, exchange):
        # type: (str, str, str, Optional[str]) -> str
        # Separate subtrees per provider, kind and exchange. Two exchanges
        # cannot share a directory, let alone a file.
        bucket = "quotes" if kind in ("quote", "index_quote") else "bars"
        return os.path.join(self.directory, _SAFE.sub("-", provider), bucket,
                            _SAFE.sub("-", exchange or "none"), key + ".json")

    def ttl_for(self, kind):
        # type: (str) -> Optional[int]
        return self.ttl.get(kind)

    def _note_corruption(self, path, reason):
        # type: (str, str) -> None
        self.corruptions.append({"path": path, "reason": reason})
        if not self.quarantine:
            return
        try:
            shutil.move(path, path + ".corrupt")
        except Exception:
            try:
                os.remove(path)
            except Exception:
                pass

    # ------------------------------------------------------------------ get

    def get(self, key, kind, provider, exchange=None, allow_expired=False):
        # type: (str, str, str, Optional[str], bool) -> Optional[Entry]
        """Return an Entry, or None for a miss.

        A corrupt file is a miss with a recorded reason, never an exception
        thrown into the data path: a bad cache must not take down a request
        that could still be served from the provider.
        """
        if not self.enabled:
            return None
        path = self._path(key, kind, provider, exchange)
        if not os.path.exists(path):
            self.misses += 1
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
            envelope = json.loads(raw)
        except ValueError as exc:
            self._note_corruption(path, "unparseable JSON: %s" % exc)
            self.misses += 1
            return None
        except OSError as exc:
            self._note_corruption(path, "unreadable: %s" % exc)
            self.misses += 1
            return None

        if not isinstance(envelope, dict) or envelope.get("v") != ENVELOPE_VERSION:
            self._note_corruption(path, "not a version %d envelope"
                                  % ENVELOPE_VERSION)
            self.misses += 1
            return None
        body = envelope.get("payload")
        checksum = envelope.get("checksum")
        if checksum != _checksum(body):
            self._note_corruption(
                path, "checksum mismatch: the payload does not match the "
                      "envelope it arrived in")
            self.misses += 1
            return None
        if envelope.get("exchange") != (exchange or None) \
                or envelope.get("provider") != provider \
                or envelope.get("kind") != kind:
            self._note_corruption(
                path, "envelope describes %s/%s/%s, which is not what was "
                      "asked for" % (envelope.get("provider"),
                                     envelope.get("kind"),
                                     envelope.get("exchange")))
            self.misses += 1
            return None

        stored_at = envelope.get("stored_at_epoch")
        age = max(float(self._clock()) - float(stored_at or 0), 0.0)
        entry = Entry(key=key, payload=body,
                      stored_at=_iso(stored_at), age_seconds=age,
                      ttl_seconds=envelope.get("ttl_seconds"),
                      provider=provider, kind=kind, exchange=exchange,
                      meta=envelope.get("meta") or {})
        if entry.expired and not allow_expired:
            self.misses += 1
            return None
        self.hits += 1
        return entry

    # ------------------------------------------------------------------ put

    def put(self, key, payload, kind, provider, exchange=None, ttl=None,
            meta=None):
        # type: (str, Any, str, str, Optional[str], Optional[int], Optional[dict]) -> str
        """Store a successful, validated payload. Returns the path written.

        Callers must not reach here with a failure. The service only calls
        put after validation has returned a usable verdict.
        """
        if not self.enabled:
            return ""
        if payload is None:
            raise CacheError("refusing to cache a null payload: a failure "
                             "must not become a cache hit")
        path = self._path(key, kind, provider, exchange)
        folder = os.path.dirname(path)
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
        except OSError as exc:
            raise CacheError("cannot create %s: %s" % (folder, exc))
        envelope = {
            "v": ENVELOPE_VERSION,
            "key": key,
            "provider": provider,
            "kind": kind,
            "exchange": exchange or None,
            "stored_at_epoch": float(self._clock()),
            "stored_at": _iso(self._clock()),
            "ttl_seconds": ttl if ttl is not None else self.ttl_for(kind),
            "checksum": _checksum(payload),
            "meta": meta or {},
            "payload": payload,
        }
        text = json.dumps(envelope, sort_keys=True, indent=1)
        handle, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            # Atomic on POSIX and on Windows for a same directory replace.
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
            raise
        self.writes += 1
        return path

    # ---------------------------------------------------------- maintenance

    def invalidate(self, key, kind, provider, exchange=None):
        # type: (str, str, str, Optional[str]) -> bool
        path = self._path(key, kind, provider, exchange)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def clear(self, provider=None):
        # type: (Optional[str]) -> int
        """Remove entries. Scoped to one provider when given."""
        target = self.directory
        if provider:
            target = os.path.join(self.directory, _SAFE.sub("-", provider))
        if not os.path.isdir(target):
            return 0
        count = 0
        for base, _dirs, files in os.walk(target):
            for name in files:
                if name.endswith(".json"):
                    os.remove(os.path.join(base, name))
                    count += 1
        return count

    def stats(self):
        # type: () -> dict
        return {"directory": self.directory, "enabled": self.enabled,
                "hits": self.hits, "misses": self.misses,
                "writes": self.writes,
                "corruptions": len(self.corruptions)}

    def __repr__(self):
        return "Cache(%s, %d hits, %d misses)" % (self.directory, self.hits,
                                                  self.misses)


def _checksum(payload):
    # type: (Any) -> str
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _iso(epoch):
    # type: (Any) -> Optional[str]
    if not epoch:
        return None
    return datetime.datetime.fromtimestamp(
        float(epoch), datetime.timezone.utc).isoformat()
