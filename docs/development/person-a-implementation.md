# Person A implementation and handoff

## Repository inspection

The starting main branch implements a FastAPI health endpoint and a frontend
placeholder. Architecture, API routes, domain services, migrations and evaluation
are mostly proposed design. There are no existing agent or defense implementations.
This branch adds Person A components without changing Person B's app factory,
API, database, domain, retrieval, Memory Guard, Sentinel or policy gateway modules.

## Implemented

- `agents/assistant.py`: bounded retrieve → model → memory proposal → Sentinel
  review → gateway authorization → simulated tool execution loop.
- `agents/contracts.py`: provisional typed ports matching the two-person workflow.
  Model-controlled fields are separated from application-owned session/task IDs,
  original user task, proposal actor, timestamps and original source content.
- `infrastructure/llm/providers.py`: OpenAI structured-output adapter and scripted
  provider. All external content stays inside a data message. No tool executes
  directly from a provider response. Provider errors/refusals abort safely.
- `infrastructure/sandbox/tools.py`: in-memory email/document reading, synthetic
  customer search, and report delivery recording. No filesystem, SMTP or HTTP
  operations. Reserved `.example` destinations only, including baseline mode.
- `services/red_team/`: seeded bounded scenario generation, isolated two-session
  experiment execution, observable outcome checks and offline wiring demo.

## Run locally

From the repository root, with Python 3.12+:

```sh
python -m venv .venv
# Activate .venv for your shell, then:
python -m pip install pydantic openai pytest pytest-asyncio fastapi httpx ruff mypy
```

Set `PYTHONPATH=backend/src` (PowerShell: `$env:PYTHONPATH = "backend/src"`), then:

```sh
python -m cybotech.services.red_team.demo
python -m pytest backend/tests
python -m ruff check backend/src/cybotech/agents backend/src/cybotech/infrastructure/llm backend/src/cybotech/infrastructure/sandbox backend/src/cybotech/services/red_team backend/tests/unit/test_person_a.py
```

The existing package metadata references a missing backend README; these commands
run from source and do not require changing shared dependency metadata.

## Live provider integration

Construct `OpenAIProvider(OpenAI(timeout=30, max_retries=0), model_name)` and inject
it into `Assistant` together with real service adapters. The SDK reads the API key
from the environment. Set an explicit model supported by your account. The adapter
caps completion tokens; orchestration caps steps and proposals per step. Do not
send private company data. No paid provider calls were used to validate this branch.

Provider implementation reference:
https://developers.openai.com/api/docs/guides/structured-outputs

## Person B handoff

Implement adapters for `MemoryPort`, `RetrievalPort`, `SentinelPort`, `GatewayPort`
and `EventPort`; then inject those adapters when constructing `Assistant`.
These provisional Pydantic models are not replacements for your canonical domain
types. Translate them at the boundary until the team agrees on shared types.

Memory Guard must resolve `source_document_id` against its canonical document
registry. Register supplied source documents before running a task. Retrieval must
check canonical status, expiry and permissions. The agent never writes memory.
Sentinel's finding must be persisted and correlated to the exact action before
authorization; its recommendation is never authorization. The current `review`
return shape is provisional. The gateway must independently enforce all policy
and approval checks. The event adapter must persist decisions with session/run
context and sequence numbers; the demo event list is not a durable audit log.

Agent-side reviewer failure blocks execution even if a gateway double permits it.
Gateway and retrieval failures abort; exceptions are recorded by type without raw
provider exception strings. Do not wire demo services into API endpoints.

## Evidence and remaining work

The offline model intentionally complies with attacks. Demo protection quarantines
all proposed memories; it is intentionally simplistic, not a semantic detector.
All generated attack families use the same scripted cross-session behavior in this
demo. These results prove wiring and outcome assertions only, not real attack
success rates, benign task performance or defense effectiveness.

Still required: real Person B services, durable sessions and PostgreSQL audit
records, API integration, live model trials, benign/held-out evaluation sets,
objective-specific live success checks (including approval bypass), persistent
run metadata and dashboard integration. This branch does not claim all 15 project
invariants or database integration tests are complete.

Review security boundaries together before merging. Main remains unchanged.
