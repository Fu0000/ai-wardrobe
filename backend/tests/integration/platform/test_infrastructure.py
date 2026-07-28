import asyncio
import os
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.core.rate_limit import RedisTokenBucketRateLimiter
from app.database import models as database_models  # noqa: F401
from app.database.base import Base
from app.modules.assets.models import (
    AssetKind,
    AssetStatus,
    PhotoPurpose,
    SourcePhoto,
    UserAsset,
)
from app.modules.assets.repository import AssetRepository
from app.modules.diagnosis.models import (
    DiagnosisStatus,
    OptimizationStatus,
    StyleDiagnosis,
    StyleOptimizationResult,
)
from app.modules.diagnosis.repository import DiagnosisRepository
from app.modules.governance.deletion_repository import DeletionRepository
from app.modules.growth.models import ShareRecord, ShareStatus
from app.modules.growth.repository import GrowthRepository
from app.modules.identity.models import User
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from app.modules.jobs.repository import JobRepository
from app.modules.optimization.repository import OptimizationRepository

RUN_INTEGRATION_TESTS = os.getenv("AIW_RUN_INTEGRATION_TESTS") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION_TESTS,
        reason="set AIW_RUN_INTEGRATION_TESTS=1 with disposable PostgreSQL and Redis",
    ),
]


async def test_database_is_at_one_migration_head_with_all_model_tables() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            applied_heads = set(result.scalars())
            result = await connection.execute(
                text(
                    """
                    SELECT tablename
                    FROM pg_catalog.pg_tables
                    WHERE schemaname = current_schema()
                    """
                )
            )
            actual_tables = set(result.scalars())
    finally:
        await engine.dispose()

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert applied_heads == set(script.get_heads())
    assert set(Base.metadata.tables).issubset(actual_tables)


async def test_redis_token_bucket_is_atomic_under_concurrency() -> None:
    settings = Settings()
    redis_client = Redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=False,
    )
    limiter = RedisTokenBucketRateLimiter(redis_client)
    key = f"aiw:test:rate:{uuid4().hex}"
    try:
        decisions = await asyncio.gather(
            *[
                limiter.acquire(
                    key=key,
                    capacity=5,
                    window_seconds=60,
                )
                for _ in range(20)
            ]
        )
        assert sum(decision.allowed for decision in decisions) == 5
        assert all(
            decision.retry_after_seconds >= 1 for decision in decisions if not decision.allowed
        )
    finally:
        await redis_client.delete(key)
        await limiter.close()


async def test_repository_ownership_is_enforced_by_postgresql_queries() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    connection = await engine.connect()
    transaction = await connection.begin()
    try:
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            owner = User(id=uuid4())
            other_user = User(id=uuid4())
            session.add_all([owner, other_user])
            await session.flush()

            source_asset = UserAsset(
                id=uuid4(),
                user_id=owner.id,
                kind=AssetKind.USER_UPLOAD,
                status=AssetStatus.READY,
                bucket="integration-test",
                object_key=f"ownership/{uuid4().hex}/source.jpg",
            )
            result_asset = UserAsset(
                id=uuid4(),
                user_id=owner.id,
                kind=AssetKind.OPTIMIZATION_RESULT,
                status=AssetStatus.READY,
                bucket="integration-test",
                object_key=f"ownership/{uuid4().hex}/result.jpg",
            )
            share_asset = UserAsset(
                id=uuid4(),
                user_id=owner.id,
                kind=AssetKind.SHARE_DERIVATIVE,
                status=AssetStatus.READY,
                bucket="integration-test",
                object_key=f"ownership/{uuid4().hex}/share.jpg",
            )
            other_asset = UserAsset(
                id=uuid4(),
                user_id=other_user.id,
                kind=AssetKind.SHARE_DERIVATIVE,
                status=AssetStatus.READY,
                bucket="integration-test",
                object_key=f"ownership/{uuid4().hex}/other.jpg",
            )
            session.add_all([source_asset, result_asset, share_asset, other_asset])
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
                status=JobStatus.COMPLETED,
                idempotency_key=f"diagnosis-{uuid4().hex}",
                request_hash="a" * 64,
            )
            optimization_job = GenerationJob(
                id=uuid4(),
                user_id=owner.id,
                task_type=JobTaskType.STYLE_OPTIMIZATION,
                status=JobStatus.COMPLETED,
                idempotency_key=f"optimization-{uuid4().hex}",
                request_hash="b" * 64,
            )
            share_job = GenerationJob(
                id=uuid4(),
                user_id=owner.id,
                task_type=JobTaskType.SHARE_ASSET,
                status=JobStatus.COMPLETED,
                idempotency_key=f"share-{uuid4().hex}",
                request_hash="c" * 64,
            )
            other_job = GenerationJob(
                id=uuid4(),
                user_id=other_user.id,
                task_type=JobTaskType.SHARE_ASSET,
                status=JobStatus.COMPLETED,
                idempotency_key=f"other-share-{uuid4().hex}",
                request_hash="d" * 64,
            )
            session.add_all(
                [diagnosis_job, optimization_job, share_job, other_job],
            )
            await session.flush()

            diagnosis = StyleDiagnosis(
                id=uuid4(),
                user_id=owner.id,
                source_photo_id=source_photo.id,
                job_id=diagnosis_job.id,
                occasion="DAILY",
                status=DiagnosisStatus.COMPLETED,
            )
            session.add(diagnosis)
            await session.flush()

            optimization = StyleOptimizationResult(
                id=uuid4(),
                user_id=owner.id,
                diagnosis_id=diagnosis.id,
                job_id=optimization_job.id,
                result_asset_id=result_asset.id,
                status=OptimizationStatus.COMPLETED,
                change_level=1,
                change_summary=[],
            )
            session.add(optimization)
            await session.flush()

            share = ShareRecord(
                id=uuid4(),
                user_id=owner.id,
                target_type="StyleOptimizationResult",
                target_id=optimization.id,
                job_id=share_job.id,
                scene_code=uuid4().hex,
                share_asset_id=share_asset.id,
                public_payload={"ai_edited": True},
                status=ShareStatus.ACTIVE,
            )
            session.add(share)
            await session.flush()

            assets = AssetRepository(session)
            diagnoses = DiagnosisRepository(session)
            optimizations = OptimizationRepository(session)
            growth = GrowthRepository(session)
            jobs = JobRepository(session)
            deletions = DeletionRepository(session)

            assert await assets.get_owned(asset_id=source_asset.id, user_id=owner.id)
            assert await jobs.get_owned(job_id=diagnosis_job.id, user_id=owner.id)
            assert await diagnoses.get_owned(
                diagnosis_id=diagnosis.id,
                user_id=owner.id,
            )
            assert await diagnoses.get_owned_source_asset(
                diagnosis_id=diagnosis.id,
                user_id=owner.id,
            )
            owner_optimization = await optimizations.get_owned(
                optimization_id=optimization.id,
                user_id=owner.id,
            )
            assert owner_optimization is not None
            assert owner_optimization.result_asset is not None
            assert owner_optimization.result_asset.id == result_asset.id
            assert await growth.get_by_job(job_id=share_job.id, user_id=owner.id)
            owner_scene = await growth.get_scene(scene_code=share.scene_code)
            assert owner_scene is not None
            assert owner_scene.job is not None
            assert owner_scene.asset is not None

            assert (
                await assets.get_owned(
                    asset_id=source_asset.id,
                    user_id=other_user.id,
                )
                is None
            )
            assert (
                await jobs.get_owned(
                    job_id=diagnosis_job.id,
                    user_id=other_user.id,
                )
                is None
            )
            assert (
                await diagnoses.get_owned(
                    diagnosis_id=diagnosis.id,
                    user_id=other_user.id,
                )
                is None
            )
            assert (
                await diagnoses.get_owned_source_asset(
                    diagnosis_id=diagnosis.id,
                    user_id=other_user.id,
                )
                is None
            )
            assert (
                await optimizations.get_owned(
                    optimization_id=optimization.id,
                    user_id=other_user.id,
                )
                is None
            )
            assert (
                await growth.get_by_job(
                    job_id=share_job.id,
                    user_id=other_user.id,
                )
                is None
            )

            optimization.result_asset_id = other_asset.id
            share.share_asset_id = other_asset.id
            share.job_id = other_job.id
            await session.flush()

            poisoned_optimization = await optimizations.get_owned(
                optimization_id=optimization.id,
                user_id=owner.id,
            )
            assert poisoned_optimization is not None
            assert poisoned_optimization.result_asset is None
            poisoned_scene = await growth.get_scene(scene_code=share.scene_code)
            assert poisoned_scene is not None
            assert poisoned_scene.job is None
            assert poisoned_scene.asset is None

            optimization.result_asset_id = result_asset.id
            share.share_asset_id = share_asset.id
            share.job_id = share_job.id
            await session.flush()

            assert (
                await deletions.asset_deletion_plan(
                    user_id=other_user.id,
                    asset_id=source_asset.id,
                )
                is None
            )
            deletion_plan = await deletions.asset_deletion_plan(
                user_id=owner.id,
                asset_id=source_asset.id,
            )
            assert deletion_plan is not None
            assert set(deletion_plan.asset_ids) == {
                source_asset.id,
                result_asset.id,
                share_asset.id,
            }
            assert set(deletion_plan.diagnosis_ids) == {diagnosis.id}
            assert set(deletion_plan.optimization_ids) == {optimization.id}
            assert set(deletion_plan.share_ids) == {share.id}

            await deletions.purge_asset_plan(deletion_plan)
            await session.flush()

            assert (
                await assets.get_owned(
                    asset_id=source_asset.id,
                    user_id=owner.id,
                )
                is None
            )
            assert (
                await assets.get_owned(
                    asset_id=result_asset.id,
                    user_id=owner.id,
                )
                is None
            )
            assert (
                await assets.get_owned(
                    asset_id=share_asset.id,
                    user_id=owner.id,
                )
                is None
            )
            assert await assets.get_owned(
                asset_id=other_asset.id,
                user_id=other_user.id,
            )
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()
        await engine.dispose()
