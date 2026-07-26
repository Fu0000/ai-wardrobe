"""删除闭包的数据库级验证。

`tests/test_deletion_closure.py` 用静态分析保证「每张用户数据表都写进了
purge_account」，不依赖数据库；本文件在真实 PostgreSQL 上验证「执行后确实
零残留」，并确认清理是按用户收敛而非全表清空。两层互补，缺一不可。
"""

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.database import models as database_models  # noqa: F401
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
from app.modules.events.models import OutboxEvent
from app.modules.feedback.models import BetaFeedback, FeedbackCategory
from app.modules.governance.deletion_repository import DeletionRepository
from app.modules.governance.models import (
    DeletionJob,
    DeletionStatus,
    DeletionType,
    QuotaReservation,
    QuotaReservationStatus,
    QuotaType,
    UsageCounter,
)
from app.modules.governance.purge_closure import (
    PURGE_EXEMPT_TABLES,
    USER_TABLE_NAME,
    user_owned_tables,
)
from app.modules.growth.models import (
    ShareRecord,
    ShareStatus,
    UserEvent,
    VoteChoice,
    VoteRecord,
)
from app.modules.identity.models import (
    IdentityProvider,
    User,
    UserIdentity,
    UserProfile,
    UserStatus,
)
from app.modules.jobs.models import (
    AIInvocation,
    GenerationJob,
    InvocationStatus,
    JobStatus,
    JobTaskType,
)

RUN_INTEGRATION_TESTS = os.getenv("AIW_RUN_INTEGRATION_TESTS") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION_TESTS,
        reason="set AIW_RUN_INTEGRATION_TESTS=1 with disposable PostgreSQL and Redis",
    ),
]


async def test_purge_account_leaves_no_user_owned_rows() -> None:
    """注销后，除登记豁免外的每张用户数据表都必须零残留。"""

    settings = Settings()
    engine = create_async_engine(settings.database_url)
    connection = await engine.connect()
    transaction = await connection.begin()
    try:
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            owner = User(id=uuid4())
            bystander = User(id=uuid4())
            session.add_all([owner, bystander])
            await session.flush()

            deletion_job, deletion_generation_job = await _populate(
                session,
                owner=owner,
                bystander=bystander,
            )

            owned = user_owned_tables()
            # 先证明数据真的造出来了，否则「清理后为空」会退化成空转。
            populated = await _tables_with_rows(session, owned=owned, user_id=owner.id)
            missing = set(owned) - populated
            assert not missing, f"这些表在 purge 前无数据，无法证明它们被清理：{sorted(missing)}"

            await DeletionRepository(session).purge_account(
                user_id=owner.id,
                keep_deletion_id=deletion_job.id,
                keep_generation_job_id=deletion_generation_job.id,
            )
            await session.flush()

            residual = await _tables_with_rows(session, owned=owned, user_id=owner.id)
            leaked = residual - set(PURGE_EXEMPT_TABLES)
            assert not leaked, (
                f"注销后仍残留用户数据：{sorted(leaked)}；"
                "请把这些表补进 purge_account，而不是加入 PURGE_EXEMPT_TABLES。"
            )

            # 豁免表必须真的留存，否则说明豁免登记与实现已脱节。
            assert residual == set(PURGE_EXEMPT_TABLES)

            # 用户主行保留为墓碑，标识列已随 user_identities 一并删除。
            tombstone = await session.get(User, owner.id)
            assert tombstone is not None
            assert tombstone.status is UserStatus.DELETED

            # 旁观者数据完好，证明清理按用户收敛而非全表清空。
            bystander_rows = await _tables_with_rows(
                session,
                owned=owned,
                user_id=bystander.id,
            )
            assert {"beta_feedback", "user_identities", "user_profiles"} <= bystander_rows
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()
        await engine.dispose()


async def test_beta_feedback_is_removed_on_account_purge() -> None:
    """回归锁定：反馈内容含用户自由文本，注销后不得留存。"""

    settings = Settings()
    engine = create_async_engine(settings.database_url)
    connection = await engine.connect()
    transaction = await connection.begin()
    try:
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            owner = User(id=uuid4())
            bystander = User(id=uuid4())
            session.add_all([owner, bystander])
            await session.flush()

            deletion_job, deletion_generation_job = await _populate(
                session,
                owner=owner,
                bystander=bystander,
            )

            await DeletionRepository(session).purge_account(
                user_id=owner.id,
                keep_deletion_id=deletion_job.id,
                keep_generation_job_id=deletion_generation_job.id,
            )
            await session.flush()

            result = await session.execute(
                text("SELECT count(*) FROM beta_feedback WHERE user_id = :user_id"),
                {"user_id": owner.id},
            )
            assert result.scalar_one() == 0

            result = await session.execute(
                text("SELECT count(*) FROM beta_feedback WHERE user_id = :user_id"),
                {"user_id": bystander.id},
            )
            assert result.scalar_one() == 1
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()
        await engine.dispose()


async def _tables_with_rows(
    session: AsyncSession,
    *,
    owned: dict[str, str],
    user_id: UUID,
) -> set[str]:
    """返回该用户在哪些表中仍有行。表名与列名来自模型元数据，非外部输入。"""

    present: set[str] = set()
    for table_name, column_name in sorted(owned.items()):
        statement = text(
            f"SELECT 1 FROM {table_name} WHERE {column_name} = :user_id LIMIT 1"  # noqa: S608
        )
        result = await session.execute(statement, {"user_id": user_id})
        if result.first() is not None:
            present.add(table_name)
    return present


async def _populate(
    session: AsyncSession,
    *,
    owner: User,
    bystander: User,
) -> tuple[DeletionJob, GenerationJob]:
    """为每张用户数据表造出至少一行，返回需在 purge 后存活的注销任务。"""

    for index, user in enumerate((owner, bystander)):
        session.add(
            UserIdentity(
                id=uuid4(),
                user_id=user.id,
                provider=IdentityProvider.WECHAT,
                provider_subject_hash=uuid4().hex,
                provider_subject_encrypted="ciphertext",
            )
        )
        session.add(
            UserProfile(
                id=uuid4(),
                user_id=user.id,
                display_name=f"tester-{index}",
                has_ai_processing_consent=True,
            )
        )
        session.add(
            UsageCounter(
                id=uuid4(),
                user_id=user.id,
                quota_type=QuotaType.DIAGNOSIS,
                period_key="2026-07",
                used=1,
            )
        )
        session.add(
            BetaFeedback(
                id=uuid4(),
                user_id=user.id,
                category=FeedbackCategory.AI_QUALITY,
                message="诊断结果与实际穿搭不符。",
                idempotency_key=f"feedback-{uuid4().hex}",
                request_hash="d" * 64,
            )
        )
    await session.flush()

    source_asset = UserAsset(
        id=uuid4(),
        user_id=owner.id,
        kind=AssetKind.USER_UPLOAD,
        status=AssetStatus.READY,
        bucket="integration-test",
        object_key=f"purge/{uuid4().hex}/source.jpg",
    )
    share_asset = UserAsset(
        id=uuid4(),
        user_id=owner.id,
        kind=AssetKind.SHARE_DERIVATIVE,
        status=AssetStatus.READY,
        bucket="integration-test",
        object_key=f"purge/{uuid4().hex}/share.jpg",
    )
    session.add_all([source_asset, share_asset])
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
    deletion_generation_job = GenerationJob(
        id=uuid4(),
        user_id=owner.id,
        task_type=JobTaskType.DELETION,
        status=JobStatus.PROCESSING,
        idempotency_key=f"deletion-{uuid4().hex}",
        request_hash="f" * 64,
    )
    session.add_all([diagnosis_job, optimization_job, share_job, deletion_generation_job])
    await session.flush()

    # ai_invocations 与 quota_reservations 挂在会被保留的注销 Job 上，
    # 用以验证它们不依赖 generation_jobs 级联、而是被显式按用户清理。
    session.add(
        AIInvocation(
            id=uuid4(),
            job_id=deletion_generation_job.id,
            user_id=owner.id,
            status=InvocationStatus.SUCCEEDED,
            provider="openai",
            model="gpt-5.6-terra",
            prompt_version="v1",
            schema_version="v1",
            started_at=datetime.now(UTC),
        )
    )
    session.add(
        QuotaReservation(
            id=uuid4(),
            user_id=owner.id,
            job_id=deletion_generation_job.id,
            quota_type=QuotaType.DIAGNOSIS,
            status=QuotaReservationStatus.RESERVED,
            amount=1,
            period_keys=["2026-07"],
        )
    )

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

    session.add(
        VoteRecord(
            id=uuid4(),
            share_id=share.id,
            voter_user_id=owner.id,
            choice=VoteChoice.AFTER,
            voter_fingerprint_hash=uuid4().hex,
        )
    )
    session.add(
        UserEvent(
            id=uuid4(),
            user_id=owner.id,
            event_name="share.asset.created",
            entity_type="ShareRecord",
            entity_id=share.id,
            dedupe_key=uuid4().hex,
        )
    )
    session.add(
        OutboxEvent(
            id=uuid4(),
            aggregate_type="GenerationJob",
            aggregate_id=diagnosis_job.id,
            event_type="GenerationJobCreated",
            payload={"job_id": str(diagnosis_job.id)},
        )
    )

    deletion_job = DeletionJob(
        id=uuid4(),
        user_id=owner.id,
        job_id=deletion_generation_job.id,
        deletion_type=DeletionType.ACCOUNT,
        status=DeletionStatus.PROCESSING,
    )
    session.add(deletion_job)
    await session.flush()

    assert USER_TABLE_NAME not in user_owned_tables()
    return deletion_job, deletion_generation_job
