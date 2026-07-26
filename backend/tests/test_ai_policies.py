import pytest

from app.core.config import Settings
from app.modules.ai.policies import (
    PolicySnapshotError,
    diagnosis_job_policy,
    diagnosis_policy,
    is_canary_cohort,
    optimization_critic_policy,
    optimization_image_policy,
    optimization_job_policies,
    policy_snapshot,
)


def test_canary_cohort_is_sticky_and_honors_boundaries() -> None:
    assert is_canary_cohort(cohort_key="user-1", percentage=0) is False
    assert is_canary_cohort(cohort_key="user-1", percentage=100) is True
    assert is_canary_cohort(
        cohort_key="user-1",
        percentage=17,
    ) == is_canary_cohort(cohort_key="user-1", percentage=17)


def test_diagnosis_job_uses_frozen_model_snapshot() -> None:
    original = Settings(diagnosis_primary_model="stable-v1")
    snapshot = policy_snapshot(diagnosis_policy(original))
    deployed = Settings(diagnosis_primary_model="stable-v2")

    resolved = diagnosis_job_policy(deployed, snapshot)

    assert resolved.routes[0].model == "stable-v1"


def test_canary_policy_changes_only_the_selected_primary() -> None:
    settings = Settings(
        ai_canary_percentage=10,
        diagnosis_canary_model="candidate",
        diagnosis_primary_model="stable",
        diagnosis_fallback_model="fallback",
    )

    selected = diagnosis_policy(settings, use_canary=True)

    assert [route.model for route in selected.routes] == ["candidate", "fallback"]


def test_policy_snapshot_rejects_unexpected_provider() -> None:
    settings = Settings()
    snapshot = policy_snapshot(diagnosis_policy(settings))
    routes = snapshot["routes"]
    assert isinstance(routes, list)
    routes[0] = {"provider": "untrusted", "model": "model"}

    with pytest.raises(PolicySnapshotError, match="route is invalid"):
        diagnosis_job_policy(settings, snapshot)


def test_optimization_snapshot_keeps_legacy_attempt_count_compatible() -> None:
    settings = Settings(optimization_max_generation_attempts=2)
    image, critic, attempts = optimization_job_policies(
        settings,
        {
            "image_edit": policy_snapshot(optimization_image_policy(settings)),
            "critic": policy_snapshot(optimization_critic_policy(settings)),
        },
    )

    assert image.routes[0].provider == "openai-image"
    assert critic.routes[0].provider == "openai"
    assert attempts == 2
