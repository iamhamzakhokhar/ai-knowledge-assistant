# Accounts and teams

Start the [local Compose stack](infrastructure.md), then open `/signup` in
the web app. Create a team from the dashboard or join using an invite from its
owner or admin. A new account has no access to the example teams seeded by
the infrastructure command. Keep using the same hostname for the web app;
`localhost` and `127.0.0.1` have separate cookies.

| Permission                                                      | Owner | Admin | Member |
| --------------------------------------------------------------- | ----- | ----- | ------ |
| View their team's workspace, members, sources and job summaries | Yes   | Yes   | Yes    |
| Invite or remove members                                        | Yes   | Yes   | No     |
| Invite, promote, demote or remove admins                        | Yes   | No    | No     |
| Remove or reassign the owner                                    | No    | No    | No     |

Each team has a founding owner. Ownership transfer and team deletion are
outside this feature. Invitations expire after 24 hours, are single-use and
are tied to the intended account email. The server rechecks the inviter's
current permissions when the recipient joins. Give the invite token to that
person privately. Tokens are accepted in the join form and are not placed in
links or URLs.

The email is an account identifier; the local stack does not verify mailbox
ownership, send invitation email, offer password recovery, or require MFA.
Use fictional credentials in a local demo. Public sign-up is not a deployment
step; deployment and production account policy are separate release work.

## Session and authorization boundary

FastAPI uses salted [scrypt](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
password hashes and stores a digest of a random session token in PostgreSQL.
Sessions expire after eight hours. The browser holds only an opaque host-only
`HttpOnly` cookie. It is `SameSite=Lax` and uses `Secure` outside local HTTP
development. Sign-out revokes the current server-side session. The dashboard
validates its session on the server; every protected API request checks it
independently. Session state is not exposed to the widget or stored in browser
storage.

Redis limits combined sign-up and sign-in attempts per account identifier and
observed peer over a 15-minute window. The process also caps concurrent password
hashes. A Next.js proxy can share one observed peer budget across browsers; the
service does not trust client-supplied forwarding headers to raise that limit.

Browser requests use a same-origin Next.js route handler, which sends only
approved requests to the server-configured `API_INTERNAL_URL`. Private
responses are not cached. Every state-changing auth or team request requires
JSON and a browser `Origin` that exactly matches `CORS_ORIGINS`, including
sign-up, sign-in and sign-out. The proxy preserves the original Origin. These
checks complement the cookie's SameSite setting; CORS alone does not authorize
an operation. See [OWASP's CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

The server resolves membership and role from the signed-in account. Team and
resource IDs in paths identify the requested target; they confer no access.
The authorized team context is set inside the database transaction. PostgreSQL
row-level security independently restricts source and job reads, including
individual lookups by ID. Role changes and membership removal take effect on
subsequent requests, without requiring the user to sign in again. Removing a
membership invalidates that recipient's outstanding invitations for the team.
It does not remove the account's access to its other teams.

The only queue task is still the internal infrastructure probe. No browser
endpoint can enqueue it or pass a session into the worker. Before adding
user-initiated jobs, persist their tenant and initiator on the server, load the
record at execution, and recheck any permission the action still requires.
Source files, citations, caches and analytics do not exist yet. Their future
storage keys and queries must use the same authorized tenant scope; a public
widget identifier must never confer dashboard or team membership.

## Verify the boundary

CI runs the API integration tests against real PostgreSQL and Redis in an
isolated Compose stack. It exercises independent teams, existing foreign
resource IDs, role changes, revocation and rejected cross-origin mutations.
Playwright uses the same disposable stack to verify sign-up, team creation,
invite redemption, removal and sign-out on desktop and mobile. A local
`pnpm test:browser` without a stack runs signed-out navigation and widget tests;
set `BROWSER_BASE_URL` to a disposable local stack to include the account flow.
