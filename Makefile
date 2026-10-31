# =============================================================================
# Cybotech — Makefile
# Convenience targets for local development.
# Requires: docker, docker compose, uv, pnpm
# =============================================================================

.PHONY: help setup up down logs db-shell \
        backend-install backend-dev backend-test backend-lint backend-typecheck \
        frontend-install frontend-dev frontend-build frontend-test \
        migrate migrate-create \
        red-team evaluate demo \
        clean clean-pyc clean-node

# Default target
help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}' | \
		sort

# -----------------------------------------------------------------------------
# Docker Compose
# -----------------------------------------------------------------------------
setup: ## Copy .env.example → .env if .env doesn't exist
	@[ -f .env ] || (cp .env.example .env && echo "Created .env — fill in real values before starting.")
	@[ -f backend/.env ] || (cp backend/.env.example backend/.env && echo "Created backend/.env")
	@[ -f frontend/.env ] || (cp frontend/.env.example frontend/.env && echo "Created frontend/.env")

up: ## Start all services via Docker Compose
	docker compose up --build

up-db: ## Start only the database service
	docker compose up -d db

down: ## Stop all services
	docker compose down

logs: ## Follow logs for all services
	docker compose logs -f

db-shell: ## Open a psql shell in the running db container
	docker compose exec db psql -U $${POSTGRES_USER:-cybotech_user} -d $${POSTGRES_DB:-cybotech}

# -----------------------------------------------------------------------------
# Backend (Python / uv)
# NOTE: Run these from the repo root; uv will pick up backend/pyproject.toml
# -----------------------------------------------------------------------------
backend-install: ## Install backend dependencies with uv
	cd backend && uv sync --frozen

backend-dev: ## Start the FastAPI dev server (requires DB running)
	cd backend && uv run uvicorn cybotech.main:app --reload --host 127.0.0.1 --port 8000

backend-test: ## Run backend tests with pytest
	cd backend && uv run pytest tests/ -v

backend-lint: ## Lint backend Python with Ruff
	cd backend && uv run ruff check src/ tests/

backend-fmt: ## Format backend Python with Ruff
	cd backend && uv run ruff format src/ tests/

backend-typecheck: ## Type-check backend with mypy
	cd backend && uv run mypy src/

# -----------------------------------------------------------------------------
# Frontend (Node / pnpm)
# -----------------------------------------------------------------------------
frontend-install: ## Install frontend dependencies with pnpm
	cd frontend && pnpm install --frozen-lockfile

frontend-dev: ## Start the Vite dev server
	cd frontend && pnpm dev

frontend-build: ## Build the frontend for production
	cd frontend && pnpm build

frontend-test: ## Run frontend tests
	cd frontend && pnpm exec vitest run

frontend-lint: ## Lint frontend TypeScript
	cd frontend && pnpm lint

frontend-typecheck: ## Type-check frontend TypeScript
	cd frontend && pnpm typecheck

# -----------------------------------------------------------------------------
# Database migrations (Alembic) — requires backend deps installed
# -----------------------------------------------------------------------------
migrate: ## Apply all pending Alembic migrations
	cd backend && uv run alembic upgrade head

migrate-create: ## Create a new Alembic migration (use: make migrate-create MSG="add sessions table")
	cd backend && uv run alembic revision --autogenerate -m "$(MSG)"

migrate-down: ## Downgrade one migration step
	cd backend && uv run alembic downgrade -1

# -----------------------------------------------------------------------------
# Red-team and evaluation
#
# make red-team
#   Runs a bounded scripted Vibe Hacker experiment (offline by default).
#   No live-model API calls unless CYBOTECH_MODEL_MODE=live is set AND
#   --mode live is passed explicitly via ARGS.
#   Persists results to DATABASE_URL for later evaluation.
#
#   Pass extra arguments via ARGS:
#     make red-team ARGS="--cases 10 --configurations full"
#     make red-team ARGS="--include-benign"
#     make red-team ARGS="--split heldout --objective memory_poisoning"
#
# make evaluate
#   Reads persisted experiment records from DATABASE_URL and computes
#   genuine security metrics.  Read-only: no experiment data is mutated.
#
#   Pass extra arguments via ARGS:
#     make evaluate ARGS="--configuration full"
#     make evaluate ARGS="--generation-mode scripted"
#     make evaluate ARGS="--output json"
#
# DATABASE_URL must be set (or export it in your shell).
# Default for local development: sqlite:///./lab.db
# -----------------------------------------------------------------------------
ARGS ?=

red-team: ## Run bounded scripted Vibe Hacker experiments (offline by default; persists to DATABASE_URL)
	@[ -n "$$DATABASE_URL" ] || (echo "[red-team] DATABASE_URL not set. Using sqlite:///./lab.db"; export DATABASE_URL=sqlite:///./lab.db)
	cd backend && DATABASE_URL=$${DATABASE_URL:-sqlite:///./lab.db} \
		uv run python -m cybotech.cli.red_team $(ARGS)

evaluate: ## Compute security metrics from persisted experiment records (read-only)
	@[ -n "$$DATABASE_URL" ] || (echo "[evaluate] DATABASE_URL not set. Using sqlite:///./lab.db")
	cd backend && DATABASE_URL=$${DATABASE_URL:-sqlite:///./lab.db} \
		uv run python -m cybotech.cli.evaluate $(ARGS)

demo: ## Run a bounded offline baseline/full experiment with a benign control, then evaluate it
	cd backend && DATABASE_URL=$${DATABASE_URL:-sqlite:///./lab.db} \
		uv run python -m cybotech.cli.red_team --cases 1 --configurations baseline,full --include-benign
	cd backend && DATABASE_URL=$${DATABASE_URL:-sqlite:///./lab.db} \
		uv run python -m cybotech.cli.evaluate

# -----------------------------------------------------------------------------
# Quality — run all checks
# -----------------------------------------------------------------------------
lint: backend-lint frontend-lint ## Lint all code
typecheck: backend-typecheck frontend-typecheck ## Type-check all code
test: backend-test frontend-test ## Run all tests
check: lint typecheck test ## Run lint + typecheck + test

# -----------------------------------------------------------------------------
# Cleanup
# -----------------------------------------------------------------------------
clean-pyc: ## Remove Python bytecode files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name "*.pyo" -delete 2>/dev/null || true

clean-node: ## Remove node_modules
	rm -rf frontend/node_modules

clean: clean-pyc clean-node ## Remove all generated artifacts
	rm -rf backend/dist backend/build backend/.ruff_cache backend/.mypy_cache
	rm -rf frontend/dist frontend/.vite
	rm -rf .pytest_cache backend/.pytest_cache
	rm -rf htmlcov .coverage coverage.xml
