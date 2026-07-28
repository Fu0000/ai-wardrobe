from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import stat
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit


class InfrastructureAuditError(ValueError):
    """Raised when Staging infrastructure evidence violates the release contract."""


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_PATTERN = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9._/-]*:[0-9a-f]{40}$")
CLB_ID_PATTERN = re.compile(r"^lb-[a-z0-9]{8,}$")
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
EXPECTED_DEPLOYMENTS = {
    "ai-wardrobe-api",
    "ai-wardrobe-beat",
    "ai-wardrobe-worker-fast",
    "ai-wardrobe-worker-image",
    "ai-wardrobe-worker-maintenance",
    "ai-wardrobe-worker-media",
}
EVIDENCE_FILES = {
    "compute_contract": "compute-contract.json",
    "data_contract": "data-contract.json",
    "terraform_clb_id": "terraform-edge-clb-id.txt",
    "terraform_vips": "terraform-edge-vips.json",
    "kube_context": "kube-context.txt",
    "namespace": "namespace.json",
    "configmap": "configmap.json",
    "deployments": "deployments.json",
    "nodes": "nodes.json",
    "ingress": "ingress.json",
    "controller_version": "controller-version.txt",
    "secret_checks": "secret-checks.json",
    "health_status": "health-status.txt",
    "health": "health.json",
    "https_headers": "https-headers.txt",
    "redirect": "redirect.json",
}


def _require(condition: bool, check: str) -> None:
    if not condition:
        raise InfrastructureAuditError(f"failed check: {check}")


def _private_directory(path: Path, *, label: str) -> Path:
    _require(path.is_dir() and not path.is_symlink(), f"{label}.private_directory")
    _require(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, f"{label}.private_mode")
    return path.resolve()


def _evidence_path(directory: Path, name: str) -> Path:
    path = directory / EVIDENCE_FILES[name]
    _require(path.is_file() and not path.is_symlink(), f"evidence.{name}.regular_file")
    _require(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, f"evidence.{name}.private_mode")
    _require(path.stat().st_size <= MAX_EVIDENCE_BYTES, f"evidence.{name}.bounded_size")
    return path


def _read_text(directory: Path, name: str) -> str:
    try:
        return _evidence_path(directory, name).read_text(encoding="utf-8").strip()
    except UnicodeError as error:
        raise InfrastructureAuditError(f"failed check: evidence.{name}.utf8") from error


def _read_json(directory: Path, name: str) -> Any:
    try:
        return json.loads(_read_text(directory, name))
    except json.JSONDecodeError as error:
        raise InfrastructureAuditError(f"failed check: evidence.{name}.json") from error


def _mapping(value: Any, check: str) -> Mapping[str, Any]:
    _require(isinstance(value, dict), check)
    return cast(Mapping[str, Any], value)


def _sequence(value: Any, check: str) -> Sequence[Any]:
    _require(isinstance(value, list), check)
    return cast(Sequence[Any], value)


def _expect_contract(
    contract: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    check_prefix: str,
) -> None:
    for key, expected_value in expected.items():
        actual_value = contract.get(key)
        if isinstance(expected_value, bool):
            matches = actual_value is expected_value
        else:
            matches = actual_value == expected_value
        _require(matches, f"{check_prefix}.{key}")


def _parse_version(value: str, *, check: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise InfrastructureAuditError(f"failed check: {check}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _validate_terraform(directory: Path, expected_clb_id: str) -> dict[str, Any]:
    compute = _mapping(_read_json(directory, "compute_contract"), "terraform.compute.mapping")
    data = _mapping(_read_json(directory, "data_contract"), "terraform.data.mapping")

    _expect_contract(
        compute,
        {
            "cluster_private_only": True,
            "cluster_deletion_protection": True,
            "network_type": "GR",
            "pod_cidr": "172.20.0.0/16",
            "service_cidr": "172.21.0.0/20",
            "node_public_ip": False,
            "node_min_size": 2,
            "node_max_size": 4,
            "cross_zone_subnet_count": 2,
            "audit_enabled": True,
            "event_persistence_enabled": True,
            "audit_retention_days": 15,
            "edge_access_retention_days": 15,
            "edge_clb_delete_protection": True,
            "edge_clb_cross_zone": True,
            "edge_clb_public_ipv4": True,
            "edge_clb_pass_to_target": True,
            "edge_clb_bandwidth_mbps": 20,
            "edge_public_ports": [80, 443],
            "nat_product_version": 2,
            "shared_egress_eip": True,
            "private_deployment_runner_only": True,
        },
        check_prefix="terraform.compute",
    )
    cluster_version = str(compute.get("cluster_version", ""))
    _require(
        _parse_version(cluster_version, check="terraform.compute.cluster_version_format")
        >= (1, 30, 0),
        "terraform.compute.cluster_version_floor",
    )

    _expect_contract(
        data,
        {
            "postgresql_major_version": "18",
            "postgresql_tde_enabled": True,
            "postgresql_tls_enabled": True,
            "postgresql_public_access": False,
            "postgresql_backup_days": 14,
            "redis_version": "7.0",
            "redis_tls_enabled": True,
            "redis_public_access": False,
            "redis_replicas": 2,
        },
        check_prefix="terraform.data",
    )

    terraform_clb_id = _read_text(directory, "terraform_clb_id")
    _require(terraform_clb_id == expected_clb_id, "terraform.edge_clb_id")
    terraform_vips_raw = _sequence(
        _read_json(directory, "terraform_vips"),
        "terraform.edge_vips.list",
    )
    _require(bool(terraform_vips_raw), "terraform.edge_vips.nonempty")
    terraform_vips: set[str] = set()
    for raw_address in terraform_vips_raw:
        _require(isinstance(raw_address, str), "terraform.edge_vips.string")
        address = ipaddress.ip_address(raw_address)
        _require(address.version == 4, "terraform.edge_vips.ipv4")
        terraform_vips.add(str(address))
    _require(
        len(terraform_vips) == len(terraform_vips_raw),
        "terraform.edge_vips.unique",
    )

    return {
        "cluster_version": cluster_version,
        "postgresql_version": str(data["postgresql_major_version"]),
        "redis_version": str(data["redis_version"]),
        "terraform_vips": terraform_vips,
    }


def _validate_configmap(configmap: Mapping[str, Any]) -> int:
    metadata = _mapping(configmap.get("metadata"), "kubernetes.configmap.metadata")
    _require(metadata.get("name") == "ai-wardrobe-config", "kubernetes.configmap.name")
    _require(metadata.get("namespace") == "ai-wardrobe", "kubernetes.configmap.namespace")
    data = _mapping(configmap.get("data"), "kubernetes.configmap.data")
    _expect_contract(
        data,
        {
            "AIW_ENVIRONMENT": "staging",
            "AIW_DEBUG": "false",
            "AIW_EXPOSE_API_DOCS": "false",
            "AIW_WECHAT_LOGIN_ENABLED": "true",
            "AIW_OPENAI_ENABLED": "true",
            "AIW_COS_ENABLED": "true",
            "AIW_RATE_LIMIT_ENABLED": "true",
            "AIW_OTEL_ENABLED": "true",
        },
        check_prefix="kubernetes.configmap",
    )
    try:
        trusted_cidrs = json.loads(str(data.get("AIW_TRUSTED_PROXY_CIDRS", "")))
    except json.JSONDecodeError as error:
        raise InfrastructureAuditError(
            "failed check: kubernetes.configmap.trusted_proxy_json"
        ) from error
    _require(
        isinstance(trusted_cidrs, list) and bool(trusted_cidrs),
        "kubernetes.configmap.trusted_proxy_nonempty",
    )
    for raw_cidr in trusted_cidrs:
        _require(isinstance(raw_cidr, str), "kubernetes.configmap.trusted_proxy_string")
        network = ipaddress.ip_network(raw_cidr, strict=False)
        _require(network.prefixlen > 0, "kubernetes.configmap.trusted_proxy_bounded")

    canary_percentage = int(str(data.get("AIW_AI_CANARY_PERCENTAGE", "-1")))
    _require(
        canary_percentage in {0, 10, 50, 100},
        "kubernetes.configmap.canary_stage",
    )
    return canary_percentage


def _validate_deployments(deployments: Mapping[str, Any], expected_image: str) -> int:
    items = _sequence(deployments.get("items"), "kubernetes.deployments.items")
    observed_names: set[str] = set()
    for item_value in items:
        item = _mapping(item_value, "kubernetes.deployment.mapping")
        metadata = _mapping(item.get("metadata"), "kubernetes.deployment.metadata")
        name = metadata.get("name")
        if not isinstance(name, str):
            raise InfrastructureAuditError("failed check: kubernetes.deployment.name")
        observed_names.add(name)

        spec = _mapping(item.get("spec"), "kubernetes.deployment.spec")
        status = _mapping(item.get("status"), "kubernetes.deployment.status")
        desired = spec.get("replicas", 1)
        _require(
            isinstance(desired, int) and not isinstance(desired, bool) and desired >= 1,
            "kubernetes.deployment.desired",
        )
        for field in ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas"):
            _require(status.get(field, 0) == desired, f"kubernetes.deployment.{field}")
        _require(status.get("unavailableReplicas", 0) == 0, "kubernetes.deployment.available")
        _require(
            status.get("observedGeneration") == metadata.get("generation"),
            "kubernetes.deployment.observed_generation",
        )

        template = _mapping(spec.get("template"), "kubernetes.deployment.template")
        pod_spec = _mapping(template.get("spec"), "kubernetes.deployment.pod_spec")
        containers = _sequence(
            pod_spec.get("containers"),
            "kubernetes.deployment.containers",
        )
        _require(bool(containers), "kubernetes.deployment.containers_nonempty")
        for container_value in containers:
            container = _mapping(container_value, "kubernetes.deployment.container")
            _require(container.get("image") == expected_image, "kubernetes.deployment.image")

    _require(observed_names == EXPECTED_DEPLOYMENTS, "kubernetes.deployments.exact_set")
    return len(items)


def _validate_nodes(nodes: Mapping[str, Any]) -> tuple[int, int]:
    items = _sequence(nodes.get("items"), "kubernetes.nodes.items")
    _require(len(items) >= 2, "kubernetes.nodes.minimum")
    zones: set[str] = set()
    for item_value in items:
        item = _mapping(item_value, "kubernetes.node.mapping")
        metadata = _mapping(item.get("metadata"), "kubernetes.node.metadata")
        labels = _mapping(metadata.get("labels"), "kubernetes.node.labels")
        _require(
            labels.get("ai-wardrobe/environment") == "staging",
            "kubernetes.node.environment",
        )
        zone = labels.get("topology.kubernetes.io/zone")
        if not isinstance(zone, str) or not zone:
            raise InfrastructureAuditError("failed check: kubernetes.node.zone")
        zones.add(zone)

        status = _mapping(item.get("status"), "kubernetes.node.status")
        conditions = _sequence(status.get("conditions"), "kubernetes.node.conditions")
        ready = any(
            isinstance(condition, dict)
            and condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in conditions
        )
        _require(ready, "kubernetes.node.ready")
        addresses = _sequence(status.get("addresses"), "kubernetes.node.addresses")
        _require(
            not any(
                isinstance(address, dict) and address.get("type") == "ExternalIP"
                for address in addresses
            ),
            "kubernetes.node.no_external_ip",
        )
    _require(len(zones) >= 2, "kubernetes.nodes.cross_zone")
    return len(items), len(zones)


def _ingress_addresses(ingress: Mapping[str, Any]) -> list[str]:
    status = _mapping(ingress.get("status"), "kubernetes.ingress.status")
    load_balancer = _mapping(
        status.get("loadBalancer"),
        "kubernetes.ingress.load_balancer",
    )
    entries = _sequence(
        load_balancer.get("ingress"),
        "kubernetes.ingress.addresses",
    )
    addresses: list[str] = []
    for entry_value in entries:
        entry = _mapping(entry_value, "kubernetes.ingress.address")
        address = entry.get("ip") or entry.get("hostname")
        if not isinstance(address, str) or not address:
            raise InfrastructureAuditError("failed check: kubernetes.ingress.address_value")
        addresses.append(address)
    _require(bool(addresses), "kubernetes.ingress.address_nonempty")
    return addresses


def _validate_ingress(
    ingress: Mapping[str, Any],
    *,
    api_host: str,
    expected_clb_id: str,
    expected_certificate_id: str,
) -> list[str]:
    metadata = _mapping(ingress.get("metadata"), "kubernetes.ingress.metadata")
    _require(metadata.get("name") == "ai-wardrobe-api", "kubernetes.ingress.name")
    _require(metadata.get("namespace") == "ai-wardrobe", "kubernetes.ingress.namespace")
    annotations = _mapping(
        metadata.get("annotations"),
        "kubernetes.ingress.annotations",
    )
    _expect_contract(
        annotations,
        {
            "kubernetes.io/ingress.class": "qcloud",
            "kubernetes.io/ingress.existLbId": expected_clb_id,
            "kubernetes.io/ingress.qcloud-loadbalance-id": expected_clb_id,
            "ingress.cloud.tencent.com/auto-rewrite": "true",
            "ingress.cloud.tencent.com/deletion-protection": "true",
        },
        check_prefix="kubernetes.ingress.annotation",
    )
    try:
        ports = json.loads(str(annotations.get("ingress.cloud.tencent.com/listen-ports", "")))
        certificate = json.loads(str(annotations.get("ingress.cloud.tencent.com/certificate", "")))
        rewrite = json.loads(
            str(annotations.get("ingress.cloud.tencent.com/auto-rewrite-code", ""))
        )
    except json.JSONDecodeError as error:
        raise InfrastructureAuditError(
            "failed check: kubernetes.ingress.annotation_json"
        ) from error
    _require(ports == [{"HTTP": 80}, {"HTTPS": 443}], "kubernetes.ingress.ports")
    _require(
        certificate == [{"hosts": [api_host], "qcloud_cert_id": [expected_certificate_id]}],
        "kubernetes.ingress.certificate",
    )
    _require(rewrite == {"defaultRewriteCode": 307}, "kubernetes.ingress.redirect_code")

    spec = _mapping(ingress.get("spec"), "kubernetes.ingress.spec")
    _require(not spec.get("tls"), "kubernetes.ingress.annotation_tls_only")
    rules = _sequence(spec.get("rules"), "kubernetes.ingress.rules")
    _require(
        len(rules) == 1 and isinstance(rules[0], dict) and rules[0].get("host") == api_host,
        "kubernetes.ingress.host",
    )
    return _ingress_addresses(ingress)


def _resolve_ipv4(host: str) -> set[str]:
    try:
        results = socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise InfrastructureAuditError("failed check: edge.dns_resolution") from error
    return {str(result[4][0]) for result in results}


def _validate_edge(
    directory: Path,
    *,
    api_host: str,
    api_base_url: str,
    terraform_vips: set[str],
    ingress_addresses: Sequence[str],
) -> int:
    health_status = _read_text(directory, "health_status")
    _require(health_status == "200", "edge.https_ready_status")
    health = _mapping(_read_json(directory, "health"), "edge.health.mapping")
    _expect_contract(
        health,
        {
            "status": "ready",
            "environment": "staging",
        },
        check_prefix="edge.health",
    )
    dependencies = _mapping(health.get("dependencies"), "edge.health.dependencies")
    _expect_contract(
        dependencies,
        {
            "database": "ok",
            "redis": "ok",
            "object_storage": "ok",
        },
        check_prefix="edge.health.dependency",
    )

    headers = _read_text(directory, "https_headers")
    hsts_values = [
        line.split(":", 1)[1].strip()
        for line in headers.splitlines()
        if line.casefold().startswith("strict-transport-security:")
    ]
    _require(
        bool(hsts_values) and hsts_values[-1].casefold() == "max-age=31536000; includesubdomains",
        "edge.hsts",
    )
    redirect = _mapping(_read_json(directory, "redirect"), "edge.redirect.mapping")
    _require(redirect.get("status") == 307, "edge.redirect.status")
    _require(
        redirect.get("location") == f"{api_base_url.rstrip('/')}/health/ready",
        "edge.redirect.location",
    )

    dns_addresses = _resolve_ipv4(api_host)
    _require(dns_addresses == terraform_vips, "edge.dns_exact_vips")
    ingress_resolved: set[str] = set()
    for address in ingress_addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            ingress_resolved.update(_resolve_ipv4(address))
        else:
            _require(parsed.version == 4, "edge.ingress_address_ipv4")
            ingress_resolved.add(str(parsed))
    _require(
        bool(ingress_resolved) and ingress_resolved.issubset(terraform_vips),
        "edge.ingress_matches_terraform",
    )
    return len(dns_addresses)


def validate_arguments(
    *,
    candidate_sha: str,
    expected_image: str,
    api_host: str,
    api_base_url: str,
    expected_clb_id: str,
    expected_certificate_id: str,
    expected_kube_context: str,
) -> None:
    _require(SHA_PATTERN.fullmatch(candidate_sha) is not None, "input.candidate_sha")
    _require(IMAGE_PATTERN.fullmatch(expected_image) is not None, "input.expected_image")
    _require(expected_image.endswith(f":{candidate_sha}"), "input.image_sha")
    _require(CLB_ID_PATTERN.fullmatch(expected_clb_id) is not None, "input.edge_clb_id")
    _require(
        re.fullmatch(r"[A-Za-z0-9_-]{6,64}", expected_certificate_id) is not None,
        "input.certificate_id",
    )
    _require(
        "staging" in expected_kube_context.casefold()
        and "prod" not in expected_kube_context.casefold(),
        "input.kube_context",
    )
    parsed_url = urlsplit(api_base_url)
    _require(
        parsed_url.scheme == "https"
        and parsed_url.hostname == api_host
        and parsed_url.port in (None, 443)
        and parsed_url.path in ("", "/")
        and parsed_url.username is None
        and parsed_url.password is None
        and not parsed_url.query
        and not parsed_url.fragment,
        "input.api_origin",
    )
    _require(
        api_host == api_host.casefold() and len(api_host) <= 253 and not api_host.endswith("."),
        "input.api_host",
    )
    try:
        ipaddress.ip_address(api_host)
    except ValueError:
        pass
    else:
        raise InfrastructureAuditError("failed check: input.api_host_dns")
    host_labels = api_host.split(".")
    _require(
        len(host_labels) >= 2
        and all(DNS_LABEL_PATTERN.fullmatch(label) is not None for label in host_labels),
        "input.api_host_labels",
    )


def audit(
    directory: Path,
    *,
    candidate_sha: str,
    expected_image: str,
    api_host: str,
    api_base_url: str,
    expected_clb_id: str,
    expected_certificate_id: str,
    expected_kube_context: str,
) -> dict[str, Any]:
    validate_arguments(
        candidate_sha=candidate_sha,
        expected_image=expected_image,
        api_host=api_host,
        api_base_url=api_base_url,
        expected_clb_id=expected_clb_id,
        expected_certificate_id=expected_certificate_id,
        expected_kube_context=expected_kube_context,
    )
    evidence_directory = _private_directory(directory, label="evidence")
    terraform = _validate_terraform(evidence_directory, expected_clb_id)

    kube_context = _read_text(evidence_directory, "kube_context")
    _require(kube_context == expected_kube_context, "kubernetes.context")
    namespace = _mapping(
        _read_json(evidence_directory, "namespace"),
        "kubernetes.namespace.mapping",
    )
    _require(
        _mapping(namespace.get("metadata"), "kubernetes.namespace.metadata").get("name")
        == "ai-wardrobe",
        "kubernetes.namespace.name",
    )
    _require(
        _mapping(namespace.get("status"), "kubernetes.namespace.status").get("phase") == "Active",
        "kubernetes.namespace.active",
    )
    canary_percentage = _validate_configmap(
        _mapping(
            _read_json(evidence_directory, "configmap"),
            "kubernetes.configmap.mapping",
        )
    )
    deployment_count = _validate_deployments(
        _mapping(
            _read_json(evidence_directory, "deployments"),
            "kubernetes.deployments.mapping",
        ),
        expected_image,
    )
    node_count, zone_count = _validate_nodes(
        _mapping(_read_json(evidence_directory, "nodes"), "kubernetes.nodes.mapping")
    )
    ingress_addresses = _validate_ingress(
        _mapping(
            _read_json(evidence_directory, "ingress"),
            "kubernetes.ingress.mapping",
        ),
        api_host=api_host,
        expected_clb_id=expected_clb_id,
        expected_certificate_id=expected_certificate_id,
    )
    controller_version = _read_text(evidence_directory, "controller_version")
    _require(
        _parse_version(
            controller_version,
            check="kubernetes.ingress_controller_version_format",
        )
        >= (2, 11, 0),
        "kubernetes.ingress_controller_version_floor",
    )
    secret_checks = _mapping(
        _read_json(evidence_directory, "secret_checks"),
        "kubernetes.secret_checks.mapping",
    )
    _expect_contract(
        secret_checks,
        {
            "application_secret_present": True,
            "registry_secret_present": True,
            "postgresql_ca_present": True,
            "redis_ca_present": True,
        },
        check_prefix="kubernetes.secret",
    )
    dns_address_count = _validate_edge(
        evidence_directory,
        api_host=api_host,
        api_base_url=api_base_url,
        terraform_vips=cast(set[str], terraform["terraform_vips"]),
        ingress_addresses=ingress_addresses,
    )

    return {
        "schema_version": 1,
        "result": "PASS",
        "environment": "staging",
        "candidate_sha": candidate_sha,
        "observed_at": datetime.now(UTC).isoformat(),
        "checks": {
            "terraform_state_contract": "PASS",
            "kubernetes_runtime": "PASS",
            "private_data_dependencies": "PASS",
            "fixed_https_edge": "PASS",
            "secret_presence_without_values": "PASS",
        },
        "versions": {
            "kubernetes": terraform["cluster_version"],
            "tke_ingress_controller": controller_version,
            "postgresql": terraform["postgresql_version"],
            "redis": terraform["redis_version"],
        },
        "runtime": {
            "deployment_count": deployment_count,
            "node_count": node_count,
            "availability_zone_count": zone_count,
            "dns_address_count": dns_address_count,
            "canary_percentage": canary_percentage,
        },
        "redaction": {
            "cloud_resource_ids": True,
            "network_addresses": True,
            "kubernetes_context": True,
            "credentials_and_secret_values": True,
        },
    }


def write_private_report(path: Path, report: Mapping[str, Any]) -> None:
    _require(not path.exists() and not path.is_symlink(), "report.output_absent")
    _private_directory(path.parent, label="report")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit Staging infrastructure from read-only, ephemeral evidence"
    )
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--api-host", required=True)
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--expected-edge-clb-id", required=True)
    parser.add_argument("--expected-tls-certificate-id", required=True)
    parser.add_argument("--expected-kube-context", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        report = audit(
            arguments.evidence_dir,
            candidate_sha=arguments.candidate_sha,
            expected_image=arguments.expected_image,
            api_host=arguments.api_host,
            api_base_url=arguments.api_base_url,
            expected_clb_id=arguments.expected_edge_clb_id,
            expected_certificate_id=arguments.expected_tls_certificate_id,
            expected_kube_context=arguments.expected_kube_context,
        )
        write_private_report(arguments.report, report)
    except (InfrastructureAuditError, OSError, UnicodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
