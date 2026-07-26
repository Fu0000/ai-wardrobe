from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.governance.models import (
    QuotaPeriod,
    QuotaPolicy,
    QuotaReservation,
    QuotaReservationStatus,
    QuotaType,
    UsageCounter,
)
from app.modules.jobs.models import GenerationJob


class QuotaError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class QuotaExceededError(QuotaError):
    def __init__(self, *, period: QuotaPeriod, limit: int) -> None:
        super().__init__("QUOTA_EXCEEDED")
        self.period = period
        self.limit = limit


@dataclass(frozen=True, slots=True)
class QuotaReservationResult:
    reservation_id: UUID
    status: QuotaReservationStatus
    remaining: int


def period_key(period: QuotaPeriod, at: datetime) -> str:
    instant = at.astimezone(UTC)
    if period == QuotaPeriod.DAILY:
        return instant.strftime("%Y-%m-%d")
    if period == QuotaPeriod.MONTHLY:
        return instant.strftime("%Y-%m")
    return "lifetime"


class QuotaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(
        self,
        *,
        user_id: UUID,
        job_id: UUID,
        quota_type: QuotaType,
        plan: str = "FREE",
        amount: int = 1,
        now: datetime | None = None,
    ) -> QuotaReservationResult:
        if amount <= 0:
            raise ValueError("quota reservation amount must be positive")
        reserved_at = now or datetime.now(UTC)

        job_result = await self._session.execute(
            select(GenerationJob)
            .where(
                GenerationJob.id == job_id,
                GenerationJob.user_id == user_id,
            )
            .with_for_update()
        )
        if job_result.scalar_one_or_none() is None:
            raise QuotaError("JOB_NOT_FOUND")

        existing = await self._reservation_for_job(job_id, for_update=True)
        if existing is not None:
            if (
                existing.user_id != user_id
                or existing.quota_type != quota_type
                or existing.amount != amount
            ):
                raise QuotaError("QUOTA_RESERVATION_CONFLICT")
            return QuotaReservationResult(
                reservation_id=existing.id,
                status=existing.status,
                remaining=await self._remaining(existing),
            )

        policies = await self._active_policies(
            plan=plan,
            quota_type=quota_type,
            now=reserved_at,
        )
        if not policies:
            raise QuotaError("QUOTA_POLICY_NOT_FOUND")

        period_limits = {period_key(policy.period, reserved_at): policy for policy in policies}
        ordered_keys = sorted(period_limits)
        for key in ordered_keys:
            await self._session.execute(
                insert(UsageCounter)
                .values(
                    id=uuid4(),
                    user_id=user_id,
                    quota_type=quota_type,
                    period_key=key,
                    used=0,
                    reserved=0,
                    version=0,
                )
                .on_conflict_do_nothing(constraint="uq_usage_counters_user_quota_period")
            )

        counters = await self._locked_counters(
            user_id=user_id,
            quota_type=quota_type,
            period_keys=ordered_keys,
        )
        if len(counters) != len(ordered_keys):
            raise QuotaError("QUOTA_COUNTER_INCOMPLETE")

        remaining_values: list[int] = []
        for counter in counters:
            policy = period_limits[counter.period_key]
            remaining = policy.limit_value - counter.used - counter.reserved
            if remaining < amount:
                raise QuotaExceededError(
                    period=policy.period,
                    limit=policy.limit_value,
                )
            remaining_values.append(remaining - amount)

        reservation = QuotaReservation(
            id=uuid4(),
            user_id=user_id,
            job_id=job_id,
            quota_type=quota_type,
            status=QuotaReservationStatus.RESERVED,
            amount=amount,
            period_keys=ordered_keys,
        )
        self._session.add(reservation)
        for counter in counters:
            counter.reserved += amount
            counter.version += 1
        await self._session.flush()

        return QuotaReservationResult(
            reservation_id=reservation.id,
            status=reservation.status,
            remaining=min(remaining_values),
        )

    async def commit(
        self,
        *,
        job_id: UUID,
        now: datetime | None = None,
    ) -> QuotaReservationResult:
        return await self._settle(
            job_id=job_id,
            target=QuotaReservationStatus.COMMITTED,
            now=now,
        )

    async def release(
        self,
        *,
        job_id: UUID,
        now: datetime | None = None,
    ) -> QuotaReservationResult:
        return await self._settle(
            job_id=job_id,
            target=QuotaReservationStatus.RELEASED,
            now=now,
        )

    async def _settle(
        self,
        *,
        job_id: UUID,
        target: QuotaReservationStatus,
        now: datetime | None,
    ) -> QuotaReservationResult:
        settled_at = now or datetime.now(UTC)
        reservation = await self._reservation_for_job(job_id, for_update=True)
        if reservation is None:
            raise QuotaError("QUOTA_RESERVATION_NOT_FOUND")
        if reservation.status == target:
            return QuotaReservationResult(
                reservation_id=reservation.id,
                status=reservation.status,
                remaining=await self._remaining(reservation),
            )
        if reservation.status != QuotaReservationStatus.RESERVED:
            raise QuotaError("QUOTA_RESERVATION_ALREADY_SETTLED")

        counters = await self._locked_counters(
            user_id=reservation.user_id,
            quota_type=reservation.quota_type,
            period_keys=reservation.period_keys,
        )
        if len(counters) != len(reservation.period_keys):
            raise QuotaError("QUOTA_COUNTER_INCOMPLETE")

        for counter in counters:
            if counter.reserved < reservation.amount:
                raise QuotaError("QUOTA_COUNTER_UNDERFLOW")
            counter.reserved -= reservation.amount
            if target == QuotaReservationStatus.COMMITTED:
                counter.used += reservation.amount
            counter.version += 1

        reservation.status = target
        if target == QuotaReservationStatus.COMMITTED:
            reservation.committed_at = settled_at
        else:
            reservation.released_at = settled_at
        await self._session.flush()
        return QuotaReservationResult(
            reservation_id=reservation.id,
            status=reservation.status,
            remaining=await self._remaining(reservation, counters=counters),
        )

    async def _active_policies(
        self,
        *,
        plan: str,
        quota_type: QuotaType,
        now: datetime,
    ) -> list[QuotaPolicy]:
        result = await self._session.execute(
            select(QuotaPolicy)
            .where(
                QuotaPolicy.plan == plan,
                QuotaPolicy.quota_type == quota_type,
                or_(QuotaPolicy.active_from.is_(None), QuotaPolicy.active_from <= now),
                or_(QuotaPolicy.active_until.is_(None), QuotaPolicy.active_until > now),
            )
            .order_by(QuotaPolicy.period)
        )
        return list(result.scalars())

    async def _locked_counters(
        self,
        *,
        user_id: UUID,
        quota_type: QuotaType,
        period_keys: list[str],
    ) -> list[UsageCounter]:
        result = await self._session.execute(
            select(UsageCounter)
            .where(
                UsageCounter.user_id == user_id,
                UsageCounter.quota_type == quota_type,
                UsageCounter.period_key.in_(period_keys),
            )
            .order_by(UsageCounter.period_key)
            .with_for_update()
        )
        return list(result.scalars())

    async def _reservation_for_job(
        self,
        job_id: UUID,
        *,
        for_update: bool,
    ) -> QuotaReservation | None:
        statement = select(QuotaReservation).where(QuotaReservation.job_id == job_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def _remaining(
        self,
        reservation: QuotaReservation,
        *,
        counters: list[UsageCounter] | None = None,
    ) -> int:
        locked = counters or await self._locked_counters(
            user_id=reservation.user_id,
            quota_type=reservation.quota_type,
            period_keys=reservation.period_keys,
        )
        policies = await self._active_policies(
            plan="FREE",
            quota_type=reservation.quota_type,
            now=datetime.now(UTC),
        )
        limits = {policy.period: policy.limit_value for policy in policies}
        remaining_values: list[int] = []
        for counter in locked:
            counter_period = (
                QuotaPeriod.DAILY
                if len(counter.period_key) == 10
                else (QuotaPeriod.MONTHLY if len(counter.period_key) == 7 else QuotaPeriod.LIFETIME)
            )
            limit = limits.get(counter_period)
            if limit is not None:
                remaining_values.append(limit - counter.used - counter.reserved)
        return min(remaining_values) if remaining_values else 0
