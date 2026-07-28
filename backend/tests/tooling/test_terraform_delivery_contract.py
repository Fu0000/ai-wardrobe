import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_terraform_plan_entrypoint_fails_closed_without_confirmation(
    tmp_path: Path,
) -> None:
    environment = {
        "PATH": os.environ["PATH"],
        "AIW_LOG_DIR": str(tmp_path),
    }

    completed = subprocess.run(  # noqa: S603 - target is a repository-owned script
        [REPO_ROOT / "scripts" / "infra.sh", "terraform-staging-plan"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode != 0
    assert "AIW_TERRAFORM_PLAN_CONFIRMATION" in (completed.stdout + completed.stderr)


def test_cloud_plan_workflow_is_read_only_and_ephemeral() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "terraform-staging-plan.yml").read_text(
        encoding="utf-8"
    )

    assert "environment: staging-infrastructure-plan" in workflow
    assert 'test "$GITHUB_REF_NAME" = "develop"' in workflow
    assert "I_ACKNOWLEDGE_STAGING_PLAN_READ_ONLY" in workflow
    assert "terraform apply" not in workflow.casefold()
    assert "actions/upload-artifact" not in workflow
    assert '"$RUNNER_TEMP/aiw-terraform-plan/staging.tfplan"' in workflow


def test_terraform_provider_lock_covers_ci_runner_platform() -> None:
    lock_file = (REPO_ROOT / "infra" / "terraform" / "staging" / ".terraform.lock.hcl").read_text(
        encoding="utf-8"
    )

    # Terraform verifies an unpacked provider with the platform-specific h1
    # checksum. Keep the linux_amd64 hash used by GitHub-hosted runners in
    # addition to the linux_arm64 hash used by Apple Silicon Docker Desktop.
    assert '"h1:0mHRI8e7JNtgjI5e0MMPMlwt8+nSNjvtrZLVrXSgzMY="' in lock_file
    assert '"h1:JyT7WfhGyKqHmYzkhZs7h8IBo9kvstQ4X0VtqTLe55w="' in lock_file


def test_private_tke_workflows_require_the_vpc_runner() -> None:
    for workflow_name in ("deploy-staging.yml", "ai-canary-staging.yml"):
        workflow = (REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")

        assert "self-hosted" in workflow
        assert "ai-wardrobe-staging" in workflow
        assert "runs-on: ubuntu-latest" not in workflow
        assert '"$RUNNER_TEMP/kubeconfig"' in workflow


def test_staging_deploy_requires_fixed_https_edge_and_server_dry_run() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "deploy-staging.yml").read_text(
        encoding="utf-8"
    )

    for marker in (
        "infra/k8s/staging",
        "tke-ingress-controller-config",
        "STAGING_API_HOST",
        "STAGING_TLS_CERT_ID",
        "STAGING_EDGE_CLB_ID",
        "staging_manifests.py",
        "--dry-run=server",
        "--proto '=https'",
        "--tlsv1.2",
        'test "$redirect_code" = "307"',
    ):
        assert marker in workflow
    assert "tr '[:upper:]' '[:lower:]'" in workflow
    assert "ghcr.io/${{ github.repository_owner }}" not in workflow
