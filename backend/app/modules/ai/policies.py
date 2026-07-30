import hashlib
from collections.abc import Mapping

from app.core.config import Settings
from app.modules.ai.contracts import ModelRoute, TaskPolicy
from app.modules.ai.local_provider import (
    LOCAL_CRITIC_MODEL,
    LOCAL_DIAGNOSIS_MODEL,
    LOCAL_IMAGE_MODEL,
    LOCAL_IMAGE_PROVIDER_NAME,
    LOCAL_STRUCTURED_PROVIDER_NAME,
)


class PolicySnapshotError(Exception):
    pass


def is_canary_cohort(*, cohort_key: str, percentage: int) -> bool:
    if percentage <= 0:
        return False
    if percentage >= 100:
        return True
    digest = hashlib.sha256(cohort_key.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], byteorder="big") % 100
    return bucket < percentage


def diagnosis_policy(
    settings: Settings,
    *,
    use_canary: bool = False,
) -> TaskPolicy:
    if settings.local_ai_enabled:
        return TaskPolicy(
            routes=(
                ModelRoute(
                    provider=LOCAL_STRUCTURED_PROVIDER_NAME,
                    model=LOCAL_DIAGNOSIS_MODEL,
                ),
            ),
            timeout_seconds=settings.diagnosis_timeout_seconds,
            cost_ceiling_microunits=settings.diagnosis_cost_ceiling_microunits,
            quality_threshold=0.8,
        )
    primary_model = (
        settings.diagnosis_canary_model
        if use_canary and settings.diagnosis_canary_model
        else settings.diagnosis_primary_model
    )
    return TaskPolicy(
        routes=(
            ModelRoute(provider="openai", model=primary_model),
            ModelRoute(provider="openai", model=settings.diagnosis_fallback_model),
        ),
        timeout_seconds=settings.diagnosis_timeout_seconds,
        cost_ceiling_microunits=settings.diagnosis_cost_ceiling_microunits,
        quality_threshold=0.8,
    )


def optimization_image_policy(
    settings: Settings,
    *,
    use_canary: bool = False,
) -> TaskPolicy:
    if settings.local_ai_enabled:
        return TaskPolicy(
            routes=(
                ModelRoute(
                    provider=LOCAL_IMAGE_PROVIDER_NAME,
                    model=LOCAL_IMAGE_MODEL,
                ),
            ),
            timeout_seconds=settings.optimization_image_timeout_seconds,
            cost_ceiling_microunits=settings.optimization_image_cost_ceiling_microunits,
            quality_threshold=0.8,
        )
    model = (
        settings.optimization_image_canary_model
        if use_canary and settings.optimization_image_canary_model
        else settings.optimization_image_model
    )
    return TaskPolicy(
        routes=(ModelRoute(provider="openai-image", model=model),),
        timeout_seconds=settings.optimization_image_timeout_seconds,
        cost_ceiling_microunits=settings.optimization_image_cost_ceiling_microunits,
        quality_threshold=0.8,
    )


def optimization_critic_policy(
    settings: Settings,
    *,
    use_canary: bool = False,
) -> TaskPolicy:
    if settings.local_ai_enabled:
        return TaskPolicy(
            routes=(
                ModelRoute(
                    provider=LOCAL_STRUCTURED_PROVIDER_NAME,
                    model=LOCAL_CRITIC_MODEL,
                ),
            ),
            timeout_seconds=settings.optimization_critic_timeout_seconds,
            cost_ceiling_microunits=settings.optimization_critic_cost_ceiling_microunits,
            quality_threshold=0.8,
        )
    primary_model = (
        settings.optimization_critic_canary_model
        if use_canary and settings.optimization_critic_canary_model
        else settings.optimization_critic_primary_model
    )
    return TaskPolicy(
        routes=(
            ModelRoute(
                provider="openai",
                model=primary_model,
            ),
            ModelRoute(
                provider="openai",
                model=settings.optimization_critic_fallback_model,
            ),
        ),
        timeout_seconds=settings.optimization_critic_timeout_seconds,
        cost_ceiling_microunits=settings.optimization_critic_cost_ceiling_microunits,
        quality_threshold=0.8,
    )


def policy_snapshot(policy: TaskPolicy) -> dict[str, object]:
    return {
        "routes": [{"provider": route.provider, "model": route.model} for route in policy.routes],
        "timeout_seconds": policy.timeout_seconds,
        "cost_ceiling_microunits": policy.cost_ceiling_microunits,
        "quality_threshold": policy.quality_threshold,
    }


def policy_from_snapshot(
    snapshot: object,
    *,
    fallback: TaskPolicy,
    allowed_providers: frozenset[str],
    max_routes: int,
) -> TaskPolicy:
    if snapshot is None:
        return fallback
    if not isinstance(snapshot, Mapping):
        raise PolicySnapshotError("policy snapshot must be an object")

    raw_routes = snapshot.get("routes")
    if not isinstance(raw_routes, list) or not 1 <= len(raw_routes) <= max_routes:
        raise PolicySnapshotError("policy snapshot routes are invalid")
    routes: list[ModelRoute] = []
    for raw_route in raw_routes:
        if not isinstance(raw_route, Mapping):
            raise PolicySnapshotError("policy snapshot route must be an object")
        provider = raw_route.get("provider")
        model = raw_route.get("model")
        if (
            not isinstance(provider, str)
            or provider not in allowed_providers
            or not isinstance(model, str)
            or not 1 <= len(model) <= 160
        ):
            raise PolicySnapshotError("policy snapshot route is invalid")
        routes.append(ModelRoute(provider=provider, model=model))

    timeout_seconds = snapshot.get("timeout_seconds")
    cost_ceiling = snapshot.get("cost_ceiling_microunits")
    quality_threshold = snapshot.get("quality_threshold")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int | float)
        or not 0 < timeout_seconds <= 600
    ):
        raise PolicySnapshotError("policy snapshot timeout is invalid")
    if (
        isinstance(cost_ceiling, bool)
        or not isinstance(cost_ceiling, int)
        or not 0 <= cost_ceiling <= 10_000_000
    ):
        raise PolicySnapshotError("policy snapshot cost ceiling is invalid")
    if (
        isinstance(quality_threshold, bool)
        or not isinstance(quality_threshold, int | float)
        or not 0 <= quality_threshold <= 1
    ):
        raise PolicySnapshotError("policy snapshot quality threshold is invalid")

    return TaskPolicy(
        routes=tuple(routes),
        timeout_seconds=float(timeout_seconds),
        cost_ceiling_microunits=cost_ceiling,
        quality_threshold=float(quality_threshold),
    )


def diagnosis_job_policy(
    settings: Settings,
    snapshot: object,
) -> TaskPolicy:
    return policy_from_snapshot(
        snapshot,
        fallback=diagnosis_policy(settings),
        allowed_providers=frozenset({"openai", LOCAL_STRUCTURED_PROVIDER_NAME}),
        max_routes=2,
    )


def optimization_job_policies(
    settings: Settings,
    snapshot: object,
) -> tuple[TaskPolicy, TaskPolicy, int]:
    if snapshot is None:
        return (
            optimization_image_policy(settings),
            optimization_critic_policy(settings),
            settings.optimization_max_generation_attempts,
        )
    if not isinstance(snapshot, Mapping):
        raise PolicySnapshotError("optimization policy snapshot must be an object")

    attempts = snapshot.get(
        "max_generation_attempts",
        settings.optimization_max_generation_attempts,
    )
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 3:
        raise PolicySnapshotError("optimization generation attempts are invalid")
    if "image_edit" not in snapshot or "critic" not in snapshot:
        raise PolicySnapshotError("optimization policy snapshot is incomplete")
    image_policy = policy_from_snapshot(
        snapshot.get("image_edit"),
        fallback=optimization_image_policy(settings),
        allowed_providers=frozenset({"openai-image", LOCAL_IMAGE_PROVIDER_NAME}),
        max_routes=1,
    )
    critic_policy = policy_from_snapshot(
        snapshot.get("critic"),
        fallback=optimization_critic_policy(settings),
        allowed_providers=frozenset({"openai", LOCAL_STRUCTURED_PROVIDER_NAME}),
        max_routes=2,
    )
    return image_policy, critic_policy, attempts
