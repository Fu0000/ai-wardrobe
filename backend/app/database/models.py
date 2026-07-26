"""Import all ORM models so Alembic receives complete metadata."""

from app.modules.assets.models import SourcePhoto, UserAsset
from app.modules.diagnosis.models import StyleDiagnosis, StyleOptimizationResult
from app.modules.events.models import OutboxEvent
from app.modules.feedback.models import BetaFeedback
from app.modules.governance.models import (
    DeletionJob,
    QuotaPolicy,
    QuotaReservation,
    UsageCounter,
)
from app.modules.growth.models import ShareRecord, UserEvent, VoteRecord
from app.modules.identity.models import User, UserIdentity, UserProfile
from app.modules.jobs.models import AIInvocation, GenerationJob

__all__ = [
    "AIInvocation",
    "BetaFeedback",
    "DeletionJob",
    "GenerationJob",
    "OutboxEvent",
    "QuotaPolicy",
    "QuotaReservation",
    "ShareRecord",
    "SourcePhoto",
    "StyleDiagnosis",
    "StyleOptimizationResult",
    "UsageCounter",
    "User",
    "UserAsset",
    "UserEvent",
    "UserIdentity",
    "UserProfile",
    "VoteRecord",
]
