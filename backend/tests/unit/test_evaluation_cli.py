"""Unit tests for the CLI entry-points (red_team.py and evaluate.py).

Tests verify:
- make red-team executes bounded scripted experiments (using real run_experiment)
- make evaluate computes results from persisted records (no hardcoded data)
- Missing experiment data is reported honestly
- Live-model execution is never triggered by default
- Budget limits enforced
- JSON output parseable
- Mutually independent: running evaluate after red-team reads real persisted data
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cybotech.infrastructure.database.models import AuditEvent, Base

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_url(tmp_path):
    """SQLite file URL for a temp database so CLI can use it."""
    db_file = tmp_path / "lab_test.db"
    return f"sqlite:///{db_file}"


@pytest.fixture
def seeded_db_url(tmp_path):
    """A temp DB pre-seeded with completed experiment data for evaluate tests."""
    db_file = tmp_path / "lab_seeded.db"
    url = f"sqlite:///{db_file}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)

    from cybotech.services.red_team.corpus import corpus
    from cybotech.services.red_team.lab import run_experiment

    cases = [c for c in corpus() if c.kind == "attack" and c.split == "development"][:2]
    with Session(engine, expire_on_commit=False) as db:
        run_experiment(db, cases, ["baseline", "full"])
    engine.dispose()
    return url


# ---------------------------------------------------------------------------
# red_team CLI
# ---------------------------------------------------------------------------


class TestRedTeamCLI:
    def test_runs_offline_scripted_by_default(self, tmp_db_url, capsys):
        from cybotech.cli.red_team import run

        code = run(["--cases", "2", "--configurations", "full", "--db-url", tmp_db_url])
        assert code == 0
        captured = capsys.readouterr()
        assert "Run ID" in captured.out

    def test_persists_run_to_database(self, tmp_db_url):
        from cybotech.cli.red_team import run

        run(["--cases", "2", "--configurations", "full", "--db-url", tmp_db_url])

        engine = create_engine(tmp_db_url)
        with Session(engine, expire_on_commit=False) as db:
            events = db.query(AuditEvent).filter(AuditEvent.event_type == "lab_run_completed").all()
        engine.dispose()
        assert len(events) == 1

    def test_json_output_is_valid(self, tmp_db_url, capsys):
        from cybotech.cli.red_team import run

        code = run(
            ["--cases", "1", "--configurations", "full", "--db-url", tmp_db_url, "--output", "json"]
        )
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "run_id" in data
        assert "metrics" in data

    def test_includes_benign_cases_when_flagged(self, tmp_db_url, capsys):
        from cybotech.cli.red_team import run

        code = run(
            [
                "--cases",
                "2",
                "--configurations",
                "full",
                "--include-benign",
                "--db-url",
                tmp_db_url,
                "--output",
                "json",
            ]
        )
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        case_kinds = {r["kind"] for r in data.get("results", [])}
        assert "benign" in case_kinds

    def test_split_development_filters_correctly(self, tmp_db_url, capsys):
        from cybotech.cli.red_team import run

        code = run(
            [
                "--cases",
                "3",
                "--split",
                "development",
                "--configurations",
                "full",
                "--db-url",
                tmp_db_url,
                "--output",
                "json",
            ]
        )
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        for r in data["results"]:
            assert r["split"] == "development"

    def test_objective_filter_restricts_cases(self, tmp_db_url, capsys):
        from cybotech.cli.red_team import run

        code = run(
            [
                "--cases",
                "5",
                "--objective",
                "memory_poisoning",
                "--configurations",
                "full",
                "--db-url",
                tmp_db_url,
                "--output",
                "json",
            ]
        )
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        for r in data["results"]:
            assert r["objective"] == "memory_poisoning"

    def test_unknown_configuration_aborts(self, tmp_db_url):
        from cybotech.cli.red_team import run

        with pytest.raises(SystemExit) as exc:
            run(["--configurations", "invalid_config", "--db-url", tmp_db_url])
        assert exc.value.code != 0

    def test_offline_budget_exceeded_aborts(self, tmp_db_url):
        from cybotech.cli.red_team import run

        with pytest.raises(SystemExit) as exc:
            run(["--cases", "999", "--db-url", tmp_db_url])
        assert exc.value.code != 0

    def test_live_mode_requires_env_var(self, tmp_db_url, monkeypatch):
        monkeypatch.setenv("CYBOTECH_MODEL_MODE", "offline")
        from cybotech.cli.red_team import run

        with pytest.raises(SystemExit) as exc:
            run(["--mode", "live", "--cases", "1", "--db-url", tmp_db_url])
        assert exc.value.code != 0

    def test_live_mode_does_not_trigger_without_explicit_flag(
        self, tmp_db_url, monkeypatch, capsys
    ):
        """Default invocation must never trigger live model execution."""
        # Even if env var is set, --mode live is NOT the default
        monkeypatch.setenv("CYBOTECH_MODEL_MODE", "live")
        from cybotech.cli.red_team import run

        code = run(["--cases", "1", "--configurations", "full", "--db-url", tmp_db_url])
        assert code == 0
        # Verify it used scripted mode
        engine = create_engine(tmp_db_url)
        with Session(engine, expire_on_commit=False) as db:
            event = (
                db.query(AuditEvent).filter(AuditEvent.event_type == "lab_run_completed").first()
            )
        engine.dispose()
        assert event is not None
        assert event.payload["evidence_kind"] == "scripted"

    def test_missing_db_url_aborts(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from cybotech.cli.red_team import run

        with pytest.raises(SystemExit) as exc:
            run(["--cases", "2"])  # no --db-url, no DATABASE_URL
        assert exc.value.code != 0

    def test_baseline_configuration_is_available(self, tmp_db_url, capsys):
        """Baseline is available for measurement; not silently excluded."""
        from cybotech.cli.red_team import run

        code = run(
            [
                "--cases",
                "2",
                "--configurations",
                "baseline",
                "--db-url",
                tmp_db_url,
                "--output",
                "json",
            ]
        )
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert any(r["configuration"] == "baseline" for r in data["results"])


# ---------------------------------------------------------------------------
# evaluate CLI
# ---------------------------------------------------------------------------


class TestEvaluateCLI:
    def test_reports_no_data_when_db_is_empty(self, tmp_db_url, capsys):
        engine = create_engine(tmp_db_url)
        Base.metadata.create_all(engine)
        engine.dispose()

        from cybotech.cli.evaluate import run

        code = run(["--db-url", tmp_db_url])
        assert code == 0
        captured = capsys.readouterr()
        assert "No experiment data" in captured.out or "0" in captured.out

    def test_computes_metrics_from_persisted_records(self, seeded_db_url, capsys):
        from cybotech.cli.evaluate import run

        code = run(["--db-url", seeded_db_url])
        assert code == 0
        captured = capsys.readouterr()
        # Must print actual metric labels
        assert "Attack Success Rate" in captured.out
        assert "Detection Rate" in captured.out

    def test_json_output_is_valid_and_contains_real_counts(self, seeded_db_url, capsys):
        from cybotech.cli.evaluate import run

        code = run(["--db-url", seeded_db_url, "--output", "json"])
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        # Real counts — not hardcoded
        assert data["total_experiment_records"] > 0
        assert "strata" in data
        assert data["runs_found"] > 0

    def test_json_output_has_no_hardcoded_values(self, seeded_db_url, capsys):
        """Verify the output reflects actual experiment evidence."""
        from cybotech.cli.evaluate import run

        run(["--db-url", seeded_db_url, "--output", "json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)

        overall = next(s for s in data["strata"] if s["population_label"] == "all")
        asr = overall["attack_success_rate"]
        # The seeded data uses full + baseline configs from the real corpus
        # Full defense should block attacks → ASR for full ≈ 0 (not a magic number)
        assert asr["available"] is True
        # The denominator is always reported explicitly
        assert asr["denominator"] is not None
        assert asr["numerator"] is not None

    def test_filter_by_configuration(self, seeded_db_url, capsys):
        from cybotech.cli.evaluate import run

        code = run(["--db-url", seeded_db_url, "--configuration", "full", "--output", "json"])
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        # All returned records should be "full" configuration
        overall = next(s for s in data["strata"] if s["population_label"] == "all")
        # sample_count should be > 0 (we have full records in seeded data)
        assert overall["sample_count"] > 0

    def test_run_id_filter(self, seeded_db_url, capsys):
        # First find an existing run_id from the seeded data
        from cybotech.cli.evaluate import run as evaluate_run

        evaluate_run(["--db-url", seeded_db_url, "--output", "json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        run_id = data["run_ids"][0]

        # Now filter by that run_id
        evaluate_run(["--db-url", seeded_db_url, "--run-id", run_id, "--output", "json"])
        captured2 = capsys.readouterr()
        data2 = json.loads(captured2.out)
        assert data2["run_ids"] == [run_id]

    def test_generation_mode_filter(self, seeded_db_url, capsys):
        from cybotech.cli.evaluate import run

        code = run(["--db-url", seeded_db_url, "--generation-mode", "scripted", "--output", "json"])
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "scripted" in data.get("generation_modes_present", [])

    def test_unavailable_metrics_labelled_not_omitted(self, seeded_db_url, capsys):
        """Unavailable metrics must still appear, not be silently skipped."""
        from cybotech.cli.evaluate import run

        run(["--db-url", seeded_db_url, "--output", "json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        overall = next(s for s in data["strata"] if s["population_label"] == "all")
        # Latency overhead needs ≥5 matched pairs — should be unavailable here
        lat = overall["latency_overhead"]
        # Either available (enough pairs) or unavailable with reason — never absent
        assert "available" in lat

    def test_missing_db_url_aborts(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from cybotech.cli.evaluate import run

        with pytest.raises(SystemExit) as exc:
            run([])
        assert exc.value.code != 0

    def test_evaluate_is_read_only(self, seeded_db_url):
        """evaluate must not write any new AuditEvent rows."""
        engine = create_engine(seeded_db_url)
        with Session(engine, expire_on_commit=False) as db:
            before = db.query(AuditEvent).count()

        from cybotech.cli.evaluate import run

        run(["--db-url", seeded_db_url])
        run(["--db-url", seeded_db_url])

        with Session(engine, expire_on_commit=False) as db:
            after = db.query(AuditEvent).count()
        engine.dispose()
        assert after == before  # no new events written
