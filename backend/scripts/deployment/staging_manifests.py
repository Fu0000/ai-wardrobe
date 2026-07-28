from __future__ import annotations

import argparse
import ipaddress
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class ManifestRenderError(ValueError):
    """Raised when a Staging manifest input violates the release contract."""


@dataclass(frozen=True, slots=True)
class StagingManifestConfig:
    image: str
    api_host: str
    api_base_url: str
    tls_certificate_id: str
    edge_clb_id: str
    ingress_controller_version: str


PLACEHOLDER_COUNTS = {
    "AIW_IMAGE_PLACEHOLDER": None,
    "AIW_STAGING_API_HOST_PLACEHOLDER": 2,
    "AIW_STAGING_TLS_CERT_ID_PLACEHOLDER": 1,
    "AIW_STAGING_EDGE_CLB_ID_PLACEHOLDER": 1,
}
PLACEHOLDER_PATTERN = re.compile(r"\bAIW_[A-Z0-9_]+_PLACEHOLDER\b")
IMAGE_PATTERN = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9._/-]*:[0-9a-f]{40}$")
CERTIFICATE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
CLB_ID_PATTERN = re.compile(r"^lb-[a-z0-9]{8,}$")
DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
VERSION_PATTERN = re.compile(r"^v?([0-9]+)\.([0-9]+)\.([0-9]+)(?:[-+].*)?$")
MINIMUM_INGRESS_CONTROLLER_VERSION = (2, 11, 0)


def validate_dns_host(host: str) -> None:
    if host != host.lower() or len(host) > 253 or host.endswith("."):
        raise ManifestRenderError("Staging API host must be a lowercase absolute DNS name")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ManifestRenderError("Staging API host must be a DNS name, not an IP address")
    labels = host.split(".")
    if len(labels) < 2 or not all(DNS_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise ManifestRenderError("Staging API host contains an invalid DNS label")


def parse_controller_version(version: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ManifestRenderError("TKE Ingress Controller version is not semantic")
    parsed = (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
    )
    if parsed < MINIMUM_INGRESS_CONTROLLER_VERSION:
        raise ManifestRenderError("TKE Ingress Controller must be at least v2.11.0")
    return parsed


def validate_config(config: StagingManifestConfig) -> None:
    validate_dns_host(config.api_host)
    if IMAGE_PATTERN.fullmatch(config.image) is None:
        raise ManifestRenderError("image must be a GHCR repository with a 40-character Git SHA tag")
    if CERTIFICATE_ID_PATTERN.fullmatch(config.tls_certificate_id) is None:
        raise ManifestRenderError("TLS certificate ID has an invalid format")
    if CLB_ID_PATTERN.fullmatch(config.edge_clb_id) is None:
        raise ManifestRenderError("edge CLB ID has an invalid format")
    parse_controller_version(config.ingress_controller_version)

    try:
        base_url = urlsplit(config.api_base_url)
        base_url_port = base_url.port
    except ValueError as error:
        raise ManifestRenderError("Staging API base URL is malformed") from error
    if (
        base_url.scheme != "https"
        or base_url.hostname != config.api_host
        or base_url_port not in (None, 443)
        or base_url.path not in ("", "/")
        or base_url.username is not None
        or base_url.password is not None
        or base_url.query
        or base_url.fragment
    ):
        raise ManifestRenderError(
            "Staging API base URL must be the exact HTTPS origin for the configured host"
        )


def render_staging_manifests(
    template: str,
    config: StagingManifestConfig,
) -> str:
    validate_config(config)
    for placeholder, expected_count in PLACEHOLDER_COUNTS.items():
        count = template.count(placeholder)
        if expected_count is None and count < 1:
            raise ManifestRenderError(f"required placeholder is missing: {placeholder}")
        if expected_count is not None and count != expected_count:
            raise ManifestRenderError(
                f"placeholder count changed for {placeholder}: {count} != {expected_count}"
            )

    rendered = template
    replacements = {
        "AIW_IMAGE_PLACEHOLDER": config.image,
        "AIW_STAGING_API_HOST_PLACEHOLDER": config.api_host,
        "AIW_STAGING_TLS_CERT_ID_PLACEHOLDER": config.tls_certificate_id,
        "AIW_STAGING_EDGE_CLB_ID_PLACEHOLDER": config.edge_clb_id,
    }
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)

    leftovers = sorted(set(PLACEHOLDER_PATTERN.findall(rendered)))
    if leftovers:
        raise ManifestRenderError(f"unresolved manifest placeholders: {', '.join(leftovers)}")
    return rendered


def write_private_manifest(path: Path, content: str) -> None:
    if path.exists() or path.is_symlink():
        raise ManifestRenderError("rendered manifest output already exists")
    if path.parent.is_symlink():
        raise ManifestRenderError("rendered manifest directory cannot be a symlink")
    parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
    if parent_mode & 0o077:
        raise ManifestRenderError("rendered manifest directory must deny group/other access")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render fail-closed Staging manifests")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--api-host", required=True)
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--tls-certificate-id", required=True)
    parser.add_argument("--edge-clb-id", required=True)
    parser.add_argument("--ingress-controller-version", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if not arguments.input.is_file() or arguments.input.is_symlink():
            raise ManifestRenderError("manifest template must be a regular non-symlink file")
        template = arguments.input.read_text(encoding="utf-8")
        config = StagingManifestConfig(
            image=arguments.image,
            api_host=arguments.api_host,
            api_base_url=arguments.api_base_url,
            tls_certificate_id=arguments.tls_certificate_id,
            edge_clb_id=arguments.edge_clb_id,
            ingress_controller_version=arguments.ingress_controller_version,
        )
        write_private_manifest(
            arguments.output,
            render_staging_manifests(template, config),
        )
    except (ManifestRenderError, OSError, UnicodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
