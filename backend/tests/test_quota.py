from datetime import UTC, datetime

from app.modules.governance.models import QuotaPeriod
from app.modules.governance.quota import period_key


def test_quota_period_keys_use_utc_boundaries() -> None:
    instant = datetime.fromisoformat("2026-08-01T00:30:00+08:00")

    assert period_key(QuotaPeriod.DAILY, instant) == "2026-07-31"
    assert period_key(QuotaPeriod.MONTHLY, instant) == "2026-07"
    assert period_key(QuotaPeriod.LIFETIME, instant) == "lifetime"


def test_quota_period_key_is_stable_for_utc_input() -> None:
    instant = datetime(2026, 7, 26, 10, 30, tzinfo=UTC)

    assert period_key(QuotaPeriod.DAILY, instant) == "2026-07-26"
    assert period_key(QuotaPeriod.MONTHLY, instant) == "2026-07"
