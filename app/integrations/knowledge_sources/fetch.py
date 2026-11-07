"""Bounded HTTPS PDF acquisition from explicit public hosts, with DNS pinning.

Every redirect gets a new connection pool. The socket target is the validated IP,
while Host, TLS SNI and certificate verification retain the approved hostname.
This boundary does not establish that a public document is legally authoritative.
"""

from __future__ import annotations

import hashlib
import ipaddress
import math
import re
import socket
import time
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import BoundedSemaphore
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

MAX_BYTES = 20 * 1024 * 1024
Resolver = Callable[[str], Sequence[str]]
_REDIRECTS = {301, 302, 303, 307, 308}
_DNS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="knowledge-source-dns")
_DNS_SLOTS = BoundedSemaphore(2)
_HOST = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}\Z")


class SourceFetchError(ValueError):
    """A stable error code; upstream bodies, credentials and URLs are never echoed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str
    content_type: str
    body: bytes
    sha256: str


def _hostname(value: str) -> str:
    try:
        host = value.encode("idna").decode("ascii").lower()
    except (UnicodeError, AttributeError) as exc:
        raise SourceFetchError("SOURCE_HOST_INVALID") from exc
    if not _HOST.fullmatch(host):
        raise SourceFetchError("SOURCE_HOST_INVALID")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise SourceFetchError("SOURCE_IP_LITERAL_DENIED")


def _validate_url(url: str, allowed_hosts: Collection[str]) -> tuple[str, str]:
    if not isinstance(url, str) or not 1 <= len(url) <= 4096:
        raise SourceFetchError("SOURCE_URL_INVALID")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise SourceFetchError("SOURCE_URL_INVALID")
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
            raise SourceFetchError("SOURCE_URL_DENIED")
        if parsed.port not in (None, 443) or not parsed.hostname or "\\" in parsed.netloc:
            raise SourceFetchError("SOURCE_URL_DENIED")
        host = _hostname(parsed.hostname)
        approved = {_hostname(item) for item in allowed_hosts}
        if not approved or host not in approved:
            raise SourceFetchError("SOURCE_HOST_DENIED")
        # Canonical HTTPS origin without credentials, fragments or alternative ports.
        normalized = urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
        return normalized, host
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, SourceFetchError):
            raise
        raise SourceFetchError("SOURCE_URL_INVALID") from exc


def _resolve(host: str) -> Sequence[str]:
    return [
        str(item[4][0])
        for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    ]


def _resolve_bounded(host: str, resolver: Resolver, timeout: float) -> Sequence[str]:
    """Bound caller wait and outstanding DNS work; OS DNS itself is not cancellable."""
    deadline = time.monotonic() + timeout
    if not _DNS_SLOTS.acquire(timeout=timeout):
        raise SourceFetchError("SOURCE_DNS_BUSY")

    def resolve_and_release() -> Sequence[str]:
        try:
            return resolver(host)
        finally:
            _DNS_SLOTS.release()

    try:
        pending = _DNS_POOL.submit(resolve_and_release)
    except RuntimeError as exc:
        _DNS_SLOTS.release()
        raise SourceFetchError("SOURCE_DNS_FAILED") from exc
    try:
        return pending.result(timeout=max(0, deadline - time.monotonic()))
    except TimeoutError as exc:
        # An outstanding resolver retains its slot until it actually completes.
        raise SourceFetchError("SOURCE_DNS_TIMEOUT") from exc


def _public_addresses(host: str, resolver: Resolver) -> list[str]:
    try:
        answers = resolver(host)
        if not answers or isinstance(answers, str):
            raise SourceFetchError("SOURCE_DNS_DENIED")
        addresses = []
        for answer in answers:
            address = ipaddress.ip_address(answer)
            if isinstance(address, ipaddress.IPv6Address) and (
                address.sixtofour is not None or address.teredo is not None
            ):
                raise SourceFetchError("SOURCE_DNS_DENIED")
            if not address.is_global or any(
                (
                    address.is_private,
                    address.is_loopback,
                    address.is_link_local,
                    address.is_multicast,
                    address.is_reserved,
                    address.is_unspecified,
                )
            ):
                raise SourceFetchError("SOURCE_DNS_DENIED")
            addresses.append(str(address))
        return sorted(set(addresses))
    except SourceFetchError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise SourceFetchError("SOURCE_DNS_FAILED") from exc


class _PinnedTransport(httpx.BaseTransport):
    def __init__(self, hostname: str, address: str, inner: httpx.BaseTransport) -> None:
        self.hostname = hostname
        self.address = address
        self.inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https" or request.url.host != self.hostname:
            raise SourceFetchError("SOURCE_TRANSPORT_DENIED")
        headers = request.headers.copy()
        headers["Host"] = self.hostname
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=self.address),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": self.hostname},
        )
        return self.inner.handle_request(pinned)

    def close(self) -> None:
        self.inner.close()


def fetch_pdf(
    url: str,
    *,
    allowed_hosts: Collection[str],
    max_bytes: int = MAX_BYTES,
    connect_timeout: float = 5,
    read_timeout: float = 15,
    max_redirects: int = 3,
    max_elapsed_s: float = 60,
    transport: httpx.BaseTransport | None = None,
    resolver: Resolver | None = None,
) -> FetchResult:
    """Fetch one PDF without proxies, automatic redirects or unbounded buffering.

    Injected transport/resolver are backend test seams, never request parameters.
    Read/connect deadlines bound socket operations; the elapsed deadline is checked
    between redirects/chunks. A blocked read can take up to its read timeout.
    """
    if (
        type(max_bytes) is not int
        or not 1 <= max_bytes <= MAX_BYTES
        or type(max_redirects) is not int
        or not 0 <= max_redirects <= 5
        or any(
            type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 60
            for value in (connect_timeout, read_timeout, max_elapsed_s)
        )
        or isinstance(allowed_hosts, str)
    ):
        raise SourceFetchError("SOURCE_FETCH_CONFIGURATION_INVALID")
    deadline = time.monotonic() + max_elapsed_s
    requested_url, _ = _validate_url(url, allowed_hosts)
    current = requested_url
    resolve = resolver or _resolve
    try:
        for redirect in range(max_redirects + 1):
            current, host = _validate_url(current, allowed_hosts)
            if time.monotonic() >= deadline:
                raise SourceFetchError("SOURCE_FETCH_TIMEOUT")
            addresses = _public_addresses(
                host,
                lambda hostname: _resolve_bounded(
                    hostname, resolve, min(connect_timeout, deadline - time.monotonic())
                ),
            )
            # No pool is reused across redirects/hostnames, including shared CDN IPs.
            inner = transport or httpx.HTTPTransport(
                verify=True,
                trust_env=False,
                retries=0,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            )
            pinned = _PinnedTransport(host, addresses[0], inner)
            with (
                httpx.Client(
                    transport=pinned,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
                    headers={"Accept": "application/pdf", "Accept-Encoding": "identity"},
                ) as client,
                client.stream("GET", current) as response,
            ):
                if response.status_code in _REDIRECTS:
                    location = response.headers.get("location")
                    if not location:
                        raise SourceFetchError("SOURCE_REDIRECT_INVALID")
                    if redirect >= max_redirects:
                        raise SourceFetchError("SOURCE_REDIRECT_LIMIT")
                    current, _ = _validate_url(urljoin(current, location), allowed_hosts)
                    continue
                if response.status_code != 200:
                    raise SourceFetchError("SOURCE_HTTP_STATUS")
                content_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if content_type != "application/pdf":
                    raise SourceFetchError("SOURCE_NOT_PDF")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise SourceFetchError("SOURCE_ENCODING_DENIED")
                declared = response.headers.get("content-length")
                if declared is not None:
                    if not declared.isdecimal():
                        raise SourceFetchError("SOURCE_LENGTH_INVALID")
                    length = int(declared)
                    if length > max_bytes:
                        raise SourceFetchError("SOURCE_TOO_LARGE")
                body = bytearray()
                # Encodings were rejected above, so bytes are the original payload.
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if time.monotonic() >= deadline:
                        raise SourceFetchError("SOURCE_FETCH_TIMEOUT")
                    if len(body) + len(chunk) > max_bytes:
                        raise SourceFetchError("SOURCE_TOO_LARGE")
                    body.extend(chunk)
                if declared is not None and len(body) != int(declared):
                    raise SourceFetchError("SOURCE_LENGTH_INVALID")
                if not body.startswith(b"%PDF-"):
                    raise SourceFetchError("SOURCE_NOT_PDF")
                data = bytes(body)
                return FetchResult(
                    requested_url=requested_url,
                    final_url=current,
                    content_type=content_type,
                    body=data,
                    sha256=hashlib.sha256(data).hexdigest(),
                )
        raise SourceFetchError("SOURCE_REDIRECT_LIMIT")
    except SourceFetchError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise SourceFetchError("SOURCE_FETCH_FAILED") from exc
