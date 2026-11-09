# Security policy

KnowledgeDesk is a pre-release portfolio project. Only the current `main` branch
receives security fixes. There is no supported stable release yet, and other
branches or older commits do not receive promised backports. This project offers
no response-time, remediation or security SLA.

## Report a vulnerability privately

Use GitHub's
[private vulnerability report form](https://github.com/iamhamzakhokhar/ai-knowledge-assistant/security/advisories/new).
If the private form is unavailable, do not publish vulnerability details in an
issue or pull request. The repository's native security-setting verification is
tracked in [the security guide](docs/security.md).

Include the affected commit, component, expected impact and a small reproducible
example using synthetic data. Describe the permissions and configuration needed
to reproduce it. Remove personal information, customer documents, session data,
credentials and unrelated logs from the report. Do not submit a real secret as
proof of exposure.

If a real credential was exposed, revoke or rotate it promptly through its
provider, then remove the exposed value and coordinate any necessary history
cleanup. Deleting a file alone does not invalidate a credential. Report its type
and affected location without copying its value into the report, chat or logs.

## Current scope

The implemented foundation includes a web shell, API health/readiness/status,
local PostgreSQL/pgvector infrastructure, tenant-scoped internal database access,
authenticated Redis and a JSON-serialized RQ infrastructure probe. User
authentication, document ingestion, AI chat and external agent actions are still
planned. Internal tenant IDs do not authenticate a user, and the probe is not a
public enqueue API.

See [security controls and limitations](docs/security.md) and
[local infrastructure boundaries](docs/infrastructure.md). Automated checks
provide specific detection and validation; they do not establish that the
project is free of vulnerabilities.
