# Person B security implementation

## Local verification

The backend requires PostgreSQL with pgvector. The migration enables the `vector`
extension and creates the memory, provenance, policy-decision, and audit tables.

```sh
cp .env.example .env
# Set POSTGRES_PASSWORD and APP_SECRET_KEY in .env. OPENAI_API_KEY may be blank.
docker compose up --build
```

The backend container runs `alembic upgrade head` before starting FastAPI. The
registered `POST /api/v1/assistant/run` path uses the existing Person A Assistant,
PostgreSQL-backed Memory Guard, pgvector retrieval, deterministic Sentinel, policy
gateway, persistent audit adapter, and sandbox dispatcher. It requires a configured
OpenAI-compatible API key for chat and embedding calls. Check
`GET /api/v1/security-events` after submitting proposals or reviewing actions. The
frontend reads paginated records and complete server-side summary counts, and
subscribes to `/api/v1/events/stream` for updates and persisted replay.
The local seeded destination is `reports@internal.example`; configure the trusted
allowlist with `ALLOWED_EMAIL_DESTINATIONS`.

For deterministic backend tests, from `backend/` run `PYTHONPATH=src pytest -q`.
For the frontend production build, from `frontend/` run `pnpm build`.

## Integration boundary and limitations

The security services implement the existing `MemoryProposal` and `ActionProposal`
contracts. `build_security_ports(...)` adapts Memory Guard, trusted retrieval,
Sentinel, the persistent gateway, and audit persistence to Person A's existing
Assistant ports; inject those ports alongside Person A's existing sandbox
dispatcher. No primary agent or tool executor is replaced. The assistant's
in-process red-team demo remains an explicit in-memory double. The PostgreSQL
migration must be applied before using the persistent security APIs.

Memory proposals, memory review, approval, assistant execution, and gateway
mutation routes return 503 unless `CYBOTECH_SECURITY_MODE=local_demo` is explicitly
set. In that mode they accept loopback clients only; `make backend-dev` and Compose
bind published ports to loopback. This is a local-development network boundary,
not employee authentication. Reviewer names are audit metadata, not identity
proof. Never set `local_demo` on a remotely reachable deployment; production needs
an authenticated identity and authorization layer before enabling these routes.
The API ignores submitted memory trust labels; only source IDs explicitly listed
in `CYBOTECH_VERIFIED_SOURCE_IDS` can be trusted. Keep that value under operator
control.

The assistant runtime uses the configured OpenAI-compatible embedding endpoint
with 384-dimensional vectors. Embedding failure aborts the proposal before it is
persisted. Dashboard and SSE delivery are observational; gateway authorization is
performed and durably recorded before the sandbox executor is called. Audit
persistence is database logging, not tamper-proof or cryptographically chained.
No test establishes universal prompt-injection immunity. PostgreSQL and pgvector
integration tests require a dedicated disposable database via
`CYBOTECH_TEST_DATABASE_URL`; never point that variable at a shared database.
