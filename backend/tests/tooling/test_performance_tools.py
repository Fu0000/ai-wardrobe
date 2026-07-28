import stat
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from scripts.performance_tools import (
    PerformanceToolError,
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
