# Contributing to Cybotech

Thank you for your interest in contributing. This guide covers the development setup, conventions, and expectations for working on this repository.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Local setup](#2-local-setup)
3. [Development workflow](#3-development-workflow)
4. [Code quality](#4-code-quality)
5. [Testing expectations](#5-testing-expectations)
6. [Commit and branch conventions](#6-commit-and-branch-conventions)
7. [Pull request process](#7-pull-request-process)
8. [Secret and data handling](#8-secret-and-data-handling)
9. [Security-sensitive code](#9-security-sensitive-code)
10. [Prohibited artifacts](#10-prohibited-artifacts)

---

## 1. Prerequisites

| Tool | Minimum version | Notes |
|---|---|---|
| Docker | 24+ | With Compose v2 (`docker compose`) |
| Python | 3.12 | Managed via `uv` |
| uv | latest | `pip install uv` or see [docs.astral.sh/uv](https://docs.astral.sh/uv/) |
| Node.js | 20 LTS | |
| pnpm | 9 | `npm install -g pnpm` |
| Git | 2.40+ | |

---

## 2. Local setup

```bash
# Clone
git clone https://github.com/AlliedEdge/cybotech-agent-security.git
cd cybotech-agent-security

# Copy environment templates
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
# Fill in real values — at minimum POSTGRES_PASSWORD, APP_SECRET_KEY, OPENAI_API_KEY

# Start the database
docker compose up -d db

# Install backend dependencies [planned — requires backend/pyproject.toml lock]
make backend-install

# Install frontend dependencies [planned — requires frontend/package.json lock]
make frontend-install

# Apply migrations [planned — Week 1]
make migrate
```

Commands marked `[planned]` require application code not yet implemented. The database and Docker Compose are available now.

---

## 3. Development workflow

```bash
# Start backend dev server [planned]
make backend-dev

# Start frontend dev server [planned]
make frontend-dev

# Run all quality checks
make check

# Run only tests
make test

# Run only linting
make lint

# Run only type checking
make typecheck
```

Refer to the root `Makefile` for all available targets and their exact commands.

---

## 4. Code quality

### Python

- Formatter and linter: **Ruff** (`make backend-lint`, `make backend-fmt`)
- Type checker: **mypy** in strict mode (`make backend-typecheck`)
- Line length: 100 characters
- Python version target: 3.12
- All public functions and methods require type annotations

### TypeScript

- Strict mode enabled in all `tsconfig*.json` files
- Formatter: Prettier (configured via `frontend/package.json`)
- Linter: ESLint with TypeScript rules
- No `any` types in non-test code without a documented justification comment

### General

- No trailing whitespace (enforced by `.editorconfig`)
- LF line endings
- Final newline on every file
- No commented-out dead code in committed files

---

## 5. Testing expectations

A feature is not done until it is tested. The minimum bar for any security-relevant code:

| Test type | What it must cover |
|---|---|
| Unit | Observable behavior of the function or class in isolation |
| Unit | Failure path (exception, unavailable dependency, malformed input) |
| Integration | Interaction with PostgreSQL — memory state, audit event written |
| Security | The specific invariant the component is meant to enforce |

### Security invariant tests

The `backend/tests/security/` directory contains tests that assert the 15 security invariants from the README. These tests must pass on every commit to `main`. They are not optional.

### Test data

- Use synthetic fixtures from `evaluation/datasets/synthetic/` or `backend/tests/fixtures/`
- Never use real customer data, real email addresses, or real API keys in tests
- Fixture data must be deterministic — no random values without a fixed seed

### Running tests

```bash
# Backend
make backend-test

# Frontend
make frontend-test

# End-to-end [planned — Week 6]
# cd tests/e2e && pnpm exec playwright test
```

---

## 6. Commit and branch conventions

### Branch naming

```
feat/short-description          # new feature
fix/short-description           # bug fix
docs/short-description          # documentation only
security/short-description      # security fix or hardening
test/short-description          # test additions or corrections
refactor/short-description      # refactoring without behavior change
chore/short-description         # tooling, deps, CI changes
```

### Conventional commits

This project follows [Conventional Commits](https://www.conventionalcommits.org/) 1.0.0:

```
<type>(<scope>): <short summary>

[optional body]

[optional footer(s)]
```

**Types:** `feat`, `fix`, `docs`, `security`, `test`, `refactor`, `chore`, `perf`

**Scopes:** `memory-guard`, `gateway`, `sentinel`, `agent`, `retrieval`, `audit`, `dashboard`, `red-team`, `evaluation`, `api`, `infra`, `deps`

**Examples:**

```
feat(memory-guard): add quarantine state and retrieval filter
fix(gateway): deny when approval hash does not match action arguments
security(gateway): fail-closed when policy DB unreachable
docs(threat-model): document retrieval manipulation attack surface
test(security): assert invariant 7 — revoked memory excluded from retrieval
```

### Commit hygiene

- One logical change per commit
- Reference issue numbers in the footer: `Closes #42`
- Do not squash security fix commits — history is evidence

---

## 7. Pull request process

1. Open a PR against `main` from a feature branch
2. Fill in the PR template (`.github/PULL_REQUEST_TEMPLATE.md`)
3. All CI checks must pass
4. Security-critical code (Memory Guard, Policy Gateway, Sentinel, audit events) requires review from both team members
5. Include test evidence in the PR description — what the tests prove, not just that they pass
6. Keep PRs focused: one feature or fix per PR, not a sprint's worth of changes
7. Do not merge your own PR without review

---

## 8. Secret and data handling

- Never commit real API keys, passwords, or credentials to any file
- Use `.env` files (git-ignored) for local secrets
- Provide only safe placeholder values in `.env.example` files
- Audit logs must not record raw secrets, real PII, or real credentials
- If you accidentally commit a secret: rotate it immediately, then clean the history

### Checking for secrets before committing

```bash
# Manual check — look for anything that looks like a real key
git diff --cached | grep -iE "(api_key|secret|password|token)\s*=\s*['\"][^'\"]{8,}"
```

A secrets-scanning CI step is planned. Until it is in place, check manually before every commit.

---

## 9. Security-sensitive code

The following modules are security-critical. Changes require extra scrutiny:

| Module | Why it matters |
|---|---|
| `services/memory_guard/` | Controls what enters trusted memory |
| `services/policy_gateway/` | Final authorization for all tool calls |
| `services/retrieval/` | Filters what the agent can see |
| `services/sentinel/` | Pre-execution review; must not block gateway independence |
| `domain/policies/` | Policy rules read by the gateway |
| `infrastructure/database/` | Direct database access |

For changes to these modules:

- Write or update the relevant security invariant test
- Describe the failure mode you considered in the PR description
- Both team members must review and approve before merge

---

## 10. Prohibited artifacts

Do not commit:

- Real API keys, secrets, passwords, or credentials
- Real customer data or PII of any kind
- AI model weight files (`.gguf`, `.safetensors`, `.pt`, `.bin`, etc.)
- Generated lockfiles from failed or incomplete dependency resolution
- Fabricated benchmark results, invented test outcomes, or fake metrics
- Commented-out code blocks left from debugging
- Files generated by the evaluation engine (use `evaluation/reports/committed/` for deliberately version-controlled reports)

---

*For vulnerability reports, see [SECURITY.md](SECURITY.md).*
*For community standards, see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).*
