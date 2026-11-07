"""Downloader contracts use fake HTTP and DNS only."""

import hashlib
import threading

import httpx
import pytest

from app.integrations.knowledge_sources.fetch import SourceFetchError, fetch_pdf

PDF = b"%PDF-1.7\nsynthetic fixture\n%%EOF"
HOSTS = {"regulator.example", "archive.example"}


def resolve(host: str) -> list[str]:
    return ["93.184.216.34"]


def download(handler, url="https://regulator.example/document.pdf", **kwargs):
    return fetch_pdf(
        url,
        allowed_hosts=HOSTS,
        resolver=resolve,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_pdf_bytes_hash_and_dns_pinning_preserve_tls_identity():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=PDF)

    result = download(handler)
    assert result.body == PDF
    assert result.sha256 == hashlib.sha256(PDF).hexdigest()
    assert result.requested_url == result.final_url == "https://regulator.example/document.pdf"
    assert result.content_type == "application/pdf"
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "regulator.example"
    assert seen[0].extensions["sni_hostname"] == "regulator.example"
    assert seen[0].headers["accept-encoding"] == "identity"
    assert seen[0].extensions["timeout"]["connect"] == 5
    assert seen[0].extensions["timeout"]["read"] == 15


@pytest.mark.parametrize(
    "url",
    [
        "http://regulator.example/a.pdf",
        "https://user:password@regulator.example/a.pdf",
        "https://@regulator.example/a.pdf",
        "https://regulator.example:444/a.pdf",
        "https://127.0.0.1/a.pdf",
        "https://[::1]/a.pdf",
        "https://8.8.8.8/a.pdf",
        "https://regulator.example.evil.test/a.pdf",
        "https://unapproved.example/a.pdf",
        "https://regulator.example./a.pdf",
        "https://regulator.example/\na.pdf",
    ],
)
def test_unsafe_urls_never_reach_transport(url):
    seen = []
    with pytest.raises(SourceFetchError):
        download(lambda request: seen.append(request), url)
    assert seen == []


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.1"],
        ["169.254.169.254"],
        ["192.168.1.1"],
        ["100.64.0.1"],
        ["224.0.0.1"],
        ["0.0.0.0"],
        ["192.0.2.1"],
        ["::1"],
        ["fe80::1"],
        ["fc00::1"],
        ["ff02::1"],
        ["::ffff:127.0.0.1"],
        ["93.184.216.34", "10.0.0.1"],
        [],
        ["not-an-ip"],
    ],
)
def test_every_resolved_address_must_be_public(addresses):
    seen = []
    with pytest.raises(SourceFetchError):
        fetch_pdf(
            "https://regulator.example/a.pdf",
            allowed_hosts=HOSTS,
            resolver=lambda host: addresses,
            transport=httpx.MockTransport(lambda request: seen.append(request)),
        )
    assert seen == []


def test_each_redirect_is_validated_and_pinned_before_following():
    seen = []

    def handler(request):
        seen.append(request.headers["host"])
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "https://archive.example/final.pdf"})
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=PDF)

    result = download(handler)
    assert seen == ["regulator.example", "archive.example"]
    assert result.final_url == "https://archive.example/final.pdf"


@pytest.mark.parametrize(
    "location",
    [
        "http://archive.example/a.pdf",
        "https://evil.example/a.pdf",
        "https://127.0.0.1/a.pdf",
        "https://user@archive.example/a.pdf",
        "https://archive.example:8443/a.pdf",
    ],
)
def test_unsafe_redirect_never_followed(location):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": location})

    with pytest.raises(SourceFetchError):
        download(handler)
    assert len(seen) == 1


def test_dns_rebinding_check_on_redirect_and_original_hostname_never_connected():
    seen = []
    calls = []

    def resolver(host):
        calls.append(host)
        return ["93.184.216.34"] if len(calls) == 1 else ["127.0.0.1"]

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "/other.pdf"})

    with pytest.raises(SourceFetchError):
        fetch_pdf(
            "https://regulator.example/a.pdf",
            allowed_hosts=HOSTS,
            resolver=resolver,
            transport=httpx.MockTransport(handler),
        )
    assert len(seen) == 1
    assert seen[0].url.host == "93.184.216.34"


def test_relative_redirect_and_redirect_limit():
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(307, headers={"location": "/new.pdf"})
        return httpx.Response(
            200, headers={"content-type": "application/pdf; charset=binary"}, content=PDF
        )

    assert download(handler).final_url == "https://regulator.example/new.pdf"
    with pytest.raises(SourceFetchError) as caught:
        download(
            lambda request: httpx.Response(302, headers={"location": "/loop.pdf"}), max_redirects=1
        )
    assert caught.value.code == "SOURCE_REDIRECT_LIMIT"


@pytest.mark.parametrize(
    "headers,body",
    [
        ({"content-type": "text/html"}, b"<html>login</html>"),
        ({"content-type": "application/pdf"}, b"<html>blocked</html>"),
        ({"content-type": "text/html"}, PDF),
        ({}, PDF),
        ({"content-type": "application/pdf", "content-encoding": "gzip"}, PDF),
        ({"content-type": "application/pdf", "content-length": "999999999"}, PDF),
        ({"content-type": "application/pdf", "content-length": "-1"}, PDF),
        ({"content-type": "application/pdf", "content-length": "invalid"}, PDF),
        ({"content-type": "application/pdf", "content-length": "5"}, PDF),
    ],
)
def test_non_pdf_or_unbounded_responses_rejected(headers, body):
    with pytest.raises(SourceFetchError):
        download(lambda request: httpx.Response(200, headers=headers, content=body))


def test_streamed_size_limit_cannot_be_bypassed_without_content_length():
    class LargeStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"%PDF-1.7\n"
            yield b"x" * 32

    with pytest.raises(SourceFetchError) as caught:
        download(
            lambda request: httpx.Response(
                200, headers={"content-type": "application/pdf"}, stream=LargeStream()
            ),
            max_bytes=20,
        )
    assert caught.value.code == "SOURCE_TOO_LARGE"


@pytest.mark.parametrize("error", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError])
def test_network_failures_expose_only_stable_codes(error):
    def handler(request):
        raise error("sensitive upstream details", request=request)

    with pytest.raises(SourceFetchError) as caught:
        download(handler)
    assert str(caught.value) == "SOURCE_FETCH_FAILED"
    assert "sensitive" not in str(caught.value)


def test_dns_failure_and_invalid_configuration():
    def resolver(host):
        raise OSError("private DNS details")

    with pytest.raises(SourceFetchError) as caught:
        fetch_pdf("https://regulator.example/a.pdf", allowed_hosts=HOSTS, resolver=resolver)
    assert caught.value.code == "SOURCE_DNS_FAILED"
    for changes in (
        {"max_bytes": 21 * 1024 * 1024},
        {"max_bytes": 0},
        {"connect_timeout": 0},
        {"read_timeout": float("nan")},
        {"max_redirects": 6},
    ):
        with pytest.raises(SourceFetchError):
            download(lambda request: None, **changes)


def test_slow_dns_is_bounded_without_starting_http():
    release = threading.Event()
    seen = []

    def slow_resolver(host):
        release.wait()
        return ["93.184.216.34"]

    try:
        with pytest.raises(SourceFetchError) as caught:
            fetch_pdf(
                "https://regulator.example/a.pdf",
                allowed_hosts=HOSTS,
                resolver=slow_resolver,
                transport=httpx.MockTransport(lambda request: seen.append(request)),
                connect_timeout=0.01,
            )
        assert caught.value.code == "SOURCE_DNS_TIMEOUT"
        assert seen == []
    finally:
        release.set()
