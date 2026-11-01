"""Evaluation API router.

Exposes evaluation metrics, experiment records, and run-trigger endpoints.

Security constraints (identical to security.py):
  - Requires CYBOTECH_SECURITY_MODE=local_demo.
  - Accepts only loopback clients (127.0.0.1, ::1, localhost).
  - Does not allow normal assistant users to activate baseline security profiles.
  - Read endpoints never mutate experiment outcomes.
  - POST /evaluations/run delegates to the existing run_experiment() harness and
    enforces the same case/configuration budget limits.
  - Live-model execution requires an explicit mode="live" parameter AND the
    CYBOTECH_MODEL_MODE=live environment variable to be set.  It is never
    triggered by default.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from cybotech.api.v1.security import (
    lab_lock,
    require_local_security_access,
    runtime_mode,
)
from cybotech.infrastructure.database.session import get_db
from cybotech.services.evaluation.contracts import (
    ExperimentOutcome,
    ExperimentRecord,
    LatencyMetric,
    ProportionMetric,
)
from cybotech.services.evaluation.service import EvaluationService
from cybotech.services.red_team.corpus import corpus
from cybotech.services.red_team.lab import CONFIGURATIONS, Configuration, run_experiment

DbSession = Annotated[Session, Depends(get_db)]

router = APIRouter(
    prefix="/evaluations",
    dependencies=[Depends(require_local_security_access)],
)

# ---------------------------------------------------------------------------
# Request/response helpers
# ---------------------------------------------------------------------------

_VALID_GENERATION_MODES = {"scripted", "live_model"}
_VALID_CONFIGURATIONS = set(CONFIGURATIONS)
_VALID_OBJECTIVES = {
    "memory_poisoning",
    "unauthorized_disclosure",
    "workflow_hijack",
    "utility",
}
_VALID_KINDS = {"attack", "benign"}
_VALID_OUTCOMES = {o.value for o in ExperimentOutcome}


def _validate_filter(
    generation_mode: str | None,
    configuration: str | None,
    scenario_kind: str | None,
    scenario_objective: str | None,
) -> None:
    if generation_mode is not None and generation_mode not in _VALID_GENERATION_MODES:
        raise HTTPException(
            422,
            f"generation_mode must be one of {sorted(_VALID_GENERATION_MODES)}",
        )
    if configuration is not None and configuration not in _VALID_CONFIGURATIONS:
        raise HTTPException(
            422,
            f"configuration must be one of {sorted(_VALID_CONFIGURATIONS)}",
        )
    if scenario_kind is not None and scenario_kind not in _VALID_KINDS:
        raise HTTPException(422, f"scenario_kind must be one of {sorted(_VALID_KINDS)}")
    if scenario_objective is not None and scenario_objective not in _VALID_OBJECTIVES:
        raise HTTPException(
            422,
            f"scenario_objective must be one of {sorted(_VALID_OBJECTIVES)}",
        )


def _serialize_metric(m: ProportionMetric | LatencyMetric) -> dict[str, Any]:
    """Convert a ProportionMetric or LatencyMetric to a plain dict."""
    return m.model_dump(mode="json")


def _serialize_record(r: ExperimentRecord) -> dict[str, Any]:
    return r.model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /evaluations/summary
# ---------------------------------------------------------------------------


@router.get("/summary")
def evaluation_summary(
    db: DbSession,
    generation_mode: str | None = None,
    configuration: str | None = None,
    run_id: str | None = None,
    scenario_kind: str | None = None,
    scenario_objective: str | None = None,
    corpus_version: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Overall evaluation summary.

    Computes all security metrics over persisted experiment records, stratified
    by generation mode and defense configuration.

    Returns actual denominators and numerators.  Unavailable metrics are clearly
    labelled with an unavailability_reason.  Failed/incomplete/invalid records
    are counted but never silently removed.

    Filters
    -------
    All filter parameters are optional and combinable.  Unfiltered returns all runs.
    """
    _validate_filter(generation_mode, configuration, scenario_kind, scenario_objective)
    svc = EvaluationService(db)
    summary, parse_errors = svc.evaluate(
        generation_mode=generation_mode,
        configuration=configuration,
        run_id=run_id,
        scenario_kind=scenario_kind,
        scenario_objective=scenario_objective,
        corpus_version=corpus_version,
        model_name=model_name,
    )
    result = summary.model_dump(mode="json")
    # Attach parse-error count so callers can see whether any records were
    # unreadable.  Full details available via /evaluations/experiments.
    result["parse_error_count"] = len(parse_errors)
    return result


# ---------------------------------------------------------------------------
# GET /evaluations/metrics
# ---------------------------------------------------------------------------


@router.get("/metrics")
def evaluation_metrics(
    db: DbSession,
    generation_mode: str | None = None,
    configuration: str | None = None,
    run_id: str | None = None,
    scenario_kind: str | None = None,
    scenario_objective: str | None = None,
    corpus_version: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Per-metric breakdown.

    Returns the same data as /summary but shaped as a flat list of metric objects
    across all strata.  Useful for tooling that wants to iterate metrics directly.

    Each item includes:
      - population_label: which stratum this metric belongs to
      - metric_name: the metric identifier
      - available: whether the metric could be computed
      - numerator / denominator / proportion / percent
      - confidence_interval_95 (Wilson score, where applicable)
      - unavailability_reason (when available=false)
    """
    _validate_filter(generation_mode, configuration, scenario_kind, scenario_objective)
    svc = EvaluationService(db)
    summary, parse_errors = svc.evaluate(
        generation_mode=generation_mode,
        configuration=configuration,
        run_id=run_id,
        scenario_kind=scenario_kind,
        scenario_objective=scenario_objective,
        corpus_version=corpus_version,
        model_name=model_name,
    )

    items: list[dict[str, Any]] = []
    proportion_fields = [
        "attack_success_rate",
        "memory_poisoning_success_rate",
        "unauthorized_tool_execution_rate",
        "detection_rate",
        "false_positive_rate",
    ]
    for stratum in summary.strata:
        label = stratum.population_label
        for field in proportion_fields:
            m = getattr(stratum, field)
            entry = m.model_dump(mode="json")
            entry["population_label"] = label
            entry["metric_name"] = field
            items.append(entry)
        lm = stratum.latency_overhead
        lat_entry = lm.model_dump(mode="json")
        lat_entry["population_label"] = label
        lat_entry["metric_name"] = "latency_overhead"
        items.append(lat_entry)

    return {
        "items": items,
        "strata_count": len(summary.strata),
        "total_experiment_records": summary.total_experiment_records,
        "parse_error_count": len(parse_errors),
    }


# ---------------------------------------------------------------------------
# GET /evaluations/experiments
# ---------------------------------------------------------------------------


@router.get("/experiments")
def list_experiments(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    generation_mode: str | None = None,
    configuration: str | None = None,
    run_id: str | None = None,
    scenario_kind: str | None = None,
    scenario_objective: str | None = None,
    corpus_version: str | None = None,
    model_name: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    """Paginated list of individual experiment records.

    Each record carries:
      - Full experiment metadata (run_id, scenario_id, corpus_version, …)
      - Applied defense_profile and configuration
      - Generation mode and model identity
      - Raw evidence (poisoned_memory, attacker_delivery, legitimate_task_success)
      - Derived outcome (authoritative — not model-claimed)
      - Execution timing

    This endpoint never mutates outcome records.
    """
    _validate_filter(generation_mode, configuration, scenario_kind, scenario_objective)
    if outcome is not None and outcome not in _VALID_OUTCOMES:
        raise HTTPException(422, f"outcome must be one of {sorted(_VALID_OUTCOMES)}")

    svc = EvaluationService(db)
    outcome_enum = ExperimentOutcome(outcome) if outcome is not None else None
    records, total, parse_errors = svc.get_experiment_records(
        limit=limit,
        offset=offset,
        generation_mode=generation_mode,
        configuration=configuration,
        run_id=run_id,
        scenario_kind=scenario_kind,
        scenario_objective=scenario_objective,
        corpus_version=corpus_version,
        model_name=model_name,
        outcome=outcome_enum,
    )

    return {
        "items": [_serialize_record(r) for r in records],
        "limit": limit,
        "offset": offset,
        "total": total,
        "parse_error_count": len(parse_errors),
    }


# ---------------------------------------------------------------------------
# GET /evaluations/experiments/{run_id}/{case_id}
# ---------------------------------------------------------------------------


@router.get("/experiments/{run_id}/{case_id}")
def get_experiment(
    run_id: str,
    case_id: str,
    db: DbSession,
) -> dict[str, Any]:
    """Single experiment record identified by run_id + case_id.

    Returns the full normalised ExperimentRecord including all evidence fields
    and the derived outcome.  Read-only; does not mutate any persisted data.
    """
    svc = EvaluationService(db)
    record = svc.get_experiment_by_id(case_id, run_id=run_id)
    if record is None:
        raise HTTPException(404, f"Experiment not found: run_id={run_id!r} case_id={case_id!r}")
    return _serialize_record(record)


# ---------------------------------------------------------------------------
# GET /evaluations/runs
# ---------------------------------------------------------------------------


@router.get("/runs")
def list_evaluation_runs(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """List persisted lab runs available for evaluation.

    Returns run-level metadata only (no per-case detail).
    Use /evaluations/experiments?run_id=<id> for case-level data.
    """
    svc = EvaluationService(db)
    items, total = svc.list_runs(limit=limit, offset=offset)
    return {"items": items, "limit": limit, "offset": offset, "total": total}


# ---------------------------------------------------------------------------
# POST /evaluations/run
# ---------------------------------------------------------------------------


class EvalRunIn(BaseModel):
    """Parameters for triggering a new bounded red-team experiment that
    immediately feeds into the evaluation engine.

    Restrictions
    ------------
    - mode="live" requires CYBOTECH_MODEL_MODE=live.  Never triggered by default.
    - case_ids are validated against the known corpus.
    - Max 60 cases offline, 3 live.
    - configurations must be unique and come from the documented set.
    - The evaluation engine cannot be used to weaken application security:
      baseline experiments are available for measurement purposes only and
      never affect the production assistant's defence configuration.
    """

    model_config = ConfigDict(extra="forbid")

    case_ids: list[str] = Field(min_length=1, max_length=60)
    configurations: list[Configuration] = Field(min_length=1, max_length=4)
    mode: Literal["offline", "live"] = "offline"


@router.post("/run")
def trigger_evaluation_run(body: EvalRunIn, db: DbSession) -> dict[str, Any]:
    """Execute a bounded experiment and immediately return evaluation metrics.

    The experiment is run synchronously in the same request.  For long runs
    (many cases x configurations) this may take a while.

    Live-model execution
    --------------------
    Setting mode="live" requires CYBOTECH_MODEL_MODE=live to be set in the
    environment.  This is never the default.  Live execution spends external
    API credits and is limited to 3 cases per call.

    The baseline configuration is available here for measurement comparisons.
    It is isolated to this lab harness and does not affect the production
    assistant's security configuration.
    """
    registry = {case.id: case for case in corpus()}
    if len(set(body.case_ids)) != len(body.case_ids):
        raise HTTPException(422, "case_ids must be unique")
    unknown = [i for i in body.case_ids if i not in registry]
    if unknown:
        raise HTTPException(422, f"Unknown case IDs: {unknown}")
    if len(set(body.configurations)) != len(body.configurations):
        raise HTTPException(422, "configurations must be unique")

    if body.mode == "live":
        if runtime_mode() != "live":
            raise HTTPException(
                422,
                "Live experiments require CYBOTECH_MODEL_MODE=live. "
                "Not triggered by default to avoid unintended API spending.",
            )
        if len(body.case_ids) > 3:
            raise HTTPException(422, "Live mode allows at most 3 cases per call")

    if not lab_lock.acquire(blocking=False):
        raise HTTPException(409, "An experiment is already running; wait for completion")
    try:
        live_model = None
        if body.mode == "live":
            from cybotech.agents.providers import configured_model
            from cybotech.agents.registry import AgentId
            from cybotech.api.v1.security import (
                runtime_client,
                runtime_memory_reviewer,
                runtime_reviewer,
            )
            from cybotech.infrastructure.llm.providers import OpenAIProvider

            live_model = OpenAIProvider(runtime_client(), configured_model(AgentId.PRIMARY))

        cases = [registry[i] for i in body.case_ids]
        if live_model is not None:
            run_result = run_experiment(
                db,
                cases,
                body.configurations,
                live_model,
                reviewer=runtime_reviewer(),
                memory_reviewer=runtime_memory_reviewer(),
            )
        else:
            run_result = run_experiment(db, cases, body.configurations, live_model)
    finally:
        lab_lock.release()

    # After the run is persisted, pull evaluation metrics for this specific run.
    svc = EvaluationService(db)
    run_id = run_result["run_id"]
    eval_summary, parse_errors = svc.evaluate(run_id=run_id)

    return {
        "run_id": run_id,
        "corpus_version": run_result.get("corpus_version"),
        "corpus_sha256": run_result.get("corpus_sha256"),
        "evidence_kind": run_result.get("evidence_kind"),
        "case_count": len(body.case_ids),
        "configuration_count": len(body.configurations),
        "evaluation": eval_summary.model_dump(mode="json"),
        "parse_error_count": len(parse_errors),
    }


# ---------------------------------------------------------------------------
# GET /evaluations/comparability
# ---------------------------------------------------------------------------


@router.get("/comparability")
def baseline_vs_full_comparability(
    db: DbSession,
    generation_mode: str | None = None,
    corpus_version: str | None = None,
) -> dict[str, Any]:
    """Check whether baseline and full-defense populations are comparable.

    Returns the comparability verdict and, if comparable, the full evaluation
    summary for the matched population.

    If populations differ in model, corpus, or generation mode, the response
    documents the specific mismatches and does NOT label the result as a
    controlled comparison.
    """
    if generation_mode is not None and generation_mode not in _VALID_GENERATION_MODES:
        raise HTTPException(
            422, f"generation_mode must be one of {sorted(_VALID_GENERATION_MODES)}"
        )
    svc = EvaluationService(db)
    check, summary = svc.compare_baseline_vs_full(
        generation_mode=generation_mode,
        corpus_version=corpus_version,
    )
    result: dict[str, Any] = {"comparability": check.model_dump(mode="json")}
    if summary is not None:
        result["evaluation"] = summary.model_dump(mode="json")
    else:
        result["evaluation"] = None
        result["note"] = (
            "Populations are not comparable. See comparability.mismatches for details. "
            "No controlled comparison has been produced."
        )
    return result
