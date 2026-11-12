"""Bounded, text-only document extraction for the ingestion workhorse."""

import logging
import re
import stat
import xml.etree.ElementTree as ET
import zlib
from io import BytesIO
from typing import TypedDict
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from pypdf import PdfReader, apply_configuration
from pypdf.errors import DependencyError, FileNotDecryptedError, LimitReachedError, PdfReadError

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_SECTIONS = 256
MAX_EXTRACTED_BYTES = 1024 * 1024
MAX_DOCX_ENTRIES = 256
MAX_DOCX_INFLATED_BYTES = 20 * 1024 * 1024
MAX_DOCX_XML_BYTES = 2 * 1024 * 1024
_MAX_XML_DEPTH = 64
_MAX_XML_ELEMENTS = 50_000
_MAX_PDF_STREAM_BYTES = 4 * 1024 * 1024
_MAX_HEADING_CHARS = 200

_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ATX_HEADING = re.compile(r" {0,3}#{1,6}[ \t]+(.+)")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_CONTENT_TYPES = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_DOCX_MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_WORD_NAMESPACES = (
    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}",
    "{http://purl.oclc.org/ooxml/wordprocessingml/main}",
)


class DocumentSection(TypedDict):
    text: str
    page: int | None
    heading: str | None


class DocumentParseError(ValueError):
    """A stable error code suitable for storage without document data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Sections:
    def __init__(self) -> None:
        self.items: list[DocumentSection] = []
        self.byte_count = 0

    def add(self, text: str, *, page: int | None = None, heading: str | None = None) -> None:
        text = _CONTROLS.sub("", text).strip()
        if not text:
            return
        if heading is not None:
            heading = _CONTROLS.sub("", heading).strip()[:_MAX_HEADING_CHARS] or None
        try:
            size = len(text.encode("utf-8")) + len((heading or "").encode("utf-8"))
        except UnicodeError:
            raise DocumentParseError("invalid_document") from None
        if len(self.items) >= MAX_SECTIONS or self.byte_count + size > MAX_EXTRACTED_BYTES:
            raise DocumentParseError("document_limits_exceeded")
        self.byte_count += size
        self.items.append({"text": text, "page": page, "heading": heading})


def _decode_markdown(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise DocumentParseError("invalid_document") from None
    if _CONTROLS.search(text):
        raise DocumentParseError("invalid_document")
    return text


def _validate_input(data: bytes, kind: str) -> str | None:
    if kind not in {"pdf", "docx", "markdown"}:
        raise DocumentParseError("unsupported_type")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentParseError("document_too_large")
    if not data:
        raise DocumentParseError("empty_document")
    if kind == "markdown":
        return _decode_markdown(data)
    signature = b"%PDF-" if kind == "pdf" else b"PK\x03\x04"
    if not data.startswith(signature):
        raise DocumentParseError("invalid_document")
    return None


def validate_signature(data: bytes, kind: str) -> None:
    """Check size and format markers without opening PDF objects or ZIP entries."""
    _validate_input(data, kind)


def _pdf_sections(data: bytes, sections: _Sections) -> None:
    has_page_content = False
    # pypdf diagnostics may quote untrusted input. Each RQ workhorse runs one job.
    logger = logging.getLogger("pypdf")
    previous_level = logger.level
    logger.setLevel(logging.CRITICAL + 1)
    try:
        with (
            apply_configuration(
                maximum_declared_stream_length=MAX_DOCUMENT_BYTES,
                array_based_stream_maximum_output_length=_MAX_PDF_STREAM_BYTES,
                zlib_maximum_output_length=_MAX_PDF_STREAM_BYTES,
                lzw_maximum_output_length=_MAX_PDF_STREAM_BYTES,
                run_length_maximum_output_length=_MAX_PDF_STREAM_BYTES,
                brotli_maximum_output_length=_MAX_PDF_STREAM_BYTES,
                stream_decoding_work_maximum_length=4 * _MAX_PDF_STREAM_BYTES,
                image_maximum_buffer_size=_MAX_PDF_STREAM_BYTES,
                stream_filters_maximum_length=4,
                stream_parsing_work_maximum_count=150_000,
                page_tree_maximum_entries=512,
                page_tree_maximum_depth=20,
                page_tree_maximum_page_entries=25_000,
                xform_maximum_invocations_per_extraction=1000,
                jbig2dec_binary=None,
            ),
            PdfReader(
                BytesIO(data),
                strict=True,
                root_object_recovery_limit=128,
                xref_stream_maximum_entries=25_000,
            ) as reader,
        ):
            if reader.is_encrypted:
                raise DocumentParseError("encrypted_document")
            # Check the declared count before pypdf flattens the bounded page tree.
            declared_count = reader.root_object["/Pages"]["/Count"]
            if not isinstance(declared_count, int) or declared_count < 0:
                raise DocumentParseError("invalid_document")
            if declared_count > MAX_PDF_PAGES or len(reader.pages) > MAX_PDF_PAGES:
                raise DocumentParseError("document_limits_exceeded")
            for number, page in enumerate(reader.pages, start=1):
                contents = page.get_contents()
                if contents is None:
                    continue
                stream = contents.get_data()
                if len(stream) > _MAX_PDF_STREAM_BYTES:
                    raise DocumentParseError("document_limits_exceeded")
                if stream.strip():
                    has_page_content = True
                    sections.add(page.extract_text(), page=number)
    except DocumentParseError:
        raise
    except LimitReachedError, RecursionError, MemoryError:
        raise DocumentParseError("document_limits_exceeded") from None
    except FileNotDecryptedError:
        raise DocumentParseError("encrypted_document") from None
    except (
        PdfReadError,
        DependencyError,
        AttributeError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        EOFError,
        OverflowError,
        zlib.error,
    ):
        raise DocumentParseError("invalid_document") from None
    finally:
        logger.setLevel(previous_level)
    if has_page_content and not sections.items:
        # Image-only or other graphical pages need a text layer before ingestion.
        raise DocumentParseError("scanned_pdf")


def _docx_members(package: ZipFile) -> dict[str, ZipInfo]:
    entries = package.infolist()
    if len(entries) > MAX_DOCX_ENTRIES:
        raise DocumentParseError("document_limits_exceeded")
    members = {}
    seen = set()
    inflated_bytes = 0
    for entry in entries:
        name = entry.filename
        path = name.rstrip("/")
        key = path.casefold()
        file_type = stat.S_IFMT(entry.external_attr >> 16)
        if (
            entry.orig_filename != name
            or _CONTROLS.search(name)
            or "\\" in name
            or ":" in name
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or key in seen
            or (entry.create_system == 3 and file_type not in {0, stat.S_IFREG, stat.S_IFDIR})
            or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
        ):
            raise DocumentParseError("invalid_document")
        if entry.flag_bits & 1:
            raise DocumentParseError("encrypted_document")
        seen.add(key)
        inflated_bytes += entry.file_size
        if inflated_bytes > MAX_DOCX_INFLATED_BYTES:
            raise DocumentParseError("document_limits_exceeded")
        if not entry.is_dir():
            members[name] = entry
    return members


class _DocumentTree(ET.TreeBuilder):
    """Stop XML construction before excessive depth or element allocation."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.elements = 0

    def start(self, tag: str, attrs: dict[str, str]) -> ET.Element:
        self.depth += 1
        self.elements += 1
        if self.depth > _MAX_XML_DEPTH or self.elements > _MAX_XML_ELEMENTS:
            raise DocumentParseError("document_limits_exceeded")
        return super().start(tag, attrs)

    def end(self, tag: str) -> ET.Element:
        self.depth -= 1
        return super().end(tag)

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        # Expat calls the target before processing the declared entities.
        raise DocumentParseError("invalid_document")


def _read_xml(package: ZipFile, entry: ZipInfo | None) -> ET.Element:
    if entry is None:
        raise DocumentParseError("invalid_document")
    if entry.file_size > MAX_DOCX_XML_BYTES:
        raise DocumentParseError("document_limits_exceeded")
    with package.open(entry) as stream:
        data = stream.read(MAX_DOCX_XML_BYTES + 1)
    if len(data) > MAX_DOCX_XML_BYTES:
        raise DocumentParseError("document_limits_exceeded")
    if len(data) != entry.file_size:
        raise DocumentParseError("invalid_document")
    return ET.fromstring(data, parser=ET.XMLParser(target=_DocumentTree()))


def _paragraph_text(paragraph: ET.Element, prefix: str) -> str:
    pieces = []
    pending = list(reversed(paragraph))
    while pending:
        element = pending.pop()
        if element.tag == prefix + "t":
            pieces.append(element.text or "")
        elif element.tag == prefix + "tab":
            pieces.append("\t")
        elif element.tag in {prefix + "br", prefix + "cr"}:
            pieces.append("\n")
        elif element.tag != prefix + "p":
            # Nested paragraphs, such as text boxes, are visited separately.
            pending.extend(reversed(element))
    return "".join(pieces)


def _docx_sections(data: bytes, sections: _Sections) -> None:
    try:
        with ZipFile(BytesIO(data)) as package:
            members = _docx_members(package)
            types = _read_xml(package, members.get("[Content_Types].xml"))
            if types.tag != _CONTENT_TYPES + "Types" or not any(
                override.get("PartName") == "/word/document.xml"
                and override.get("ContentType") == _DOCX_MAIN_TYPE
                for override in types.findall(_CONTENT_TYPES + "Override")
            ):
                raise DocumentParseError("invalid_document")
            # Read only the Word document XML. Relationships and embedded parts are never opened.
            root = _read_xml(package, members.get("word/document.xml"))
    except DocumentParseError:
        raise
    except BadZipFile, ET.ParseError, EOFError, LookupError, ValueError, RuntimeError, zlib.error:
        raise DocumentParseError("invalid_document") from None

    prefix = next(
        (namespace for namespace in _WORD_NAMESPACES if root.tag == namespace + "document"),
        None,
    )
    body = root.find(prefix + "body") if prefix is not None else None
    if prefix is None or body is None:
        raise DocumentParseError("invalid_document")

    lines: list[str] = []
    heading = None
    for paragraph in body.iter(prefix + "p"):
        text = _paragraph_text(paragraph, prefix).strip()
        if not text:
            continue
        style = paragraph.find(f"{prefix}pPr/{prefix}pStyle")
        style_name = ""
        if style is not None:
            style_name = style.get(prefix + "val", "").replace(" ", "").lower()
        if re.fullmatch(r"title|heading[1-9]", style_name):
            sections.add("\n".join(lines), heading=heading)
            heading, lines = text, []
        lines.append(text)
    sections.add("\n".join(lines), heading=heading)


def _markdown_sections(text: str, sections: _Sections) -> None:
    lines: list[str] = []
    heading = None
    fence = None
    for line in text.splitlines():
        marker = _FENCE.fullmatch(line)
        if marker:
            run, suffix = marker.groups()
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not suffix.strip():
                fence = None
            lines.append(line)
            continue
        match = _ATX_HEADING.fullmatch(line) if fence is None else None
        if match:
            label = re.sub(r"[ \t]+#+[ \t]*$", "", match[1]).strip()
            if label:
                sections.add("\n".join(lines), heading=heading)
                heading, lines = label, [label]
                continue
        lines.append(line)
    sections.add("\n".join(lines), heading=heading)


def parse_document(data: bytes, kind: str) -> list[DocumentSection]:
    """Extract text in the worker and return complete sections or one safe error."""
    markdown = _validate_input(data, kind)
    sections = _Sections()
    if kind == "pdf":
        _pdf_sections(data, sections)
    elif kind == "docx":
        _docx_sections(data, sections)
    elif markdown is not None:
        _markdown_sections(markdown, sections)
    if not sections.items:
        raise DocumentParseError("empty_document")
    return sections.items
