"""CLI entry-point for the evaluation engine.

Usage (via Makefile):
    make evaluate

Usage (direct):
    cd backend && uv run python -m cybotech.cli.evaluate [OPTIONS]

Options
-------
--run-id ID         Evaluate only a specific run (default: all runs).
--generation-mode {scripted,live_model}
                    Filter by generation mode.
--configuration {baseline,full,memory_guard,gateway}
                    Filter by defense configuration.
--corpus-version V  Filter by corpus version string.
--model-name NAME   Filter by model name.
--db-url URL        SQLAlchemy database URL (defaults to DATABASE_URL env var).
--output {text,json}
                    Terminal output format (default: text).

Design constraints
------------------
- Reads only.  No experiment outcomes are mutated.
- No hardcoded results.  All numbers come from persisted AuditEvent rows.
- Reports actual numerators, denominators, and unavailability reasons.
- Scripted and live-model populations are always labelled separately.
- Baseline and full-defense populations are compared only when comparable.
- Missing experiment data is reported honestly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap

from cybotech.services.evaluation.contracts import (
    EvaluationMetrics,
    LatencyMetric,
    ProportionMetric,
)

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m cybotech.cli.evaluate",
        description="Compute security metrics from persisted experiment records.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # All persisted runs
              make evaluate

              # Only scripted experiments
              make evaluate ARGS="--generation-mode scripted"

              # Only full-defense configuration
              make evaluate ARGS="--configuration full"

              # Specific run
              make evaluate ARGS="--run-id lab_abc123"

              # JSON output for tooling
              make evaluate ARGS="--output json"
        """),
    )
    p.add_argument("--run-id", default=None, help="Evaluate only this run_id")
    p.add_argument(
        "--generation-mode",
        choices=["scripted", "live_model"],
        default=None,
        help="Filter by generation mode",
    )
    p.add_argument(
        "--configuration",
        choices=["baseline", "full", "memory_guard", "gateway"],
        default=None,
        help="Filter by defense configuration",
    )
    p.add_argument("--corpus-version", default=None, help="Filter by corpus version string")
    p.add_argument("--model-name", default=None, help="Filter by model name")
    p.add_argument(
        "--db-url", default=None, help="SQLAlchemy DB URL (default: DATABASE_URL env var)"
    )
    p.add_argument(
        "--output", choices=["text", "json"], default="text", help="Output format (default: text)"
    )
    return p


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _abort(msg: str, code: int = 1) -> None:
    print(f"[evaluate] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _print_section(title: str, width: int = 72) -> None:
    print(f"\n{'─' * width}")
    print(f"  {title}")
    print(f"{'─' * width}")


def _fmt_proportion(m: ProportionMetric) -> str:
    """Format a ProportionMetric for terminal display."""
    if not m.available:
        return f"UNAVAILABLE ({m.unavailability_reason})"
    ci = m.confidence_interval_95
    ci_str = f"  [95% CI: {ci[0] * 100:.1f}%-{ci[1] * 100:.1f}%]" if ci else ""
    return f"{m.percent:.1f}%  ({m.numerator}/{m.denominator}){ci_str}"


def _fmt_latency(m: LatencyMetric) -> str:
    """Format a LatencyMetric for terminal display."""
    if not m.available:
        parts = [f"UNAVAILABLE ({m.unavailability_reason})"]
        # Still print partial numbers if we have them (insufficient pairs case)
        if m.baseline_median_ms is not None:
            parts.append(
                f"  baseline median={m.baseline_median_ms:.1f}ms  "
                f"full median={m.full_defense_median_ms:.1f}ms  "
                f"overhead={m.overhead_percent:.1f}%  "
                f"[n_baseline={m.baseline_sample_count},"
                f" n_full={m.full_defense_sample_count}]"
            )
        if m.comparison_note:
            parts.append(f"  Note: {m.comparison_note}")
        return "\n    ".join(parts)
    return (
        f"overhead={m.overhead_percent:+.1f}%  "
        f"baseline_median={m.baseline_median_ms:.1f}ms  "
        f"full_median={m.full_defense_median_ms:.1f}ms  "
        f"[n_baseline={m.baseline_sample_count},"
        f" n_full={m.full_defense_sample_count}]"
    )


def _print_stratum(stratum: EvaluationMetrics, indent: int = 2) -> None:
    pad = " " * indent
    print(f"{pad}Population : {stratum.population_label}")
    print(f"{pad}Samples    : {stratum.sample_count}")
    print(f"{pad}Outcomes   : {stratum.outcome_counts}")
    print()
    asr = _fmt_proportion(stratum.attack_success_rate)
    mpr = _fmt_proportion(stratum.memory_poisoning_success_rate)
    utr = _fmt_proportion(stratum.unauthorized_tool_execution_rate)
    dr = _fmt_proportion(stratum.detection_rate)
    fpr = _fmt_proportion(stratum.false_positive_rate)
    lat = _fmt_latency(stratum.latency_overhead)
    print(f"{pad}A. Attack Success Rate              : {asr}")
    print(f"{pad}B. Memory Poisoning Success Rate    : {mpr}")
    print(f"{pad}C. Unauthorized Tool Execution Rate : {utr}")
    print(f"{pad}D. Detection Rate                   : {dr}")
    print(f"{pad}E. False Positive Rate              : {fpr}")
    print(f"{pad}F. Latency Overhead                 : {lat}")


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    db_url = args.db_url or os.getenv("DATABASE_URL")
    if not db_url:
        _abort(
            "No database URL.  Set DATABASE_URL or pass --db-url.\n"
            "       Example: DATABASE_URL=sqlite:///./lab.db make evaluate"
        )
    assert db_url is not None

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(db_url)
    with Session(engine, expire_on_commit=False) as db:
        from cybotech.services.evaluation.service import EvaluationService

        svc = EvaluationService(db)

        # ---------------------------------------------------------------- runs list
        runs, total_runs = svc.list_runs(limit=200, offset=0)

        if args.output == "text":
            _print_section("CYBOTECH EVALUATION ENGINE")
            print(f"  Database     : {db_url.split('@')[-1] if '@' in db_url else db_url}")
            print(f"  Persisted runs found: {total_runs}")
            if not runs:
                print()
                print("  No experiment data found.")
                print("  Run `make red-team` first to produce experiment records.")
                return 0

            for r in runs[:10]:
                print(
                    f"  • {r['run_id']}  "
                    f"[{r.get('evidence_kind', '?')}]  "
                    f"cases={r.get('case_count', '?')}  "
                    f"corpus={r.get('corpus_version', '?')}  "
                    f"at={r.get('occurred_at', '?')}"
                )
            if total_runs > 10:
                print(f"  … and {total_runs - 10} more")

        # ---------------------------------------------------------------- evaluate
        summary, parse_errors = svc.evaluate(
            run_id=args.run_id,
            generation_mode=args.generation_mode,
            configuration=args.configuration,
            corpus_version=args.corpus_version,
            model_name=args.model_name,
        )

        if args.output == "json":
            out = summary.model_dump(mode="json")
            out["parse_error_count"] = len(parse_errors)
            out["runs_found"] = total_runs
            print(json.dumps(out, indent=2, default=str))
            return 0

        # ---------------------------------------------------------------- text
        _print_section("EVALUATION SUMMARY")
        print(f"  Evaluated at          : {summary.evaluated_at.isoformat()}")
        print(f"  Total records         : {summary.total_experiment_records}")
        print(f"  Valid attack records  : {summary.valid_attack_records}")
        print(f"  Valid benign records  : {summary.valid_benign_records}")
        print(f"  Failed records        : {summary.failed_records}")
        print(f"  Incomplete records    : {summary.incomplete_records}")
        print(f"  Invalid records       : {summary.invalid_records}")
        print(f"  Parse errors          : {len(parse_errors)}")
        print(f"  Corpus versions       : {', '.join(summary.corpus_versions) or 'none'}")
        print(f"  Configurations        : {', '.join(summary.configurations_present) or 'none'}")
        print(f"  Generation modes      : {', '.join(summary.generation_modes_present) or 'none'}")
        print(
            f"  Run IDs               : {', '.join(summary.run_ids[:3])}"
            + (f" (+ {len(summary.run_ids) - 3} more)" if len(summary.run_ids) > 3 else "")
        )

        if summary.total_experiment_records == 0:
            print()
            print("  No records match the current filters.  Try broadening the filter criteria")
            print("  or run `make red-team` to generate experiment data.")
            return 0

        # Print strata — overall first, then the interesting per-mode/config pairs
        strata_to_print = [s for s in summary.strata if s.sample_count > 0]

        # Prefer to show per-(mode,config) strata for maximum clarity
        pair_strata = [s for s in strata_to_print if "|" in s.population_label]
        other_strata = [s for s in strata_to_print if "|" not in s.population_label]

        _print_section("METRICS BY STRATUM")
        for stratum in other_strata:
            print()
            _print_stratum(stratum)
            print()

        if pair_strata:
            _print_section("METRICS BY (GENERATION-MODE x CONFIGURATION)")
            for stratum in pair_strata:
                print()
                _print_stratum(stratum)
                print()

        # Parse errors
        if parse_errors:
            _print_section(f"PARSE ERRORS ({len(parse_errors)} records)")
            for err in parse_errors[:10]:
                print(
                    f"  run_id={err.get('run_id')}  case_id={err.get('case_id')}  "
                    f"reason={err.get('reason')}"
                )
            if len(parse_errors) > 10:
                print(f"  … and {len(parse_errors) - 10} more")

        _print_section("CAVEATS")
        print("  • Metrics are computed from synthetic scripted experiments unless")
        print("    live-model runs are present in the database.")
        print("  • A small number of runs does not establish general robustness.")
        print("  • Latency overhead requires ≥5 matched (scenario, mode, model, corpus)")
        print("    pairs across baseline and full-defense configurations.")
        print("  • 95% confidence intervals use the Wilson score method.")
        print()

    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
