import stat
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from scripts.performance_tools import (
    PerformanceToolError,
    build_ai_capacity_report,
    build_api_report,
    validate_local_fixture_settings,
    write_fixture_file,
)


def test_fixture_settings_reject_non_local_environment() -> None:
    settings = Settings.model_construct(
        environment="staging",
        database_url="postgresql+psycopg://user:pass@localhost/database",
    )

    with pytest.raises(PerformanceToolError, match="local/test"):
        validate_local_fixture_settings(settings)


def test_fixture_settings_reject_remote_database() -> None:
    settings = Settings.model_construct(
        environment="local",
        database_url="postgresql+psycopg://user:pass@db.internal/database",
    )

    with pytest.raises(PerformanceToolError, match="loopback"):
        validate_local_fixture_settings(settings)


def test_fixture_file_is_private_and_cannot_be_overwritten(tmp_path: Path) -> None:
    destination = tmp_path / "fixture.json"

    write_fixture_file(destination, user_id=uuid4(), access_token="secret-token")

    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    with pytest.raises(PerformanceToolError, match="already exists"):
        write_fixture_file(destination, user_id=uuid4(), access_token="replacement")


def test_api_report_extracts_sanitized_gate_metrics() -> None:
    summary = {
        "metrics": {
            "http_reqs": {"count": 1_200, "rate": 19.8},
            "http_req_failed{scenario:api_reads}": {"value": 0.001},
            "http_req_duration{scenario:api_reads}": {
                "med": 8.5,
                "p(90)": 15.2,
                "p(95)": 21.7,
                "p(99)": 44.3,
                "max": 78.9,
            },
            "business_success": {"value": 0.999},
        }
    }

    report = build_api_report(
        summary,
        git_sha="abc1234",
        k6_image="grafana/k6:2.1.0@sha256:example",
        k6_exit_code=0,
        start_rate=5,
        target_rate=20,
        duration="4m30s",
    )

    assert "Conclusion: **PASS**" in report
    assert "P95 latency | 21.70 ms | <500 ms" in report
    assert "HTTP failure rate | 0.100% | <1%" in report
    assert "secret-token" not in report


def test_ai_capacity_report_enforces_stage_and_release_thresholds() -> None:
    summary = {
        "metrics": {
            "iterations": {"values": {"count": 10, "rate": 0.5}},
            "http_reqs": {"values": {"count": 120, "rate": 6}},
            "http_req_failed{scenario:authorized_ai_jobs}": {"values": {"rate": 0.01}},
            "diagnosis_success": {"values": {"rate": 1}},
            "correlation_headers": {"values": {"rate": 1}},
            "diagnosis_total_latency": {
                "values": {
                    "med": 8_000,
                    "p(90)": 15_000,
                    "p(95)": 18_000,
                }
            },
        }
    }

    report, passed = build_ai_capacity_report(
        summary,
        git_sha="a" * 40,
        k6_image="grafana/k6:2.1.0@sha256:example",
        k6_exit_code=0,
        stage=10,
        vus=10,
        api_replicas=2,
        worker_replicas=2,
    )
    wrong_stage, wrong_stage_passed = build_ai_capacity_report(
        summary,
        git_sha="a" * 40,
        k6_image="grafana/k6:2.1.0@sha256:example",
        k6_exit_code=0,
        stage=30,
        vus=10,
        api_replicas=2,
        worker_replicas=2,
    )

    assert "Conclusion: **PASS**" in report
    assert passed is True
    assert "Diagnosis P95 | 18000.00 ms | <30000 ms" in report
    assert "Conclusion: **FAIL**" in wrong_stage
    assert wrong_stage_passed is False
