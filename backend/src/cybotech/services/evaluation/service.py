"""Evaluation service.

Reads persisted AuditEvent rows produced by lab.run_experiment() and converts
them into validated ExperimentRecords.  All metric calculations are delegated
to metrics.py and aggregation.py.

Persistence contract
--------------------
lab.run_experiment() writes three event types to AuditEvent:
  1. lab_run_started     — {run_id, cases, configurations}
  2. lab_case_completed  — {run_id, case_id, kind, objective, …full case result…}
  3. lab_run_completed   — {run_id, corpus_version, corpus_sha256, evidence_kind,
                            embedding, reviewer, metrics, results}

The evaluation service builds ExperimentRecord objects by pairing each
lab_case_completed payload with the run-level metadata from lab_run_completed.

Evaluation reads are non-mutating: no existing AuditEvent or Memory row is
modified, created, or deleted by the evaluation service.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from cybotech.infrastructure.database.models import AuditEvent
from cybotech.services.evaluation.aggregation import (
    build_evaluation_summary,
    check_comparability,
    filter_records,
    paginate,
)
from cybotech.services.evaluation.contracts import (
    ComparabilityCheck,
    EvaluationSummary,
    ExperimentOutcome,
    ExperimentRecord,
)
from cybotech.services.evaluation.validation import (
    ValidationError,
    validate_case_payload,
    validate_run_payload,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(value: datetime | str | object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
        except ValueError:
            pass
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Public service class
# ---------------------------------------------------------------------------


class EvaluationService:
    """Reads persisted experiment evidence and exposes evaluation results.

    This service never mutates experiment outcomes.  All database access
    is read-only SELECT statements against AuditEvent rows.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Record loading
    # ------------------------------------------------------------------

    def load_all_records(self) -> tuple[list[ExperimentRecord], list[dict[str, Any]]]:
        """Load and validate every persisted experiment case.

        Returns
        -------
        (records, parse_errors)
            records      - Successfully validated ExperimentRecord objects.
            parse_errors - Dicts describing payloads that failed validation.
                           These are reported transparently; never silently dropped.
        """
        # Fetch all completed run events ordered oldest-first so we can index them.
        run_events: dict[str, dict[str, Any]] = {}
        for event in self._db.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "lab_run_completed")
            .order_by(AuditEvent.id)
        ):
            try:
                meta = validate_run_payload(event.payload)
                meta["_occurred_at"] = event.occurred_at
                meta["_audit_event_id"] = event.event_id
                run_events[meta["run_id"]] = meta
            except ValidationError:
                # Malformed run events are noted but don't prevent other runs from loading.
                pass

        # Fetch all case events.
        records: list[ExperimentRecord] = []
        parse_errors: list[dict[str, Any]] = []

        for event in self._db.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "lab_case_completed")
            .order_by(AuditEvent.id)
        ):
            payload = event.payload
            run_id = payload.get("run_id", "")
            run_meta = run_events.get(run_id)

            if run_meta is None:
                # Case belongs to a run whose completion event hasn't been stored yet
                # (run still in progress) or was corrupted.  Skip gracefully.
                parse_errors.append(
                    {
                        "reason": "run_metadata_not_found",
                        "run_id": run_id,
                        "case_id": payload.get("case_id"),
                        "audit_event_id": event.event_id,
                    }
                )
                continue

            occurred_at = _parse_timestamp(run_meta.get("_occurred_at") or event.occurred_at)
            try:
                record, _ = validate_case_payload(
                    payload,
                    run_id=run_meta["run_id"],
                    corpus_version=run_meta["corpus_version"],
                    corpus_sha256=run_meta["corpus_sha256"],
                    generation_mode=run_meta["generation_mode"],
                    embedding_model=run_meta["embedding_model"],
                    reviewer=run_meta["reviewer"],
                    run_timestamp=occurred_at,
                    audit_event_id=event.event_id,
                )
                records.append(record)
            except ValidationError as exc:
                parse_errors.append(
                    {
                        "reason": exc.reason,
                        "case_id": payload.get("case_id"),
                        "run_id": run_id,
                        "audit_event_id": event.event_id,
                    }
                )

        return records, parse_errors

    def load_records_for_run(
        self, run_id: str
    ) -> tuple[list[ExperimentRecord], list[dict[str, Any]]]:
        """Load records for a single run_id."""
        all_records, all_errors = self.load_all_records()
        records = [r for r in all_records if r.run_id == run_id]
        errors = [e for e in all_errors if e.get("run_id") == run_id]
        return records, errors

    # ------------------------------------------------------------------
    # Run listing
    # ------------------------------------------------------------------

    def list_runs(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """List persisted run summaries (metadata only, no case-level details)."""
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.event_type == "lab_run_completed")
            .order_by(desc(AuditEvent.id))
        )
        all_events = self._db.scalars(stmt).all()
        items = []
        for event in all_events:
            p = event.payload
            items.append(
                {
                    "run_id": p.get("run_id"),
                    "corpus_version": p.get("corpus_version"),
                    "corpus_sha256": p.get("corpus_sha256"),
                    "evidence_kind": p.get("evidence_kind"),
                    "embedding_model": p.get("embedding"),
                    "reviewer": p.get("reviewer"),
                    "case_count": len(p.get("results", [])),
                    "occurred_at": (event.occurred_at.isoformat() if event.occurred_at else None),
                    "audit_event_id": event.event_id,
                }
            )
        page, total = paginate(items, limit=limit, offset=offset)
        return page, total

    def get_run_payload(self, run_id: str) -> dict[str, Any] | None:
        """Return the raw lab_run_completed payload for a single run."""
        event = self._db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "lab_run_completed",
                AuditEvent.correlation_id == run_id,
            )
        )
        return event.payload if event else None

    # ------------------------------------------------------------------
    # Evaluation entry points
    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        generation_mode: str | None = None,
        configuration: str | None = None,
        run_id: str | None = None,
        scenario_kind: str | None = None,
        scenario_objective: str | None = None,
        corpus_version: str | None = None,
        model_name: str | None = None,
    ) -> tuple[EvaluationSummary, list[dict[str, Any]]]:
        """Compute a full EvaluationSummary over filtered persisted records.

        Returns
        -------
        (summary, parse_errors)
            summary      - The full EvaluationSummary with all strata.
            parse_errors - Records that could not be parsed.
        """
        all_records, parse_errors = self.load_all_records()
        filtered = filter_records(
            all_records,
            generation_mode=generation_mode,
            configuration=configuration,
            run_id=run_id,
            scenario_kind=scenario_kind,
            scenario_objective=scenario_objective,
            corpus_version=corpus_version,
            model_name=model_name,
        )
        summary = build_evaluation_summary(filtered)
        return summary, parse_errors

    def compare_baseline_vs_full(
        self,
        *,
        generation_mode: str | None = None,
        corpus_version: str | None = None,
    ) -> tuple[ComparabilityCheck, EvaluationSummary | None]:
        """Verify comparability between baseline and full-defense populations,
        then compute metrics for the full-defense stratum if comparable.

        Returns (check, summary_or_none).
        If not comparable, summary is None and the check documents the mismatches.
        """
        all_records, _ = self.load_all_records()

        baseline = filter_records(
            all_records,
            configuration="baseline",
            generation_mode=generation_mode,
            corpus_version=corpus_version,
        )
        full = filter_records(
            all_records,
            configuration="full",
            generation_mode=generation_mode,
            corpus_version=corpus_version,
        )

        check = check_comparability(baseline, full)
        if not check.comparable:
            return check, None

        summary = build_evaluation_summary(baseline + full)
        return check, summary

    def get_experiment_records(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        generation_mode: str | None = None,
        configuration: str | None = None,
        run_id: str | None = None,
        scenario_kind: str | None = None,
        scenario_objective: str | None = None,
        corpus_version: str | None = None,
        model_name: str | None = None,
        outcome: ExperimentOutcome | None = None,
    ) -> tuple[list[ExperimentRecord], int, list[dict[str, Any]]]:
        """Paginated list of ExperimentRecords after filtering.

        Returns (page, total_count, parse_errors).
        """
        all_records, parse_errors = self.load_all_records()
        filtered = filter_records(
            all_records,
            generation_mode=generation_mode,
            configuration=configuration,
            run_id=run_id,
            scenario_kind=scenario_kind,
            scenario_objective=scenario_objective,
            corpus_version=corpus_version,
            model_name=model_name,
        )
        if outcome is not None:
            filtered = [record for record in filtered if record.outcome == outcome]
        page, total = paginate(filtered, limit=limit, offset=offset)
        return page, total, parse_errors

    def get_experiment_by_id(
        self, case_id: str, run_id: str | None = None
    ) -> ExperimentRecord | None:
        """Look up a specific experiment record by case_id (and optionally run_id)."""
        all_records, _ = self.load_all_records()
        for r in all_records:
            if r.case_id == case_id:
                if run_id is None or r.run_id == run_id:
                    return r
        return None
