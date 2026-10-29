# Local Deployment

## Purpose

This document describes the local Docker Compose environment used for development and evaluation. It covers the services, internal network, environment configuration, persistence, and safe shutdown procedures.

This is not a production deployment guide. Cybotech is a local research and demonstration project.

---

## Services

| Service | Image / Build | Exposed port | Purpose |
|---|---|---|---|
| `db` | `pgvector/pgvector:pg16` | `127.0.0.1:5432` | PostgreSQL 16 with pgvector pre-installed |
| `backend` | `./backend/Dockerfile` | `127.0.0.1:8000` | FastAPI application server |
| `frontend` | `./frontend/Dockerfile` | `127.0.0.1:5173` | React/Vite development server |

All ports are bound to the loopback address (`127.0.0.1`) by default. No services are accessible from outside the developer's machine without explicitly changing the bind address.

---

## Internal network

Docker Compose creates an internal bridge network (`cybotech_default` unless overridden). Services communicate by service name:

- Backend connects to the database at `db:5432`
- Frontend proxies API requests to `backend:8000` (via Vite proxy config in development)
- Services do not communicate directly with the host network except through the bound ports above

---

## PostgreSQL and pgvector setup

The `pgvector/pgvector:pg16` image ships PostgreSQL 16 with the pgvector extension pre-installed. The extension is enabled during first-start initialization by the script at `infra/docker/postgres/init.sql`:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

This script runs once when the container starts with an empty data directory. It does not run on subsequent starts against an existing volume.

---

## Persistence

The database volume is declared as a named Docker volume:

```yaml
volumes:
  pg_data:
    driver: local
```

This volume persists across `docker compose down`. To fully reset the database:

```bash
docker compose down -v     # removes named volumes — all data is lost
docker compose up -d db    # starts fresh with empty database
make migrate               # re-applies all migrations (planned)
```

---

## Environment configuration

Each service reads its configuration from environment variables. These are sourced from the root `.env` file (or from service-specific overrides) and are **not baked into Docker images**.

### Required variables before first start

| Variable | Where to set | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | `.env` | Database password — must be set before `docker compose up` |
| `APP_SECRET_KEY` | `.env` | Application secret — 32+ character random string |
| `OPENAI_API_KEY` | `.env` | Model provider key — not required to start the database alone |

Compose will exit with an error if any `?`-required variable is unset. This is intentional — it prevents starting with an insecure configuration.

### Environment file hierarchy

```
.env                    ← root, read by Docker Compose
backend/.env            ← backend-specific overrides (same variables, for local non-Docker dev)
frontend/.env           ← frontend-specific (VITE_ prefixed variables)
```

Copy from the corresponding `.env.example` files. Never commit real `.env` files.

---

## Starting services

```bash
# First-time setup
make setup              # copies .env.example → .env if .env doesn't exist

# Start only the database (useful for running backend without Docker)
docker compose up -d db

# Start all services
docker compose up --build

# Start in background
docker compose up --build -d

# Follow logs
docker compose logs -f

# Follow logs for a single service
docker compose logs -f backend
```

---

## Stopping services

```bash
# Stop containers (keeps volumes)
docker compose down

# Stop containers and remove named volumes (full reset)
docker compose down -v

# Stop a single service
docker compose stop backend
```

---

## Database shell

```bash
# Open a psql shell in the running database container
make db-shell
# or directly:
docker compose exec db psql -U cybotech_user -d cybotech
```

---

## Backend without Docker

For faster iteration on backend code, you can run the backend directly while the database runs in Docker:

```bash
# Start only the database
docker compose up -d db

# Set POSTGRES_HOST=localhost in backend/.env (it is by default)

# Run the FastAPI dev server
make backend-dev
```

---

## Frontend development

The Vite dev server runs with hot module replacement. In development it proxies `/api` requests to the backend:

```bash
make frontend-dev
# opens http://localhost:5173
```

The proxy configuration lives in `frontend/vite.config.ts`.

---

## Health checks

The `db` service uses a `pg_isready` health check. The `backend` service uses a curl check against `/api/v1/health`. The `backend` service will not start until `db` reports healthy.

If the backend health check fails: check that migrations have been applied and the database is reachable.

```bash
docker compose ps          # shows health status for each service
docker compose logs db     # postgres startup logs
docker compose logs backend # fastapi startup logs
```

---

## Security notes for local development

- All ports bind to `127.0.0.1` — no external access without intentional reconfiguration
- Secrets are never embedded in images; they are injected at container startup from the host environment
- The `compose.override.yaml` file (git-ignored) can be used for local overrides without touching the committed `compose.yaml`
- Do not push Docker images built with real credentials in the build context — the `.dockerignore` excludes `.env` and secret files, but verify this before any image push

---

## Related documents

- Architecture overview → [`overview.md`](overview.md)
- Backend environment variables → `backend/.env.example`
- Frontend environment variables → `frontend/.env.example`
- Root environment variables → `.env.example`
- Makefile targets → `Makefile`
