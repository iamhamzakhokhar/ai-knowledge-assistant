"""Generate and validate vectors without exposing provider credentials or source text."""

import hashlib
import http.client
import json
import math
import re
import ssl
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from knowledge_assistant.config import Settings
from knowledge_assistant.indexing_chunks import INDEXING_VERSION
from knowledge_assistant.secret_files import SecretFileError, read_secret_file

LOCAL_MODEL = "feature-hash-v1"
LOCAL_DIMENSION = 256
MAX_EMBEDDING_INPUTS = 2048
MAX_INPUT_BYTES = 8192
_OPENAI_MODELS = frozenset({"text-embedding-3-small", "text-embedding-3-large"})
_BATCH_SIZE = 32
_RESPONSE_LIMIT = 4 * 1024 * 1024


class EmbeddingError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    provider: str
    model: str
    dimension: int
    index_version: int = INDEXING_VERSION


def embedding_config(settings: Settings) -> EmbeddingConfig:
    if settings.embedding_provider == "local":
        if settings.embedding_model not in {
            None,
            LOCAL_MODEL,
        } or settings.embedding_dimension not in {None, LOCAL_DIMENSION}:
            raise EmbeddingError("embedding_config_invalid")
        return EmbeddingConfig("local", LOCAL_MODEL, LOCAL_DIMENSION)
    model = settings.embedding_model or "text-embedding-3-small"
    if model not in _OPENAI_MODELS:
        raise EmbeddingError("embedding_config_invalid")
    return EmbeddingConfig("openai", model, settings.embedding_dimension or 1536)


def validate_embeddings(vectors: object, count: int, dimension: int) -> list[list[float]]:
    """Reject invalid geometry before it reaches pgvector or cosine search."""
    if not 1 <= dimension <= 1536:
        raise EmbeddingError("embedding_dimension_mismatch")
    if not isinstance(vectors, list) or len(vectors) != count:
        raise EmbeddingError("embedding_invalid_response")
    validated = []
    for vector in vectors:
        if not isinstance(vector, list):
            raise EmbeddingError("embedding_invalid_response")
        if len(vector) != dimension:
            raise EmbeddingError("embedding_dimension_mismatch")
        if any(type(value) not in {int, float} or not -1e6 <= value <= 1e6 for value in vector):
            raise EmbeddingError("embedding_invalid_response")
        if math.hypot(*vector) < 1e-20:
            raise EmbeddingError("embedding_invalid_response")
        validated.append([float(value) for value in vector])
    return validated


def _local_embedding(text: str) -> list[float]:
    words = re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()) or [text]
    result = [0.0] * LOCAL_DIMENSION
    for word in words:
        digest = hashlib.blake2s(word.encode("utf-8"), digest_size=4).digest()
        result[int.from_bytes(digest[:2], "big") % LOCAL_DIMENSION] += 1.0
    length = math.hypot(*result)
    return [value / length for value in result]


def _request_openai(body: bytes, api_key: str, timeout: float) -> object:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    connection = http.client.HTTPSConnection("api.openai.com", timeout=timeout, context=context)
    try:
        connection.request(
            "POST",
            "/v1/embeddings",
            body,
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with connection.getresponse() as response:
            if response.status != 200:
                raise EmbeddingError("embedding_unavailable")
            payload = response.read(_RESPONSE_LIMIT + 1)
        if len(payload) > _RESPONSE_LIMIT:
            raise EmbeddingError("embedding_invalid_response")
        return json.loads(payload)
    except (UnicodeError, ValueError) as error:
        if isinstance(error, EmbeddingError):
            raise
        raise EmbeddingError("embedding_invalid_response") from None
    except OSError, http.client.HTTPException:
        raise EmbeddingError("embedding_unavailable") from None
    finally:
        connection.close()


def _openai_embeddings(
    texts: Sequence[str], config: EmbeddingConfig, settings: Settings
) -> list[list[float]]:
    try:
        key = read_secret_file(settings.openai_api_key_file)
        if not key.isascii() or not key.isprintable() or any(char.isspace() for char in key):
            raise SecretFileError("Invalid configured credential")
    except SecretFileError:
        raise EmbeddingError("embedding_credentials_missing") from None

    deadline = time.monotonic() + 60
    all_vectors = []
    for offset in range(0, len(texts), _BATCH_SIZE):
        batch = list(texts[offset : offset + _BATCH_SIZE])
        body = json.dumps(
            {
                "model": config.model,
                "input": batch,
                "encoding_format": "float",
                "dimensions": config.dimension,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        timeout = min(12.0, deadline - time.monotonic())
        if timeout <= 0:
            raise EmbeddingError("embedding_unavailable")
        payload = _request_openai(body, key, timeout)
        if not isinstance(payload, dict) or payload.get("model") != config.model:
            raise EmbeddingError("embedding_invalid_response")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingError("embedding_invalid_response")
        ordered = {}
        for item in data:
            index = item.get("index") if isinstance(item, dict) else None
            if type(index) is not int or not 0 <= index < len(batch) or index in ordered:
                raise EmbeddingError("embedding_invalid_response")
            ordered[index] = item.get("embedding")
        all_vectors.extend(
            validate_embeddings(
                [ordered[i] for i in range(len(batch))], len(batch), config.dimension
            )
        )
    return all_vectors


def embed_texts(
    texts: Sequence[str], config: EmbeddingConfig, settings: Settings
) -> list[list[float]]:
    if config != embedding_config(settings):
        raise EmbeddingError("embedding_config_invalid")
    try:
        valid = 1 <= len(texts) <= MAX_EMBEDDING_INPUTS and all(
            text.strip() and len(text.encode("utf-8")) <= MAX_INPUT_BYTES for text in texts
        )
    except UnicodeError:
        valid = False
    if not valid:
        raise EmbeddingError("embedding_input_limit")
    if config.provider == "local":
        return [_local_embedding(text) for text in texts]
    return _openai_embeddings(texts, config, settings)
