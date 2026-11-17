"""Stable, bounded text windows and citation metadata for indexed snapshots."""

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from typing import TypedDict

from knowledge_assistant.website_crawler import CrawlInputError, normalize_website_url

INDEXING_VERSION = 1
MAX_CHUNKS = 2048
MAX_CHUNK_CHARACTERS = 1200
CHUNK_OVERLAP_CHARACTERS = 180


class IndexChunk(TypedDict):
    ordinal: int
    anchor: str
    text: str
    page: int | None
    heading: str | None
    url: str | None
    title: str | None


class ChunkInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _label(value: object, length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or "\x00" in value or len(value) > length:
        raise ChunkInputError("invalid_source_content")
    return value.strip() or None


def _windows(content: str) -> Iterator[str]:
    start = 0
    while start < len(content):
        end = min(start + MAX_CHUNK_CHARACTERS, len(content))
        if end < len(content):
            boundary = content.rfind(" ", end - CHUNK_OVERLAP_CHARACTERS, end + 1)
            if boundary > start:
                end = boundary
        yield content[start:end].strip()
        if end == len(content):
            break
        next_start = end - CHUNK_OVERLAP_CHARACTERS
        # Prefer starting at the next word when the overlap starts midword.
        if content[next_start - 1] != " ":
            boundary = content.find(" ", next_start, next_start + 50)
            if boundary != -1:
                next_start = boundary + 1
        start = next_start


def build_chunks(source_kind: str, entries: Sequence[Mapping[str, object]]) -> list[IndexChunk]:
    """Split completed document sections or crawl pages without mixing citations."""
    if source_kind not in {"document", "website"}:
        raise ChunkInputError("invalid_source_content")
    chunks: list[IndexChunk] = []
    seen_urls: set[str] = set()
    for entry_number, entry in enumerate(entries, start=1):
        if not isinstance(entry, Mapping):
            raise ChunkInputError("invalid_source_content")
        page = heading = url = title = None
        if source_kind == "document":
            page = entry.get("page")
            if page is not None and (type(page) is not int or not 1 <= page <= 100):
                raise ChunkInputError("invalid_source_content")
            heading = _label(entry.get("heading"), 200)
            section = f"section-{entry_number}"
            anchor_prefix = f"page-{page}-{section}" if page is not None else section
        else:
            if entry.get("status") != "completed":
                continue
            url = _label(entry.get("final_url") or entry.get("url"), 2048)
            try:
                canonical = normalize_website_url(url) if url is not None else None
            except CrawlInputError:
                raise ChunkInputError("invalid_source_content") from None
            if url is None or url != canonical:
                raise ChunkInputError("invalid_source_content")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            title = _label(entry.get("title"), 200)
            anchor_prefix = "url-" + hashlib.sha256(url.encode("ascii")).hexdigest()[:24]
        text = entry.get("text")
        if not isinstance(text, str) or "\x00" in text:
            raise ChunkInputError("invalid_source_content")
        content = " ".join(text.split())
        for window, chunk_text in enumerate(_windows(content), start=1):
            if len(chunks) >= MAX_CHUNKS:
                raise ChunkInputError("index_limits_exceeded")
            chunks.append(
                {
                    "ordinal": len(chunks),
                    "anchor": f"{anchor_prefix}-chunk-{window}",
                    "text": chunk_text,
                    "page": page,
                    "heading": heading,
                    "url": url,
                    "title": title,
                }
            )
    return chunks


def chunk_input_hash(chunks: Sequence[IndexChunk]) -> bytes:
    """Fingerprint normalized text, order and citation data for publish deduplication."""
    payload = json.dumps(chunks, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).digest()
