# Multi-agent migration: implementation and limits

## Before and after

Previously, the primary LangGraph directly sequenced a model, deterministic
security ports, and simulated tools. Model reviewers were optional callbacks;
the attacker graph submitted cases to a separate experiment entry point.

```text
Before: task -> primary graph -> memory service / sentinel -> gateway -> sandbox
        Vibe Hacker -> isolated experiment -> primary graph

Now:    task -> SupervisorGraph -> primary model -> typed memory handoff
                                      |              -> model memory review
                                      |              -> memory lifecycle service
                                      +-> typed Sentinel handoff -> model + rules
                                                                 -> gateway -> sandbox
        Vibe Hacker -> synthetic artifact -> isolated SupervisorGraph -> persisted evidence
```

`SupervisorGraph` reuses the existing `PrimaryAssistantGraph` StateGraph; it is
not a separate process or a durable distributed supervisor. The new memory node
performs a separate model invocation in live model-review mode. Sentinel receives
a typed action and bounded untrusted document evidence. Both can add restrictions;
only the memory lifecycle service changes memory state and only the gateway
authorizes a simulated tool. Explicit offline/deterministic mode still exists for
regression tests and the local demonstration.

The fixed registry permits the primary agent to reason, retrieve, and propose;
Memory Guard and Sentinel can only review; Vibe Hacker can only create synthetic
artifacts and submit isolated experiments; the supervisor can coordinate. The
registry is an internal permission policy, not end-user authentication. Typed
handoffs validate source, destination, operation, trust label and references.
Model/provider identifiers and handoff outcome are recorded without the full
handoff source text. The workflow bounds model turns and review handoffs; each
provider call uses a timeout and no automatic retries. A workflow deadline is checked
between steps and before execution; it does not preempt a blocking provider call. There
is no durable recovery after process failure yet.

## Provider configuration

Set `CYBOTECH_MODEL_MODE=live` and either provide `OPENAI_API_KEY` (and optionally
`OPENAI_BASE_URL`) or set `CYBOTECH_PROVIDER=ollama` with a local Ollama-compatible
endpoint in `OLLAMA_BASE_URL`. The default Ollama model is `qwen2.5:7b`. Optional
per-role model variables are `CYBOTECH_PRIMARY_MODEL`, `CYBOTECH_MEMORY_MODEL`,
`CYBOTECH_SENTINEL_MODEL`, and `CYBOTECH_ATTACK_MODEL`. Model review is the live
default; `CYBOTECH_REVIEW_MODE=deterministic` explicitly selects the legacy mode.
Ollama mode uses the existing local token-hash embeddings, not semantic Ollama
embeddings. Switching embedding models requires re-indexing existing data.

Real OpenAI and Ollama calls have not been verified for this migration. The local
Ollama service and model must be installed separately. Structured-output support
depends on the configured provider and model. A failed configured reviewer must not
be silently replaced with a test double.

## Remaining work before calling this migration complete

- Add authenticated end-user identities, tenant isolation, and an authenticated
  deployment boundary; the local demo's loopback restriction is not authentication.
- Replace the remaining legacy `Assistant` entry point if it is used outside the
  registered API; audit callers before removing it. The normal API and lab use the
  shared supervisor graph.
- Extend typed handoffs and auditable model recommendations across every workflow;
  generated lab runs have a parent handoff, but the current metadata does not
  constitute a general agent-to-agent messaging system.
- Add a preemptive total deadline, durable execution, and a combined token/request budget.
- Reduce existing audit payloads that may contain action arguments and tool results;
  do not use real corporate data in the demo meanwhile.
- Run PostgreSQL/pgvector integration and real model trials against both providers.
  Neither a local PostgreSQL service nor Docker/Ollama was available during this work.
- Perform an independent security review and validate red-team outcomes against
  model-generated attacks and model victims rather than treating scripted results
  as real-world efficacy.

## Next verification steps

1. Install Docker Desktop and run the disposable PostgreSQL/pgvector procedure in
   [the backend README](../backend/README.md). It applies migrations and runs both
   PostgreSQL integration tests against a local test database.
2. For a local model trial, install Ollama and the `qwen2.5:7b` model, then set
   `CYBOTECH_PROVIDER=ollama` and `CYBOTECH_MODEL_MODE=live` for the backend process.
   Run a synthetic assistant task and a single generated attack, then verify all
   agent handoffs and the gateway decision. Do not use actual corporate data.
3. Repeat with a configured OpenAI-compatible provider if required, compare the
   evidence, and have a second engineer review the authorization and audit paths.
