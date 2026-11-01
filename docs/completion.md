# Running the local demonstration

This project is a research demo using synthetic data and simulated tools, not a production security product. By default, the assistant is deterministic and extractive; Sentinel and Memory Guard use deterministic rules. Scripted lab results measure the controls against a deliberately compliant model double, not the robustness of an actual LLM. Lab runs use an isolated SQLite database per case and persist results and traces to the application audit store.

## Reproducible Phase 5 demonstration

From the repository root, use an explicit disposable SQLite file and local-demo access:

```bash
export DATABASE_URL='sqlite:///./cybotech-demo.db'
export CYBOTECH_SECURITY_MODE=local_demo
make demo
```

The target runs one versioned synthetic attack and one benign control through both the
isolated `baseline` and `full` profiles, then evaluates the persisted audit records. It
never makes a live-model call, sends real email, or uses non-synthetic data. The results
are exploratory; latency overhead stays unavailable until at least five matched pairs exist.

In separate terminals, start `cd backend && DATABASE_URL="$DATABASE_URL" CYBOTECH_SECURITY_MODE=local_demo uv run uvicorn cybotech.main:app --reload` and `cd frontend && pnpm dev`.
The workbench shows persisted metrics in **Overview**, controlled-comparison status in
**Evaluations**, case evidence in **Attack Explorer**, and bounded scripted runs in
**Experiment Control**. Local-demo access is loopback-only and is not production authentication.

Live provider use is optional and unverified here. It requires explicit
`CYBOTECH_MODEL_MODE=live`, an environment-supplied `OPENAI_API_KEY`, and a maximum of
three synthetic cases. Ordinary tests and `make demo` stay offline. Do not put keys in UI,
source, or logs. For disposable PostgreSQL/pgvector verification, follow `backend/README.md`;
never target shared or production data.

## Start without API credits

Install Python 3.12–3.14, Node 20+ and uv. From `backend`, run `uv sync --frozen --extra dev` and `uv run python -m cybotech.local`. From `frontend`, install dependencies and run `npm run dev`. Open http://localhost:5173. The backend creates local SQLite data under `backend/.local/` and listens on loopback only. For PostgreSQL/pgvector, use Docker Compose after copying `.env.example` to `.env` and setting the required local passwords. Compose builds from the locked dependencies and runs Alembic migrations. Docker Desktop bridge addresses vary; configure `CYBOTECH_LOCAL_PROXY_HOSTS` only for the local bridge if necessary.

## Demo story

To try document RAG, attach a synthetic PDF, TXT, MD, or EML document in Assistant and send
a question. The file limit is 1 MB. You can ask follow-up questions in the same browser tab.
The assistant retrieves chunks for that session as untrusted evidence. Offline mode returns
extracts; live mode passes the chunks to the configured model. Session identifiers are demo
scoping, not authenticated identities. Do not upload confidential documents.

Chat messages are displayed only while the page remains open; previous messages are not
automatically included in later model requests. Documents remain scoped to the browser-tab
session. In Attack Lab, **Generate and launch attack** requires a configured live model. It
generates a synthetic payload and submits it to an isolated live-model supervisor. The
separate live-primary comparison uses predefined cases. Live integration has not yet been
verified against a real provider; these modes can incur charges.

The attacker graph can generate a synthetic payload through a configured model. The dashboard
submits it to the isolated live-model supervisor and persists the experiment; standalone graph
calls without an injected runner retain the scripted victim. Neither live path has been verified
against a real provider as part of this change.

1. In Assistant, attach a synthetic document and ask a question. The answer uses retrieved document evidence and shows task-specific security details.
2. In Attack Lab, compare a scenario with baseline and full defenses, expand each result to inspect the recorded trace, and run the held-out suite. Download the report or replay it after refresh.
3. In Audit Timeline, expand simulated action approvals, create a request, review its exact contents, approve, and execute it in the simulator. A consumed approval cannot be reused.

The corpus contains 30 attack and 30 benign synthetic cases; held-out and development splits use separate wording templates, but are not independently curated. Attack success is objective-specific: a trusted poisoned memory, delivery to the attacker, or an unauthorized workflow delivery. Errors are excluded from rates and counted separately. Every case starts with a fresh isolated store. The baseline disables controls only inside the lab. No outbound email is sent.

For optional live primary-model experiments set `CYBOTECH_MODEL_MODE=live`, `OPENAI_API_KEY`, and optionally `OPENAI_MODEL`. The API permits at most three live cases per run and up to four configurations. Live runs can use billable requests and send the synthetic input to the provider. Model reviewers are used by default in live mode; local embeddings are used in the lab. Offline and live embeddings are different spaces; existing approved memories must be re-embedded before retrieval after changing modes.

In live mode, independent structured model reviewers are enabled by default (optionally set `OPENAI_REVIEW_MODEL`, or the per-agent model settings). The deterministic checks still run and the model cannot grant permissions. A refusal, malformed response, timeout, or provider failure blocks the action. This review can incur additional API charges. `CYBOTECH_REVIEW_MODE=deterministic` explicitly selects the legacy review mode.

In this mode, pending memory proposals from registered sources also receive model screening for hidden instructions and unsupported facts. A flag or unavailable reviewer quarantines the proposal; a clean model finding still requires human review. Untrusted sources are quarantined directly.

Security routes require `CYBOTECH_SECURITY_MODE=local_demo` and local access; this is not authentication. Do not expose this demo to the internet or real corporate data. Source trust comes from exact content matches against an operator-owned registry, configurable through `CYBOTECH_SOURCE_REGISTRY`. The default registry contains only a synthetic example. Human approvals are local demo actions, not authenticated identities.
