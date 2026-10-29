# Scripts

Utility scripts for local development, CI, and operational tasks.

## Structure

| Directory | Contents |
|---|---|
| `dev/` | Local development helpers (seed data, reset state, start services) |

## Planned scripts

| Script | Purpose | Status |
|---|---|---|
| `dev/seed_db.py` | Populate synthetic fixtures into a fresh database | Planned (Week 1) |
| `dev/reset_run.sh` | Reset evaluation run state between experiments | Planned (Week 2) |
| `dev/check_env.sh` | Validate required environment variables before startup | Planned (Week 1) |

All scripts must operate on synthetic data only. No real customer data, credentials, or external services.

> **Note:** Prefer `make <target>` shortcuts from the repository root for common operations. See the root `Makefile` for available targets.
