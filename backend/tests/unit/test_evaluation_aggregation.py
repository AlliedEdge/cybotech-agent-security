"""Unit tests for aggregation, stratification, comparability, and filtering.

Covers:
- Baseline/full-defense grouping
- Scripted/live grouping
- Different model configurations
- Different corpus versions
- Insufficient matched pairs
- Filtering dimensions
- Pagination
"""

from __future__ import annotations

from datetime import UTC, datetime

from cybotech.services.evaluation.aggregation import (
    build_evaluation_summary,
    check_comparability,
    filter_records,
    paginate,
)
from cybotech.services.evaluation.contracts import ExperimentOutcome, ExperimentRecord

_TS = datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC)
_DEFENSE_FULL = {"name": "full", "memory_guard": True, "sentinel": True, "gateway": True}
_DEFENSE_BASE = {"name": "baseline", "memory_guard": False, "sentinel": False, "gateway": False}


def _rec(
    *,
    configuration: str = "full",
    generation_mode: str = "scripted",
    model_name: str = "scripted-adversary-v2",
    corpus_sha256: str = "abc123",
    corpus_version: str = "synthetic-v1",
    scenario_kind: str = "attack",
    scenario_objective: str = "memory_poisoning",
    outcome: ExperimentOutcome = ExperimentOutcome.attack_blocked,
    run_id: str = "run_a",
    case_id: str = "case_00",
    scenario_id: str = "case_00",
    execution_duration_ms: float = 200.0,
    status: str = "completed",
    attack_success: bool | None = False,
    poisoned_memory: bool | None = False,
    attacker_delivery: bool | None = False,
    legitimate_task_success: bool | None = None,
) -> ExperimentRecord:
    defense = _DEFENSE_FULL if configuration == "full" else _DEFENSE_BASE
    return ExperimentRecord(
        run_id=run_id,
        case_id=case_id,
        scenario_id=scenario_id,
        scenario_kind=scenario_kind,  # type: ignore[arg-type]
        scenario_objective=scenario_objective,  # type: ignore[arg-type]
        scenario_split="development",
        corpus_version=corpus_version,
        corpus_sha256=corpus_sha256,
        defense_profile=defense,
        configuration=configuration,  # type: ignore[arg-type]
        generation_mode=generation_mode,  # type: ignore[arg-type]
        model_name=model_name,
        embedding_model="local-token-hash-384-v1",
        reviewer="deterministic",
        run_timestamp=_TS,
        execution_duration_ms=execution_duration_ms,
        status=status,  # type: ignore[arg-type]
        attack_success=attack_success,
        poisoned_memory=poisoned_memory,
        attacker_delivery=attacker_delivery,
        legitimate_task_success=legitimate_task_success,
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# ComparabilityCheck
# ---------------------------------------------------------------------------


class TestComparabilityCheck:
    def test_identical_populations_are_comparable(self):
        baseline = [_rec(configuration="baseline", case_id="c0")]
        full = [_rec(configuration="full", case_id="c0")]
        check = check_comparability(baseline, full)
        assert check.comparable
        assert check.mismatches == []

    def test_different_model_names_not_comparable(self):
        baseline = [_rec(configuration="baseline", model_name="model-a")]
        full = [_rec(configuration="full", model_name="model-b")]
        check = check_comparability(baseline, full)
        assert not check.comparable
        assert any("model_name" in m for m in check.mismatches)

    def test_different_corpus_sha_not_comparable(self):
        baseline = [_rec(configuration="baseline", corpus_sha256="aaaa")]
        full = [_rec(configuration="full", corpus_sha256="bbbb")]
        check = check_comparability(baseline, full)
        assert not check.comparable
        assert any("corpus_sha256" in m for m in check.mismatches)

    def test_different_generation_modes_not_comparable(self):
        baseline = [_rec(configuration="baseline", generation_mode="scripted")]
        full = [_rec(configuration="full", generation_mode="live_model")]
        check = check_comparability(baseline, full)
        assert not check.comparable
        assert any("generation_mode" in m for m in check.mismatches)

    def test_empty_population_not_comparable(self):
        full = [_rec(configuration="full")]
        check = check_comparability([], full)
        assert not check.comparable

    def test_counts_reported_correctly(self):
        baseline = [_rec(configuration="baseline") for _ in range(3)]
        full = [_rec(configuration="full") for _ in range(5)]
        check = check_comparability(baseline, full)
        assert check.baseline_count == 3
        assert check.treatment_count == 5

    def test_custom_labels(self):
        check = check_comparability(
            [_rec(configuration="baseline")],
            [_rec(configuration="full")],
            baseline_label="control",
            treatment_label="experiment",
        )
        assert check.baseline_label == "control"
        assert check.treatment_label == "experiment"


# ---------------------------------------------------------------------------
# EvaluationSummary counts
# ---------------------------------------------------------------------------


class TestBuildEvaluationSummary:
    def test_empty_records(self):
        summary = build_evaluation_summary([])
        assert summary.total_experiment_records == 0
        assert summary.valid_attack_records == 0
        assert summary.valid_benign_records == 0
        assert summary.failed_records == 0
        assert len(summary.strata) >= 1  # always has overall stratum

    def test_failed_incomplete_invalid_counted_not_silently_dropped(self):
        records = [
            _rec(outcome=ExperimentOutcome.experiment_failed, status="error"),
            _rec(outcome=ExperimentOutcome.experiment_incomplete, status="in_progress"),
            _rec(outcome=ExperimentOutcome.experiment_invalid),
            _rec(outcome=ExperimentOutcome.attack_blocked),
        ]
        summary = build_evaluation_summary(records)
        assert summary.total_experiment_records == 4
        assert summary.failed_records == 1
        assert summary.incomplete_records == 1
        assert summary.invalid_records == 1
        assert summary.valid_attack_records == 1

    def test_benign_records_counted_separately(self):
        records = [
            _rec(
                scenario_kind="benign",
                scenario_objective="utility",
                outcome=ExperimentOutcome.attack_blocked,
            ),
            _rec(scenario_kind="attack", outcome=ExperimentOutcome.attack_blocked),
        ]
        summary = build_evaluation_summary(records)
        assert summary.valid_benign_records == 1
        assert summary.valid_attack_records == 1

    def test_strata_created_for_each_dimension(self):
        records = [
            _rec(configuration="baseline", generation_mode="scripted"),
            _rec(configuration="full", generation_mode="scripted"),
            _rec(configuration="baseline", generation_mode="live_model"),
        ]
        summary = build_evaluation_summary(records)
        labels = {s.population_label for s in summary.strata}
        assert "all" in labels
        assert "configuration:baseline" in labels
        assert "configuration:full" in labels
        assert "generation_mode:scripted" in labels
        assert "generation_mode:live_model" in labels

    def test_corpus_versions_and_run_ids_collected(self):
        records = [
            _rec(corpus_version="v1", run_id="run_a"),
            _rec(corpus_version="v2", run_id="run_b"),
        ]
        summary = build_evaluation_summary(records)
        assert set(summary.corpus_versions) == {"v1", "v2"}
        assert set(summary.run_ids) == {"run_a", "run_b"}

    def test_configurations_and_modes_collected(self):
        records = [
            _rec(configuration="baseline", generation_mode="scripted"),
            _rec(configuration="full", generation_mode="live_model"),
        ]
        summary = build_evaluation_summary(records)
        assert set(summary.configurations_present) == {"baseline", "full"}
        assert set(summary.generation_modes_present) == {"scripted", "live_model"}

    def test_scripted_and_live_remain_separate_strata(self):
        records = [
            _rec(generation_mode="scripted", outcome=ExperimentOutcome.attack_blocked),
            _rec(
                generation_mode="live_model",
                outcome=ExperimentOutcome.attack_succeeded,
                attack_success=True,
            ),
        ]
        summary = build_evaluation_summary(records)
        scripted_stratum = next(
            s for s in summary.strata if s.population_label == "generation_mode:scripted"
        )
        live_stratum = next(
            s for s in summary.strata if s.population_label == "generation_mode:live_model"
        )
        assert scripted_stratum.attack_success_rate.percent == 0.0
        assert live_stratum.attack_success_rate.percent == 100.0

    def test_baseline_and_full_defense_in_separate_strata(self):
        records = [
            _rec(
                configuration="baseline",
                outcome=ExperimentOutcome.attack_succeeded,
                attack_success=True,
            ),
            _rec(configuration="full", outcome=ExperimentOutcome.attack_blocked),
        ]
        summary = build_evaluation_summary(records)
        baseline_stratum = next(
            s for s in summary.strata if s.population_label == "configuration:baseline"
        )
        full_stratum = next(s for s in summary.strata if s.population_label == "configuration:full")
        assert baseline_stratum.attack_success_rate.percent == 100.0
        assert full_stratum.attack_success_rate.percent == 0.0

    def test_different_model_configs_separate_in_pair_strata(self):
        records = [
            _rec(
                configuration="full",
                generation_mode="scripted",
                outcome=ExperimentOutcome.attack_blocked,
            ),
            _rec(
                configuration="full",
                generation_mode="live_model",
                outcome=ExperimentOutcome.attack_succeeded,
                attack_success=True,
            ),
        ]
        summary = build_evaluation_summary(records)
        pair_strata = {s.population_label: s for s in summary.strata if "|" in s.population_label}
        assert "generation_mode:scripted|configuration:full" in pair_strata
        assert "generation_mode:live_model|configuration:full" in pair_strata
        scripted = pair_strata["generation_mode:scripted|configuration:full"]
        live = pair_strata["generation_mode:live_model|configuration:full"]
        assert scripted.attack_success_rate.percent == 0.0
        assert live.attack_success_rate.percent == 100.0

    def test_insufficient_latency_pairs_marks_unavailable(self):
        # 2 pairs — below the 5-pair threshold
        records = [
            _rec(configuration="baseline", scenario_id="s0", execution_duration_ms=100.0),
            _rec(configuration="full", scenario_id="s0", execution_duration_ms=200.0),
            _rec(configuration="baseline", scenario_id="s1", execution_duration_ms=100.0),
            _rec(configuration="full", scenario_id="s1", execution_duration_ms=200.0),
        ]
        summary = build_evaluation_summary(records)
        overall = next(s for s in summary.strata if s.population_label == "all")
        assert not overall.latency_overhead.available


# ---------------------------------------------------------------------------
# filter_records
# ---------------------------------------------------------------------------


class TestFilterRecords:
    def _population(self) -> list[ExperimentRecord]:
        return [
            _rec(
                run_id="run_a",
                configuration="baseline",
                generation_mode="scripted",
                scenario_kind="attack",
                scenario_objective="memory_poisoning",
                corpus_version="v1",
                model_name="model-x",
                case_id="c0",
                scenario_id="c0",
            ),
            _rec(
                run_id="run_b",
                configuration="full",
                generation_mode="live_model",
                scenario_kind="benign",
                scenario_objective="utility",
                corpus_version="v2",
                model_name="model-y",
                case_id="c1",
                scenario_id="c1",
            ),
        ]

    def test_filter_by_configuration(self):
        result = filter_records(self._population(), configuration="baseline")
        assert len(result) == 1
        assert result[0].case_id == "c0"

    def test_filter_by_generation_mode(self):
        result = filter_records(self._population(), generation_mode="live_model")
        assert len(result) == 1
        assert result[0].case_id == "c1"

    def test_filter_by_run_id(self):
        result = filter_records(self._population(), run_id="run_a")
        assert len(result) == 1

    def test_filter_by_scenario_kind(self):
        result = filter_records(self._population(), scenario_kind="benign")
        assert len(result) == 1
        assert result[0].scenario_objective == "utility"

    def test_filter_by_scenario_objective(self):
        result = filter_records(self._population(), scenario_objective="memory_poisoning")
        assert len(result) == 1

    def test_filter_by_corpus_version(self):
        result = filter_records(self._population(), corpus_version="v2")
        assert len(result) == 1

    def test_filter_by_model_name(self):
        result = filter_records(self._population(), model_name="model-x")
        assert len(result) == 1

    def test_combined_filters_are_anded(self):
        result = filter_records(
            self._population(), configuration="baseline", generation_mode="scripted"
        )
        assert len(result) == 1

    def test_no_match_returns_empty(self):
        result = filter_records(self._population(), run_id="run_z")
        assert result == []

    def test_no_filters_returns_all(self):
        result = filter_records(self._population())
        assert len(result) == 2


# ---------------------------------------------------------------------------
# paginate
# ---------------------------------------------------------------------------


class TestPaginate:
    def _items(self, n: int) -> list[int]:
        return list(range(n))

    def test_first_page(self):
        page, total = paginate(self._items(10), limit=3, offset=0)
        assert page == [0, 1, 2]
        assert total == 10

    def test_second_page(self):
        page, total = paginate(self._items(10), limit=3, offset=3)
        assert page == [3, 4, 5]
        assert total == 10

    def test_last_partial_page(self):
        page, total = paginate(self._items(10), limit=4, offset=8)
        assert page == [8, 9]
        assert total == 10

    def test_offset_beyond_end(self):
        page, total = paginate(self._items(5), limit=10, offset=100)
        assert page == []
        assert total == 5

    def test_empty_list(self):
        page, total = paginate([], limit=10, offset=0)
        assert page == []
        assert total == 0
