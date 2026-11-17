import pytest

from knowledge_assistant import indexing_chunks
from knowledge_assistant.indexing_chunks import ChunkInputError, build_chunks, chunk_input_hash


def test_document_windows_overlap_and_never_mix_citation_sections() -> None:
    first_page = " ".join(f"entry-{index:04d}" for index in range(220))
    sections = [
        {"page": 3, "heading": "Shipping policy", "text": first_page},
        {"page": 4, "heading": "Hours", "text": "Open Monday.\r\n Closed Saturday."},
        {"page": None, "heading": None, "text": "  "},
    ]
    chunks = build_chunks("document", sections)
    assert len(chunks) == 4
    assert [chunk["ordinal"] for chunk in chunks] == list(range(len(chunks)))
    assert [chunk["page"] for chunk in chunks] == [3, 3, 3, 4]
    assert all(chunk["heading"] == "Shipping policy" for chunk in chunks[:-1])
    assert all(len(chunk["text"]) <= 1200 for chunk in chunks)
    assert all(chunk["url"] is None and chunk["title"] is None for chunk in chunks)
    end_of_first = chunks[0]["text"].split()[-1]
    assert end_of_first in chunks[1]["text"].split()
    assert chunks[-1]["text"] == "Open Monday. Closed Saturday."
    assert len({chunk["anchor"] for chunk in chunks}) == len(chunks)
    assert chunks == build_chunks("document", sections)
    assert chunk_input_hash(chunks) == chunk_input_hash(build_chunks("document", sections))


def test_unbroken_unicode_content_is_split_and_overlap_makes_progress() -> None:
    text = "🚲" * 3000
    chunks = build_chunks("document", [{"text": text, "page": 100}])
    assert [len(chunk["text"]) for chunk in chunks] == [1200, 1200, 960]
    assert all(len(chunk["text"].encode()) <= 8192 for chunk in chunks)
    assert all(chunk["page"] == 100 for chunk in chunks)


def test_website_anchors_track_final_url_across_crawl_orders() -> None:
    guide = {
        "status": "completed",
        "url": "https://fixture.example/guide",
        "final_url": "https://fixture.example/manual",
        "title": "Fixture guide",
        "text": "The guide has a map.",
    }
    hours = {
        "status": "completed",
        "url": "https://fixture.example/hours",
        "text": "Open weekends.",
    }
    skipped = {"status": "skipped", "url": "https://fixture.example/private"}
    initial = build_chunks("website", [guide, hours, skipped, guide])
    reordered = build_chunks("website", [hours, guide])
    assert len(initial) == 2
    assert initial[0]["url"] == guide["final_url"]
    assert initial[0]["title"] == guide["title"]
    assert initial[0]["page"] is None and initial[0]["heading"] is None
    assert initial[0]["anchor"] == reordered[1]["anchor"]
    assert initial[1]["anchor"] == reordered[0]["anchor"]
    assert chunk_input_hash(initial) != chunk_input_hash(reordered)
    modified = build_chunks("website", [{**guide, "title": "New title"}, hours])
    assert modified[0]["anchor"] == initial[0]["anchor"]
    assert chunk_input_hash(modified) != chunk_input_hash(initial)


@pytest.mark.parametrize(
    ("kind", "entry"),
    [
        ("unrecognized", {"text": "A guide"}),
        ("document", None),
        ("document", {"page": True, "text": "A guide"}),
        ("document", {"heading": "a" * 201, "text": "A guide"}),
        ("document", {"text": ["A guide"]}),
        ("website", {"status": "completed", "url": "file:///guide", "text": "A guide"}),
        (
            "website",
            {
                "status": "completed",
                "url": "https://fixture.example/?view=full",
                "text": "A guide",
            },
        ),
    ],
)
def test_bad_snapshot_data_returns_a_safe_code(kind, entry) -> None:
    with pytest.raises(ChunkInputError, match="^invalid_source_content$"):
        build_chunks(kind, [entry])


def test_chunk_limit_discards_unpublishable_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(indexing_chunks, "MAX_CHUNKS", 2)
    with pytest.raises(ChunkInputError, match="^index_limits_exceeded$"):
        build_chunks("document", [{"text": "abc" * 1000}])
