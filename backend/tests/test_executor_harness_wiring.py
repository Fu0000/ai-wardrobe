from pathlib import Path

EXECUTOR_PATHS = (
    "modules/diagnosis/executor.py",
    "modules/optimization/executor.py",
    "modules/growth/executor.py",
    "modules/governance/deletion_executor.py",
)


def test_all_business_executors_delegate_fencing_to_shared_harness() -> None:
    app_root = Path(__file__).parents[1] / "app"
    for relative_path in EXECUTOR_PATHS:
        source = (app_root / relative_path).read_text()
        assert "JobExecutionHarness" in source
        assert "self._execution.claim(" in source
        assert "self._execution.finalize_failure(" in source
        assert "self._execution.mark_retryable(" in source
        assert "self._execution.complete(" in source
        assert "execution_lease_expires_at" not in source
        assert "STALE_EXECUTION_RECOVERED" not in source
