"""The only thing in this package that touches the network.

Keeping it in one place is what makes the rest testable offline: every
provider takes a transport, and the test suite passes a FixtureTransport that
reads a file. No provider calls urllib itself, so no provider can be made to
reach the network by a test that forgot to patch something.

What it enforces:

    HTTPS only, and only to a host the provider declared. A redirect to
    another host is refused rather than followed, so a hijacked redirect
    cannot turn a market data request into a request somewhere else.

    A timeout on every call. There is no code path with no timeout.

    A read limit. Responses are read up to max_bytes and refused beyond it,
    so a provider that starts streaming a gigabyte cannot exhaust memory. The
    whole market NSE bhavcopy is 208 KB zipped and the BSE one 888 KB plain,
    so the 32 MB default is generous.

    Errors with no headers in them. Request headers are never copied into an
    exception, a log line or a retry history, so a credential in a header of
    some future authenticated provider cannot leak through an error message.

    Defensive JSON. A body that is not JSON, or is JSON but not an object,
    raises BadResponse rather than returning something a normalizer will
    misread.
"""

import json
import socket
from typing import Any, Dict, Optional

import urllib.error as urlerror
import urllib.request as urlrequest
from urllib.parse import urlsplit

from .. import retry as retry_mod

DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_BYTES = 32 * 1024 * 1024

# Sent so that an endpoint can see what this is. No credential, ever.
DEFAULT_USER_AGENT = "indiacore/0.2 (India market data; python-urllib)"


class TransportError(Exception):
    """A request did not produce a usable response."""

    retry_class = retry_mod.RETRYABLE

    def __init__(self, message, status=None, retry_after=None, url=None):
        Exception.__init__(self, message)
        self.status = status
        self.retry_after = retry_after
        # The path only. A query string can carry an API key for some
        # providers, so it is dropped before the URL is ever stored.
        self.url = _scrub(url)


class HttpError(TransportError):
    """The server answered with an error status."""

    def __init__(self, status, reason, url=None, retry_after=None, body=None):
        TransportError.__init__(
            self, "HTTP %s %s for %s" % (status, reason, _scrub(url)),
            status=status, retry_after=retry_after, url=url)
        self.retry_class = retry_mod.classify_status(status) or retry_mod.FATAL
        self.reason = reason
        # Kept short, and only for a normalizer to read a provider's own
        # error envelope.
        self.body = (body or b"")[:2048]


class TransportTimeout(TransportError):
    """The request timed out."""

    retry_class = retry_mod.RETRYABLE


class ResponseTooLarge(TransportError):
    """The response exceeded max_bytes."""

    retry_class = retry_mod.FATAL


class BadResponse(TransportError):
    """The body was not what the endpoint is documented to return."""

    retry_class = retry_mod.FATAL


class UnsafeUrl(TransportError):
    """The URL was not https, or not on an allowed host."""

    retry_class = retry_mod.FATAL


def _scrub(url):
    # type: (Optional[str]) -> str
    """Scheme, host and path. No query string, no credentials."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        return "%s://%s%s" % (parts.scheme, parts.hostname or "", parts.path)
    except Exception:
        return "a url that could not be parsed"


class HttpTransport(object):
    """Plain HTTPS GET with the guards above."""

    def __init__(self, allowed_hosts, timeout=DEFAULT_TIMEOUT,
                 max_bytes=DEFAULT_MAX_BYTES, user_agent=DEFAULT_USER_AGENT,
                 opener=None):
        # type: (Any, float, int, str, Any) -> None
        self.allowed_hosts = tuple(allowed_hosts)
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)
        self.user_agent = user_agent
        self._opener = opener or urlrequest.urlopen
        self.requests = 0

    def check_url(self, url):
        # type: (str) -> str
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise UnsafeUrl("refusing a non https URL: %s" % _scrub(url),
                            url=url)
        host = (parts.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise UnsafeUrl(
                "%s is not one of this provider's declared hosts (%s)"
                % (host, ", ".join(self.allowed_hosts)), url=url)
        if parts.username or parts.password:
            raise UnsafeUrl("refusing a URL with credentials in it", url=url)
        return url

    def get_bytes(self, url, headers=None):
        # type: (str, Optional[Dict[str, str]]) -> bytes
        self.check_url(url)
        send = {"User-Agent": self.user_agent}
        if headers:
            send.update(headers)
        request = urlrequest.Request(url, headers=send)
        self.requests += 1
        try:
            response = self._opener(request, timeout=self.timeout)
        except urlerror.HTTPError as exc:
            body = b""
            try:
                body = exc.read(2048)
            except Exception:
                pass
            retry_after = None
            try:
                retry_after = exc.headers.get("Retry-After")
            except Exception:
                pass
            raise HttpError(exc.code, exc.reason, url=url,
                            retry_after=retry_after, body=body)
        except socket.timeout:
            raise TransportTimeout("timed out after %.1fs for %s"
                                   % (self.timeout, _scrub(url)), url=url)
        except urlerror.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.timeout):
                raise TransportTimeout("timed out after %.1fs for %s"
                                       % (self.timeout, _scrub(url)), url=url)
            raise TransportError("could not reach %s: %s"
                                 % (_scrub(url), reason), url=url)
        try:
            # One byte past the limit, so a response exactly at the limit is
            # accepted and one over it is caught rather than truncated.
            body = response.read(self.max_bytes + 1)
        finally:
            try:
                response.close()
            except Exception:
                pass
        if len(body) > self.max_bytes:
            raise ResponseTooLarge(
                "%s returned more than the %d byte limit"
                % (_scrub(url), self.max_bytes), url=url)
        return body

    def get_json(self, url, headers=None):
        # type: (str, Optional[Dict[str, str]]) -> dict
        raw = self.get_bytes(url, headers)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BadResponse("%s did not return UTF-8: %s" % (_scrub(url), exc),
                              url=url)
        try:
            doc = json.loads(text)
        except ValueError as exc:
            raise BadResponse(
                "%s did not return valid JSON (%s). First bytes: %r"
                % (_scrub(url), exc, text[:120]), url=url)
        if not isinstance(doc, dict):
            raise BadResponse(
                "%s returned a %s at the top level, not an object"
                % (_scrub(url), type(doc).__name__), url=url)
        return doc

    def __repr__(self):
        return "HttpTransport(%s)" % ", ".join(self.allowed_hosts)


class FixtureTransport(object):
    """Serves recorded payloads, so the suite never needs the network.

    Built with a mapping of URL substring to either a path on disk or an
    exception instance to raise. A URL that matches nothing raises, rather
    than returning empty bytes, so a test cannot pass by accident when a
    provider changes the URL it builds.
    """

    def __init__(self, routes=None, allowed_hosts=("*",)):
        # type: (Any, Any) -> None
        self.routes = dict(routes or {})
        self.allowed_hosts = tuple(allowed_hosts)
        self.calls = []  # type: list

    def route(self, fragment, target):
        # type: (str, Any) -> None
        self.routes[fragment] = target

    def check_url(self, url):
        # type: (str) -> str
        return url

    def _resolve(self, url):
        # type: (str) -> Any
        self.calls.append(url)
        # Longest fragment first, so a specific route ("period1=...") wins
        # over a general one ("SBIN.NS?interval=1d") rather than depending on
        # the order the routes happened to be added in.
        for fragment in sorted(self.routes, key=len, reverse=True):
            target = self.routes[fragment]
            if fragment in url:
                if isinstance(target, BaseException):
                    raise target
                if callable(target):
                    return target(url)
                return target
        raise TransportError("no fixture routed for %s" % _scrub(url), url=url)

    def get_bytes(self, url, headers=None):
        # type: (str, Optional[Dict[str, str]]) -> bytes
        target = self._resolve(url)
        if isinstance(target, bytes):
            return target
        with open(target, "rb") as fh:
            return fh.read()

    def get_json(self, url, headers=None):
        # type: (str, Optional[Dict[str, str]]) -> dict
        raw = self.get_bytes(url, headers)
        try:
            doc = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise BadResponse("fixture for %s is not valid JSON: %s"
                              % (_scrub(url), exc), url=url)
        if not isinstance(doc, dict):
            raise BadResponse("fixture for %s is not an object" % _scrub(url),
                              url=url)
        return doc

    def __repr__(self):
        return "FixtureTransport(%d routes, %d calls)" % (len(self.routes),
                                                          len(self.calls))
