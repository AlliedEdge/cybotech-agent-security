"""Experiment record aggregation and stratification.

Aggregation groups validated ExperimentRecords along stratification dimensions
and calls the metric calculations on each stratum.

Stratification dimensions
-------------------------
- generation_mode  : scripted | live_model
- configuration    : baseline | memory_guard | gateway | full
- model_name       : the attacker/assistant model identifier
- corpus_sha256    : identifies the exact corpus used
- corpus_version   : the named corpus version

Baseline/full-defense comparisons first check comparability via ComparabilityCheck.
Incompatible populations are never merged into a labelled comparison.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from cybotech.services.evaluation.contracts import (
    ComparabilityCheck,
    DefenseConfiguration,
    EvaluationMetrics,
    EvaluationSummary,
    ExperimentOutcome,
    ExperimentRecord,
    GenerationMode,
)
from cybotech.services.evaluation.metrics import (
    attack_success_rate,
    count_outcomes,
    detection_rate,
    false_positive_rate,
    latency_overhead,
    memory_poisoning_success_rate,
    unauthorized_tool_execution_rate,
)

# ---------------------------------------------------------------------------
# Comparability
# ---------------------------------------------------------------------------


def check_comparability(
    baseline: Sequence[ExperimentRecord],
    treatment: Sequence[ExperimentRecord],
    baseline_label: str = "baseline",
    treatment_label: str = "full",
) -> ComparabilityCheck:
    """Verify that two populations can be used in a controlled comparison.

    Checks that the sets of (generation_mode, model_name, corpus_sha256)
    overlap meaningfully.  If the sets do not intersect we report incompatible.
    """
    mismatches: list[str] = []

    def _keys(recs: Sequence[ExperimentRecord]) -> set[tuple[str, str, str]]:
        return {(r.generation_mode, r.model_name, r.corpus_sha256) for r in recs}

    b_keys = _keys(baseline)
    t_keys = _keys(treatment)

    intersection = b_keys & t_keys
    if not intersection:
        mismatches.append(
            "no overlapping (generation_mode, model_name, corpus_sha256) tuples — "
            "populations are not comparable"
        )

    b_modes = {r.generation_mode for r in baseline}
    t_modes = {r.generation_mode for r in treatment}
    if b_modes != t_modes:
        mismatches.append(
            f"generation_mode mismatch: baseline={sorted(b_modes)} treatment={sorted(t_modes)}"
        )

    b_models = {r.model_name for r in baseline}
    t_models = {r.model_name for r in treatment}
    if b_models != t_models:
        mismatches.append(
            f"model_name mismatch: baseline={sorted(b_models)} treatment={sorted(t_models)}"
        )

    b_corpus = {r.corpus_sha256 for r in baseline}
    t_corpus = {r.corpus_sha256 for r in treatment}
    if b_corpus != t_corpus:
        mismatches.append(
            f"corpus_sha256 mismatch: baseline has {len(b_corpus)} version(s), "
            f"treatment has {len(t_corpus)} version(s)"
        )

    return ComparabilityCheck(
        comparable=len(mismatches) == 0,
        mismatches=mismatches,
        baseline_label=baseline_label,
        treatment_label=treatment_label,
        baseline_count=len(baseline),
        treatment_count=len(treatment),
    )


# ---------------------------------------------------------------------------
# Single-stratum metrics assembly
# ---------------------------------------------------------------------------


def _build_stratum_metrics(
    records: Sequence[ExperimentRecord],
    *,
    population_label: str,
    generation_mode: GenerationMode | str | None,
    configuration: DefenseConfiguration | str | None,
) -> EvaluationMetrics:
    """Compute all metrics for a single stratum of records."""
    baseline = [r for r in records if r.configuration == "baseline"]
    full = [r for r in records if r.configuration == "full"]

    return EvaluationMetrics(
        population_label=population_label,
        generation_mode=generation_mode,  # type: ignore[arg-type]
        configuration=configuration,  # type: ignore[arg-type]
        sample_count=len(records),
        attack_success_rate=attack_success_rate(records),
        memory_poisoning_success_rate=memory_poisoning_success_rate(records),
        unauthorized_tool_execution_rate=unauthorized_tool_execution_rate(records),
        detection_rate=detection_rate(records),
        false_positive_rate=false_positive_rate(records),
        latency_overhead=latency_overhead(baseline, full),
        outcome_counts=count_outcomes(records),
    )


# ---------------------------------------------------------------------------
# Full evaluation summary
# ---------------------------------------------------------------------------


def build_evaluation_summary(records: Sequence[ExperimentRecord]) -> EvaluationSummary:
    """Aggregate all validated records into a full EvaluationSummary.

    Stratification:
      1. All records together (overall).
      2. Per-generation-mode.
      3. Per-configuration.
      4. Per (generation_mode, configuration) pair.
      Baseline/full-defense latency comparisons check comparability first.

    Failed, incomplete, and invalid records are counted and excluded from
    metric denominators (they are never silently dropped).
    """
    records = list(records)

    # Counts — never silently exclude
    failed = sum(1 for r in records if r.outcome == ExperimentOutcome.experiment_failed)
    incomplete = sum(1 for r in records if r.outcome == ExperimentOutcome.experiment_incomplete)
    invalid = sum(1 for r in records if r.outcome == ExperimentOutcome.experiment_invalid)
    valid_attacks = sum(
        1
        for r in records
        if r.scenario_kind == "attack"
        and r.outcome
        not in {
            ExperimentOutcome.experiment_failed,
            ExperimentOutcome.experiment_incomplete,
            ExperimentOutcome.experiment_invalid,
        }
    )
    valid_benign = sum(
        1
        for r in records
        if r.scenario_kind == "benign"
        and r.outcome
        not in {
            ExperimentOutcome.experiment_failed,
            ExperimentOutcome.experiment_incomplete,
            ExperimentOutcome.experiment_invalid,
        }
    )

    strata: list[EvaluationMetrics] = []

    # --- Overall ---
    strata.append(
        _build_stratum_metrics(
            records,
            population_label="all",
            generation_mode="mixed",
            configuration="mixed",
        )
    )

    # --- Per generation_mode ---
    by_mode: dict[str, list[ExperimentRecord]] = defaultdict(list)
    for r in records:
        by_mode[r.generation_mode].append(r)
    for mode, mode_records in sorted(by_mode.items()):
        strata.append(
            _build_stratum_metrics(
                mode_records,
                population_label=f"generation_mode:{mode}",
                generation_mode=mode,
                configuration="mixed",
            )
        )

    # --- Per configuration ---
    by_config: dict[str, list[ExperimentRecord]] = defaultdict(list)
    for r in records:
        by_config[r.configuration].append(r)
    for config, config_records in sorted(by_config.items()):
        strata.append(
            _build_stratum_metrics(
                config_records,
                population_label=f"configuration:{config}",
                generation_mode="mixed",
                configuration=config,
            )
        )

    # --- Per (generation_mode, configuration) ---
    by_pair: dict[tuple[str, str], list[ExperimentRecord]] = defaultdict(list)
    for r in records:
        by_pair[(r.generation_mode, r.configuration)].append(r)
    for (mode, config), pair_records in sorted(by_pair.items()):
        strata.append(
            _build_stratum_metrics(
                pair_records,
                population_label=f"generation_mode:{mode}|configuration:{config}",
                generation_mode=mode,
                configuration=config,
            )
        )

    corpus_versions: list[str] = sorted({r.corpus_version for r in records})
    configurations_present: list[str] = sorted({r.configuration for r in records})
    generation_modes_present: list[str] = sorted({r.generation_mode for r in records})
    run_ids: list[str] = sorted({r.run_id for r in records})

    return EvaluationSummary(
        evaluated_at=datetime.now(UTC),
        total_experiment_records=len(records),
        valid_attack_records=valid_attacks,
        valid_benign_records=valid_benign,
        failed_records=failed,
        incomplete_records=incomplete,
        invalid_records=invalid,
        strata=strata,
        corpus_versions=corpus_versions,
        configurations_present=configurations_present,
        generation_modes_present=generation_modes_present,
        run_ids=run_ids,
    )


# ---------------------------------------------------------------------------
# Filter helpers used by the API layer
# ---------------------------------------------------------------------------


def filter_records(
    records: Sequence[ExperimentRecord],
    *,
    generation_mode: str | None = None,
    configuration: str | None = None,
    run_id: str | None = None,
    scenario_kind: str | None = None,
    scenario_objective: str | None = None,
    corpus_version: str | None = None,
    model_name: str | None = None,
) -> list[ExperimentRecord]:
    """Apply filter dimensions to a record sequence.  All conditions are AND-combined."""
    result = list(records)
    if generation_mode is not None:
        result = [r for r in result if r.generation_mode == generation_mode]
    if configuration is not None:
        result = [r for r in result if r.configuration == configuration]
    if run_id is not None:
        result = [r for r in result if r.run_id == run_id]
    if scenario_kind is not None:
        result = [r for r in result if r.scenario_kind == scenario_kind]
    if scenario_objective is not None:
        result = [r for r in result if r.scenario_objective == scenario_objective]
    if corpus_version is not None:
        result = [r for r in result if r.corpus_version == corpus_version]
    if model_name is not None:
        result = [r for r in result if r.model_name == model_name]
    return result


def paginate(items: list[Any], *, limit: int, offset: int) -> tuple[list[Any], int]:
    """Return (page, total)."""
    return items[offset : offset + limit], len(items)
