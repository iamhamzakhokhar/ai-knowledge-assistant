# Knowledge index

Completed document extractions and website crawls queue indexing. The **Knowledge
index** panel shows each source and its active version. Owners and admins can
reindex completed sources, replace a document from **Documents**, or remove a
source from the index. Members can read indexing status. For sources imported
before indexing was added, use **Reindex** to build their first version.

Each chunk stays inside one document section or website page. The chunker
prefers whitespace boundaries, uses up to 1,200 characters per chunk, and
overlaps adjacent chunks by up to 180 characters. It records source IDs,
version IDs, stable chunk anchors, and available page, heading, URL and title
metadata. Reindexing identical inputs with the same provider, model, dimension
and indexing revision reuses the existing version and anchors.

A replacement keeps the source ID. Extraction and indexing have separate
status. The previous indexed version stays active while the worker prepares
the replacement. A database transaction publishes the new version after all
vectors pass validation. Source removal clears the active version and removes
its indexed content. The original remains available in **Documents** or
**Websites**. A removed source stays removed from the index.

The active chunk query filters by team and full embedding configuration.
Citation lookups resolve only anchors from that source's current active version.
Publishing or removing a version therefore changes visibility as soon as the
transaction commits. Search and answer caches should be keyed by active version
IDs when introduced. This indexing release reads directly from PostgreSQL.

## Embedding models

The default `local` provider embeds normalized word counts into 256 dimensions
using `feature-hash-v1`. It is a deterministic lexical baseline that runs
inside the private network. The optional `openai` provider sends chunk text to
the OpenAI embeddings endpoint and can incur API charges. Configure it only
for sources whose data can be shared with that provider.

The optional Compose override gives the indexing worker a dedicated egress
network and mounts an API key file. Set `OPENAI_EMBEDDING_KEY_PATH` to a private
file outside the checkout, readable by container UID 10001. The file contains
one line. Then start Compose with both files.

```sh
docker compose -f compose.yaml -f compose.openai.yaml up --build --wait
```

`EMBEDDING_MODEL` defaults to `text-embedding-3-small`. The adapter also supports
`text-embedding-3-large`. `EMBEDDING_DIMENSION` defaults to 1536 in this mode and
accepts values from 1 through 1536. The adapter is verified with mocked API
responses. See the [OpenAI embeddings API reference](https://developers.openai.com/api/reference/resources/embeddings/methods/create).

After changing the model, provider, or dimension, recreate the indexing worker
and reindex each source. Compatible results become visible as each source
publishes. Queries using the new configuration exclude older incompatible
vectors. Keep all indexing workers on the same configuration during a run.
