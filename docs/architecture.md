# Architecture

KnowledgeDesk is a portfolio project by Hamza Zahid Khokhar. It implements local
infrastructure, accounts and team authorization. Knowledge and AI workflows
remain on the roadmap.

## Implemented architecture

```mermaid
flowchart LR
    Browser[Browser] --> Web[Next.js site and protected dashboard]
    HostPage[Independent host test page] --> Widget[Disconnected Web Component]
    Client[Developer or HTTP client] --> API[FastAPI]
    Web --> API
    API --> Liveness[GET /health]
    API --> Readiness[GET /ready]
    Readiness --> DB[(PostgreSQL 18 and pgvector)]
    Readiness --> Redis[(Authenticated Redis)]
    Probe[Internal Python probe] --> DB
    Probe --> Redis
    Redis --> Worker[RQ ingestion queue worker]
    Worker --> DB
    Migrate[Owner-only migration service] --> DB
    Seed[Fictional seed command] --> DB
```

### Web and API

`apps/web` renders a responsive marketing page, public project overview, and
protected team dashboard. Its
sample answer is illustrative. The standalone widget supports one-script mounting,
Shadow DOM, configured text/color, keyboard closing and focus restoration. Both
remain disconnected from a working conversation service.

The API exposes `/health`, `/ready`, `/api/v1/status` and `/docs`. Liveness only
reports that the API process can respond. Readiness returns `200` after the
application database connection executes `SELECT 1` and authenticated Redis
responds to `PING`; failures return a generic `503` without connection details.
Readiness does not assert worker execution or AI capabilities. The status endpoint
continues to report ingestion, retrieval, chat, agents and MCP as unavailable.

### Storage and the internal tenant boundary

Alembic revision `0001` creates `teams`, `knowledge_sources` and `job_runs` and
enables the vector extension. All three tables enable and force row-level
security. Policies match the transaction's tenant UUID against the team's own
`id` or a row's `team_id`. Without tenant context, the application role sees no
rows. pgvector is available, but document chunks, embeddings and retrieval
indexes have not been implemented.

`aka_owner` applies migrations. Runtime `aka_app` is a non-owner login with
`NOSUPERUSER`, `NOINHERIT` and `NOBYPASSRLS`, table DML and schema usage. It cannot
create schema objects or temporary tables, read migration state, inherit owner
membership, or bypass tenant policies. Bootstrap rejects an existing application
role with unsafe flags, memberships or ownership rather than broadening it.

`tenant_connection` opens a transaction and uses transaction-local
`set_config('app.team_id', ..., true)`. Tenant context ends with commit or
rollback before a connection returns to the pool. Protected HTTP handlers
resolve the actor's current membership before accessing a team's resources;
only trusted server code chooses the database context. Internal seeds and
probes are separate from user authorization.

### Identity and teams

The web server checks an opaque session through FastAPI before displaying the
dashboard. FastAPI checks live membership and role on each protected request.
Account registration, team creation, invite redemption and membership changes
have explicit server-side checks. PostgreSQL stores identity and session state
and isolates tenant resources through transaction-local scope. See
[accounts and teams](authentication.md) for roles, revocation and CSRF rules.

The optional seed command inserts two fictional teams and one example source per
team through the application role. Repeated seeding preserves existing records.
It does not parse documents, call a model or build a knowledge index.

### Redis and the worker probe

The private RQ queue is named `ingestion` in preparation for later work. Its only
current task is an infrastructure probe. Internal `enqueue_probe` persists a
`queued` job record before enqueueing a fixed dotted task with UUID string
arguments and a deterministic job ID. Producer and worker use `JSONSerializer`;
no pickle payload is needed for this flow.

The worker runs the task under its tenant transaction, moves the existing record
through `running` to `completed`, and stores a small result. Duplicate completed
runs return the same result; queue/execution failures have safe recorded failure
states. Queue/result retention and execution time are bounded. There is no
public enqueue route or model/document work. Possession of the private service
credential is a trusted infrastructure boundary, not an end-user permission.

### Compose boundaries and persistence

[compose.yaml](../compose.yaml) starts `secret-init`, PostgreSQL, Redis,
`migrate`, API, worker and web services. PostgreSQL and Redis use the internal
backend network with no published ports. Web/API publish only loopback ports
`3000` and `8000`; the worker has no published port. The API and worker also join
the frontend network for their application-service connectivity.

The one-shot secret initializer has no network and generates persistent random
credentials inside three private named volumes: `postgres-secrets`,
`app-secrets` and `redis-secrets`. PostgreSQL/migration bootstrap and opt-in tests
can read the owner credential. API and worker mount only the application volume
read-only and run as UID/GID `10001`; they receive no owner password. Redis reads
its private password/config volume. Repeated startup preserves existing secrets.
Files are owner-only (`0400`) for the consuming service UID. PostgreSQL and
migration/test UIDs receive separate identical files in the PostgreSQL volume;
supplementary group access permits directory traversal only.

PostgreSQL data is mounted at `/var/lib/postgresql`, as required by the selected
PostgreSQL 18 image layout. Redis persists data under `/data` with append-only
storage enabled. Normal Compose shutdown keeps data and secret volumes.
The [infrastructure guide](infrastructure.md) covers startup, migrations and
explicitly scoped recovery/reset operations.

## Verification boundary

Unit tests can run without service connections. The opt-in integration suite
uses real PostgreSQL, Redis, web, API and worker services; its migration
roundtrip creates and removes a separate `aka_test_<uuid>` database. A CI runner
uses a disposable `aka-ci-...` Compose project to exercise clean startup,
fictional seeds, tenant isolation, JSON worker completion, restart persistence
and a backup/restore check. Those checks establish evidence only when their
workflow run passes; implementation and unit results alone do not prove them.

## Planned architecture

The following diagram is a design target, not a diagram of running services.
Resource choices and exact interfaces will be finalized in their feature issues.

```mermaid
flowchart LR
    Team[Authenticated team member] --> Web[Next.js dashboard]
    Visitor[Website visitor] --> Widget[Scoped embeddable widget]
    Agent[Authorized MCP client] --> MCP[MCP tools]
    Web --> API[FastAPI application services]
    Widget --> API
    MCP --> API
    API --> DB[(PostgreSQL and pgvector)]
    API --> Queue[(Redis queue and limits)]
    Queue --> Worker[Ingestion and execution workers]
    Worker --> DB
    Worker --> Sources[Tenant-scoped source storage]
    Worker --> Providers[Configured model and embedding providers]
    API --> Providers
    API --> Approvals[Durable action proposals and human approval]
    Approvals --> Worker
    API --> Stripe[Stripe test billing]
    API --> Traces[Redacted Langfuse traces]
```

## Planned boundaries

### Identity and tenancy

Extend the existing membership checks and database scope to documents, chunks,
citations, actions, usage and analytics as those features are built. File keys,
caches and background jobs need the same isolation rules.

A widget identifier is public configuration. Allowed-domain checks and CORS are
abuse/browser-origin controls, not authentication. Widget sessions will be
short-lived, narrowly scoped and subject to team/session budgets. MCP tools must
reuse the application's role checks and approval path.

### Knowledge ingestion and retrieval

Uploads and bounded website crawls will produce durable jobs. Crawlers must
validate the actual connection destination and every redirect, enforce public
network boundaries and stop at documented page/byte/time limits. Parsing and
indexing failures must have visible states and safe retries.

Each active document version will own its chunks, embedding-model metadata and
stable citation anchors. Keyword/vector queries and reranking must all filter
authorized active sources. Updates and deletions must change search visibility
and invalidate relevant caches. Changing an embedding model requires a compatible
index or an explicit reindex.

### Answers and actions

Provider-specific adapters will keep API keys server-side and implement a shared
streaming/schema contract. Answers must cite available authorized evidence and
state when the knowledge base is insufficient. Untrusted source content cannot
change authorization or tool policy.

Proposed actions will be immutable, tenant-scoped records. A human approval binds
to the exact payload; changing it requires another review. Execution must recheck
current authorization, use idempotency keys and retain an audit trail across
retries. Read-only chat responses and approved external side effects are separate
workflows.

### Usage, privacy and operations

Atomic reservations will enforce limits before costly work, with reconciliation
for actual tokens, failures and cancellation. Cache keys must include tenant and
knowledge/configuration context. Billing entitlements come from verified,
deduplicated Stripe test events rather than client assertions.

Traces should correlate API requests, jobs and actions while minimizing personal
data. Provider usage and estimated cost must be labeled distinctly. Backups,
restore checks, deployment health and rollback procedures belong to the release
work, alongside an isolated and bounded public demo.

## Where the work is tracked

[Setup issues #1–#3](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/milestone/1)
establish the foundation. The
[Core features milestone](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/milestone/2)
covers implemented product behavior, followed by measured
[Quality](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/milestone/3)
and [Release](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/milestone/4)
work. An architectural plan does not count as a completed feature or validation.
