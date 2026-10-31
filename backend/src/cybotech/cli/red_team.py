"""CLI entry-point for bounded red-team experiment execution.

Usage (via Makefile):
    make red-team

Usage (direct):
    cd backend && uv run python -m cybotech.cli.red_team [OPTIONS]

Options
-------
--cases N           Maximum number of attack cases to run (default: 6, max: 60 offline / 3 live).
--split {development,heldout,all}
                    Which corpus split to use (default: development).
--objective {memory_poisoning,unauthorized_disclosure,workflow_hijack,all}
                    Restrict to a single attack objective (default: all).
--configurations {baseline,full,memory_guard,gateway,all}
                    Comma-separated configurations to run (default: baseline,full).
--include-benign    Also run a matching number of benign control cases.
--mode {offline,live}
                    offline: scripted adversary, no external API calls (default).
                    live:    live-model adversary — requires CYBOTECH_MODEL_MODE=live
                             and OPENAI_API_KEY.  Never triggered automatically.
--db-url URL        SQLAlchemy database URL (defaults to DATABASE_URL env var).
                    Experiments are persisted here for later evaluation.
--output {text,json}
                    Terminal output format (default: text).

Design constraints
------------------
- Offline mode by default.  Live mode requires an explicit --mode live flag AND
  CYBOTECH_MODEL_MODE=live in the environment.  Live execution is never triggered
  by ordinary `make backend-test` or CI runs.
- Scripted adversary only in offline mode: no model API credits spent.
- Case limit is enforced before execution starts.
- Every run produces a clearly labelled run_id in output.
- Baseline and full-defense configurations are explicitly labelled.
- The production assistant's security configuration is never changed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from typing import Any, cast

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m cybotech.cli.red_team",
        description="Run a bounded synthetic red-team experiment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Default: 6 dev-split attack cases, baseline + full configs, offline
              make red-team

              # 10 cases, full defense only
              make red-team ARGS="--cases 10 --configurations full"

              # Include benign controls
              make red-team ARGS="--cases 6 --include-benign"

              # LIVE MODE — requires explicit env var; never runs automatically
              CYBOTECH_MODEL_MODE=live make red-team ARGS="--mode live --cases 3"
        """),
    )
    p.add_argument(
        "--cases",
        type=int,
        default=6,
        metavar="N",
        help="Number of attack cases (default: 6, max: 60 offline / 3 live)",
    )
    p.add_argument(
        "--split",
        choices=["development", "heldout", "all"],
        default="development",
        help="Corpus split to use (default: development)",
    )
    p.add_argument(
        "--objective",
        choices=["memory_poisoning", "unauthorized_disclosure", "workflow_hijack", "all"],
        default="all",
        help="Attack objective filter (default: all)",
    )
    p.add_argument(
        "--configurations",
        default="baseline,full",
        help="Comma-separated configurations: baseline,full,memory_guard,gateway "
        "(default: baseline,full)",
    )
    p.add_argument(
        "--include-benign",
        action="store_true",
        help="Also run benign control cases (up to --cases count)",
    )
    p.add_argument(
        "--mode",
        choices=["offline", "live"],
        default="offline",
        help="offline (default) or live (requires CYBOTECH_MODEL_MODE=live)",
    )
    p.add_argument(
        "--db-url", default=None, help="SQLAlchemy DB URL (default: DATABASE_URL env var)"
    )
    p.add_argument(
        "--output", choices=["text", "json"], default="text", help="Output format (default: text)"
    )
    return p


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _resolve_configurations(raw: str) -> list[str]:
    from cybotech.services.red_team.lab import CONFIGURATIONS

    valid = set(CONFIGURATIONS)
    if raw.strip() == "all":
        return list(CONFIGURATIONS)
    parts = [c.strip() for c in raw.split(",") if c.strip()]
    unknown = [c for c in parts if c not in valid]
    if unknown:
        _abort(f"Unknown configurations: {unknown}. Valid: {sorted(valid)}")
    if len(set(parts)) != len(parts):
        _abort("Configurations must be unique")
    return parts


def _abort(msg: str, code: int = 1) -> None:
    print(f"[red-team] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _print_section(title: str, width: int = 72) -> None:
    print(f"\n{'─' * width}")
    print(f"  {title}")
    print(f"{'─' * width}")


def _format_rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # ------------------------------------------------------------------ guards
    configurations = _resolve_configurations(args.configurations)

    if args.mode == "live":
        if os.getenv("CYBOTECH_MODEL_MODE") != "live":
            _abort(
                "Live mode requires CYBOTECH_MODEL_MODE=live in the environment.\n"
                "       This is intentional — live execution spends external API credits.\n"
                "       Set the variable explicitly to opt in."
            )
        if args.cases > 3:
            _abort("Live mode allows at most 3 cases per call.")
    else:
        if args.cases > 60:
            _abort("Offline mode allows at most 60 cases per call.")

    # ------------------------------------------------------------------ corpus
    from cybotech.services.red_team.corpus import corpus as load_corpus

    all_cases = load_corpus()

    attack_cases = [c for c in all_cases if c.kind == "attack"]
    if args.split != "all":
        attack_cases = [c for c in attack_cases if c.split == args.split]
    if args.objective != "all":
        attack_cases = [c for c in attack_cases if c.objective == args.objective]
    attack_cases = attack_cases[: args.cases]

    selected_cases = list(attack_cases)
    if args.include_benign:
        benign_cases = [c for c in all_cases if c.kind == "benign"]
        if args.split != "all":
            benign_cases = [c for c in benign_cases if c.split == args.split]
        benign_cases = benign_cases[: args.cases]
        selected_cases = selected_cases + benign_cases

    if not selected_cases:
        _abort("No cases selected after filtering. Check --split and --objective arguments.")

    live_budget = 3 if args.mode == "live" else 60
    if len(selected_cases) > live_budget:
        _abort(
            f"Selected {len(selected_cases)} cases but budget for "
            f"{'live' if args.mode == 'live' else 'offline'} mode is {live_budget}."
        )

    # ------------------------------------------------------------------ database
    db_url = args.db_url or os.getenv("DATABASE_URL")
    if not db_url:
        _abort(
            "No database URL.  Set DATABASE_URL or pass --db-url.\n"
            "       For a quick local run: DATABASE_URL=sqlite:///./lab.db make red-team"
        )
    assert db_url is not None

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from cybotech.infrastructure.database.models import Base

    engine = create_engine(db_url)
    Base.metadata.create_all(engine)  # safe no-op if tables already exist

    # ------------------------------------------------------------------ live model
    from cybotech.agents.contracts import ModelProvider

    live_model: ModelProvider | None = None
    if args.mode == "live":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            _abort("Live mode requires OPENAI_API_KEY to be set.")
        from openai import OpenAI

        from cybotech.infrastructure.llm.providers import OpenAIProvider

        live_model = OpenAIProvider(
            OpenAI(api_key=api_key, timeout=30, max_retries=0),
            os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        )

    # ------------------------------------------------------------------ run
    from cybotech.services.red_team.lab import Configuration, run_experiment

    if args.output == "text":
        _print_section("CYBOTECH RED-TEAM EXPERIMENT")
        print(f"  Mode         : {args.mode}")
        print(f"  Configurations: {', '.join(configurations)}")
        n_benign = len(selected_cases) - len(attack_cases)
        benign_suffix = f", {n_benign} benign" if args.include_benign else ""
        print(f"  Cases        : {len(selected_cases)} ({len(attack_cases)} attack{benign_suffix})")
        print(f"  Corpus split : {args.split}")
        if args.mode == "live":
            model_name = live_model.name if live_model else "unknown"
            print(f"  Live model   : {model_name}")
        print()
        print("  Running experiment — each case uses an isolated in-memory database.")
        print("  Results will be persisted to the application database for evaluation.")

    with Session(engine, expire_on_commit=False) as db:
        report = run_experiment(
            db, selected_cases, cast(list[Configuration], configurations), live_model
        )

    run_id = report["run_id"]
    results: list[dict[str, Any]] = report.get("results", [])
    metrics: list[dict[str, Any]] = report.get("metrics", [])

    if args.output == "json":
        # Strip the verbose per-case trace to keep output manageable.
        stripped = {k: v for k, v in report.items() if k != "results"}
        stripped["results"] = [{k2: v2 for k2, v2 in r.items() if k2 != "trace"} for r in results]
        print(json.dumps(stripped, indent=2, default=str))
        return 0

    # ------------------------------------------------------------------ text summary
    _print_section("EXPERIMENT COMPLETE")
    print(f"  Run ID       : {run_id}")
    print(
        f"  Corpus       : {report.get('corpus_version')} "
        f"(sha256: {report.get('corpus_sha256', '')[:16]}…)"
    )
    print(f"  Evidence kind: {report.get('evidence_kind')}")
    print(f"  Storage      : {report.get('storage')}")
    print(f"  Embedding    : {report.get('embedding')}")
    print()

    # Per-case table
    has_benign = any(r.get("kind") == "benign" for r in results)

    print("  Attack cases:")
    print(
        f"  {'case_id':<35} {'config':<14} {'status':<10} "
        f"{'attack_ok':<10} {'poisoned':<10} {'delivered':<10} {'ms':>8}"
    )
    print("  " + "─" * 105)
    for r in results:
        if r.get("kind") != "attack":
            continue
        ok_sym = "✓" if r.get("attack_success") else "✗"
        p_sym = "✓" if r.get("poisoned_memory") else "✗"
        d_sym = "✓" if r.get("attacker_delivery") else "✗"
        status = r.get("status", "?")
        print(
            f"  {r['case_id']:<35} {r['configuration']:<14} {status:<10} "
            f"{ok_sym:<10} {p_sym:<10} {d_sym:<10} {r.get('latency_ms', 0.0):>8.1f}"
        )

    if has_benign:
        print()
        print("  Benign control cases:")
        print(f"  {'case_id':<35} {'config':<14} {'status':<10} {'utility_ok':<12} {'ms':>8}")
        print("  " + "─" * 85)
        for r in results:
            if r.get("kind") != "benign":
                continue
            u_sym = "✓" if r.get("legitimate_task_success") else "✗"
            print(
                f"  {r['case_id']:<35} {r['configuration']:<14} "
                f"{r.get('status', '?'):<10} {u_sym:<12} "
                f"{r.get('latency_ms', 0.0):>8.1f}"
            )

    # Per-configuration summary
    _print_section("PER-CONFIGURATION METRICS (from run_experiment)")
    for m in metrics:
        config = m.get("configuration", "?")
        n = m.get("cases", 0)
        errors = m.get("errors", 0)
        asr = _format_rate(m.get("attack_success_rate"))
        fpr = _format_rate(m.get("benign_failure_rate"))
        lat = m.get("mean_latency_ms", 0.0)
        print(
            f"  {config:<14}  cases={n:<4}  errors={errors:<3}  "
            f"attack_success_rate={asr:<8}  benign_failure_rate={fpr:<8}  "
            f"mean_ms={lat:.1f}"
        )

    _print_section("NOTE")
    print("  Scripted adversary exercises deterministic security controls.")
    print("  A small number of scripted runs does not establish general robustness.")
    print("  Use `make evaluate` to compute metrics over all persisted runs.")
    if args.mode != "live":
        print()
        print("  Live-model execution: set CYBOTECH_MODEL_MODE=live and pass --mode live.")
        print("  Live runs spend external API credits and are never triggered automatically.")
    print()

    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
