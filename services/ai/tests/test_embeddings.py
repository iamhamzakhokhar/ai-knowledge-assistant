"""Check the offline provider and malformed paid-provider responses without network I/O."""

import json
import math

import pytest

from knowledge_assistant import embeddings
from knowledge_assistant.config import Settings


def test_local_vectors_are_repeatable_normalized_and_token_based():
    settings = Settings(embedding_provider="local")
    config = embeddings.embedding_config(settings)
    vectors = embeddings.embed_texts(
        ["Service hours", "ＳＥＲＶＩＣＥ HOURS", "Refund policy"], config, settings
    )
    assert config == embeddings.EmbeddingConfig("local", "feature-hash-v1", 256, 1)
    assert vectors[0] == vectors[1] and vectors[0] != vectors[2]
    assert all(math.hypot(*vector) == pytest.approx(1) for vector in vectors)


@pytest.mark.parametrize(
    ("vectors", "error"),
    [
        ([[1.0]], "embedding_dimension_mismatch"),
        ([[0.0, 0.0]], "embedding_invalid_response"),
        ([[float("nan"), 1.0]], "embedding_invalid_response"),
        ([[True, 1]], "embedding_invalid_response"),
        ([], "embedding_invalid_response"),
    ],
)
def test_invalid_vectors_are_rejected(vectors, error):
    with pytest.raises(embeddings.EmbeddingError, match=error):
        embeddings.validate_embeddings(vectors, 1, 2)


def test_openai_contract_maps_batch_indices_and_rejects_wrong_dimensions(tmp_path, monkeypatch):
    key_file = tmp_path / "test-key"
    key_file.write_text("fixture-credential\n")
    settings = Settings(
        embedding_provider="openai", embedding_dimension=2, openai_api_key_file=key_file
    )
    config = embeddings.embedding_config(settings)
    data = [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]
    calls = []

    def fixture_request(body, credential, timeout):
        assert credential == "fixture-credential" and 0 < timeout <= 12
        calls.append(json.loads(body))
        return {"model": config.model, "data": data}

    monkeypatch.setattr(embeddings, "_request_openai", fixture_request)
    assert embeddings.embed_texts(["First", "Second"], config, settings) == [[1.0, 0.0], [0.0, 1.0]]
    assert calls == [
        {
            "input": ["First", "Second"],
            "model": config.model,
            "encoding_format": "float",
            "dimensions": 2,
        }
    ]
    data[0]["embedding"] = [0, 1, 2]
    with pytest.raises(embeddings.EmbeddingError, match="embedding_dimension_mismatch"):
        embeddings.embed_texts(["First", "Second"], config, settings)


def test_missing_provider_key_and_oversized_input_fail_before_request():
    settings = Settings(embedding_provider="openai", openai_api_key_file=None)
    config = embeddings.embedding_config(settings)
    with pytest.raises(embeddings.EmbeddingError, match="embedding_credentials_missing"):
        embeddings.embed_texts(["Text"], config, settings)
    with pytest.raises(embeddings.EmbeddingError, match="embedding_input_limit"):
        embeddings.embed_texts(["é" * 5000], config, settings)
