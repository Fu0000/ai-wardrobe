import pytest

from scripts.smoke_mvp import SmokeError, should_trust_environment, validate_base_url


def test_local_smoke_bypasses_environment_proxies() -> None:
    assert not should_trust_environment("http://127.0.0.1:8000")
    assert not should_trust_environment("http://localhost:8000")
    assert not should_trust_environment("http://[::1]:8000")
    assert should_trust_environment("https://staging.example.com")


def test_smoke_base_url_requires_https_outside_localhost() -> None:
    assert validate_base_url("http://localhost:8000/") == "http://localhost:8000"
    assert validate_base_url("https://staging.example.com/") == "https://staging.example.com"

    with pytest.raises(SmokeError):
        validate_base_url("http://staging.example.com")
