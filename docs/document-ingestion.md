# Upload and extract documents

Start the [local Compose stack](infrastructure.md), sign in, and select a team
in the dashboard. An owner or admin can choose a file in **Documents** and
upload it. All team members can see its status and preview the extracted text
when processing finishes.

- Each file can be at most **5 MiB**. Supported names end in `.pdf`, `.docx`,
  `.md`, or `.markdown`.
- PDFs are limited to **100 pages**. Text retains page numbers. Image-only
  PDFs require a text layer from OCR before upload. Password-protected PDFs
  must be unlocked first.
- DOCX extraction reads text and heading styles from the document XML. It does
  not load external relationships, run macros, or paginate Word documents.
- Markdown must be UTF-8. Headings label sections; the preview displays text.
  It does not render embedded markup or fetch links.

Extraction limits output to 1 MiB of UTF-8 and 256 sections. PDF streams
are bounded. DOCX archives are limited to 256 entries and 20 MiB of declared
expanded size; each XML part read is limited to 2 MiB. Files that fail
validation or cannot be read have a short error explaining the next action.
This feature extracts text; it does not index embeddings or provide answers.
See the [pypdf extraction notes](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
for PDF text-layer limitations.

## Status and retries

The dashboard shows **Queued**, **Processing**, **Completed**, or **Failed**.
It refreshes active jobs while visible. Uploading the same bytes to the same
team returns the existing record; another team has a separate record. Use
**Retry** on a failed document after addressing the reported problem. There
are at most three attempts per document. Upload a corrected file if extraction
keeps failing.

The API commits the original and queued record before sending a JSON RQ job.
The worker's arguments identify the team, document and attempt; they contain
no file contents or browser credentials. The worker resolves the exact stored
record and checks the initiator's current role before reading the original and
again before publishing text. It publishes sections and completion together.
Queued, processing, and failed attempts return no extracted text.

An attempt that cannot reach Redis is marked failed. A lost delivery or an
interrupted worker becomes retryable when its lease expires. The queue lease
is five minutes; the processing lease is 90 seconds, with a 60-second RQ
execution limit. An expired attempt is displayed as failed. Retrying advances
the attempt number so late or duplicate deliveries cannot publish over the
current attempt.

## Private storage and API

Originals are stored in PostgreSQL under `(team_id, document_id)`, separate
from public assets or executable files. Forced row-level security keeps the
bytes in the selected tenant, and there is no original-file download route.
Only completed sections are available through the protected detail API. Database
backups contain the originals and extracted text; keep them private.

The team document collection is `/api/v1/teams/{team_id}/documents`.
`GET` lists records. `POST` sends the file bytes as `application/octet-stream`
and the percent-encoded UTF-8 filename in `X-Document-Name`. Read a record at
`/{document_id}`, or send JSON `{}` to `/{document_id}/retry`. Browser writes
require the configured exact Origin and a current team session. The dashboard
uses the matching same-origin proxy routes.

The Compose worker joins the private backend network and has a 512 MiB memory limit.
No provider key or outbound network request is needed for extraction. The
integration tests exercise all three formats with PostgreSQL, duplicate
delivery, invalid input, tenant and role checks, and recovery from an interrupted
attempt. Browser tests also exercise the live Redis-to-worker path.
