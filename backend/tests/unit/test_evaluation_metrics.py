"""Unit tests for evaluation metric calculations.

Tests cover all six metrics, outcome integrity, and the Wilson confidence interval.
All tests use hand-crafted ExperimentRecord fixtures — no live DB, no network.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cybotech.services.evaluation.contracts import ExperimentOutcome, ExperimentRecord
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
# Fixtures
# ---------------------------------------------------------------------------

_TIMESTAMP = datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC)
_DEFENSE = {"name": "full", "memory_guard": True, "sentinel": True, "gateway": True}


def _record(
    *,
    scenario_kind: str = "attack",
    scenario_objective: str = "memory_poisoning",
    outcome: ExperimentOutcome = ExperimentOutcome.attack_blocked,
    attack_success: bool | None = False,
    poisoned_memory: bool | None = False,
    attacker_delivery: bool | None = False,
    legitimate_task_success: bool | None = None,
    configuration: str = "full",
    generation_mode: str = "scripted",
    model_name: str = "scripted-adversary-v2",
    corpus_sha256: str = "abc123",
    corpus_version: str = "synthetic-v1",
    execution_duration_ms: float = 200.0,
    status: str = "completed",
    run_id: str = "run_test",
    case_id: str = "case_test",
    scenario_id: str = "case_test",
) -> ExperimentRecord:
    return ExperimentRecord(
        run_id=run_id,
        case_id=case_id,
        scenario_id=scenario_id,
        scenario_kind=scenario_kind,  # type: ignore[arg-type]
        scenario_objective=scenario_objective,  # type: ignore[arg-type]
        scenario_split="development",
        corpus_version=corpus_version,
        corpus_sha256=corpus_sha256,
        defense_profile=_DEFENSE,
        configuration=configuration,  # type: ignore[arg-type]
        generation_mode=generation_mode,  # type: ignore[arg-type]
        model_name=model_name,
        embedding_model="local-token-hash-384-v1",
        reviewer="deterministic",
        run_timestamp=_TIMESTAMP,
        execution_duration_ms=execution_duration_ms,
        status=status,  # type: ignore[arg-type]
        attack_success=attack_success,
        poisoned_memory=poisoned_memory,
        attacker_delivery=attacker_delivery,
        legitimate_task_success=legitimate_task_success,
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# A. Attack Success Rate
# ---------------------------------------------------------------------------

class TestAttackSuccessRate:
    def test_all_blocked(self):
        records = [_record(outcome=ExperimentOutcome.attack_blocked) for _ in range(5)]
        m = attack_success_rate(records)
        assert m.available
        assert m.numerator == 0
        assert m.denominator == 5
        assert m.proportion == 0.0
        assert m.percent == 0.0

    def test_all_succeeded(self):
        records = [
            _record(outcome=ExperimentOutcome.attack_succeeded, attack_success=True)
            for _ in range(4)
        ]
        m = attack_success_rate(records)
        assert m.available
        assert m.numerator == 4
        assert m.denominator == 4
        assert m.proportion == 1.0

    def test_partial_success_does_not_count_as_full_success(self):
        records = [
            _record(outcome=ExperimentOutcome.attack_succeeded, attack_success=True),
            _record(outcome=ExperimentOutcome.partially_succeeded, attack_success=False),
            _record(outcome=ExperimentOutcome.attack_blocked),
        ]
        m = attack_success_rate(records)
        assert m.numerator == 1
        assert m.denominator == 3

    def test_failed_experiments_excluded_from_denominator(self):
        records = [
            _record(outcome=ExperimentOutcome.attack_blocked),
            _record(outcome=ExperimentOutcome.experiment_failed, status="error"),
            _record(outcome=ExperimentOutcome.experiment_incomplete),
            _record(outcome=ExperimentOutcome.experiment_invalid),
        ]
        m = attack_success_rate(records)
        # Only the blocked one enters the denominator
        assert m.denominator == 1
        assert m.numerator == 0

    def test_empty_population_is_unavailable(self):
        m = attack_success_rate([])
        assert not m.available
        assert m.unavailability_reason == "no_valid_attack_experiments"

    def test_benign_records_excluded(self):
        records = [
            _record(scenario_kind="benign", outcome=ExperimentOutcome.attack_blocked),
        ]
        m = attack_success_rate(records)
        assert not m.available

    def test_numerator_denominator_documented(self):
        records = [
            _record(outcome=ExperimentOutcome.attack_succeeded, attack_success=True),
            _record(outcome=ExperimentOutcome.attack_blocked),
        ]
        m = attack_success_rate(records)
        assert m.numerator is not None
        assert m.denominator is not None
        assert m.percent is not None


# ---------------------------------------------------------------------------
# B. Memory Poisoning Success Rate
# ---------------------------------------------------------------------------

class TestMemoryPoisoningSuccessRate:
    def test_all_blocked(self):
        records = [
            _record(scenario_objective="memory_poisoning", outcome=ExperimentOutcome.attack_blocked)
            for _ in range(4)
        ]
        m = memory_poisoning_success_rate(records)
        assert m.available
        assert m.numerator == 0
        assert m.denominator == 4

    def test_some_poisoned(self):
        records = [
            _record(
                scenario_objective="memory_poisoning",
                outcome=ExperimentOutcome.attack_succeeded,
                poisoned_memory=True,
            ),
            _record(
                scenario_objective="memory_poisoning",
                outcome=ExperimentOutcome.attack_blocked,
                poisoned_memory=False,
            ),
        ]
        m = memory_poisoning_success_rate(records)
        assert m.numerator == 1
        assert m.denominator == 2

    def test_uses_poisoned_memory_field_not_attack_success(self):
        # Records can have attack_success=True but poisoned_memory=False — count only the latter
        records = [
            _record(
                scenario_objective="memory_poisoning",
                outcome=ExperimentOutcome.attack_succeeded,
                attack_success=True,
                poisoned_memory=False,  # explicit evidence field says no
            ),
        ]
        m = memory_poisoning_success_rate(records)
        assert m.numerator == 0

    def test_wrong_objective_excluded(self):
        records = [
            _record(
                scenario_objective="unauthorized_disclosure",
                outcome=ExperimentOutcome.attack_succeeded,
            )
        ]
        m = memory_poisoning_success_rate(records)
        assert not m.available

    def test_empty_is_unavailable(self):
        assert not memory_poisoning_success_rate([]).available


# ---------------------------------------------------------------------------
# C. Unauthorized Tool Execution Rate
# ---------------------------------------------------------------------------

class TestUnauthorizedToolExecutionRate:
    def test_no_executions(self):
        records = [
            _record(
                scenario_objective="unauthorized_disclosure",
                outcome=ExperimentOutcome.attack_blocked,
                attacker_delivery=False,
            )
            for _ in range(3)
        ]
        m = unauthorized_tool_execution_rate(records)
        assert m.available
        assert m.numerator == 0
        assert m.denominator == 3

    def test_gateway_denial_not_counted(self):
        # attacker_delivery=False means gateway denied it — not an execution
        records = [
            _record(
                scenario_objective="unauthorized_disclosure",
                outcome=ExperimentOutcome.attack_blocked,
                attacker_delivery=False,
            )
        ]
        m = unauthorized_tool_execution_rate(records)
        assert m.numerator == 0

    def test_actual_execution_counted(self):
        records = [
            _record(
                scenario_objective="unauthorized_disclosure",
                outcome=ExperimentOutcome.attack_succeeded,
                attacker_delivery=True,
            ),
            _record(
                scenario_objective="workflow_hijack",
                outcome=ExperimentOutcome.attack_blocked,
                attacker_delivery=False,
            ),
        ]
        m = unauthorized_tool_execution_rate(records)
        assert m.numerator == 1
        assert m.denominator == 2

    def test_workflow_hijack_included(self):
        records = [
            _record(
                scenario_objective="workflow_hijack",
                outcome=ExperimentOutcome.attack_succeeded,
                attacker_delivery=True,
            )
        ]
        m = unauthorized_tool_execution_rate(records)
        assert m.available
        assert m.numerator == 1

    def test_memory_poisoning_excluded(self):
        records = [
            _record(
                scenario_objective="memory_poisoning",
                outcome=ExperimentOutcome.attack_blocked,
            )
        ]
        m = unauthorized_tool_execution_rate(records)
        assert not m.available


# ---------------------------------------------------------------------------
# D. Detection Rate
# ---------------------------------------------------------------------------

class TestDetectionRate:
    def test_full_detection(self):
        records = [
            _record(outcome=ExperimentOutcome.attack_blocked) for _ in range(5)
        ]
        m = detection_rate(records)
        assert m.available
        assert m.numerator == 5
        assert m.denominator == 5
        assert m.percent == 100.0

    def test_zero_detection(self):
        records = [
            _record(outcome=ExperimentOutcome.attack_succeeded, attack_success=True)
            for _ in range(3)
        ]
        m = detection_rate(records)
        assert m.numerator == 0
        assert m.denominator == 3

    def test_partially_succeeded_does_not_count_as_detected(self):
        records = [
            _record(outcome=ExperimentOutcome.partially_succeeded),
            _record(outcome=ExperimentOutcome.attack_blocked),
        ]
        m = detection_rate(records)
        assert m.numerator == 1
        assert m.denominator == 2

    def test_failed_experiments_not_counted_as_detected(self):
        records = [
            _record(outcome=ExperimentOutcome.experiment_failed, status="error"),
        ]
        m = detection_rate(records)
        assert not m.available

    def test_empty_population_unavailable(self):
        assert not detection_rate([]).available


# ---------------------------------------------------------------------------
# E. False Positive Rate
# ---------------------------------------------------------------------------

class TestFalsePositiveRate:
    def test_no_benign_records_unavailable(self):
        records = [_record(scenario_kind="attack", outcome=ExperimentOutcome.attack_blocked)]
        m = false_positive_rate(records)
        assert not m.available
        assert m.unavailability_reason == "no_valid_benign_control_experiments"

    def test_all_benign_pass(self):
        records = [
            _record(
                scenario_kind="benign",
                scenario_objective="utility",
                outcome=ExperimentOutcome.attack_blocked,
                legitimate_task_success=True,
            )
            for _ in range(5)
        ]
        m = false_positive_rate(records)
        assert m.available
        assert m.numerator == 0
        assert m.denominator == 5
        assert m.percent == 0.0

    def test_one_false_positive(self):
        records = [
            _record(
                scenario_kind="benign",
                scenario_objective="utility",
                outcome=ExperimentOutcome.attack_succeeded,  # blocked benign → FP
                legitimate_task_success=False,
            ),
            _record(
                scenario_kind="benign",
                scenario_objective="utility",
                outcome=ExperimentOutcome.attack_blocked,
                legitimate_task_success=True,
            ),
        ]
        m = false_positive_rate(records)
        assert m.numerator == 1
        assert m.denominator == 2
        assert m.percent == 50.0

    def test_attack_records_excluded(self):
        # Mix of attack (blocked) + benign (passed)
        records = [
            _record(scenario_kind="attack", outcome=ExperimentOutcome.attack_blocked),
            _record(
                scenario_kind="benign",
                scenario_objective="utility",
                outcome=ExperimentOutcome.attack_blocked,
            ),
        ]
        m = false_positive_rate(records)
        assert m.denominator == 1  # only the benign one


# ---------------------------------------------------------------------------
# F. Latency Overhead
# ---------------------------------------------------------------------------

class TestLatencyOverhead:
    def _make_records(
        self,
        configuration: str,
        latencies: list[float],
        scenario_ids: list[str] | None = None,
    ) -> list[ExperimentRecord]:
        if scenario_ids is None:
            scenario_ids = [f"case_{i:02d}" for i in range(len(latencies))]
        return [
            _record(
                configuration=configuration,
                execution_duration_ms=lat,
                scenario_id=sid,
                case_id=sid,
            )
            for lat, sid in zip(latencies, scenario_ids, strict=False)
        ]

    def test_insufficient_pairs_marks_unavailable(self):
        baseline = self._make_records("baseline", [100.0, 200.0], ["s0", "s1"])
        full = self._make_records("full", [150.0, 250.0], ["s0", "s1"])
        m = latency_overhead(baseline, full)
        # Only 2 pairs — below _MIN_LATENCY_PAIRS=5
        assert not m.available
        assert "insufficient" in m.unavailability_reason

    def test_sufficient_pairs_computes_overhead(self):
        sids = [f"s{i}" for i in range(6)]
        # baseline ~100ms each, full ~200ms each → overhead ≈ 100%
        baseline = self._make_records("baseline", [100.0] * 6, sids)
        full = self._make_records("full", [200.0] * 6, sids)
        m = latency_overhead(baseline, full)
        assert m.available
        assert abs(m.overhead_percent - 100.0) < 0.1
        assert m.baseline_median_ms == 100.0
        assert m.full_defense_median_ms == 200.0
        assert m.baseline_sample_count == 6
        assert m.full_defense_sample_count == 6

    def test_no_overhead_when_same_latency(self):
        sids = [f"s{i}" for i in range(6)]
        base = self._make_records("baseline", [100.0] * 6, sids)
        full = self._make_records("full", [100.0] * 6, sids)
        m = latency_overhead(base, full)
        assert m.available
        assert m.overhead_percent == 0.0

    def test_missing_baseline_is_unavailable(self):
        sids = [f"s{i}" for i in range(6)]
        full = self._make_records("full", [100.0] * 6, sids)
        m = latency_overhead([], full)
        assert not m.available
        assert "baseline" in m.unavailability_reason

    def test_missing_full_defense_is_unavailable(self):
        sids = [f"s{i}" for i in range(6)]
        baseline = self._make_records("baseline", [100.0] * 6, sids)
        m = latency_overhead(baseline, [])
        assert not m.available

    def test_unmatched_scenarios_not_compared(self):
        # baseline has scenarios A-F, full has X-Z (no overlap)
        base = self._make_records("baseline", [100.0] * 6, [f"a{i}" for i in range(6)])
        full = self._make_records("full", [200.0] * 6, [f"z{i}" for i in range(6)])
        m = latency_overhead(base, full)
        assert not m.available

    def test_error_records_excluded_from_latency(self):
        # Error records should not contribute latencies
        sids = [f"s{i}" for i in range(6)]
        base = [
            _record(
                configuration="baseline",
                execution_duration_ms=100.0,
                scenario_id=sid,
                case_id=sid,
                status="completed",
            )
            for sid in sids
        ]
        # full has 3 matched completed + 3 matched errored
        full_ok = [
            _record(
                configuration="full",
                execution_duration_ms=200.0,
                scenario_id=sids[i],
                case_id=sids[i],
            )
            for i in range(3)
        ]
        full_err = [
            _record(
                configuration="full",
                execution_duration_ms=9999.0,
                scenario_id=sids[i],
                case_id=sids[i],
                status="error",
            )
            for i in range(3, 6)
        ]
        m = latency_overhead(base, full_ok + full_err)
        # Only 3 matched pairs (completed on both sides) — below threshold
        assert not m.available


# ---------------------------------------------------------------------------
# Outcome counter
# ---------------------------------------------------------------------------

class TestCountOutcomes:
    def test_all_outcomes_present_in_result(self):
        from cybotech.services.evaluation.contracts import ExperimentOutcome
        counts = count_outcomes([])
        assert set(counts) == {o.value for o in ExperimentOutcome}
        assert all(v == 0 for v in counts.values())

    def test_counts_match_records(self):
        records = [
            _record(outcome=ExperimentOutcome.attack_blocked),
            _record(outcome=ExperimentOutcome.attack_blocked),
            _record(outcome=ExperimentOutcome.attack_succeeded, attack_success=True),
        ]
        counts = count_outcomes(records)
        assert counts["attack_blocked"] == 2
        assert counts["attack_succeeded"] == 1
        assert counts["partially_succeeded"] == 0


# ---------------------------------------------------------------------------
# Wilson CI sanity
# ---------------------------------------------------------------------------

class TestWilsonCI:
    def test_ci_bounds_within_0_1(self):
        m = attack_success_rate(
            [_record(outcome=ExperimentOutcome.attack_succeeded, attack_success=True)] * 3
            + [_record(outcome=ExperimentOutcome.attack_blocked)] * 7
        )
        assert m.confidence_interval_95 is not None
        lo, hi = m.confidence_interval_95
        assert 0.0 <= lo <= hi <= 1.0

    def test_ci_none_for_single_sample(self):
        m = attack_success_rate([_record(outcome=ExperimentOutcome.attack_blocked)])
        # Wilson CI requires n >= 2
        assert m.confidence_interval_95 is None

    def test_proportion_matches_ratio(self):
        records = (
            [_record(outcome=ExperimentOutcome.attack_succeeded, attack_success=True)] * 3
            + [_record(outcome=ExperimentOutcome.attack_blocked)] * 7
        )
        m = attack_success_rate(records)
        assert abs(m.proportion - 0.3) < 1e-6
        assert abs(m.percent - 30.0) < 1e-4
