# Running the local demonstration

This project is a research demo using synthetic data and simulated tools, not a production security product. By default, the assistant is deterministic and extractive; Sentinel and Memory Guard use deterministic rules. Scripted lab results measure the controls against a deliberately compliant model double, not the robustness of an actual LLM. Lab runs use an isolated SQLite database per case and persist results and traces to the application audit store.

## Start without API credits

Install Python 3.12–3.14, Node 20+ and uv. From `backend`, run `uv sync --frozen --extra dev` and `uv run python -m cybotech.local`. From `frontend`, install dependencies and run `npm run dev`. Open http://localhost:5173. The backend creates local SQLite data under `backend/.local/` and listens on loopback only. For PostgreSQL/pgvector, use Docker Compose after copying `.env.example` to `.env` and setting the required local passwords. Compose builds from the locked dependencies and runs Alembic migrations. Docker Desktop bridge addresses vary; configure `CYBOTECH_LOCAL_PROXY_HOSTS` only for the local bridge if necessary.

## Demo story

1. In Assistant, load the verified synthetic document and run the task. Inspect its pending memory in Memories and explicitly approve it. Clear the document and ask a related follow-up to demonstrate retrieval.
2. In Attack Lab, compare a scenario with baseline and full defenses, expand each result to inspect the recorded trace, and run the held-out suite. Download the report or replay it after refresh.
3. In Approvals, create a synthetic report request, review its exact contents, approve, and execute it in the simulator. A consumed approval cannot be reused.

The corpus contains 30 attack and 30 benign synthetic cases; held-out and development splits use separate wording templates, but are not independently curated. Attack success is objective-specific: a trusted poisoned memory, delivery to the attacker, or an unauthorized workflow delivery. Errors are excluded from rates and counted separately. Every case starts with a fresh isolated store. The baseline disables controls only inside the lab. No outbound email is sent.

For optional live primary-model experiments set `CYBOTECH_MODEL_MODE=live`, `OPENAI_API_KEY`, and optionally `OPENAI_MODEL`. The API permits at most three live cases per run and up to four configurations. Live runs can use billable requests and send the synthetic input to the provider. The reviewer remains deterministic and local embeddings are used in the lab. Offline and live embeddings are different spaces; existing approved memories must be re-embedded before retrieval after changing modes.

For the assistant and approval workflow, set `CYBOTECH_REVIEW_MODE=model` in live mode to use an independent structured model reviewer (optionally set `OPENAI_REVIEW_MODEL`). The deterministic checks still run and the model cannot grant permissions. A refusal, malformed response, timeout, or provider failure blocks the action. This review can incur additional API charges. Lab runs continue to use the deterministic reviewer and record that choice in their reports.

In this mode, pending memory proposals from registered sources also receive model screening for hidden instructions and unsupported facts. A flag or unavailable reviewer quarantines the proposal; a clean model finding still requires human review. Untrusted sources are quarantined directly.

Security routes require `CYBOTECH_SECURITY_MODE=local_demo` and local access; this is not authentication. Do not expose this demo to the internet or real corporate data. Source trust comes from exact content matches against an operator-owned registry, configurable through `CYBOTECH_SOURCE_REGISTRY`. The default registry contains only a synthetic example. Human approvals are local demo actions, not authenticated identities.
