import argparse
import asyncio
import json
import mimetypes
import os
import sys
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from PIL import Image

TERMINAL_JOB_STATUSES = {
    "COMPLETED",
    "FAILED_FINAL",
    "TIMED_OUT",
    "CANCELLED",
}


class SmokeError(Exception):
    pass


class SmokeClient:
    def __init__(self, *, base_url: str, access_token: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "ai-wardrobe-staging-smoke/1.0",
            },
            timeout=httpx.Timeout(30),
            follow_redirects=False,
        )
        self._timeout_seconds = timeout_seconds
        self.evidence: list[dict[str, object]] = []

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        started_at = monotonic()
        response = await self._client.request(
            method,
            path,
            json=json_body,
            headers=headers,
        )
        duration_ms = round((monotonic() - started_at) * 1_000)
        trace_id = response.headers.get("X-Trace-ID")
        request_id = response.headers.get("X-Request-ID")
        self.evidence.append(
            {
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "trace_id": trace_id,
                "request_id": request_id,
            }
        )
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise SmokeError(
                f"{method} {path} returned non-JSON status {response.status_code}"
            ) from error
        if not 200 <= response.status_code < 300:
            safe_error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = safe_error.get("code", "UNKNOWN") if isinstance(safe_error, dict) else "UNKNOWN"
            message = (
                safe_error.get("message", "request failed")
                if isinstance(safe_error, dict)
                else "request failed"
            )
            raise SmokeError(
                f"{method} {path} failed: {response.status_code} {code} {message}; "
                f"trace={trace_id or 'none'}"
            )
        if not isinstance(payload, dict):
            raise SmokeError(f"{method} {path} returned a non-object payload")
        return payload

    async def poll_job_resource(
        self,
        path: str,
        *,
        success_status: str,
        label: str,
    ) -> dict[str, Any]:
        deadline = monotonic() + self._timeout_seconds
        delay = 1.0
        while monotonic() < deadline:
            payload = await self.request("GET", path)
            job_status = payload.get("job_status")
            if job_status == success_status:
                return payload
            if job_status in TERMINAL_JOB_STATUSES:
                raise SmokeError(
                    f"{label} ended as {job_status}: {payload.get('error_code') or 'NO_ERROR_CODE'}"
                )
            await asyncio.sleep(delay)
            delay = min(8.0, delay * 1.5)
        raise SmokeError(f"{label} exceeded {self._timeout_seconds:.0f}s")

    async def poll_share(self, scene_code: str) -> dict[str, Any]:
        deadline = monotonic() + self._timeout_seconds
        delay = 1.0
        while monotonic() < deadline:
            payload = await self.request("GET", f"/api/v1/shares/{scene_code}")
            status = payload.get("status")
            if status == "ACTIVE":
                return payload
            if status in {"FAILED", "EXPIRED", "REVOKED"}:
                raise SmokeError(
                    f"share ended as {status}: {payload.get('error_code') or 'NO_ERROR_CODE'}"
                )
            await asyncio.sleep(delay)
            delay = min(5.0, delay * 1.5)
        raise SmokeError(f"share exceeded {self._timeout_seconds:.0f}s")

    async def delete_and_wait(self, asset_id: str, run_id: str) -> None:
        deletion = await self.request(
            "DELETE",
            f"/api/v1/me/photos/{asset_id}",
            idempotency_key=f"smoke-delete-{run_id}",
        )
        if deletion.get("status") == "COMPLETED":
            return
        deadline = monotonic() + self._timeout_seconds
        delay = 1.0
        while monotonic() < deadline:
            deletion = await self.request(
                "GET",
                f"/api/v1/me/photos/{asset_id}/deletion-status",
            )
            status = deletion.get("status")
            if status == "COMPLETED":
                return
            if status == "FAILED_FINAL":
                raise SmokeError("cleanup deletion reached FAILED_FINAL")
            await asyncio.sleep(delay)
            delay = min(8.0, delay * 1.5)
        raise SmokeError("cleanup deletion timed out")


def resolve_image_metadata(value: str) -> tuple[Path, str, int]:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise SmokeError(f"image does not exist: {path}")
    with Image.open(path) as image:
        detected_format = (image.format or "").upper()
        image.verify()
    content_types = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }
    content_type = content_types.get(detected_format)
    if content_type is None:
        guessed, _ = mimetypes.guess_type(path.name)
        content_type = guessed if guessed in content_types.values() else None
    if content_type is None:
        raise SmokeError("smoke image must be JPEG, PNG, or WebP")
    return path, content_type, path.stat().st_size


def validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return value.rstrip("/")
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}:
        return value.rstrip("/")
    raise SmokeError("base URL must use HTTPS except for localhost")


async def run(args: argparse.Namespace) -> dict[str, object]:
    token = os.environ.get("AIW_SMOKE_ACCESS_TOKEN", "")
    if not token:
        raise SmokeError("set AIW_SMOKE_ACCESS_TOKEN")
    image_path, content_type, size_bytes = await asyncio.to_thread(
        resolve_image_metadata,
        args.image,
    )
    run_id = uuid4().hex
    client = SmokeClient(
        base_url=validate_base_url(args.base_url),
        access_token=token,
        timeout_seconds=args.timeout,
    )
    asset_id: str | None = None
    result: dict[str, object] = {"run_id": run_id}
    primary_error: Exception | None = None
    try:
        await client.request("GET", "/health/ready")
        await client.request(
            "PATCH",
            "/api/v1/me/profile",
            json_body={
                "has_ai_processing_consent": True,
                "consent_version": "privacy-v1",
            },
        )
        ticket = await client.request(
            "POST",
            "/api/v1/assets/upload-ticket",
            json_body={
                "content_type": content_type,
                "size_bytes": size_bytes,
            },
        )
        asset_id = str(ticket["asset_id"])
        upload_headers = {
            str(key): str(value) for key, value in dict(ticket.get("headers", {})).items()
        }
        image_bytes = await asyncio.to_thread(image_path.read_bytes)
        upload_started_at = monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as upload_client:
            upload_response = await upload_client.put(
                str(ticket["upload_url"]),
                content=image_bytes,
                headers=upload_headers,
            )
        client.evidence.append(
            {
                "method": "PUT",
                "path": "<signed-object-url>",
                "status_code": upload_response.status_code,
                "duration_ms": round((monotonic() - upload_started_at) * 1_000),
                "trace_id": None,
                "request_id": None,
            }
        )
        if not 200 <= upload_response.status_code < 300:
            raise SmokeError(f"COS upload failed with {upload_response.status_code}")
        await client.request(
            "POST",
            f"/api/v1/assets/{asset_id}/complete",
            json_body={"content_type": content_type},
        )

        diagnosis = await client.request(
            "POST",
            "/api/v1/style-diagnoses",
            json_body={"asset_id": asset_id, "occasion": args.occasion},
            idempotency_key=f"smoke-diagnosis-{run_id}",
        )
        diagnosis_id = str(diagnosis["id"])
        diagnosis = await client.poll_job_resource(
            f"/api/v1/style-diagnoses/{diagnosis_id}",
            success_status="COMPLETED",
            label="diagnosis",
        )
        result["diagnosis_id"] = diagnosis_id
        diagnosis_result = diagnosis.get("result")
        result["diagnosis_score"] = (
            diagnosis_result.get("score") if isinstance(diagnosis_result, dict) else None
        )

        optimization = await client.request(
            "POST",
            f"/api/v1/style-diagnoses/{diagnosis_id}/optimizations",
            json_body={"max_change_level": args.max_change_level},
            idempotency_key=f"smoke-optimization-{run_id}",
        )
        optimization_id = str(optimization["id"])
        optimization = await client.poll_job_resource(
            f"/api/v1/style-optimizations/{optimization_id}",
            success_status="COMPLETED",
            label="optimization",
        )
        if not optimization.get("quality_passed") or not optimization.get("after_image_url"):
            raise SmokeError("optimization completed without a safe result image")
        result["optimization_id"] = optimization_id
        result["critic_first_pass"] = optimization.get("critic_first_pass")

        share = await client.request(
            "POST",
            "/api/v1/shares",
            json_body={
                "optimization_id": optimization_id,
                "display_score": False,
                "attribution_source": "PREVIEW",
            },
            idempotency_key=f"smoke-share-{run_id}",
        )
        scene_code = str(share["scene_code"])
        share = await client.poll_share(scene_code)
        if not share.get("card_url") or share.get("ai_edited") is not True:
            raise SmokeError("active share is missing its compliant derivative")
        await client.request(
            "POST",
            "/api/v1/votes",
            json_body={"scene_code": scene_code, "choice": "AFTER"},
        )
        result["scene_code"] = scene_code
        result["status"] = "PASSED"
    except Exception as error:
        primary_error = error
        result["status"] = "FAILED"
        result["error"] = str(error) if isinstance(error, SmokeError) else type(error).__name__
    finally:
        cleanup_error: Exception | None = None
        if asset_id and not args.keep_data:
            try:
                await client.delete_and_wait(asset_id, run_id)
                result["cleanup"] = "COMPLETED"
            except Exception as error:
                cleanup_error = error
                result["cleanup"] = f"FAILED: {error}"
        result["evidence"] = client.evidence
        await client.close()
        if primary_error is None and cleanup_error is not None:
            result["status"] = "FAILED"
            result["error"] = "cleanup failed"
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the authorized AI Wardrobe MVP staging smoke path.",
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--image", required=True, help="Authorized JPEG, PNG, or WebP sample")
    parser.add_argument(
        "--occasion",
        default="DAILY",
        choices=[
            "DAILY",
            "SCHOOL",
            "WORK",
            "INTERVIEW",
            "DATE",
            "SOCIAL",
            "FORMAL",
            "TRAVEL",
            "OTHER",
        ],
    )
    parser.add_argument("--max-change-level", type=int, choices=[1, 2, 3], default=3)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep created private and derivative data for manual inspection",
    )
    return parser.parse_args()


def main() -> int:
    try:
        report = asyncio.run(run(parse_args()))
    except SmokeError as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}, ensure_ascii=False))
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
    return 0 if report.get("status") == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
