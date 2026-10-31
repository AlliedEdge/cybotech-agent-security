# Cybotech backend

FastAPI, persistent memory defenses, simulated tools, and bounded security experiments.
See the repository README for local startup and `docs/development/completion.md`
for verification and implementation limits. Offline mode never calls a model provider.

## Disposable PostgreSQL / pgvector integration test

The integration test is deliberately disabled unless its URL targets a loopback
database whose name starts with `cybotech_test_`.  The following starts an
isolated pgvector container, applies the existing Alembic migrations, runs the
test, and removes only that named container and its anonymous data volume:

```bash
docker run --rm -d --name cybotech-pgvector-test \
  -e POSTGRES_DB=cybotech_test_retrieval \
  -e POSTGRES_USER=cybotech_test \
  -e POSTGRES_PASSWORD=cybotech_test_password \
  -p 127.0.0.1:55432:5432 pgvector/pgvector:pg16
until docker exec cybotech-pgvector-test pg_isready -U cybotech_test -d cybotech_test_retrieval; do sleep 1; done
cd backend && \
  CYBOTECH_TEST_DATABASE_URL='postgresql+psycopg://cybotech_test:cybotech_test_password@127.0.0.1:55432/cybotech_test_retrieval' \
  DATABASE_URL='postgresql+psycopg://cybotech_test:cybotech_test_password@127.0.0.1:55432/cybotech_test_retrieval' \
  uv run alembic upgrade head && \
  CYBOTECH_TEST_DATABASE_URL='postgresql+psycopg://cybotech_test:cybotech_test_password@127.0.0.1:55432/cybotech_test_retrieval' \
  uv run pytest tests/integration/test_postgres_retrieval.py -v
docker stop cybotech-pgvector-test
```

The test itself inserts uniquely identified records and deletes only those rows
in a `finally` block. Do not point `CYBOTECH_TEST_DATABASE_URL` at a shared or
production database.
