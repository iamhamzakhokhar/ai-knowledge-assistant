"""Direct HTTP transport for the website crawler's validated destinations."""

import http.client
import io
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
from queue import Empty, Queue
from threading import Thread
from time import monotonic

USER_AGENT = "KnowledgeDeskCrawler"
_READ_BUFFER = 8192
_WIRE_OVERHEAD = 128 * 1024

IPAddress = IPv4Address | IPv6Address


class CrawlInputError(ValueError):
    """A stable error code safe to store with a crawl record."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class WebsiteURL:
    url: str
    scheme: str
    host: str
    port: int
    path: str

    @property
    def origin(self) -> tuple[str, str, int]:
        return self.scheme, self.host, self.port

    @property
    def authority(self) -> str:
        return f"[{self.host}]" if ":" in self.host else self.host


@dataclass(frozen=True, slots=True)
class HTTPResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


def remaining(deadline: float) -> float:
    duration = deadline - monotonic()
    if duration <= 0:
        raise CrawlInputError("timeout")
    return duration


def resolve_host(host: str, port: int, deadline: float) -> list[str]:
    """Resolve one absolute DNS name with a caller-enforced deadline."""
    result: Queue[list[str] | None] = Queue(maxsize=1)

    def resolve() -> None:
        try:
            records = socket.getaddrinfo(
                f"{host}.", port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
            )
        except OSError:
            result.put(None)
            return
        result.put([record[4][0] for record in records])

    # getaddrinfo is blocking. An expired resolver can finish independently and cannot connect.
    remaining(deadline)
    Thread(target=resolve, daemon=True).start()
    try:
        addresses = result.get(timeout=remaining(deadline))
    except Empty:
        raise CrawlInputError("timeout") from None
    if not addresses:
        raise CrawlInputError("dns_failure")
    return addresses


class _DeadlineReader(io.RawIOBase):
    def __init__(self, sock: socket.socket, deadline: float, wire_bytes: int) -> None:
        self.sock = sock
        self.deadline = deadline
        self.wire_bytes = wire_bytes

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        if self.wire_bytes <= 0:
            raise CrawlInputError("response_too_large")
        self.sock.settimeout(remaining(self.deadline))
        count = self.sock.recv_into(memoryview(buffer)[: self.wire_bytes])
        self.wire_bytes -= count
        return count


class _ResponseSocket:
    def __init__(self, sock: socket.socket, deadline: float, wire_bytes: int) -> None:
        self.sock = sock
        self.deadline = deadline
        self.wire_bytes = wire_bytes

    def makefile(self, mode: str) -> io.BufferedReader:
        return io.BufferedReader(
            _DeadlineReader(self.sock, self.deadline, self.wire_bytes), buffer_size=_READ_BUFFER
        )


def _read_response(sock: socket.socket, deadline: float, max_bytes: int) -> HTTPResult:
    with http.client.HTTPResponse(
        _ResponseSocket(sock, deadline, max_bytes + _WIRE_OVERHEAD)
    ) as response:
        response.begin()
        headers = {}
        for key in (
            "location",
            "content-type",
            "content-encoding",
            "content-length",
            "transfer-encoding",
        ):
            values = response.headers.get_all(key, [])
            if len(values) > 1:
                raise CrawlInputError("invalid_response")
            if values:
                headers[key] = values[0].strip()
        if response.status != 200:
            return HTTPResult(response.status, headers, b"")
        if headers.get("content-encoding", "identity").lower() != "identity":
            raise CrawlInputError("unsupported_encoding")
        transfer = headers.get("transfer-encoding", "").lower()
        length = headers.get("content-length")
        if transfer and (transfer != "chunked" or length is not None):
            raise CrawlInputError("invalid_response")
        if length is not None:
            if not length.isascii() or not length.isdecimal() or len(length) > 10:
                raise CrawlInputError("invalid_response")
            if int(length) > max_bytes:
                raise CrawlInputError("response_too_large")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise CrawlInputError("response_too_large")
        if length is not None and len(body) != int(length):
            raise CrawlInputError("invalid_response")
        return HTTPResult(response.status, headers, body)


def fetch_pinned(
    url: WebsiteURL, address: IPAddress, deadline: float, max_bytes: int
) -> HTTPResult:
    """Connect directly to a vetted numeric address and preserve TLS identity."""
    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    endpoint = (str(address), url.port) if address.version == 4 else (str(address), url.port, 0, 0)
    try:
        with socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP) as raw:
            raw.settimeout(remaining(deadline))
            raw.connect(endpoint)
            if url.scheme == "https":
                context = ssl.create_default_context()
                raw.settimeout(remaining(deadline))
                with context.wrap_socket(raw, server_hostname=url.host) as secured:
                    return _exchange(secured, url, deadline, max_bytes)
            return _exchange(raw, url, deadline, max_bytes)
    except TimeoutError:
        raise CrawlInputError("timeout") from None
    except http.client.HTTPException:
        raise CrawlInputError("invalid_response") from None
    except OSError:
        raise CrawlInputError("network_error") from None


def _exchange(sock: socket.socket, url: WebsiteURL, deadline: float, max_bytes: int) -> HTTPResult:
    request = (
        f"GET {url.path} HTTP/1.1\r\n"
        f"Host: {url.authority}\r\n"
        f"User-Agent: {USER_AGENT}\r\n"
        "Accept: text/html, text/plain, application/xhtml+xml, application/xml, text/xml\r\n"
        "Accept-Encoding: identity\r\n"
        "Connection: close\r\n\r\n"
    )
    sock.settimeout(remaining(deadline))
    sock.sendall(request.encode("ascii"))
    return _read_response(sock, deadline, max_bytes)
