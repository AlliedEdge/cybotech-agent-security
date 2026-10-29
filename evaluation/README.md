# Evaluation

This directory contains attack cases, synthetic datasets, baseline configurations, and evaluation reports for the Cybotech red-team platform.

## Structure

| Directory | Contents |
|---|---|
| `attack_cases/` | Fixed attack fixtures (JSON/YAML) for the three attack objectives |
| `datasets/synthetic/` | Synthetic emails, documents, and customer records for experiments |
| `baselines/` | Configuration snapshots for the four evaluation configurations |
| `reports/committed/` | Version-controlled evaluation reports and summaries |
| `reports/generated/` | Auto-generated reports (git-ignored; reproduced by the evaluation engine) |

## Evaluation configurations

1. **Baseline** — no protections, records actual vulnerable behavior
2. **Memory Guard only** — provenance + lifecycle enforcement, no gateway
3. **Policy gateway only** — deterministic tool enforcement, no memory controls
4. **Full protection** — Memory Guard + Sentinel + Policy Enforcement Gateway

See [`docs/evaluation/methodology.md`](../docs/evaluation/methodology.md) for metric definitions, case design, and repeatability requirements.

> **Status:** Datasets and cases are planned for Week 2. Do not populate with real customer data.
