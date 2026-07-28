import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest

from scripts.security_privacy_audit import (
    ResourceProbe,
    SecurityAuditError,
    SecurityPrivacyAuditor,
    validate_expected_asset_host,
    validate_staging_base_url,
)


def test_security_audit_rejects_unsafe_origins_and_asset_hosts() -> None:
    with pytest.raises(SecurityAuditError):
        validate_staging_base_url("http://staging.example.com")
    with pytest.raises(SecurityAuditError):
        validate_staging_base_url("https://user:pass@staging.example.com")
    with pytest.raises(SecurityAuditError):
        validate_expected_asset_host("127.0.0.1")
    with pytest.raises(SecurityAuditError):
        validate_expected_asset_host("8.8.8.8")
    with pytest.raises(SecurityAuditError):
        validate_expected_asset_host("-invalid.example")
    with pytest.raises(SecurityAuditError):
        validate_expected_asset_host("cos.internal")


@pytest.mark.asyncio
async def test_security_audit_passes_without_recording_secrets() -> None:
    owner_token = "owner-secret-token"
    attacker_token = "attacker-secret-token"
    owner_ids = {
        "asset_access": uuid4(),
        "job": uuid4(),
        "diagnosis": uuid4(),
        "optimization": uuid4(),
    }
    codes = {
        "assets": "ASSET_NOT_FOUND",
        "jobs": "JOB_NOT_FOUND",
        "style-diagnoses": "DIAGNOSIS_NOT_FOUND",
        "style-optimizations": "OPTIMIZATION_NOT_FOUND",
    }

    async def api_handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        common_headers = {"X-Request-ID": uuid4().hex, "X-Trace-ID": uuid4().hex}
        if token == owner_token:
            payload: dict[str, Any] = {"id": str(uuid4())}
            if "access-url" in request.url.path:
                payload = {
                    "asset_id": str(owner_ids["asset_access"]),
                    "url": "https://cos.example/object?signature=never-record-this",
                    "expires_at": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                }
            return httpx.Response(200, json=payload, headers=common_headers)
        code = next(value for segment, value in codes.items() if segment in request.url.path)
        return httpx.Response(
            404,
            json={"error": {"code": code, "message": "资源不存在。"}},
            headers=common_headers,
        )

    object_calls = 0

    async def object_handler(request: httpx.Request) -> httpx.Response:
        nonlocal object_calls
        object_calls += 1
        return httpx.Response(200 if object_calls == 1 else 403)

    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    auditor = SecurityPrivacyAuditor(
        base_url="https://staging.example.com",
        owner_token=owner_token,
        attacker_token=attacker_token,
        expected_asset_host="cos.example",
        max_ttl_seconds=900,
        max_wait_seconds=1_200,
        api_transport=httpx.MockTransport(api_handler),
        object_transport=httpx.MockTransport(object_handler),
        sleep=fake_sleep,
    )
    try:
        results = []
        asset_payload: dict[str, Any] | None = None
        definitions = [
            ("asset_access", "/api/v1/assets/{resource_id}/access-url", "ASSET_NOT_FOUND"),
            ("job", "/api/v1/jobs/{resource_id}", "JOB_NOT_FOUND"),
            (
                "diagnosis",
                "/api/v1/style-diagnoses/{resource_id}",
                "DIAGNOSIS_NOT_FOUND",
            ),
            (
                "optimization",
                "/api/v1/style-optimizations/{resource_id}",
                "OPTIMIZATION_NOT_FOUND",
            ),
        ]
        for name, path, code in definitions:
            result, payload = await auditor.probe_resource(
                ResourceProbe(name, path, owner_ids[name], code)
            )
            results.append(result)
            if name == "asset_access":
                asset_payload = payload
        assert asset_payload is not None
        signed_result = await auditor.verify_signed_url(
            asset_payload,
            wait_for_expiry=True,
            expiry_grace_seconds=5,
        )
    finally:
        await auditor.close()

    report = json.dumps(
        {
            "checks": [result.name for result in results],
            "signed_url": signed_result,
        }
    )
    assert object_calls == 2
    assert waited and 30 <= waited[0] <= 36
    assert signed_result["expired_status"] == 403
    assert owner_token not in report
    assert attacker_token not in report
    assert "never-record-this" not in report
    assert all(str(resource_id) not in report for resource_id in owner_ids.values())


@pytest.mark.asyncio
async def test_security_audit_fails_when_attacker_can_read_owner_resource() -> None:
    async def unsafe_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={"id": str(uuid4())},
            headers={"X-Request-ID": uuid4().hex, "X-Trace-ID": uuid4().hex},
        )

    auditor = SecurityPrivacyAuditor(
        base_url="https://staging.example.com",
        owner_token="owner-token",
        attacker_token="attacker-token",
        expected_asset_host="cos.example",
        max_ttl_seconds=900,
        max_wait_seconds=1_200,
        api_transport=httpx.MockTransport(unsafe_handler),
        object_transport=httpx.MockTransport(unsafe_handler),
    )
    try:
        with pytest.raises(SecurityAuditError, match="leaks foreign"):
            await auditor.probe_resource(
                ResourceProbe(
                    "job",
                    "/api/v1/jobs/{resource_id}",
                    uuid4(),
                    "JOB_NOT_FOUND",
                )
            )
    finally:
        await auditor.close()
