from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from time import monotonic
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

_EXPIRY_CONFIRMATION = "I_ACCEPT_WAIT_FOR_SIGNED_URL_EXPIRY"
_DELETION_CONFIRMATION = "I_CONFIRM_DELETE_DEDICATED_STAGING_ASSET"
_PUBLIC_DNS_HOSTNAME = re.compile(
    r"(?=.{1,253}\Z)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
)


class SecurityAuditError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResourceProbe:
    name: str
    path_template: str
    owner_resource_id: UUID
    not_found_code: str


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    owner_status: int
    attacker_status: int
    absent_status: int
    request_id_present: bool
    trace_id_present: bool
    duration_ms: int


def validate_staging_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise SecurityAuditError("Staging base URL must be a plain HTTPS origin")
    return value.rstrip("/")


def validate_expected_asset_host(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    if not host or "/" in host or ":" in host or host in {"localhost", "host.docker.internal"}:
        raise SecurityAuditError("expected asset host must be an exact public hostname")
    try:
        ip_address(host)
    except ValueError:
        if not _PUBLIC_DNS_HOSTNAME.fullmatch(host) or host.endswith(
            (".local", ".internal", ".localhost")
        ):
            raise SecurityAuditError(
                "expected asset host must be an exact public hostname"
            ) from None
    else:
        raise SecurityAuditError("expected asset host must be a public DNS hostname")
    return host


def _safe_error(response: httpx.Response) -> tuple[int, str, str]:
    try:
        payload = response.json()
    except json.JSONDecodeError as error:
        raise SecurityAuditError("API returned non-JSON error response") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        raise SecurityAuditError("API error response does not follow the contract")
    detail = payload["error"]
    code = detail.get("code")
    message = detail.get("message")
    if not isinstance(code, str) or not isinstance(message, str):
        raise SecurityAuditError("API error response is missing safe code/message")
    return response.status_code, code, message


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SecurityAuditError(f"set {name}")
    return value


def _required_uuid_env(name: str) -> UUID:
    try:
        return UUID(_required_env(name))
    except ValueError as error:
        raise SecurityAuditError(f"{name} must be a UUID") from error


def _parse_owner_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except json.JSONDecodeError as error:
        raise SecurityAuditError("owner API response is not JSON") from error
    if not isinstance(payload, dict):
        raise SecurityAuditError("owner API response must be an object")
    return payload


def _signed_url_contract(
    payload: dict[str, Any],
    *,
    expected_host: str,
    max_ttl_seconds: int,
) -> tuple[str, datetime, int]:
    url = payload.get("url")
    expires_at_raw = payload.get("expires_at")
    if not isinstance(url, str) or not isinstance(expires_at_raw, str):
        raise SecurityAuditError("asset access response is missing URL expiry metadata")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise SecurityAuditError("signed URL host or scheme violated the allowlist")
    try:
        expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise SecurityAuditError("signed URL expiry is invalid") from error
    if expires_at.tzinfo is None:
        raise SecurityAuditError("signed URL expiry must be timezone-aware")
    ttl_seconds = round((expires_at - datetime.now(UTC)).total_seconds())
    if ttl_seconds < 5 or ttl_seconds > max_ttl_seconds:
        raise SecurityAuditError("signed URL TTL is outside the approved range")
    return url, expires_at, ttl_seconds


class SecurityPrivacyAuditor:
    def __init__(
        self,
        *,
        base_url: str,
        owner_token: str,
        attacker_token: str,
        expected_asset_host: str,
        max_ttl_seconds: int,
        max_wait_seconds: int,
        api_transport: httpx.AsyncBaseTransport | None = None,
        object_transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not owner_token or not attacker_token or owner_token == attacker_token:
            raise SecurityAuditError("two distinct non-empty access tokens are required")
        if not 5 <= max_ttl_seconds <= 3_600:
            raise SecurityAuditError("maximum signed URL TTL must be 5-3600 seconds")
        if not max_ttl_seconds <= max_wait_seconds <= 3_900:
            raise SecurityAuditError("maximum audit wait must cover the signed URL TTL")
        self._owner_token = owner_token
        self._attacker_token = attacker_token
        self._expected_asset_host = validate_expected_asset_host(expected_asset_host)
        self._max_ttl_seconds = max_ttl_seconds
        self._max_wait_seconds = max_wait_seconds
        self._sleep = sleep
        self._api = httpx.AsyncClient(
            base_url=validate_staging_base_url(base_url),
            timeout=httpx.Timeout(30),
            follow_redirects=False,
            transport=api_transport,
            headers={"User-Agent": "ai-wardrobe-security-audit/1.0"},
        )
        self._objects = httpx.AsyncClient(
            timeout=httpx.Timeout(30),
            follow_redirects=False,
            trust_env=False,
            transport=object_transport,
            headers={"User-Agent": "ai-wardrobe-security-audit/1.0"},
        )

    async def close(self) -> None:
        await self._api.aclose()
        await self._objects.aclose()

    async def _get(self, path: str, token: str) -> httpx.Response:
        return await self._api.get(
            path,
            headers={"Authorization": f"Bearer {token}"},
        )

    @staticmethod
    def _require_correlation_headers(response: httpx.Response, check: str) -> None:
        if not response.headers.get("X-Request-ID") or not response.headers.get("X-Trace-ID"):
            raise SecurityAuditError(f"{check} is missing correlation headers")

    async def probe_resource(
        self,
        probe: ResourceProbe,
    ) -> tuple[ProbeResult, dict[str, Any]]:
        owner_path = probe.path_template.format(resource_id=probe.owner_resource_id)
        absent_path = probe.path_template.format(resource_id=uuid4())
        started_at = monotonic()
        owner = await self._get(owner_path, self._owner_token)
        if owner.status_code != 200:
            raise SecurityAuditError(f"{probe.name} owner positive control failed")
        owner_payload = _parse_owner_payload(owner)

        foreign = await self._get(owner_path, self._attacker_token)
        absent = await self._get(absent_path, self._attacker_token)
        if foreign.status_code != 404:
            raise SecurityAuditError(f"{probe.name} leaks foreign resource existence")
        if absent.status_code != 404:
            raise SecurityAuditError(f"{probe.name} absent-resource control failed")
        foreign_error = _safe_error(foreign)
        absent_error = _safe_error(absent)
        if foreign_error != absent_error or foreign_error[:2] != (404, probe.not_found_code):
            raise SecurityAuditError(f"{probe.name} leaks foreign resource existence")
        request_id_present = all(
            bool(response.headers.get("X-Request-ID")) for response in (owner, foreign, absent)
        )
        trace_id_present = all(
            bool(response.headers.get("X-Trace-ID")) for response in (owner, foreign, absent)
        )
        if not request_id_present or not trace_id_present:
            raise SecurityAuditError(f"{probe.name} is missing correlation headers")
        return (
            ProbeResult(
                name=probe.name,
                owner_status=owner.status_code,
                attacker_status=foreign.status_code,
                absent_status=absent.status_code,
                request_id_present=request_id_present,
                trace_id_present=trace_id_present,
                duration_ms=round((monotonic() - started_at) * 1_000),
            ),
            owner_payload,
        )

    async def verify_signed_url(
        self,
        payload: dict[str, Any],
        *,
        wait_for_expiry: bool,
        expiry_grace_seconds: int,
    ) -> dict[str, object]:
        url, expires_at, ttl_seconds = _signed_url_contract(
            payload,
            expected_host=self._expected_asset_host,
            max_ttl_seconds=self._max_ttl_seconds,
        )
        async with self._objects.stream("GET", url) as response:
            valid_status = response.status_code
        if not 200 <= valid_status < 300:
            raise SecurityAuditError("signed URL is not readable during its valid window")
        if not wait_for_expiry:
            raise SecurityAuditError("expiry verification confirmation is required")

        wait_seconds = max(
            0.0,
            (expires_at - datetime.now(UTC)).total_seconds() + expiry_grace_seconds,
        )
        if wait_seconds > self._max_wait_seconds:
            raise SecurityAuditError("signed URL expiry exceeds the approved audit wait")
        await self._sleep(wait_seconds)
        async with self._objects.stream("GET", url) as response:
            expired_status = response.status_code
        if expired_status not in {401, 403, 404}:
            raise SecurityAuditError("signed URL remained readable after expiry")
        return {
            "valid_status": valid_status,
            "expired_status": expired_status,
            "ttl_seconds": ttl_seconds,
            "wait_seconds": round(wait_seconds),
        }

    async def verify_asset_deletion(
        self,
        *,
        asset_id: UUID,
        confirmed: bool,
        max_wait_seconds: int,
        poll_interval_seconds: float,
    ) -> dict[str, object]:
        if not confirmed:
            raise SecurityAuditError("dedicated Staging asset deletion confirmation is required")
        if not 10 <= max_wait_seconds <= 900:
            raise SecurityAuditError("asset deletion wait must be 10-900 seconds")
        if not 0.1 <= poll_interval_seconds <= 10:
            raise SecurityAuditError("asset deletion poll interval must be 0.1-10 seconds")

        access_path = f"/api/v1/assets/{asset_id}/access-url"
        before_access = await self._get(access_path, self._owner_token)
        self._require_correlation_headers(before_access, "deletion asset positive control")
        if before_access.status_code != 200:
            raise SecurityAuditError("deletion asset positive control failed")
        before_payload = _parse_owner_payload(before_access)
        old_url, expires_at, ttl_seconds = _signed_url_contract(
            before_payload,
            expected_host=self._expected_asset_host,
            max_ttl_seconds=self._max_ttl_seconds,
        )
        async with self._objects.stream("GET", old_url) as response:
            before_delete_status = response.status_code
        if not 200 <= before_delete_status < 300:
            raise SecurityAuditError("deletion asset URL is unreadable before deletion")

        idempotency_key = f"security-delete-{uuid4().hex}"
        delete_path = f"/api/v1/me/photos/{asset_id}"
        headers = {
            "Authorization": f"Bearer {self._owner_token}",
            "Idempotency-Key": idempotency_key,
        }
        first = await self._api.delete(delete_path, headers=headers)
        replay = await self._api.delete(delete_path, headers=headers)
        for response in (first, replay):
            self._require_correlation_headers(response, "asset deletion request")
            if response.status_code != 202:
                raise SecurityAuditError("asset deletion request was not accepted")
        first_payload = _parse_owner_payload(first)
        replay_payload = _parse_owner_payload(replay)
        deletion_id = first_payload.get("id")
        if (
            not isinstance(deletion_id, str)
            or replay_payload.get("id") != deletion_id
            or replay_payload.get("reused") is not True
        ):
            raise SecurityAuditError("asset deletion idempotency contract failed")

        status_path = f"/api/v1/me/photos/{asset_id}/deletion-status"
        started_at = monotonic()
        poll_count = 0
        completed_payload: dict[str, Any] | None = None
        while monotonic() - started_at <= max_wait_seconds:
            poll_count += 1
            response = await self._get(status_path, self._owner_token)
            self._require_correlation_headers(response, "asset deletion status")
            if response.status_code != 200:
                raise SecurityAuditError("asset deletion status control failed")
            payload = _parse_owner_payload(response)
            status = payload.get("status")
            if status == "COMPLETED":
                completed_payload = payload
                break
            if status == "FAILED_FINAL":
                raise SecurityAuditError("asset deletion reached a final failure")
            if status not in {"PENDING", "PROCESSING", "FAILED_RETRYABLE"}:
                raise SecurityAuditError("asset deletion returned an unknown status")
            await self._sleep(poll_interval_seconds)
        if completed_payload is None:
            raise SecurityAuditError("asset deletion did not complete within the approved wait")

        completed_steps = completed_payload.get("completed_steps")
        if not isinstance(completed_steps, list) or not {
            "COS_OBJECTS_DELETED",
            "DATABASE_ROWS_PURGED",
        }.issubset(set(completed_steps)):
            raise SecurityAuditError("asset deletion completion steps are incomplete")

        after_access = await self._get(access_path, self._owner_token)
        self._require_correlation_headers(after_access, "deleted asset API control")
        if _safe_error(after_access)[:2] != (404, "ASSET_NOT_FOUND"):
            raise SecurityAuditError("deleted asset remains accessible through the API")

        remaining_ttl_seconds = round((expires_at - datetime.now(UTC)).total_seconds())
        if remaining_ttl_seconds < 5:
            raise SecurityAuditError("old signed URL expired before deletion could be proven")
        async with self._objects.stream("GET", old_url) as response:
            after_delete_status = response.status_code
        if after_delete_status not in {401, 403, 404}:
            raise SecurityAuditError("old signed URL remained readable after asset deletion")
        return {
            "request_status": first.status_code,
            "idempotent_replay_status": replay.status_code,
            "final_status": "COMPLETED",
            "completed_step_count": len(completed_steps),
            "poll_count": poll_count,
            "duration_ms": round((monotonic() - started_at) * 1_000),
            "signed_url_status_before_delete": before_delete_status,
            "signed_url_status_after_delete": after_delete_status,
            "signed_url_ttl_seconds": ttl_seconds,
            "signed_url_remaining_ttl_seconds": remaining_ttl_seconds,
            "api_status_after_delete": after_access.status_code,
        }


async def run(args: argparse.Namespace) -> dict[str, object]:
    owner_token = _required_env("AIW_SECURITY_OWNER_ACCESS_TOKEN")
    attacker_token = _required_env("AIW_SECURITY_ATTACKER_ACCESS_TOKEN")
    wait_for_expiry = os.environ.get("AIW_SECURITY_WAIT_FOR_EXPIRY") == _EXPIRY_CONFIRMATION
    confirm_deletion = os.environ.get("AIW_SECURITY_ALLOW_ASSET_DELETION") == _DELETION_CONFIRMATION
    primary_asset_id = _required_uuid_env("SECURITY_OWNER_ASSET_ID")
    deletion_asset_id = _required_uuid_env("SECURITY_DELETION_ASSET_ID")
    if primary_asset_id == deletion_asset_id:
        raise SecurityAuditError("deletion asset must differ from the isolation fixture")
    probes = [
        ResourceProbe(
            "asset_access",
            "/api/v1/assets/{resource_id}/access-url",
            primary_asset_id,
            "ASSET_NOT_FOUND",
        ),
        ResourceProbe(
            "job",
            "/api/v1/jobs/{resource_id}",
            _required_uuid_env("SECURITY_OWNER_JOB_ID"),
            "JOB_NOT_FOUND",
        ),
        ResourceProbe(
            "diagnosis",
            "/api/v1/style-diagnoses/{resource_id}",
            _required_uuid_env("SECURITY_OWNER_DIAGNOSIS_ID"),
            "DIAGNOSIS_NOT_FOUND",
        ),
        ResourceProbe(
            "optimization",
            "/api/v1/style-optimizations/{resource_id}",
            _required_uuid_env("SECURITY_OWNER_OPTIMIZATION_ID"),
            "OPTIMIZATION_NOT_FOUND",
        ),
    ]
    auditor = SecurityPrivacyAuditor(
        base_url=_required_env("STAGING_API_BASE_URL"),
        owner_token=owner_token,
        attacker_token=attacker_token,
        expected_asset_host=_required_env("SECURITY_EXPECTED_ASSET_HOST"),
        max_ttl_seconds=args.max_ttl,
        max_wait_seconds=args.max_wait,
    )
    try:
        results: list[ProbeResult] = []
        asset_payload: dict[str, Any] | None = None
        for probe in probes:
            result, payload = await auditor.probe_resource(probe)
            results.append(result)
            if probe.name == "asset_access":
                asset_payload = payload
        if asset_payload is None:
            raise SecurityAuditError("asset positive control did not return a payload")
        signed_url = await auditor.verify_signed_url(
            asset_payload,
            wait_for_expiry=wait_for_expiry,
            expiry_grace_seconds=args.expiry_grace,
        )
        deletion = await auditor.verify_asset_deletion(
            asset_id=deletion_asset_id,
            confirmed=confirm_deletion,
            max_wait_seconds=args.deletion_max_wait,
            poll_interval_seconds=args.deletion_poll_interval,
        )
        return {
            "status": "PASSED",
            "checks": [asdict(result) for result in results],
            "signed_url": signed_url,
            "deletion": deletion,
            "sensitive_values_recorded": False,
        }
    finally:
        await auditor.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Staging two-user isolation and COS signed URL expiry.",
    )
    parser.add_argument("--max-ttl", type=int, default=900)
    parser.add_argument("--max-wait", type=int, default=1_200)
    parser.add_argument("--expiry-grace", type=int, default=5)
    parser.add_argument("--deletion-max-wait", type=int, default=300)
    parser.add_argument("--deletion-poll-interval", type=float, default=2)
    return parser.parse_args()


def main() -> int:
    try:
        report = asyncio.run(run(parse_args()))
    except SecurityAuditError as error:
        print(
            json.dumps(
                {"status": "FAILED", "error": str(error)},
                ensure_ascii=False,
            )
        )
        return 1
    except (httpx.HTTPError, OSError) as error:
        print(
            json.dumps(
                {"status": "FAILED", "error": type(error).__name__},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
