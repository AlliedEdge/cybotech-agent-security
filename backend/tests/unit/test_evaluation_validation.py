"""Unit tests for experiment record validation and outcome classification.

Covers: failed/incomplete/invalid experiments, missing evidence,
duplicate evidence handling, conflicting evidence, missing scenario assertions.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cybotech.services.evaluation.contracts import ExperimentOutcome
from cybotech.services.evaluation.validation import (
    ValidationError,
    validate_case_payload,
    validate_run_payload,
)

_TS = datetime(2026, 10, 31, 10, 0, 0, tzinfo=UTC)

_RUN_META = {
    "run_id": "run_test_001",
    "corpus_version": "synthetic-v1",
    "corpus_sha256": "deadbeef" * 8,
    "generation_mode": "scripted",
    "embedding_model": "local-token-hash-384-v1",
    "reviewer": "deterministic",
    "run_timestamp": _TS,
    "audit_event_id": "evt_test_001",
}

_VALID_ATTACK_PAYLOAD = {
    "case_id": "memory_poisoning-00",
    "kind": "attack",
    "objective": "memory_poisoning",
    "split": "development",
    "configuration": "full",
    "defense_profile": {"name": "full"},
    "model": "scripted-adversary-v2",
    "status": "completed",
    "errors": [],
    "attack_success": False,
    "poisoned_memory": False,
    "attacker_delivery": False,
    "legitimate_task_success": None,
    "memory_states": ["quarantined"],
    "latency_ms": 345.0,
}


def _validate(payload: dict, **overrides):
    return validate_case_payload(payload, **{**_RUN_META, **overrides})


# ---------------------------------------------------------------------------
# Valid path
# ---------------------------------------------------------------------------

class TestValidPayload:
    def test_valid_attack_blocked_classifies_correctly(self):
        record, outcome = _validate(_VALID_ATTACK_PAYLOAD)
        assert outcome == ExperimentOutcome.attack_blocked
        assert record.outcome == ExperimentOutcome.attack_blocked
        assert record.case_id == "memory_poisoning-00"

    def test_valid_attack_succeeded(self):
        payload = {**_VALID_ATTACK_PAYLOAD, "attack_success": True, "poisoned_memory": True}
        _, outcome = _validate(payload)
        assert outcome == ExperimentOutcome.attack_succeeded

    def test_valid_partial_success(self):
        # Poisoned memory but no attacker delivery — partial
        payload = {
            **_VALID_ATTACK_PAYLOAD,
            "objective": "unauthorized_disclosure",
            "attack_success": False,
            "poisoned_memory": True,
            "attacker_delivery": False,
        }
        _, outcome = _validate(payload)
        assert outcome == ExperimentOutcome.partially_succeeded

    def test_benign_passed_classifies_as_blocked(self):
        payload = {
            **_VALID_ATTACK_PAYLOAD,
            "case_id": "benign-00",
            "kind": "benign",
            "objective": "utility",
            "attack_success": None,
            "poisoned_memory": None,
            "attacker_delivery": None,
            "legitimate_task_success": True,
        }
        _, outcome = _validate(payload)
        assert outcome == ExperimentOutcome.attack_blocked

    def test_benign_blocked_classifies_as_succeeded(self):
        """A benign task that was incorrectly blocked maps to attack_succeeded
        (false positive territory) so it enters the FPR numerator."""
        payload = {
            **_VALID_ATTACK_PAYLOAD,
            "case_id": "benign-00",
            "kind": "benign",
            "objective": "utility",
            "attack_success": None,
            "poisoned_memory": None,
            "attacker_delivery": None,
            "legitimate_task_success": False,
        }
        _, outcome = _validate(payload)
        assert outcome == ExperimentOutcome.attack_succeeded

    def test_run_timestamp_without_tz_gets_utc(self):
        naive_ts = datetime(2026, 10, 31, 10, 0, 0)
        record, _ = _validate(_VALID_ATTACK_PAYLOAD, run_timestamp=naive_ts)
        assert record.run_timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# Failed experiments
# ---------------------------------------------------------------------------

class TestFailedExperiments:
    def test_error_status_classifies_as_failed(self):
        payload = {**_VALID_ATTACK_PAYLOAD, "status": "error", "errors": ["completed"]}
        record, outcome = _validate(payload)
        assert outcome == ExperimentOutcome.experiment_failed
        assert record.status == "error"

    def test_failed_experiment_carries_errors_list(self):
        payload = {**_VALID_ATTACK_PAYLOAD, "status": "error", "errors": ["completed", "completed"]}
        record, _ = _validate(payload)
        assert record.errors == ["completed", "completed"]


# ---------------------------------------------------------------------------
# Incomplete experiments
# ---------------------------------------------------------------------------

class TestIncompleteExperiments:
    def test_unknown_status_classifies_as_incomplete(self):
        payload = {**_VALID_ATTACK_PAYLOAD, "status": "in_progress"}
        _record, outcome = _validate(payload)
        assert outcome == ExperimentOutcome.experiment_incomplete

    def test_incomplete_record_has_outcome_set(self):
        payload = {**_VALID_ATTACK_PAYLOAD, "status": "in_progress"}
        record, _ = _validate(payload)
        assert record.outcome == ExperimentOutcome.experiment_incomplete


# ---------------------------------------------------------------------------
# Invalid experiments (missing / malformed fields)
# ---------------------------------------------------------------------------

class TestInvalidExperiments:
    @pytest.mark.parametrize("missing_field", [
        "case_id", "kind", "objective", "split", "configuration",
        "defense_profile", "model", "status", "attack_success", "latency_ms",
    ])
    def test_missing_required_field_raises(self, missing_field: str):
        payload = {k: v for k, v in _VALID_ATTACK_PAYLOAD.items() if k != missing_field}
        with pytest.raises(ValidationError) as exc:
            _validate(payload)
        assert missing_field in exc.value.reason

    def test_null_status_raises(self):
        payload = {**_VALID_ATTACK_PAYLOAD, "status": None}
        with pytest.raises(ValidationError, match="status is null"):
            _validate(payload)

    def test_validation_error_carries_payload(self):
        payload = {k: v for k, v in _VALID_ATTACK_PAYLOAD.items() if k != "case_id"}
        with pytest.raises(ValidationError) as exc:
            _validate(payload)
        assert exc.value.payload is not None

    def test_run_payload_missing_required_fields(self):
        for field in ["run_id", "corpus_version", "corpus_sha256", "evidence_kind"]:
            base = {
                "run_id": "r",
                "corpus_version": "v",
                "corpus_sha256": "h",
                "evidence_kind": "scripted",
            }
            del base[field]
            with pytest.raises(ValidationError):
                validate_run_payload(base)

    def test_valid_run_payload_returns_meta(self):
        p = {
            "run_id": "run_xyz",
            "corpus_version": "synthetic-v1",
            "corpus_sha256": "abc",
            "evidence_kind": "scripted",
            "embedding": "local-token-hash-384-v1",
            "reviewer": "deterministic",
        }
        meta = validate_run_payload(p)
        assert meta["run_id"] == "run_xyz"
        assert meta["generation_mode"] == "scripted"
        assert meta["embedding_model"] == "local-token-hash-384-v1"


# ---------------------------------------------------------------------------
# Missing / conflicting evidence
# ---------------------------------------------------------------------------

class TestEvidenceHandling:
    def test_missing_poisoned_memory_does_not_count_as_poisoned(self):
        payload = {**_VALID_ATTACK_PAYLOAD, "poisoned_memory": None}
        record, outcome = _validate(payload)
        assert outcome == ExperimentOutcome.attack_blocked
        assert record.poisoned_memory is None

    def test_missing_attacker_delivery_does_not_count_as_delivered(self):
        payload = {
            **_VALID_ATTACK_PAYLOAD,
            "objective": "unauthorized_disclosure",
            "attacker_delivery": None,
        }
        record, outcome = _validate(payload)
        assert record.attacker_delivery is None
        # No positive evidence — should be blocked
        assert outcome == ExperimentOutcome.attack_blocked

    def test_conflicting_evidence_poisoned_but_attack_success_false(self):
        # attack_success=False but poisoned_memory=True → partial success
        payload = {
            **_VALID_ATTACK_PAYLOAD,
            "attack_success": False,
            "poisoned_memory": True,
        }
        _, outcome = _validate(payload)
        assert outcome == ExperimentOutcome.partially_succeeded

    def test_conflicting_evidence_attack_success_true_but_no_evidence(self):
        # attack_success=True is authoritative
        payload = {
            **_VALID_ATTACK_PAYLOAD,
            "attack_success": True,
            "poisoned_memory": False,
        }
        _, outcome = _validate(payload)
        assert outcome == ExperimentOutcome.attack_succeeded

    def test_record_preserves_all_evidence_fields(self):
        payload = {
            **_VALID_ATTACK_PAYLOAD,
            "attack_success": True,
            "poisoned_memory": True,
            "attacker_delivery": True,
            "memory_states": ["trusted"],
        }
        record, _ = _validate(payload)
        assert record.attack_success is True
        assert record.poisoned_memory is True
        assert record.attacker_delivery is True
        assert record.memory_states == ["trusted"]


# ---------------------------------------------------------------------------
# Payload hash
# ---------------------------------------------------------------------------

class TestPayloadHash:
    def test_same_payload_same_hash(self):
        r1, _ = _validate(_VALID_ATTACK_PAYLOAD)
        r2, _ = _validate(_VALID_ATTACK_PAYLOAD)
        assert r1.payload_hash == r2.payload_hash

    def test_different_configuration_different_hash(self):
        r1, _ = _validate(_VALID_ATTACK_PAYLOAD)
        payload2 = {**_VALID_ATTACK_PAYLOAD, "configuration": "baseline"}
        r2, _ = _validate(payload2)
        assert r1.payload_hash != r2.payload_hash
