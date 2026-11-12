import ipaddress
from collections.abc import Iterable
from time import monotonic

import pytest

from knowledge_assistant import website_crawler as crawler
from knowledge_assistant import website_transport as wire
from knowledge_assistant.website_crawler import (
    CrawlInputError,
    HTTPResult,
    WebsiteURL,
    crawl_website,
    normalize_website_url,
)

HOST = "crawl-fixture.invalid"
ORIGIN = f"https://{HOST}"
PUBLIC = "93.184.216.34"
IPV6 = "2606:4700:4700::1111"


def _response(status: int = 200, text: str = "", mime: str = "text/plain", **headers) -> HTTPResult:
    return HTTPResult(status, {"content-type": mime, **headers}, text.encode())


def _html(text: str) -> HTTPResult:
    return _response(text=text, mime="text/html; charset=utf-8")


def _sitemap(tag: str, item: str, locations: Iterable[str]) -> HTTPResult:
    entries = "".join(f"<{item}><loc>{location}</loc></{item}>" for location in locations)
    xml = f'<{tag} xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</{tag}>'
    return _response(text=xml, mime="application/xml")


class _Site:
    def __init__(self, routes: dict[str, HTTPResult | CrawlInputError]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str, int]] = []
        self.lookups: list[tuple[str, int]] = []
        self.answers = iter(())

    def resolve(self, host: str, port: int, deadline: float) -> Iterable[str]:
        assert deadline > monotonic()
        self.lookups.append((host, port))
        return next(self.answers, [PUBLIC, IPV6])

    def transport(
        self, url: WebsiteURL, address: wire.IPAddress, deadline: float, max_bytes: int
    ) -> HTTPResult:
        assert deadline > monotonic()
        assert url.origin == ("https", HOST, 443)
        self.requests.append((url.path, str(address), max_bytes))
        response = self.routes.get(url.path, _response(404))
        if isinstance(response, CrawlInputError):
            raise response
        return response

    def crawl(self, path: str = "/", **kwargs) -> list[crawler.CrawlPage]:
        return crawl_website(
            ORIGIN + path, resolver=self.resolve, transport=self.transport, **kwargs
        )

    @property
    def paths(self) -> list[str]:
        return [path for path, _, _ in self.requests]


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("HTTPS://Crawl-Fixture.INVALID.:443/a//./b/../%7e?q=1#two", ORIGIN + "/a/~"),
        ("http://café.example:80/café", "http://xn--caf-dma.example/caf%C3%A9"),
        (f"https://[{IPV6}]/one/..", f"https://[{IPV6}]/"),
        (f"https://{PUBLIC}/a/%2f/", f"https://{PUBLIC}/a/%2F/"),
    ],
)
def test_canonical_url_is_ascii_and_queryless(raw: str, canonical: str) -> None:
    assert normalize_website_url(raw) == canonical


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "invalid_url"),
        ("file:///manual", "invalid_url"),
        ("https://name@example.invalid/", "invalid_url"),
        ("https://example.invalid:80/", "invalid_url"),
        ("http://example.invalid:/", "invalid_url"),
        ("https://example.invalid/one two", "invalid_url"),
        ("https://example.invalid/%z0", "invalid_url"),
        ("https://example.invalid\\manual", "invalid_url"),
        ("https://singlelabel/", "invalid_url"),
        ("https://%65xample.invalid/", "invalid_url"),
        ("https://127.0.0.1/", "blocked_destination"),
        ("https://100.64.0.1/", "blocked_destination"),
        ("https://168.63.129.16/", "blocked_destination"),
        ("https://224.0.0.1/", "blocked_destination"),
        ("https://000.123.20.30/", "blocked_destination"),
        ("https://0x12345678/", "blocked_destination"),
        ("https://[::ffff:93.184.216.34]/", "blocked_destination"),
        ("https://[64:ff9b::5db8:d822]/", "blocked_destination"),
        ("https://[2002:5db8:d822::1]/", "blocked_destination"),
        (f"https://{HOST}/" + "a" * crawler.MAX_URL_LENGTH, "invalid_url"),
        (f"https://{HOST}/" + "é" * 500, "invalid_url"),
    ],
)
def test_url_policy_rejects_unsupported_and_nonpublic_inputs(raw: str, reason: str) -> None:
    with pytest.raises(CrawlInputError, match=f"^{reason}$"):
        normalize_website_url(raw)


def test_crawl_records_final_urls_and_follows_only_bounded_same_origin_links() -> None:
    site = _Site(
        {
            "/": _response(302, location="/home?ignored=1"),
            "/home": _html(
                "<head><title>Fixture &amp; docs</title></head><article><p>Welcome.</p>"
                "<script>irrelevant()</script><template>Hidden words</template>"
                '<a href="/details#one">Details</a><a href="/details?draft=1">Again</a>'
                '<a href="/broken">Broken</a><a href="/home">Self</a>'
                '<a href="https://other.invalid/">Other site</a>'
                '<a href="/no" rel="nofollow">No follow</a></article>'
            ),
            "/details": _html('<p>Useful text.</p><a href="/child">Child</a>'),
            "/child": _html('<p>Deeper text.</p><a href="/too-deep">Too deep</a>'),
        }
    )
    progress = []
    result = site.crawl(on_result=progress.append)
    assert result == progress
    assert [record["status"] for record in result] == [
        "fetched",
        "fetched",
        "failed",
        "skipped",
        "fetched",
    ]
    assert result[0]["url"] == ORIGIN + "/"
    assert result[0]["final_url"] == ORIGIN + "/home"
    assert result[0]["title"] == "Fixture & docs"
    assert "Welcome." in result[0]["text"]
    assert "Hidden words" not in result[0]["text"] and "irrelevant" not in result[0]["text"]
    assert result[2]["reason"] == "http_error"
    assert result[3]["reason"] == "duplicate"
    assert site.paths == ["/robots.txt", "/", "/home", "/details", "/broken", "/child"]
    assert site.lookups == [(HOST, 443)] * len(site.paths)
    assert all(address == PUBLIC for _, address, _ in site.requests)


@pytest.mark.parametrize("destination", ["192.0.2.5", "::ffff:93.184.216.34", "64:ff9b::5db8:d822"])
def test_every_dns_answer_must_pass_before_transport(destination: str) -> None:
    site = _Site({})
    site.answers = iter([[PUBLIC, destination]])
    result = site.crawl()
    assert result[0]["reason"] == "blocked_destination"
    assert site.lookups == [(HOST, 443)] and site.paths == []


def test_redirect_resolves_again_and_stops_before_changed_destination() -> None:
    site = _Site({"/": _response(302, location="/details")})
    site.answers = iter([[PUBLIC], [PUBLIC], ["192.0.2.7"]])
    result = site.crawl()
    assert result[0]["reason"] == "blocked_destination"
    assert site.paths == ["/robots.txt", "/"]
    assert site.lookups == [(HOST, 443)] * 3


@pytest.mark.parametrize(
    ("location", "reason"),
    [
        ("https://other.invalid/", "outside_origin"),
        (f"http://{HOST}/", "outside_origin"),
        ("/", "redirect_limit"),
        ("", "invalid_response"),
    ],
)
def test_redirect_policy_rejects_unreachable_final_targets(location: str, reason: str) -> None:
    site = _Site({"/": _response(302, location=location)})
    assert site.crawl()[0]["reason"] == reason
    assert site.paths == ["/robots.txt", "/"]


def test_robots_blocks_disallowed_hops_and_uses_agent_specific_allow_rule() -> None:
    robots = (
        "User-agent: *\nDisallow: /\nUser-agent: KnowledgeDeskCrawler\n"
        "Disallow: /manual/*\nAllow: /manual/open$\n"
    )
    site = _Site(
        {
            "/robots.txt": _response(text=robots),
            "/": _html('<a href="/manual/open">Open</a><a href="/alias">Alias</a>'),
            "/alias": _response(302, location="/manual/closed"),
            "/manual/open": _response(text="Available"),
        }
    )
    assert [page["reason"] for page in site.crawl()] == [None, None, "robots_disallowed"]
    assert site.paths == ["/robots.txt", "/", "/manual/open", "/alias"]


@pytest.mark.parametrize(
    "robots",
    [
        _response(503),
        _html("Server login"),
        _response(text="User-agent: " + "a" * 2048),
        _response(301, location="https://other.invalid/robots.txt"),
    ],
)
def test_unavailable_robots_fails_closed(robots: HTTPResult) -> None:
    site = _Site({"/robots.txt": robots})
    assert site.crawl()[0]["reason"] == "robots_unavailable"
    assert site.paths == ["/robots.txt"]


def test_sitemap_index_bounds_nested_documents_and_preserves_failed_provenance() -> None:
    site = _Site(
        {
            "/index.xml": _sitemap(
                "sitemapindex", "sitemap", [f"{ORIGIN}/{name}.xml" for name in ("a", "b", "c", "d")]
            ),
            "/a.xml": _sitemap("urlset", "url", [ORIGIN + "/entry", ORIGIN + "/entry#repeat"]),
            "/b.xml": _response(text="<unrelated/>", mime="text/xml"),
            "/c.xml": _sitemap("urlset", "url", ["https://other.invalid/foreign"]),
            "/entry": _html("<title>Entry title</title><p>Entry contents.</p>"),
        }
    )
    result = site.crawl("/index.xml", sitemap=True)
    assert [(page["url"], page["reason"]) for page in result] == [
        (ORIGIN + "/b.xml", "invalid_sitemap"),
        (ORIGIN + "/entry", None),
    ]
    assert result[1]["title"] == "Entry title"
    assert site.paths == ["/robots.txt", "/index.xml", "/a.xml", "/b.xml", "/c.xml", "/entry"]


@pytest.mark.parametrize("xml", ["<urlset/>", "<!DOCTYPE urlset><urlset/>", "<urlset>"])
def test_sitemap_without_usable_locations_reports_safe_failure(xml: str) -> None:
    site = _Site({"/index.xml": _response(text=xml, mime="application/xml")})
    assert site.crawl("/index.xml", sitemap=True)[0]["reason"] == "invalid_sitemap"


def test_page_limit_and_text_metadata_limits() -> None:
    links = "".join(f'<a href="/page-{index}">Page</a>' for index in range(30))
    site = _Site({"/": _html("<title>" + "t" * 250 + "</title><p>" + "字" * 30000 + links)})
    result = site.crawl()
    assert len(result) == crawler.MAX_CRAWL_PAGES
    assert len(result[0]["title"]) == crawler.MAX_TITLE_LENGTH
    assert len(result[0]["text"].encode()) <= crawler.MAX_TEXT_BYTES
    assert len(site.paths) == crawler.MAX_CRAWL_PAGES + 1


def test_exhausted_byte_budget_charges_unsuccessful_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler, "MAX_RESPONSE_BYTES", 80)
    monkeypatch.setattr(crawler, "MAX_CRAWL_BYTES", 110)
    site = _Site(
        {
            "/": _html('<a href="/big">Big</a><a href="/next">Next</a>'),
            "/big": CrawlInputError("response_too_large"),
            "/next": _response(text="Next"),
        }
    )
    assert [page["reason"] for page in site.crawl()] == [None, "response_too_large", "crawl_limit"]
    assert site.paths == ["/robots.txt", "/", "/big"]


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (_response(text="bytes", mime="image/png"), "unsupported_content_type"),
        (_response(text="encoded", **{"content-encoding": "gzip"}), "unsupported_encoding"),
        (_html("<style>aside{}</style>"), "empty_page"),
        (_response(text="a" * (crawler.MAX_RESPONSE_BYTES + 1)), "response_too_large"),
    ],
)
def test_unusable_page_bodies_report_stable_reasons(response: HTTPResult, reason: str) -> None:
    assert _Site({"/": response}).crawl()[0]["reason"] == reason


class _Socket:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.timeouts: list[float] = []
        self.endpoint = None
        self.request = b""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def settimeout(self, seconds: float) -> None:
        self.timeouts.append(seconds)

    def connect(self, endpoint: tuple) -> None:
        self.endpoint = endpoint

    def sendall(self, data: bytes) -> None:
        self.request = data

    def recv_into(self, buffer: memoryview) -> int:
        chunk = self.response[: len(buffer)]
        buffer[: len(chunk)] = chunk
        self.response = self.response[len(chunk) :]
        return len(chunk)


def test_native_transport_pins_socket_and_preserves_tls_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sock = _Socket(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\nHello")
    names = []

    class Context:
        verify_flags = 0

        def __init__(self, protocol):
            assert protocol == wire.ssl.PROTOCOL_TLS_CLIENT

        def load_default_certs(self, purpose):
            assert purpose == wire.ssl.Purpose.SERVER_AUTH

        def wrap_socket(self, raw, *, server_hostname):
            assert raw is sock
            assert self.minimum_version == wire.ssl.TLSVersion.TLSv1_2
            assert self.verify_flags & wire.ssl.VERIFY_X509_STRICT
            names.append(server_hostname)
            return raw

    monkeypatch.setattr(wire.socket, "socket", lambda *_: sock)
    monkeypatch.setattr(wire.ssl, "SSLContext", Context)
    url = WebsiteURL(ORIGIN + "/manual", "https", HOST, 443, "/manual")
    result = wire.fetch_pinned(url, ipaddress.ip_address(PUBLIC), monotonic() + 1, 20)
    assert sock.endpoint == (PUBLIC, 443)
    assert names == [HOST]
    assert sock.request.startswith(f"GET /manual HTTP/1.1\r\nHost: {HOST}\r\n".encode())
    assert b"Accept-Encoding: identity\r\n" in sock.request
    assert result.body == b"Hello"
    assert all(0 < timeout <= 1 for timeout in sock.timeouts)


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (b"Content-Length: 8\r\n\r\nabcdefgh", "response_too_large"),
        (b"Content-Length: 3\r\n\r\nab", "invalid_response"),
        (b"Content-Length: 3\r\nContent-Length: 4\r\n\r\nabcd", "invalid_response"),
        (b"Transfer-Encoding: chunked\r\nContent-Length: 3\r\n\r\n", "invalid_response"),
        (b"Content-Encoding: gzip\r\n\r\n", "unsupported_encoding"),
        (b"\r\nabcdef", "response_too_large"),
    ],
)
def test_native_transport_bounds_and_validates_wire_response(response: bytes, reason: str) -> None:
    sock = _Socket(b"HTTP/1.1 200 OK\r\n" + response)
    with pytest.raises(CrawlInputError, match=f"^{reason}$"):
        wire._read_response(sock, monotonic() + 1, 5)


def test_native_transport_can_read_a_bounded_chunked_response() -> None:
    sock = _Socket(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n")
    assert wire._read_response(sock, monotonic() + 1, 5).body == b"abc"


def test_real_resolver_uses_only_absolute_dns_name_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def addresses(host, port, **_):
        calls.append((host, port))
        return [(wire.socket.AF_INET, 0, 0, "", (PUBLIC, port))]

    monkeypatch.setattr(wire.socket, "getaddrinfo", addresses)
    assert wire.resolve_host(HOST, 443, monotonic() + 1) == [PUBLIC]
    assert calls == [(HOST + ".", 443)]
    with pytest.raises(CrawlInputError, match="^timeout$"):
        wire.resolve_host(HOST, 443, monotonic() - 1)
