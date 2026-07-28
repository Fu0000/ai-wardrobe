from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.core.config import Settings
from app.main import create_app
from app.modules.assets.models import (
    AssetKind,
    AssetStatus,
    PhotoPurpose,
    SourcePhoto,
    UserAsset,
)
from app.modules.diagnosis.models import (
    DiagnosisStatus,
    OptimizationStatus,
    StyleDiagnosis,
    StyleOptimizationResult,
)
from app.modules.feedback.models import BetaFeedback, FeedbackCategory
from app.modules.governance.models import DeletionJob
from app.modules.identity.models import User, UserProfile
from app.modules.identity.security import AccessTokenService
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from tests.integration.markers import requires_services

pytestmark = [pytest.mark.integration, requires_services]


@dataclass(frozen=True, slots=True)
class SecurityContext:
    client: AsyncClient
    owner_token: str
    attacker_token: str
    owner_asset_id: UUID
    owner_job_id: UUID
    owner_diagnosis_id: UUID
    owner_optimization_id: UUID
    storage: MagicMock
    session: AsyncSession
    attacker_id: UUID


async def _seed_owner_resources(
    session: AsyncSession,
) -> tuple[User, User, UserAsset, GenerationJob, StyleDiagnosis, StyleOptimizationResult]:
    owner = User(id=uuid4())
    owner.profile = UserProfile(
        display_name="security-owner",
        has_ai_processing_consent=True,
        consent_version="test-v1",
    )
    attacker = User(id=uuid4())
    attacker.profile = UserProfile(
        display_name="security-attacker",
        has_ai_processing_consent=True,
        consent_version="test-v1",
    )
    session.add_all([owner, attacker])
    await session.flush()

    source_asset = UserAsset(
        id=uuid4(),
        user_id=owner.id,
        kind=AssetKind.USER_UPLOAD,
        status=AssetStatus.READY,
        bucket="integration-security",
        object_key=f"security/{owner.id}/source.jpg",
        content_type="image/jpeg",
        size_bytes=1_024,
        width=800,
        height=1_200,
    )
    session.add(source_asset)
    await session.flush()
    source_photo = SourcePhoto(
        id=uuid4(),
        user_id=owner.id,
        asset_id=source_asset.id,
        purpose=PhotoPurpose.OUTFIT_DIAGNOSIS,
    )
    session.add(source_photo)
    await session.flush()

    diagnosis_job = GenerationJob(
        id=uuid4(),
        user_id=owner.id,
        task_type=JobTaskType.STYLE_DIAGNOSIS,
        status=JobStatus.PROCESSING,
        idempotency_key=f"security-diagnosis-{uuid4().hex}",
        request_hash="a" * 64,
        progress=50,
    )
    optimization_job = GenerationJob(
        id=uuid4(),
        user_id=owner.id,
        task_type=JobTaskType.STYLE_OPTIMIZATION,
        status=JobStatus.PENDING,
        idempotency_key=f"security-optimization-{uuid4().hex}",
        request_hash="b" * 64,
    )
    session.add_all([diagnosis_job, optimization_job])
    await session.flush()

    diagnosis = StyleDiagnosis(
        id=uuid4(),
        user_id=owner.id,
        source_photo_id=source_photo.id,
        job_id=diagnosis_job.id,
        occasion="DAILY",
        status=DiagnosisStatus.PENDING,
    )
    session.add(diagnosis)
    await session.flush()
    optimization = StyleOptimizationResult(
        id=uuid4(),
        user_id=owner.id,
        diagnosis_id=diagnosis.id,
        job_id=optimization_job.id,
        status=OptimizationStatus.PENDING,
        change_level=1,
        change_summary=[],
    )
    session.add(optimization)
    await session.flush()
    session.add(
        BetaFeedback(
            id=uuid4(),
            user_id=owner.id,
            related_job_id=diagnosis_job.id,
            category=FeedbackCategory.AI_QUALITY,
            rating=3,
            message="owner-only feedback",
            idempotency_key=f"security-feedback-{uuid4().hex}",
            request_hash="c" * 64,
        )
    )
    await session.flush()
    return owner, attacker, source_asset, diagnosis_job, diagnosis, optimization


@pytest_asyncio.fixture
async def security_context(
    session: AsyncSession,
    settings: Settings,
) -> AsyncIterator[SecurityContext]:
    owner, attacker, asset, job, diagnosis, optimization = await _seed_owner_resources(session)

    async def scoped_session() -> AsyncIterator[AsyncSession]:
        yield session

    storage = MagicMock()
    storage.create_download_url = AsyncMock(
        return_value="https://private.example/signed?redacted=1"
    )
    app = create_app(settings)
    app.dependency_overrides[get_session] = scoped_session
    app.state.object_storage = storage
    tokens = AccessTokenService(settings)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield SecurityContext(
            client=client,
            owner_token=tokens.issue(owner.id).value,
            attacker_token=tokens.issue(attacker.id).value,
            owner_asset_id=asset.id,
            owner_job_id=job.id,
            owner_diagnosis_id=diagnosis.id,
            owner_optimization_id=optimization.id,
            storage=storage,
            session=session,
            attacker_id=attacker.id,
        )


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _safe_error(response: Response) -> tuple[int, str, str]:
    error = response.json()["error"]
    return response.status_code, error["code"], error["message"]


@pytest.mark.parametrize(
    ("path_template", "expected_code"),
    [
        ("/api/v1/assets/{resource_id}/access-url", "ASSET_NOT_FOUND"),
        ("/api/v1/jobs/{resource_id}", "JOB_NOT_FOUND"),
        ("/api/v1/style-diagnoses/{resource_id}", "DIAGNOSIS_NOT_FOUND"),
        (
            "/api/v1/style-optimizations/{resource_id}",
            "OPTIMIZATION_NOT_FOUND",
        ),
    ],
)
async def test_private_reads_hide_foreign_resource_existence(
    security_context: SecurityContext,
    path_template: str,
    expected_code: str,
) -> None:
    resource_id_by_path = {
        "assets": security_context.owner_asset_id,
        "jobs": security_context.owner_job_id,
        "style-diagnoses": security_context.owner_diagnosis_id,
        "style-optimizations": security_context.owner_optimization_id,
    }
    resource_id = next(
        identifier
        for segment, identifier in resource_id_by_path.items()
        if segment in path_template
    )
    headers = _authorization(security_context.attacker_token)

    foreign = await security_context.client.get(
        path_template.format(resource_id=resource_id),
        headers=headers,
    )
    absent = await security_context.client.get(
        path_template.format(resource_id=uuid4()),
        headers=headers,
    )

    assert _safe_error(foreign) == _safe_error(absent)
    assert _safe_error(foreign)[:2] == (404, expected_code)
    security_context.storage.create_download_url.assert_not_awaited()


async def test_foreign_write_references_have_no_side_effects(
    security_context: SecurityContext,
) -> None:
    client = security_context.client
    headers = _authorization(security_context.attacker_token)
    foreign_requests = [
        (
            "POST",
            f"/api/v1/assets/{security_context.owner_asset_id}/complete",
            {"content_type": "image/jpeg"},
            {"Idempotency-Key": "unused-key"},
            "ASSET_NOT_FOUND",
        ),
        (
            "POST",
            "/api/v1/style-diagnoses",
            {"asset_id": str(security_context.owner_asset_id), "occasion": "WORK"},
            {"Idempotency-Key": "security-diagnosis-create"},
            "ASSET_NOT_FOUND",
        ),
        (
            "DELETE",
            f"/api/v1/me/photos/{security_context.owner_asset_id}",
            None,
            {"Idempotency-Key": "security-photo-delete"},
            "ASSET_NOT_FOUND",
        ),
        (
            "POST",
            "/api/v1/feedback",
            {
                "category": "AI_QUALITY",
                "rating": 1,
                "message": "This foreign job must stay private.",
                "related_job_id": str(security_context.owner_job_id),
            },
            {"Idempotency-Key": "security-feedback-create"},
            "RELATED_JOB_NOT_FOUND",
        ),
    ]

    for method, path, body, extra_headers, expected_code in foreign_requests:
        response = await client.request(
            method,
            path,
            json=body,
            headers={**headers, **extra_headers},
        )
        assert _safe_error(response)[:2] == (404, expected_code)

    attacker_jobs = await security_context.session.scalar(
        select(func.count())
        .select_from(GenerationJob)
        .where(GenerationJob.user_id == security_context.attacker_id)
    )
    attacker_deletions = await security_context.session.scalar(
        select(func.count())
        .select_from(DeletionJob)
        .where(DeletionJob.user_id == security_context.attacker_id)
    )
    attacker_feedback = await security_context.session.scalar(
        select(func.count())
        .select_from(BetaFeedback)
        .where(BetaFeedback.user_id == security_context.attacker_id)
    )
    assert (attacker_jobs, attacker_deletions, attacker_feedback) == (0, 0, 0)
    security_context.storage.create_download_url.assert_not_awaited()


async def test_positive_controls_preserve_owned_access_and_list_isolation(
    security_context: SecurityContext,
) -> None:
    client = security_context.client
    owner_headers = _authorization(security_context.owner_token)
    attacker_headers = _authorization(security_context.attacker_token)

    owner_asset = await client.get(
        f"/api/v1/assets/{security_context.owner_asset_id}/access-url",
        headers=owner_headers,
    )
    owner_job = await client.get(
        f"/api/v1/jobs/{security_context.owner_job_id}",
        headers=owner_headers,
    )
    owner_diagnosis = await client.get(
        f"/api/v1/style-diagnoses/{security_context.owner_diagnosis_id}",
        headers=owner_headers,
    )
    owner_optimization = await client.get(
        f"/api/v1/style-optimizations/{security_context.owner_optimization_id}",
        headers=owner_headers,
    )
    owner_feedback = await client.get("/api/v1/me/feedback", headers=owner_headers)
    attacker_feedback = await client.get(
        "/api/v1/me/feedback",
        headers=attacker_headers,
    )

    assert [
        owner_asset.status_code,
        owner_job.status_code,
        owner_diagnosis.status_code,
        owner_optimization.status_code,
        owner_feedback.status_code,
        attacker_feedback.status_code,
    ] == [200, 200, 200, 200, 200, 200]
    assert owner_asset.json()["url"].startswith("https://private.example/")
    assert owner_feedback.json()["items"][0]["message"] == "owner-only feedback"
    assert attacker_feedback.json() == {"items": [], "next_cursor": None}
    assert security_context.storage.create_download_url.await_count == 2
