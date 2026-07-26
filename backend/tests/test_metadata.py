from app.database import models as database_models  # noqa: F401
from app.database.base import Base

EXPECTED_P0_TABLES = {
    "ai_invocations",
    "beta_feedback",
    "deletion_jobs",
    "generation_jobs",
    "outbox_events",
    "quota_policies",
    "quota_reservations",
    "share_records",
    "source_photos",
    "style_diagnoses",
    "style_optimization_results",
    "usage_counters",
    "user_assets",
    "user_events",
    "user_identities",
    "user_profiles",
    "users",
    "vote_records",
}


def test_p0_metadata_contains_required_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_P0_TABLES


def test_all_user_resources_have_user_scoping_column() -> None:
    scoped_tables = {
        "ai_invocations",
        "beta_feedback",
        "deletion_jobs",
        "generation_jobs",
        "quota_reservations",
        "share_records",
        "source_photos",
        "style_diagnoses",
        "style_optimization_results",
        "usage_counters",
        "user_assets",
        "user_events",
    }

    for table_name in scoped_tables:
        assert "user_id" in Base.metadata.tables[table_name].columns
