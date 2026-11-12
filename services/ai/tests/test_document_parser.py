"""Exercise document extraction with small, synthetic fixtures."""

import logging
from io import BytesIO
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pypdf import PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from knowledge_assistant import document_parser as parser
from knowledge_assistant.document_parser import (
    DocumentParseError,
    parse_document,
    validate_signature,
)

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)


def _pdf(*streams: bytes | None, encrypted: bool = False) -> bytes:
    output = BytesIO()
    with PdfWriter() as writer:
        for data in streams:
            page = writer.add_blank_page(width=200, height=200)
            if data is None:
                continue
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {
                            NameObject("/F1"): DictionaryObject(
                                {
                                    NameObject("/Type"): NameObject("/Font"),
                                    NameObject("/Subtype"): NameObject("/Type1"),
                                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                                }
                            )
                        }
                    )
                }
            )
            content = DecodedStreamObject()
            content.set_data(data)
            page.replace_contents(content)
        if encrypted:
            writer.encrypt(f"Synthetic fixture {uuid4().hex}", algorithm="RC4-128")
        writer.write(output)
    return output.getvalue()


def _docx(
    paragraphs: str,
    *,
    prefix: str = "",
    namespace: str = WORD_NAMESPACE,
    content_type: str = WORD_CONTENT_TYPE,
    encoding: str = "utf-8",
    extra_parts: dict[str, bytes] | None = None,
) -> bytes:
    output = BytesIO()
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Override PartName="/word/document.xml" ContentType="{content_type}"/>'
        "</Types>"
    )
    document = (
        f'{prefix}<w:document xmlns:w="{namespace}"><w:body>{paragraphs}</w:body></w:document>'
    )
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("word/document.xml", document.encode(encoding))
        for path, data in (extra_parts or {}).items():
            package.writestr(path, data)
    return output.getvalue()


def _assert_parse_error(data: bytes, kind: str, expected: str) -> None:
    with pytest.raises(DocumentParseError, match=f"^{expected}$") as caught:
        parse_document(data, kind)
    assert caught.value.code == expected


def test_pdf_keeps_one_based_page_numbers_around_a_blank_page() -> None:
    content = _pdf(
        b"BT /F1 12 Tf 20 150 Td (First fictional page) Tj ET",
        None,
        b"BT /F1 12 Tf 20 150 Td (Third fictional page) Tj ET",
    )
    assert parse_document(content, "pdf") == [
        {"text": "First fictional page", "page": 1, "heading": None},
        {"text": "Third fictional page", "page": 3, "heading": None},
    ]


def test_pdf_rejects_encrypted_documents_and_caps_the_page_count() -> None:
    _assert_parse_error(_pdf(None, encrypted=True), "pdf", "encrypted_document")
    _assert_parse_error(_pdf(*([None] * 101)), "pdf", "document_limits_exceeded")


@pytest.mark.parametrize(
    ("content", "expected"),
    [(None, "empty_document"), (b"0 0 40 40 re f", "scanned_pdf")],
)
def test_pdf_distinguishes_blank_and_graphical_pages(content: bytes | None, expected: str) -> None:
    _assert_parse_error(_pdf(content), "pdf", expected)


def test_pdf_library_diagnostics_do_not_reveal_document_data(monkeypatch, caplog) -> None:
    marker = "Fictional diagnostic field"
    original_level = logging.getLogger("pypdf").level

    def reject_fixture(*_args, **_kwargs):
        logging.getLogger("pypdf.reader").warning(marker)
        raise PdfReadError(marker)

    monkeypatch.setattr(parser, "PdfReader", reject_fixture)
    _assert_parse_error(b"%PDF-1.7\nFictional document", "pdf", "invalid_document")
    assert marker not in caplog.text
    assert logging.getLogger("pypdf").level == original_level


def test_docx_keeps_headings_paragraphs_and_table_text() -> None:
    document = _docx(
        "<w:p><w:r><w:t>Introduction</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        "<w:r><w:t>Support guide</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Billing &amp; support</w:t><w:tab/><w:t>Hours</w:t>"
        "<w:br/><w:t>Monday</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>First table cell</w:t></w:r></w:p>"
        "</w:tc></w:tr></w:tbl>",
        extra_parts={"word/_rels/document.xml.rels": b"Unused relationship fixture"},
    )
    assert parse_document(document, "docx") == [
        {"text": "Introduction", "page": None, "heading": None},
        {
            "text": "Support guide\nBilling & support\tHours\nMonday\nFirst table cell",
            "page": None,
            "heading": "Support guide",
        },
    ]


def test_docx_reads_strict_namespace_and_utf16() -> None:
    document = _docx(
        "<w:p><w:r><w:t>Fictional café</w:t></w:r></w:p>",
        namespace="http://purl.oclc.org/ooxml/wordprocessingml/main",
        encoding="utf-16",
    )
    assert parse_document(document, "docx") == [
        {"text": "Fictional café", "page": None, "heading": None}
    ]


def test_docx_rejects_document_type_declarations_without_an_entity_fixture() -> None:
    document = _docx("<w:p/>", prefix="<!DOCTYPE w:document>")
    _assert_parse_error(document, "docx", "invalid_document")


def test_docx_rejects_ambiguous_parts_and_non_docx_content_types() -> None:
    duplicate = _docx("<w:p/>", extra_parts={"word/DOCUMENT.XML": b"An ambiguous part"})
    _assert_parse_error(duplicate, "docx", "invalid_document")
    other_format = _docx("<w:p/>", content_type="text/xml")
    _assert_parse_error(other_format, "docx", "invalid_document")
    _assert_parse_error(_docx("<w:p/>"), "docx", "empty_document")


@pytest.mark.parametrize(
    ("limit", "value"),
    [
        ("MAX_DOCX_ENTRIES", 1),
        ("MAX_DOCX_INFLATED_BYTES", 50),
        ("MAX_DOCX_XML_BYTES", 80),
        ("_MAX_XML_DEPTH", 2),
        ("_MAX_XML_ELEMENTS", 3),
    ],
)
def test_docx_limits_stop_ordinary_files_at_a_small_configured_budget(
    monkeypatch, limit: str, value: int
) -> None:
    monkeypatch.setattr(parser, limit, value)
    document = _docx("<w:p><w:r><w:t>Ordinary text</w:t></w:r></w:p>")
    _assert_parse_error(document, "docx", "document_limits_exceeded")


def test_markdown_preserves_fenced_text_and_extracts_utf8_headings() -> None:
    document = (
        "\ufeffIntroduction\r\n# First topic ###\r\nText for café\r\n"
        "~~~\r\n# A literal heading inside code\r\n~~~\r\n## Second topic\r\nFinal line"
    ).encode()
    assert parse_document(document, "markdown") == [
        {"text": "Introduction", "page": None, "heading": None},
        {
            "text": "First topic\nText for café\n~~~\n# A literal heading inside code\n~~~",
            "page": None,
            "heading": "First topic",
        },
        {"text": "Second topic\nFinal line", "page": None, "heading": "Second topic"},
    ]


def test_output_uses_utf8_byte_limit_and_rejects_excess_sections(monkeypatch) -> None:
    with monkeypatch.context() as context:
        context.setattr(parser, "MAX_EXTRACTED_BYTES", 4)
        _assert_parse_error("café".encode(), "markdown", "document_limits_exceeded")
    monkeypatch.setattr(parser, "MAX_SECTIONS", 1)
    _assert_parse_error(b"# First\nText\n# Second\nText", "markdown", "document_limits_exceeded")


@pytest.mark.parametrize(
    ("kind", "data", "expected"),
    [
        ("other", b"Fictional text", "unsupported_type"),
        ("markdown", b"", "empty_document"),
        ("markdown", b" \r\n\t", "empty_document"),
        ("markdown", b"\xff", "invalid_document"),
        ("markdown", b"Binary\x00fixture", "invalid_document"),
        ("pdf", b"Ordinary text", "invalid_document"),
        ("pdf", b"%PDF-1.7\nIncomplete file", "invalid_document"),
        ("docx", b"PK\x03\x04Incomplete file", "invalid_document"),
    ],
)
def test_parse_errors_expose_only_a_stable_code(kind: str, data: bytes, expected: str) -> None:
    _assert_parse_error(data, kind, expected)


def test_signature_check_enforces_five_mib_and_defers_structural_work() -> None:
    validate_signature(b"%PDF-1.7\nIncomplete file", "pdf")
    validate_signature(b"PK\x03\x04Incomplete file", "docx")
    limit = 5 * 1024 * 1024
    validate_signature(b"a" * limit, "markdown")
    with pytest.raises(DocumentParseError, match="^document_too_large$"):
        validate_signature(b"a" * (limit + 1), "markdown")
