# Responsible Testing

## Purpose

This document describes how Cybotech conducts security experiments safely. It defines the sandbox boundaries, synthetic data requirements, prohibited actions, secret handling rules, log redaction requirements, and bounded red-team constraints.

These rules apply to all contributors and are enforced by code design and CI configuration, not only by convention.

---

## Sandbox boundaries

All experiments run within a sandboxed local environment. The sandbox is enforced at multiple layers:

### Layer 1 — Simulated tools

Every agent-callable tool is an in-process stub in `backend/src/cybotech/infrastructure/sandbox/`. These stubs:

- Do not contain real transport clients (no SMTP, no HTTP client pointed at real services)
- Record calls to the `tool_calls` PostgreSQL table
- Return realistic synthetic responses
- Do not accept configuration that would enable real sending

If a tool is added, it must be implemented as a sandbox stub first. Real transport is never added to the MVP.

### Layer 2 — Docker network isolation

The Docker Compose network has no route to real mail infrastructure or external customer systems. The `backend` container has access to:

- The `db` container (PostgreSQL)
- The model provider API (OpenAI) — required for completions

No other external connectivity is expected or required for experiments.

### Layer 3 — Tool allowlist

The Policy Enforcement Gateway maintains an explicit allowlist of permitted tool names. Any tool call to a name not on the allowlist is denied before reaching the sandbox layer. A new tool must be added to the allowlist before it can be called.

### Layer 4 — Destination allowlist

The `send_report` tool (and any future delivery tool) validates its destination against `ALLOWED_EMAIL_DESTINATIONS` from configuration. Destinations not on the list are denied by the gateway regardless of what the model proposes. The allowlist contains only internal synthetic addresses (e.g., `reports@internal.example`).

---

## Synthetic data requirements

### What synthetic means

All data used in experiments must be invented. It must not be derived from, loosely based on, or anonymized from real data.

This covers:

- Email subjects, bodies, and sender addresses
- Customer names, records, and contact details
- Document contents and titles
- Report content and destinations
- Attack payloads

### Synthetic data properties

Good synthetic data is:

- **Realistic in structure** — it looks like the kind of content a real support assistant would encounter
- **Deterministic** — seeded random generation where randomness is needed; no live data fetching
- **Version-controlled** — stored in `evaluation/datasets/synthetic/` and committed
- **Clearly labelled** — fixture files indicate they are synthetic (e.g., `# SYNTHETIC DATA — NOT REAL`)

### Prohibited data

- Real customer names, email addresses, phone numbers, or account numbers
- Real company names used as if they are customers in the fictional scenario (use invented company names)
- Real support ticket content from any real system
- Personally identifiable information of any kind

---

## Prohibited live actions

Regardless of configuration, the following are never permitted during any experiment or test run:

| Action | Why prohibited |
|---|---|
| Sending a real email | Outside project scope; could affect real recipients |
| Making HTTP requests to real customer systems | Outside project scope; no consent |
| Accessing real credentials or API keys beyond those needed for the model provider | Unnecessary exposure |
| Storing real PII in any database or log | Privacy violation |
| Attacking third-party services | Illegal and outside scope |
| Generating real API costs beyond normal completions | Budget and responsible use |
| Accessing the host filesystem outside the Docker volume | Sandbox escape |

If an experiment accidentally makes a real action (model provider hallucination of real credentials, misconfigured network, etc.), stop, document what occurred, and rotate any potentially exposed credentials immediately.

---

## Secret handling

### Rules

1. Secrets are stored in `.env` files, which are git-ignored
2. `.env.example` files contain only placeholder values — never real secrets
3. Secrets are never logged, printed, or included in audit events
4. API keys are never included in test fixture data or attack payloads
5. Secrets are never embedded in Docker images
6. If a secret is accidentally committed: rotate it immediately, then clean the history

### Which secrets exist in this project

| Secret | Variable | Risk if exposed |
|---|---|---|
| OpenAI API key | `OPENAI_API_KEY` | Financial abuse; unauthorized model access |
| PostgreSQL password | `POSTGRES_PASSWORD` | Database access |
| Application secret key | `APP_SECRET_KEY` | Session forgery |

### Checking for accidental secret inclusion

```bash
# Before committing: check staged changes for secret patterns
git diff --cached | grep -iE "(api_key|secret|password|token)\s*=\s*['\"][^'\"]{8,}"
```

A CI secret-scanning step is planned. Until it is active, check manually before every commit touching `.env.example`, config files, or test fixtures.

---

## Log and audit redaction

Logs and audit events must not record:

- Raw secret values (API keys, passwords)
- Full content of documents or emails that might contain inadvertently real PII
- Model completions that exceed the configured maximum log length

Audit events record metadata about security decisions (what happened, what was decided, why) rather than full content dumps. The `raw_proposal` field in `memory_provenance` stores the original untrusted content for review purposes, but this field should not appear in application logs.

---

## Bounded red-team execution

The Vibe Hacker harness enforces hard limits on experiment runs:

| Limit | Configuration variable | Default |
|---|---|---|
| Maximum iterations per run | `RED_TEAM_MAX_ITERATIONS` | 50 |
| Attack cases per objective | Fixed in case definition | ≤ 10 per objective |
| State resets between runs | Automatic | Required |

### Why limits matter

Unbounded red-team execution could:

- Generate unexpected API costs at the model provider
- Run for an unreasonable time and obscure results
- Mutate experiment state in ways that make results non-reproducible

The iteration limit is checked before each case execution. The harness exits cleanly if the limit is reached, recording the partial results.

### State reset protocol

Between each independent experiment run:

1. Delete all session records and memory records created by the previous run
2. Restore the database to the pre-run snapshot state
3. Clear any in-memory state in the orchestration service
4. Record the reset completion in the run metadata

State reset must be complete and verified before the next run starts. A partial reset produces invalid comparison results.

---

## Model provider responsible use

- Use the minimum model capability required for each task
- Evaluate cases in batches to minimize redundant API calls
- Do not run the full evaluation suite unnecessarily (use development cases during implementation)
- Set `OPENAI_MODEL` to a cost-effective model during development (e.g., `gpt-4o-mini`)
- Monitor API usage during evaluation runs

---

## What is out of scope

This project explicitly does not and must not:

- Test against real production AI systems outside this repository
- Conduct red-team experiments against third-party services
- Extract, collect, or store real user data from any external source
- Claim that attack techniques demonstrated here work universally against any system
- Publish attack payloads in a way that facilitates their use against systems outside this project's scope

---

## Related documents

- Threat model → [`threat-model.md`](threat-model.md)
- Security invariants → [`security-invariants.md`](security-invariants.md)
- SECURITY.md → [`../../SECURITY.md`](../../SECURITY.md)
- Evaluation methodology → [`../evaluation/methodology.md`](../evaluation/methodology.md)
