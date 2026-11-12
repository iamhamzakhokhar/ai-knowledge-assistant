# Local infrastructure

Run the commands in this guide from the repository root. They address this
repository's Compose project, named `hamza-knowledge-assistant` by default.
[compose.yaml](../compose.yaml) is the source of truth for services and mounts.
This is a local development setup. Create an account at `/signup` to use the
authenticated dashboard; the public project overview is at `/project`.

## Start, inspect and stop

With Docker Engine and Docker Compose available:

```sh
docker compose up --build --wait
docker compose ps
```

The default stack starts the secret initializer, PostgreSQL, Redis, migrations,
API, document worker, website worker and web. Initialization and migration are one-shot services. Startup
waits for their successful completion and the running services' health checks.

Open the web at [localhost:3000](http://localhost:3000) and API docs at
[localhost:8000/docs](http://localhost:8000/docs). `WEB_PORT` and `API_PORT` can
select different loopback ports. The API has three separate operational views:

- [`/health`](http://localhost:8000/health): API process liveness only.
- [`/ready`](http://localhost:8000/ready): authenticated PostgreSQL `SELECT 1`
  and Redis `PING`; `200` with `ready`, or a generic `503` with `unavailable`.
- [`/api/v1/status`](http://localhost:8000/api/v1/status): which product
  capabilities are implemented. Infrastructure readiness does not enable chat.

The worker health check checks its own named worker record, recent heartbeat,
queue and local process. It is separate from API readiness. To inspect service
startup failures without opening secret files:

```sh
docker compose logs --tail=100 api worker migrate
docker compose exec -T worker python -m knowledge_assistant.worker --healthcheck
```

Normal shutdown keeps PostgreSQL data, Redis data and all credentials:

```sh
docker compose down
```

## Connections and local credentials

PostgreSQL and Redis have no published host ports and attach only to the
internal backend network. API joins both networks; web joins frontend, and the
document worker stays on backend. The website worker also joins `website-egress`.
Only web and API publish host ports, both bound to
`127.0.0.1`. The worker has no published port.

`secret-init` runs once per startup without network access. It generates missing
random credentials and preserves existing values in three named Docker volumes:

- `postgres-secrets` stores `postgres_password` for PostgreSQL UID `999` and an
  identical `migration_password` copy for migration/test UID `10001`.
- `app-secrets` stores the application database password and Redis password. API,
  worker, seed and tests mount it read-only; migrations also read it to provision
  the application role.
- `redis-secrets` stores Redis's password and generated server configuration,
  mounted read-only by Redis.

The initializer publishes files as owner-only read (`0400`), owned by their
consuming UID: PostgreSQL/Redis use `999`, application services use `10001`.
Root-owned directories use `0750`; migration/test supplementary group `999`
grants directory traversal without access to the PostgreSQL-owned file. API and
worker run as UID/GID `10001` and receive no owner password.
Credentials remain inside these private Docker volumes; normal startup needs no
host plaintext secret file, copied password, or password environment export.
Connection settings carry mounted file paths rather than secret values.

On an existing development volume, startup validates and preserves passwords,
tightens legacy group-readable files and creates the migration copy without
rotation. Conflicting copies or a missing canonical PostgreSQL file with a
surviving migration copy fail safely; initialization does not overwrite the
surviving credential. Rebuild and run the normal startup command to apply this
permission upgrade; no volume reset is required.

The database role `aka_owner` is reserved for local database administration and
migrations. Runtime `aka_app` is a non-owner role without superuser, inherited
role or RLS-bypass privileges. It receives table DML and schema usage, with no
schema creation, temporary-table creation or access to Alembic state. All initial
tenant tables enable and force RLS. Existing application credentials are not
rotated during migration bootstrap.

The local secrets and initialized data belong together. Deleting only a secret
volume while retaining initialized databases breaks their credential mapping.
Use the deliberate whole-project reset below only for disposable local data;
credential rotation is not implemented by the initializer.

## Storage and migrations

The selected images are `pgvector/pgvector:0.8.7-pg18` and `redis:8.10.3`.
PostgreSQL 18 persists under the volume mounted at `/var/lib/postgresql`.
Redis persists under `/data`, with append-only storage and periodic snapshots
configured. See [version sources](versions.md) for the Python database/queue pins.

The Next.js container calls FastAPI through the private Docker hostname `api`.
Browser requests use the web origin; Compose allows the configured `WEB_PORT`
on `localhost` and `127.0.0.1`. Use one hostname consistently, since the session
cookie is specific to the host. See [authentication and teams](authentication.md).

The default `migrate` service runs `upgrade head`. For explicit commands, supply
the complete Python command: this service has a normal image command, not a
migration-specific entrypoint.

```sh
docker compose run --rm migrate python -m knowledge_assistant.migrate current
docker compose run --rm migrate python -m knowledge_assistant.migrate upgrade head
```

Revision `0001` enables pgvector and creates `teams`, `knowledge_sources` and
`job_runs`, their tenant policies and runtime grants. Revision `0002` adds
identity and membership, `0003` adds private document records and originals,
and `0004` adds website imports and crawl snapshots.
The runtime database helper
sets tenant context inside each transaction. A context UUID is trusted internal
input, not proof of a user's team membership.

**Destructive migration:** `downgrade base` drops these tables and their records
in the selected database. Run it only against an explicitly disposable Compose
project or a database whose deletion is intended, after verifying the target.
The integration suite tests this operation in its own `aka_test_<uuid>` database.

```sh
# Only in the disposable Compose project you selected for a migration test:
docker compose run --rm migrate python -m knowledge_assistant.migrate downgrade base
docker compose run --rm migrate python -m knowledge_assistant.migrate upgrade head
```

The downgrade retains the installed vector extension and database roles.
Schema changes belong in migrations; runtime services do not call `create_all`.

## Fictional seeds and the worker probe

Populate two example teams, each with one fictional source:

```sh
docker compose --profile tools run --rm seed
```

Repeated runs preserve row counts and existing content. The records demonstrate
storage and RLS; they do not ingest a file, generate an embedding or enable chat.

The `ingestion` queue handles document extraction and an internal
infrastructure probe. After seeding, enqueue the probe from the application container:

```sh
docker compose exec -T api python -c "from knowledge_assistant.config import Settings; from knowledge_assistant.queueing import enqueue_probe; from knowledge_assistant.seed import DEMO_TEAM_ID; print(enqueue_probe(Settings(), DEMO_TEAM_ID))"
```

This prints a job identifier. The producer stores a tenant-scoped `queued` record
before enqueueing JSON UUID arguments. The worker moves it to `running` and then
`completed`, or records a safe failure. Inspect the example probe records:

```sh
docker compose exec -T postgres psql -U aka_owner -d knowledge_assistant -c "SELECT id, status, result FROM job_runs WHERE kind = 'probe' ORDER BY created_at DESC LIMIT 5;"
```

This command is local administration, not a public product API. There is no HTTP
enqueue route for the probe or a model call. For document work, use the team
dashboard and the [document ingestion guide](document-ingestion.md).

## Real integration and recovery checks

Start the stack and run the seed command first. Then run the test profile:

```sh
docker compose --profile test run --rm --build integration-tests
```

The test container enables `AKA_INTEGRATION_TESTS=1`. It mounts application and
owner credentials read-only for fixture setup and a uniquely named disposable
migration database. It tests pgvector, runtime role restrictions, cross-tenant
read/write denial, missing context, pool reuse after commit/rollback, JSON worker
completion, API/web readiness and Redis authentication. It removes its own random
tenant/job fixtures and disposable database. Normal unit runs skip this suite and
do not require Docker or read secret fixtures.

The test image also measures package coverage across unit/integration tests and
migration subprocesses, with an 85% combined threshold. That threshold is a CI
requirement. The [recorded run](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/actions/runs/42577536707)
passed all 104 Python cases and reported 88% combined statement/branch coverage.
The same run passed restart persistence, backup/restore and six browser checks.

CI uses [scripts/compose_smoke.py](../scripts/compose_smoke.py) with a unique
`aka-ci-...` project name. The script builds a clean stack, seeds it, runs real
integration tests, verifies PostgreSQL/Redis data survives restart, and restores
a custom-format database dump into a separate recovery-check database. GitHub
Actions then tears down only that disposable project's volumes.

For the same full check in a deliberately disposable manual project:

```sh
COMPOSE_PROJECT_NAME=aka-ci-manual python scripts/compose_smoke.py
```

After inspecting the result, the following command deletes **that manual test
project's data and secret volumes**:

```sh
docker compose --project-name aka-ci-manual --profile test down --volumes --remove-orphans
```

A passing unit suite does not prove these infrastructure checks. See the actual
[workflow run](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/actions/workflows/ci.yml)
for integration, coverage and recovery results. During issue #2 development the
local Docker Desktop engine returned HTTP `503`, so full service validation was
assigned to the Docker-capable CI runner rather than claimed from local tests.

## Back up the local database

Create a restricted custom-format dump of this project's `knowledge_assistant`
database. The destination is ignored by Git:

```sh
umask 077
mkdir -p .local/backups
docker compose exec -T postgres pg_dump -U aka_owner -d knowledge_assistant -Fc > .local/backups/dev.dump
```

A database dump contains schema, records, grants, password hashes, session and
invite digests, uploaded files and extracted text. Keep it private. Docker data/secret volumes
are separate resources; retain their mapping for the current local stack.
A successful dump alone is not a verified restore.

## Verify a restore in a separate database

Stop the API and worker while checking a recovery copy. Create the named
`aka_restore_check` database in this same Compose project's PostgreSQL service:

```sh
docker compose stop api worker website-worker
docker compose exec -T postgres createdb -U aka_owner aka_restore_check
docker compose exec -T postgres pg_restore -U aka_owner -d aka_restore_check --exit-on-error < .local/backups/dev.dump
docker compose exec -T postgres psql -U aka_owner -d aka_restore_check -c "SELECT count(*) FROM knowledge_sources;"
```

Inspect the restored records you expect. The next command deletes **only the
recovery-check database `aka_restore_check`**, then resumes the application:

```sh
docker compose exec -T postgres dropdb -U aka_owner aka_restore_check
docker compose up -d --wait api worker website-worker web
```

If a recovery-check database already exists, inspect it before choosing to remove
or reuse its name; creation should fail rather than overwrite it.

## Replace a local database from a verified backup

**Destructive replacement:** the following commands delete the entire
`knowledge_assistant` database in this Compose project's PostgreSQL container,
recreate it, and restore `.local/backups/dev.dump`. Use them only when replacing
that local dataset is intended and the backup has been verified.

```sh
docker compose stop api worker website-worker
docker compose exec -T postgres dropdb -U aka_owner --force knowledge_assistant
docker compose exec -T postgres createdb -U aka_owner -O aka_owner knowledge_assistant
docker compose exec -T postgres pg_restore -U aka_owner -d knowledge_assistant --exit-on-error < .local/backups/dev.dump
docker compose run --rm migrate python -m knowledge_assistant.migrate upgrade head
docker compose up -d --wait api worker website-worker web
```

Keep the backup until the resumed application, readiness and expected records
have been checked.

## Reset a disposable local project

**Destructive reset:** this deletes all database/Redis data and all three secret
volumes belonging to the currently selected Compose project. It is appropriate
only when that entire local dataset may be discarded:

```sh
docker compose down --volumes
```

The next startup creates a new local dataset and new credentials. For ordinary
shutdown, use `docker compose down` without `--volumes`.
