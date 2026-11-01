"""Unit tests for the EvaluationService — DB integration layer.

Tests run against an in-memory SQLite database (same pattern as security tests).
Covers: record loading, parse errors, run listing, evaluate() with filters,
compare_baseline_vs_full(), and the non-mutation guarantee.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from cybotech.infrastructure.database.models import AuditEvent, Base
from cybotech.services.evaluation.contracts import ExperimentOutcome
from cybotech.services.evaluation.service import EvaluationService

# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _add_case_event(
    db: Session,
    *,
    run_id: str,
    case_id: str,
    configuration: str = "full",
    generation_mode: str = "scripted",
    attack_success: bool = False,
    poisoned_memory: bool = False,
    attacker_delivery: bool = False,
    kind: str = "attack",
    objective: str = "memory_poisoning",
    status: str = "completed",
    latency_ms: float = 250.0,
    legitimate_task_success: bool | None = None,
    event_id_suffix: str = "",
) -> None:
    payload = {
        "run_id": run_id,
        "case_id": case_id,
        "kind": kind,
        "objective": objective,
        "split": "development",
        "configuration": configuration,
        "defense_profile": {"name": configuration},
        "model": "scripted-adversary-v2",
        "status": status,
        "errors": [] if status != "error" else ["completed"],
        "attack_success": attack_success,
        "poisoned_memory": poisoned_memory,
        "attacker_delivery": attacker_delivery,
        "legitimate_task_success": legitimate_task_success,
        "memory_states": ["quarantined"],
        "latency_ms": latency_ms,
        "trace": [],
    }
    db.add(
        AuditEvent(
            event_id=f"evt_case_{run_id}_{case_id}{event_id_suffix}",
            event_type="lab_case_completed",
            actor="lab",
            correlation_id=run_id,
            outcome=status,
            payload=payload,
        )
    )


def _add_run_event(
    db: Session,
    *,
    run_id: str,
    corpus_version: str = "synthetic-v1",
    corpus_sha256: str = "abc" * 20,
    evidence_kind: str = "scripted",
    embedding: str = "local-token-hash-384-v1",
    reviewer: str = "deterministic",
) -> None:
    db.add(
        AuditEvent(
            event_id=f"evt_run_{run_id}",
            event_type="lab_run_completed",
            actor="lab",
            correlation_id=run_id,
            outcome="completed",
            payload={
                "run_id": run_id,
                "corpus_version": corpus_version,
                "corpus_sha256": corpus_sha256,
                "evidence_kind": evidence_kind,
                "storage": "isolated_sqlite",
                "embedding": embedding,
                "reviewer": reviewer,
                "metrics": [],
                "results": [],
            },
        )
    )
    db.commit()


def _seed_run(
    db: Session,
    run_id: str,
    n_attack: int = 3,
    n_benign: int = 0,
    configuration: str = "full",
    attack_success: bool = False,
) -> None:
    for i in range(n_attack):
        _add_case_event(
            db,
            run_id=run_id,
            case_id=f"mp_{i:02d}",
            configuration=configuration,
            attack_success=attack_success,
        )
    for i in range(n_benign):
        _add_case_event(
            db,
            run_id=run_id,
            case_id=f"benign_{i:02d}",
            kind="benign",
            objective="utility",
            configuration=configuration,
            legitimate_task_success=True,
        )
    _add_run_event(db, run_id=run_id)


# ---------------------------------------------------------------------------
# load_all_records
# ---------------------------------------------------------------------------


class TestLoadAllRecords:
    def test_empty_db_returns_no_records(self, db: Session):
        svc = EvaluationService(db)
        records, errors = svc.load_all_records()
        assert records == []
        assert errors == []

    def test_seeded_records_are_loaded(self, db: Session):
        _seed_run(db, "run_a", n_attack=3)
        svc = EvaluationService(db)
        records, errors = svc.load_all_records()
        assert len(records) == 3
        assert errors == []

    def test_case_without_run_event_produces_parse_error(self, db: Session):
        # Case event exists but no matching run_completed event
        _add_case_event(db, run_id="run_orphan", case_id="c0")
        db.commit()
        svc = EvaluationService(db)
        records, errors = svc.load_all_records()
        assert records == []
        assert len(errors) == 1
        assert errors[0]["reason"] == "run_metadata_not_found"

    def test_malformed_run_event_does_not_crash(self, db: Session):
        db.add(
            AuditEvent(
                event_id="evt_bad_run",
                event_type="lab_run_completed",
                actor="lab",
                correlation_id="run_bad",
                outcome="completed",
                payload={"incomplete": True},  # missing required fields
            )
        )
        db.commit()
        svc = EvaluationService(db)
        records, _errors = svc.load_all_records()
        assert records == []

    def test_malformed_case_event_produces_parse_error(self, db: Session):
        _add_run_event(db, run_id="run_x")
        # Add a case with missing required fields
        db.add(
            AuditEvent(
                event_id="evt_bad_case",
                event_type="lab_case_completed",
                actor="lab",
                correlation_id="run_x",
                outcome="completed",
                payload={"run_id": "run_x", "case_id": "bad"},  # many fields missing
            )
        )
        db.commit()
        svc = EvaluationService(db)
        _records, errors = svc.load_all_records()
        assert len(errors) >= 1

    def test_outcomes_derived_from_evidence(self, db: Session):
        _add_case_event(db, run_id="r1", case_id="blocked", attack_success=False)
        _add_case_event(db, run_id="r1", case_id="succeeded", attack_success=True)
        _add_run_event(db, run_id="r1")
        svc = EvaluationService(db)
        records, _ = svc.load_all_records()
        by_id = {r.case_id: r for r in records}
        assert by_id["blocked"].outcome == ExperimentOutcome.attack_blocked
        assert by_id["succeeded"].outcome == ExperimentOutcome.attack_succeeded

    def test_failed_experiments_have_failed_outcome(self, db: Session):
        _add_case_event(db, run_id="r1", case_id="err", status="error")
        _add_run_event(db, run_id="r1")
        svc = EvaluationService(db)
        records, _ = svc.load_all_records()
        assert records[0].outcome == ExperimentOutcome.experiment_failed

    def test_multiple_runs_loaded_together(self, db: Session):
        _seed_run(db, "run_a", n_attack=3)
        _seed_run(db, "run_b", n_attack=2)
        svc = EvaluationService(db)
        records, _errors2 = svc.load_all_records()
        assert len(records) == 5
        assert {r.run_id for r in records} == {"run_a", "run_b"}

    def test_load_records_for_run_filters_by_run_id(self, db: Session):
        _seed_run(db, "run_a", n_attack=3)
        _seed_run(db, "run_b", n_attack=2)
        svc = EvaluationService(db)
        records, _ = svc.load_records_for_run("run_a")
        assert len(records) == 3
        assert all(r.run_id == "run_a" for r in records)


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_all_blocked_gives_zero_asr(self, db: Session):
        _seed_run(db, "run_a", n_attack=4, attack_success=False)
        svc = EvaluationService(db)
        summary, errors = svc.evaluate()
        assert errors == []
        overall = next(s for s in summary.strata if s.population_label == "all")
        assert overall.attack_success_rate.numerator == 0
        assert overall.attack_success_rate.denominator == 4

    def test_filter_by_run_id(self, db: Session):
        _seed_run(db, "run_a", n_attack=3)
        _seed_run(db, "run_b", n_attack=2)
        svc = EvaluationService(db)
        summary, _ = svc.evaluate(run_id="run_a")
        assert summary.total_experiment_records == 3

    def test_filter_by_configuration(self, db: Session):
        _seed_run(db, "run_a", n_attack=3, configuration="full")
        _seed_run(db, "run_b", n_attack=2, configuration="baseline")
        svc = EvaluationService(db)
        summary, _ = svc.evaluate(configuration="baseline")
        assert summary.total_experiment_records == 2

    def test_failed_records_in_counts(self, db: Session):
        _add_case_event(db, run_id="r1", case_id="err", status="error")
        _add_case_event(db, run_id="r1", case_id="ok", status="completed")
        _add_run_event(db, run_id="r1")
        svc = EvaluationService(db)
        summary, _ = svc.evaluate()
        assert summary.failed_records == 1
        assert summary.total_experiment_records == 2

    def test_evaluate_is_read_only(self, db: Session):
        _seed_run(db, "run_a", n_attack=2)
        initial_event_count = db.query(AuditEvent).count()
        svc = EvaluationService(db)
        svc.evaluate()
        svc.evaluate()
        svc.evaluate()
        # No new events written by evaluation
        assert db.query(AuditEvent).count() == initial_event_count

    def test_false_positive_rate_available_with_benign_records(self, db: Session):
        _seed_run(db, "run_a", n_benign=5)
        svc = EvaluationService(db)
        summary, _ = svc.evaluate()
        overall = next(s for s in summary.strata if s.population_label == "all")
        assert overall.false_positive_rate.available

    def test_false_positive_rate_unavailable_without_benign_records(self, db: Session):
        _seed_run(db, "run_a", n_attack=3)
        svc = EvaluationService(db)
        summary, _ = svc.evaluate()
        overall = next(s for s in summary.strata if s.population_label == "all")
        assert not overall.false_positive_rate.available
        reason = overall.false_positive_rate.unavailability_reason
        assert reason == "no_valid_benign_control_experiments"


# ---------------------------------------------------------------------------
# compare_baseline_vs_full()
# ---------------------------------------------------------------------------


class TestCompareBaselineVsFull:
    def test_returns_incomparable_when_only_one_configuration(self, db: Session):
        _seed_run(db, "run_full", configuration="full")
        svc = EvaluationService(db)
        check, summary = svc.compare_baseline_vs_full()
        assert not check.comparable
        assert summary is None

    def test_comparable_when_matched_populations(self, db: Session):
        # Same model/corpus for baseline and full
        _seed_run(db, "run_baseline", configuration="baseline")
        _seed_run(db, "run_full", configuration="full")
        svc = EvaluationService(db)
        check, summary = svc.compare_baseline_vs_full()
        assert check.comparable
        assert summary is not None

    def test_incomparable_populations_documented(self, db: Session):
        # Baseline and full have different corpus hashes
        _add_case_event(db, run_id="r_base", case_id="c0", configuration="baseline")
        _add_run_event(db, run_id="r_base", corpus_sha256="aaaa" * 16)

        _add_case_event(db, run_id="r_full", case_id="c0", configuration="full")
        _add_run_event(db, run_id="r_full", corpus_sha256="bbbb" * 16)

        svc = EvaluationService(db)
        check, summary = svc.compare_baseline_vs_full()
        assert not check.comparable
        assert any("corpus_sha256" in m for m in check.mismatches)
        assert summary is None

    def test_filter_by_generation_mode(self, db: Session):
        _add_case_event(
            db,
            run_id="r_base",
            case_id="c0",
            configuration="baseline",
        )
        _add_run_event(db, run_id="r_base", evidence_kind="scripted")

        _add_case_event(
            db,
            run_id="r_full",
            case_id="c0",
            configuration="full",
        )
        _add_run_event(db, run_id="r_full", evidence_kind="scripted")

        svc = EvaluationService(db)
        check, _ = svc.compare_baseline_vs_full(generation_mode="scripted")
        # Both scripted — should be comparable
        assert check.comparable


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------


class TestListRuns:
    def test_lists_seeded_runs(self, db: Session):
        _seed_run(db, "run_a")
        _seed_run(db, "run_b")
        svc = EvaluationService(db)
        items, total = svc.list_runs()
        assert total == 2
        run_ids = {i["run_id"] for i in items}
        assert run_ids == {"run_a", "run_b"}

    def test_pagination(self, db: Session):
        for i in range(5):
            _seed_run(db, f"run_{i:02d}")
        svc = EvaluationService(db)
        page1, total = svc.list_runs(limit=3, offset=0)
        assert total == 5
        assert len(page1) == 3

    def test_empty_db(self, db: Session):
        svc = EvaluationService(db)
        items, total = svc.list_runs()
        assert items == []
        assert total == 0


# ---------------------------------------------------------------------------
# get_experiment_by_id
# ---------------------------------------------------------------------------


class TestGetExperimentById:
    def test_finds_record(self, db: Session):
        _seed_run(db, "run_a", n_attack=1)
        svc = EvaluationService(db)
        record = svc.get_experiment_by_id("mp_00", run_id="run_a")
        assert record is not None
        assert record.case_id == "mp_00"

    def test_returns_none_when_not_found(self, db: Session):
        svc = EvaluationService(db)
        assert svc.get_experiment_by_id("nonexistent") is None

    def test_run_id_filter_applied(self, db: Session):
        _seed_run(db, "run_a", n_attack=1)
        _seed_run(db, "run_b", n_attack=1)
        svc = EvaluationService(db)
        # Same case_id prefix in both runs
        r_a = svc.get_experiment_by_id("mp_00", run_id="run_a")
        r_b = svc.get_experiment_by_id("mp_00", run_id="run_b")
        assert r_a is not None
        assert r_b is not None
        assert r_a.run_id == "run_a"
        assert r_b.run_id == "run_b"
