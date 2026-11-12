"""Bounded same-origin website ingestion, run by the background worker."""

import ipaddress
import re
import xml.parsers.expat as expat
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from email.message import Message
from html.parser import HTMLParser
from time import monotonic
from typing import Literal, TypedDict
from urllib.parse import quote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from knowledge_assistant.website_transport import (
    USER_AGENT,
    CrawlInputError,
    HTTPResult,
    IPAddress,
    WebsiteURL,
    fetch_pinned,
    remaining,
    resolve_host,
)

MAX_CRAWL_PAGES = 20
MAX_SITEMAPS = 4
MAX_URL_LENGTH = 2048
MAX_TITLE_LENGTH = 200
MAX_TEXT_BYTES = 48 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_CRAWL_BYTES = 4 * 1024 * 1024
CRAWL_SECONDS = 30
_HOP_SECONDS = 5
_MAX_REQUESTS = 50
_MAX_REDIRECTS = 3
_MAX_DEPTH = 2
_MAX_LINKS = 80
_MAX_DISCOVERED_PAGES = 100
_MAX_SITEMAP_LOCATIONS = 100
_MAX_XML_DEPTH = 32
_MAX_DNS_ANSWERS = 32
_ROBOT_LINES = 1024
_ROBOT_LINE_LENGTH = 2048

_PORTS = {"http": 80, "https": 443}
_REDIRECTS = {301, 302, 303, 307, 308}
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_NUMERIC_HOST = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)(?:\.(?:[0-9]+|0x[0-9a-f]+))*")
_AUTHORITY = re.compile(r"(\[[0-9a-fA-F:.]+\]|[^\[\]:]+)(?::([0-9]+))?")
_BAD_PERCENT = re.compile(r"%(?![a-fA-F0-9]{2})")
_PERCENT = re.compile(r"%([a-fA-F0-9]{2})")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_UNRESERVED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
_IPV6_NATIVE = ipaddress.IPv6Network("2000::/3")
_IPV6_SPECIAL = tuple(map(ipaddress.IPv6Network, ("2001::/23", "2002::/16")))
_CLOUD_PLATFORM_IP = ipaddress.IPv4Address("168.63.129.16")
_HIDDEN_HTML = {"script", "style", "template", "noscript", "iframe", "svg"}
_HTML_BLOCKS = {
    "article",
    "br",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "section",
    "tr",
}
_PAGE_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
_SITEMAP_TYPES = {"application/xml", "text/xml"}
_SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9}"
_SKIPPED = {
    "outside_origin",
    "duplicate",
    "robots_disallowed",
    "unsupported_content_type",
    "unsupported_encoding",
    "empty_page",
    "crawl_limit",
}


class CrawlPage(TypedDict):
    url: str
    final_url: str | None
    title: str | None
    status: Literal["fetched", "skipped", "failed"]
    reason: str | None
    text: str | None


Resolver = Callable[[str, int, float], Iterable[str]]
Transport = Callable[[WebsiteURL, IPAddress, float, int], HTTPResult]


def _public_address(address: IPAddress) -> bool:
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address == _CLOUD_PLATFORM_IP
    ):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        return address in _IPV6_NATIVE and not any(address in network for network in _IPV6_SPECIAL)
    return True


def _ip_literal(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _canonical_host(raw: str) -> str:
    bracketed = raw.startswith("[")
    literal = _ip_literal(raw[1:-1] if bracketed else raw)
    if literal is not None:
        if bracketed != isinstance(literal, ipaddress.IPv6Address):
            raise CrawlInputError("invalid_url")
        if not _public_address(literal):
            raise CrawlInputError("blocked_destination")
        return str(literal)
    if bracketed or "%" in raw:
        raise CrawlInputError("invalid_url")
    try:
        host = raw.encode("idna").decode("ascii").lower().removesuffix(".")
    except UnicodeError:
        raise CrawlInputError("invalid_url") from None
    if _NUMERIC_HOST.fullmatch(host) or _ip_literal(host) is not None:
        raise CrawlInputError("blocked_destination")
    labels = host.split(".")
    if len(labels) < 2 or len(host) > 253 or not all(_LABEL.fullmatch(x) for x in labels):
        raise CrawlInputError("invalid_url")
    return host


def _canonical_path(path: str) -> str:
    if _BAD_PERCENT.search(path):
        raise CrawlInputError("invalid_url")

    def normalize_escape(match: re.Match[str]) -> str:
        value = chr(int(match[1], 16))
        return value if value in _UNRESERVED else match[0].upper()

    path = _PERCENT.sub(normalize_escape, path)
    segments: list[str] = []
    for part in path.split("/"):
        if part == "..":
            if segments:
                segments.pop()
        elif part and part != ".":
            segments.append(part)
    cleaned = "/" + "/".join(segments)
    if segments and path.endswith(("/", "/.", "/..")):
        cleaned += "/"
    return quote(cleaned, safe="/%:@!$&'()*+,;=-._~")


def _website_url(url: str) -> WebsiteURL:
    if (
        not url
        or len(url) > MAX_URL_LENGTH
        or "\\" in url
        or _CONTROL.search(url)
        or any(character.isspace() for character in url)
    ):
        raise CrawlInputError("invalid_url")
    try:
        parts = urlsplit(url)
        port = _PORTS.get(parts.scheme)
        authority = _AUTHORITY.fullmatch(parts.netloc)
        if port is None or authority is None or "@" in parts.netloc:
            raise CrawlInputError("invalid_url")
        if authority[2] is not None and authority[2] != str(port):
            raise CrawlInputError("invalid_url")
        host = _canonical_host(authority[1])
        path = _canonical_path(parts.path)
    except CrawlInputError:
        raise
    except ValueError:
        raise CrawlInputError("invalid_url") from None
    authority_name = f"[{host}]" if ":" in host else host
    normalized = f"{parts.scheme}://{authority_name}{path}"
    if len(normalized) > MAX_URL_LENGTH:
        raise CrawlInputError("invalid_url")
    return WebsiteURL(normalized, parts.scheme, host, port, path)


def normalize_website_url(url: str) -> str:
    """Normalize a crawl URL without network access or retaining its query or fragment."""
    return _website_url(url).url


def _relative_url(base: WebsiteURL, location: str) -> WebsiteURL:
    if len(location) > MAX_URL_LENGTH or _CONTROL.search(location) or "\\" in location:
        raise CrawlInputError("invalid_url")
    try:
        return _website_url(urljoin(base.url, location))
    except CrawlInputError:
        raise
    except ValueError:
        raise CrawlInputError("invalid_url") from None


def _resolve_address(url: WebsiteURL, resolver: Resolver, deadline: float) -> IPAddress:
    literal = _ip_literal(url.host)
    if literal is not None:
        return literal
    first = None
    try:
        for count, value in enumerate(resolver(url.host, url.port, deadline), start=1):
            remaining(deadline)
            if count > _MAX_DNS_ANSWERS:
                raise CrawlInputError("dns_failure")
            address = _ip_literal(value)
            if address is None or "%" in value or not _public_address(address):
                raise CrawlInputError("blocked_destination")
            if first is None:
                first = address
    except TimeoutError:
        raise CrawlInputError("timeout") from None
    except OSError:
        raise CrawlInputError("dns_failure") from None
    if first is None:
        raise CrawlInputError("dns_failure")
    return first


@dataclass(slots=True)
class _FetchState:
    seed: WebsiteURL
    resolver: Resolver
    transport: Transport
    deadline: float = field(default_factory=lambda: monotonic() + CRAWL_SECONDS)
    bytes_left: int = field(default_factory=lambda: MAX_CRAWL_BYTES)
    requests_left: int = _MAX_REQUESTS
    robots: RobotFileParser | None = None

    def request(self, url: WebsiteURL) -> HTTPResult:
        remaining(self.deadline)
        if self.requests_left <= 0 or self.bytes_left <= 0:
            raise CrawlInputError("crawl_limit")
        self.requests_left -= 1
        deadline = min(self.deadline, monotonic() + _HOP_SECONDS)
        max_bytes = min(self.bytes_left, MAX_RESPONSE_BYTES)
        address = _resolve_address(url, self.resolver, deadline)
        remaining(deadline)
        # Retain the whole reservation when I/O ends before the body size is known.
        self.bytes_left -= max_bytes
        result = self.transport(url, address, deadline, max_bytes)
        remaining(deadline)
        if len(result.body) > max_bytes:
            raise CrawlInputError("response_too_large")
        self.bytes_left += max_bytes - len(result.body)
        if result.headers.get("content-encoding", "identity").lower() != "identity":
            raise CrawlInputError("unsupported_encoding")
        return result

    def follow(
        self, url: WebsiteURL, *, visited: set[str], for_robots: bool = False
    ) -> tuple[HTTPResult, WebsiteURL]:
        redirects: set[str] = set()
        for _ in range(_MAX_REDIRECTS + 1):
            if url.origin != self.seed.origin:
                raise CrawlInputError("outside_origin")
            if url.url in redirects:
                raise CrawlInputError("redirect_limit")
            if url.url in visited:
                raise CrawlInputError("duplicate")
            if not for_robots and self.robots is not None:
                if not self.robots.can_fetch(USER_AGENT, url.url):
                    raise CrawlInputError("robots_disallowed")
            redirects.add(url.url)
            visited.add(url.url)
            result = self.request(url)
            if result.status not in _REDIRECTS:
                return result, url
            location = result.headers.get("location")
            if not location:
                raise CrawlInputError("invalid_response")
            url = _relative_url(url, location)
        raise CrawlInputError("redirect_limit")


def _content_type(result: HTTPResult) -> tuple[str, str | None]:
    message = Message()
    raw_type = result.headers.get("content-type", "")
    message["content-type"] = raw_type
    return raw_type.split(";", 1)[0].strip().lower(), message.get_content_charset()


def _robots(state: _FetchState) -> RobotFileParser | None:
    url = _relative_url(state.seed, "/robots.txt")
    result, _ = state.follow(url, visited=set(), for_robots=True)
    if result.status in {404, 410}:
        return None
    if result.status != 200 or _content_type(result)[0] != "text/plain":
        raise CrawlInputError("robots_unavailable")
    try:
        lines = result.body.decode("utf-8-sig").splitlines()
        if len(lines) > _ROBOT_LINES or any(len(line) > _ROBOT_LINE_LENGTH for line in lines):
            raise CrawlInputError("robots_unavailable")
        policy = RobotFileParser()
        policy.parse(lines)
    except ValueError:
        raise CrawlInputError("robots_unavailable") from None
    return policy


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.text: list[str] = []
        self.links: dict[str, None] = {}
        self.hidden: list[str] = []
        self.in_title = False
        self.in_head = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HIDDEN_HTML:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag == "head":
            self.in_head = True
        if tag == "title":
            self.in_title = True
        if tag in _HTML_BLOCKS:
            self.text.append("\n")
        if tag == "a" and len(self.links) < _MAX_LINKS:
            attributes = dict(attrs)
            href = attributes.get("href")
            rel = (attributes.get("rel") or "").lower().split()
            if href and len(href) <= MAX_URL_LENGTH and "nofollow" not in rel:
                self.links[href] = None

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag == "head":
            self.in_head = False
        if tag == "title":
            self.in_title = False
        if tag in _HTML_BLOCKS:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        if self.in_title:
            self.title.append(data)
        elif not self.in_head:
            self.text.append(data)


def _clean_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = (" ".join(_CONTROL.sub(" ", line).split()) for line in value.split("\n"))
    text = "\n".join(line for line in lines if line)
    return text.encode("utf-8")[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore").strip()


def _read_page(result: HTTPResult) -> tuple[str | None, str, Iterable[str]]:
    mime, charset = _content_type(result)
    if mime not in _PAGE_TYPES:
        raise CrawlInputError("unsupported_content_type")
    charsets = {
        "utf-8",
        "utf8",
        "us-ascii",
        "ascii",
        "iso-8859-1",
        "latin-1",
        "windows-1252",
        "cp1252",
    }
    charset = charset if charset in charsets else "utf-8-sig"
    decoded = result.body.decode(charset, errors="replace")
    if mime == "text/plain":
        return None, _clean_text(decoded), ()
    parser = _PageParser()
    parser.feed(decoded)
    parser.close()
    title = _clean_text("".join(parser.title))[:MAX_TITLE_LENGTH] or None
    return title, _clean_text("".join(parser.text)), parser.links


def _read_sitemap(body: bytes) -> tuple[bool, list[str]]:
    parser = expat.ParserCreate(namespace_separator="}")
    path: list[str] = []
    locations: list[str] = []
    chunks: list[str] = []
    size = 0
    in_loc = False
    is_index = False

    def reject_doctype(*_: object) -> None:
        raise CrawlInputError("invalid_sitemap")

    def start(tag: str, _: dict[str, str]) -> None:
        nonlocal size, in_loc, is_index
        name = tag.removeprefix(_SITEMAP_NAMESPACE)
        path.append(name)
        if len(path) > _MAX_XML_DEPTH:
            raise CrawlInputError("invalid_sitemap")
        if len(path) == 1:
            if name not in {"urlset", "sitemapindex"}:
                raise CrawlInputError("invalid_sitemap")
            is_index = name == "sitemapindex"
        if len(path) == 3 and len(locations) < _MAX_SITEMAP_LOCATIONS:
            in_loc = name == "loc" and path[1] == ("sitemap" if is_index else "url")
            size = 0
            chunks.clear()

    def characters(text: str) -> None:
        nonlocal size
        if in_loc and len(path) == 3:
            size += len(text)
            if size <= MAX_URL_LENGTH:
                chunks.append(text)

    def end(_: str) -> None:
        nonlocal in_loc
        if in_loc and len(path) == 3:
            if size <= MAX_URL_LENGTH:
                locations.append("".join(chunks).strip())
            in_loc = False
        path.pop()

    parser.StartDoctypeDeclHandler = reject_doctype
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    try:
        parser.Parse(body, True)
    except expat.ExpatError, LookupError:
        raise CrawlInputError("invalid_sitemap") from None
    return is_index, locations


def _record(
    url: WebsiteURL,
    *,
    final_url: WebsiteURL | None = None,
    title: str | None = None,
    text: str | None = None,
    reason: str | None = None,
) -> CrawlPage:
    status: Literal["fetched", "skipped", "failed"] = "fetched"
    if reason is not None:
        status = "skipped" if reason in _SKIPPED else "failed"
    return {
        "url": url.url,
        "final_url": final_url.url if final_url is not None else None,
        "title": title,
        "status": status,
        "reason": reason,
        "text": text,
    }


def _discover(base: WebsiteURL, links: Iterable[str]) -> Iterable[WebsiteURL]:
    for link in links:
        if not link or len(link) > MAX_URL_LENGTH or _CONTROL.search(link) or "\\" in link:
            continue
        try:
            candidate = _relative_url(base, link)
        except CrawlInputError:
            continue
        if candidate.origin == base.origin:
            yield candidate


def _sitemaps(state: _FetchState, emit: Callable[[CrawlPage], None]) -> Iterable[WebsiteURL]:
    pending = deque([(state.seed, 0)])
    seen = {state.seed.url}
    visited: set[str] = set()
    for _ in range(MAX_SITEMAPS):
        if not pending:
            break
        url, depth = pending.popleft()
        final_url = None
        try:
            result, final_url = state.follow(url, visited=visited)
            if result.status != 200:
                raise CrawlInputError("http_error")
            if _content_type(result)[0] not in _SITEMAP_TYPES:
                raise CrawlInputError("unsupported_content_type")
            index, locations = _read_sitemap(result.body)
            if not index:
                yield from _discover(final_url, locations)
            elif depth < _MAX_DEPTH:
                for nested in _discover(final_url, locations):
                    if nested.url not in seen and len(seen) < MAX_SITEMAPS:
                        seen.add(nested.url)
                        pending.append((nested, depth + 1))
        except CrawlInputError as error:
            emit(_record(url, final_url=final_url, reason=error.code))


def crawl_website(
    url: str,
    *,
    sitemap: bool = False,
    on_result: Callable[[CrawlPage], None] | None = None,
    resolver: Resolver | None = None,
    transport: Transport | None = None,
) -> list[CrawlPage]:
    """Crawl a page or a sitemap and emit each bounded provenance record."""
    seed = _website_url(url)
    state = _FetchState(seed, resolver or resolve_host, transport or fetch_pinned)
    records: list[CrawlPage] = []

    def emit(record: CrawlPage) -> None:
        records.append(record)
        if on_result is not None:
            on_result(record)

    try:
        state.robots = _robots(state)
    except CrawlInputError as error:
        reason = error.code if error.code == "blocked_destination" else "robots_unavailable"
        emit(_record(seed, reason=reason))
        return records

    pending: deque[tuple[WebsiteURL, int]] = deque()
    queued: set[str] = set()

    def enqueue(candidates: Iterable[WebsiteURL], depth: int) -> None:
        for candidate in candidates:
            if len(queued) >= _MAX_DISCOVERED_PAGES:
                break
            if candidate.url not in queued:
                queued.add(candidate.url)
                pending.append((candidate, depth))

    enqueue(_sitemaps(state, emit) if sitemap else (seed,), 0)
    if sitemap and not pending and not records:
        emit(_record(seed, reason="invalid_sitemap"))

    visited: set[str] = set()
    for _ in range(MAX_CRAWL_PAGES):
        if not pending:
            break
        page, depth = pending.popleft()
        final_url = None
        try:
            result, final_url = state.follow(page, visited=visited)
            if result.status != 200:
                raise CrawlInputError("http_error")
            title, text, links = _read_page(result)
            if text:
                record = _record(page, final_url=final_url, title=title, text=text)
            else:
                record = _record(page, final_url=final_url, title=title, reason="empty_page")
            if depth < _MAX_DEPTH:
                enqueue(_discover(final_url, links), depth + 1)
        except CrawlInputError as error:
            record = _record(page, final_url=final_url, reason=error.code)
        emit(record)
        if record["reason"] in {"timeout", "crawl_limit"} and (
            monotonic() >= state.deadline or state.requests_left <= 0 or state.bytes_left <= 0
        ):
            break
    return records
