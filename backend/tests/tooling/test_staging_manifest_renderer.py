import stat
from pathlib import Path

import pytest

from scripts.deployment.staging_manifests import (
    ManifestRenderError,
    StagingManifestConfig,
    parse_controller_version,
    render_staging_manifests,
    write_private_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = """
image: AIW_IMAGE_PLACEHOLDER
annotation: AIW_STAGING_EDGE_CLB_ID_PLACEHOLDER
certificate: AIW_STAGING_TLS_CERT_ID_PLACEHOLDER
certificate-host: AIW_STAGING_API_HOST_PLACEHOLDER
rule-host: AIW_STAGING_API_HOST_PLACEHOLDER
"""


def config(**overrides: str) -> StagingManifestConfig:
    values = {
        "image": f"ghcr.io/example/ai-wardrobe-api:{'a' * 40}",
        "api_host": "api.staging.example.com",
        "api_base_url": "https://api.staging.example.com",
        "tls_certificate_id": "DSAuy46E",
        "edge_clb_id": "lb-6swtxxxx",
        "ingress_controller_version": "v2.11.0",
    }
    values.update(overrides)
    return StagingManifestConfig(**values)


def test_renderer_replaces_only_validated_release_values(tmp_path: Path) -> None:
    rendered = render_staging_manifests(TEMPLATE, config())
    assert "PLACEHOLDER" not in rendered
    assert "api.staging.example.com" in rendered
    assert "lb-6swtxxxx" in rendered

    output = tmp_path / "staging.yaml"
    write_private_manifest(output, rendered)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(ManifestRenderError, match="already exists"):
        write_private_manifest(output, rendered)


def test_repository_staging_overlay_matches_renderer_contract() -> None:
    template = "\n---\n".join(
        (
            (REPO_ROOT / "infra" / "k8s" / "base" / "api.yaml").read_text(encoding="utf-8"),
            (REPO_ROOT / "infra" / "k8s" / "base" / "workers.yaml").read_text(encoding="utf-8"),
            (REPO_ROOT / "infra" / "k8s" / "staging" / "ingress.yaml").read_text(encoding="utf-8"),
        )
    )

    rendered = render_staging_manifests(template, config())

    assert "kubernetes.io/ingress.class: qcloud" in rendered
    assert "ingress.cloud.tencent.com/auto-rewrite-code" in rendered
    assert '{"defaultRewriteCode":307}' in rendered
    assert "ingress.cloud.tencent.com/deletion-protection" in rendered
    assert "AIW_" not in rendered


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"api_host": "*.example.com"}, "DNS label"),
        ({"api_host": "127.0.0.1"}, "not an IP"),
        ({"api_base_url": "http://api.staging.example.com"}, "exact HTTPS origin"),
        ({"api_base_url": "https://other.example.com"}, "exact HTTPS origin"),
        ({"api_base_url": "https://api.staging.example.com:bad"}, "malformed"),
        ({"tls_certificate_id": "cert']; injected"}, "certificate ID"),
        ({"edge_clb_id": "sg-not-a-clb"}, "CLB ID"),
        ({"image": "ghcr.io/example/api:latest"}, "40-character Git SHA"),
    ],
)
def test_renderer_rejects_injection_and_edge_mismatch(
    override: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ManifestRenderError, match=message):
        render_staging_manifests(TEMPLATE, config(**override))


def test_renderer_rejects_old_controller_and_template_drift() -> None:
    with pytest.raises(ManifestRenderError, match=r"at least v2\.11\.0"):
        parse_controller_version("v2.10.9")
    with pytest.raises(ManifestRenderError, match="placeholder count changed"):
        render_staging_manifests(
            TEMPLATE.replace("rule-host: AIW_STAGING_API_HOST_PLACEHOLDER\n", ""),
            config(),
        )
    with pytest.raises(ManifestRenderError, match="unresolved"):
        render_staging_manifests(
            f"{TEMPLATE}\nunknown: AIW_UNKNOWN_PLACEHOLDER\n",
            config(),
        )
