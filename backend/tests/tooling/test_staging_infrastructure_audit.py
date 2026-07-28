from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from scripts.deployment import staging_infrastructure_audit as audit_module
from scripts.deployment.staging_infrastructure_audit import (
    InfrastructureAuditError,
    audit,
    write_private_report,
)

SHA = "a" * 40
IMAGE = f"ghcr.io/example/ai-wardrobe-api:{SHA}"
API_HOST = "staging.example.com"
API_BASE_URL = f"https://{API_HOST}"
CLB_ID = "lb-12345678"
CERTIFICATE_ID = "cert-test123"
KUBE_CONTEXT = "ai-wardrobe-staging"
EDGE_VIP = "203.0.113.10"


def _write_evidence(directory: Path, name: str, value: Any) -> None:
    path = directory / audit_module.EVIDENCE_FILES[name]
    content = value if isinstance(value, str) else json.dumps(value)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def _deployment(name: str) -> dict[str, Any]:
    return {
        "metadata": {
            "name": name,
            "namespace": "ai-wardrobe",
            "generation": 3,
        },
        "spec": {
            "replicas": 1,
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": name,
                            "image": IMAGE,
                        }
                    ]
                }
            },
        },
        "status": {
            "replicas": 1,
            "readyReplicas": 1,
            "availableReplicas": 1,
            "updatedReplicas": 1,
            "observedGeneration": 3,
        },
    }


def _node(name: str, zone: str) -> dict[str, Any]:
    return {
        "metadata": {
            "name": name,
            "labels": {
                "ai-wardrobe/environment": "staging",
                "topology.kubernetes.io/zone": zone,
            },
        },
        "status": {
            "conditions": [{"type": "Ready", "status": "True"}],
            "addresses": [{"type": "InternalIP", "address": "10.32.0.10"}],
        },
    }


@pytest.fixture
def evidence_dir(tmp_path: Path) -> Path:
    tmp_path.chmod(0o700)
    _write_evidence(
        tmp_path,
        "compute_contract",
        {
            "cluster_private_only": True,
            "cluster_deletion_protection": True,
            "cluster_version": "1.30.0",
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
    )
    _write_evidence(
        tmp_path,
        "data_contract",
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
    )
    _write_evidence(tmp_path, "terraform_clb_id", CLB_ID)
    _write_evidence(tmp_path, "terraform_vips", [EDGE_VIP])
    _write_evidence(tmp_path, "kube_context", KUBE_CONTEXT)
    _write_evidence(
        tmp_path,
        "namespace",
        {
            "metadata": {"name": "ai-wardrobe"},
            "status": {"phase": "Active"},
        },
    )
    _write_evidence(
        tmp_path,
        "configmap",
        {
            "metadata": {
                "name": "ai-wardrobe-config",
                "namespace": "ai-wardrobe",
            },
            "data": {
                "AIW_ENVIRONMENT": "staging",
                "AIW_DEBUG": "false",
                "AIW_EXPOSE_API_DOCS": "false",
                "AIW_WECHAT_LOGIN_ENABLED": "true",
                "AIW_OPENAI_ENABLED": "true",
                "AIW_COS_ENABLED": "true",
                "AIW_RATE_LIMIT_ENABLED": "true",
                "AIW_OTEL_ENABLED": "true",
                "AIW_TRUSTED_PROXY_CIDRS": '["10.32.0.0/16"]',
                "AIW_AI_CANARY_PERCENTAGE": "0",
            },
        },
    )
    _write_evidence(
        tmp_path,
        "deployments",
        {"items": [_deployment(name) for name in sorted(audit_module.EXPECTED_DEPLOYMENTS)]},
    )
    _write_evidence(
        tmp_path,
        "nodes",
        {
            "items": [
                _node("node-a", "ap-guangzhou-6"),
                _node("node-b", "ap-guangzhou-7"),
            ]
        },
    )
    _write_evidence(
        tmp_path,
        "ingress",
        {
            "metadata": {
                "name": "ai-wardrobe-api",
                "namespace": "ai-wardrobe",
                "annotations": {
                    "kubernetes.io/ingress.class": "qcloud",
                    "kubernetes.io/ingress.existLbId": CLB_ID,
                    "kubernetes.io/ingress.qcloud-loadbalance-id": CLB_ID,
                    "ingress.cloud.tencent.com/listen-ports": ('[{"HTTP":80},{"HTTPS":443}]'),
                    "ingress.cloud.tencent.com/certificate": json.dumps(
                        [{"hosts": [API_HOST], "qcloud_cert_id": [CERTIFICATE_ID]}]
                    ),
                    "ingress.cloud.tencent.com/auto-rewrite": "true",
                    "ingress.cloud.tencent.com/auto-rewrite-code": ('{"defaultRewriteCode":307}'),
                    "ingress.cloud.tencent.com/deletion-protection": "true",
                },
            },
            "spec": {
                "rules": [{"host": API_HOST}],
            },
            "status": {
                "loadBalancer": {
                    "ingress": [{"ip": EDGE_VIP}],
                }
            },
        },
    )
    _write_evidence(tmp_path, "controller_version", "v2.11.1")
    _write_evidence(
        tmp_path,
        "secret_checks",
        {
            "application_secret_present": True,
            "registry_secret_present": True,
            "postgresql_ca_present": True,
            "redis_ca_present": True,
        },
    )
    _write_evidence(tmp_path, "health_status", "200")
    _write_evidence(
        tmp_path,
        "health",
        {
            "status": "ready",
            "environment": "staging",
            "dependencies": {
                "database": "ok",
                "redis": "ok",
                "object_storage": "ok",
            },
        },
    )
    _write_evidence(
        tmp_path,
        "https_headers",
        "HTTP/2 200\r\nStrict-Transport-Security: max-age=31536000; includeSubDomains\r\n",
    )
    _write_evidence(
        tmp_path,
        "redirect",
        {
            "status": 307,
            "location": f"{API_BASE_URL}/health/ready",
        },
    )
    return tmp_path


def _run_audit(evidence_dir: Path) -> dict[str, Any]:
    return audit(
        evidence_dir,
        candidate_sha=SHA,
        expected_image=IMAGE,
        api_host=API_HOST,
        api_base_url=API_BASE_URL,
        expected_clb_id=CLB_ID,
        expected_certificate_id=CERTIFICATE_ID,
        expected_kube_context=KUBE_CONTEXT,
    )


def test_valid_evidence_produces_redacted_pass_report(
    evidence_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_module, "_resolve_ipv4", lambda _: {EDGE_VIP})

    report = _run_audit(evidence_dir)
    serialized = json.dumps(report)

    assert report["result"] == "PASS"
    assert report["runtime"] == {
        "deployment_count": 6,
        "node_count": 2,
        "availability_zone_count": 2,
        "dns_address_count": 1,
        "canary_percentage": 0,
    }
    for sensitive_value in (
        CLB_ID,
        EDGE_VIP,
        API_HOST,
        CERTIFICATE_ID,
        KUBE_CONTEXT,
    ):
        assert sensitive_value not in serialized


def test_audit_rejects_mutable_or_wrong_deployment_image(
    evidence_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_path = evidence_dir / audit_module.EVIDENCE_FILES["deployments"]
    deployments = json.loads(deployments_path.read_text(encoding="utf-8"))
    deployments["items"][0]["spec"]["template"]["spec"]["containers"][0]["image"] = (
        "ghcr.io/example/ai-wardrobe-api:latest"
    )
    deployments_path.write_text(json.dumps(deployments), encoding="utf-8")
    deployments_path.chmod(0o600)
    monkeypatch.setattr(audit_module, "_resolve_ipv4", lambda _: {EDGE_VIP})

    with pytest.raises(InfrastructureAuditError, match=r"kubernetes\.deployment\.image"):
        _run_audit(evidence_dir)


def test_audit_rejects_missing_data_ca(
    evidence_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks_path = evidence_dir / audit_module.EVIDENCE_FILES["secret_checks"]
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    checks["redis_ca_present"] = False
    checks_path.write_text(json.dumps(checks), encoding="utf-8")
    checks_path.chmod(0o600)
    monkeypatch.setattr(audit_module, "_resolve_ipv4", lambda _: {EDGE_VIP})

    with pytest.raises(
        InfrastructureAuditError,
        match=r"kubernetes\.secret\.redis_ca_present",
    ):
        _run_audit(evidence_dir)


def test_audit_rejects_dns_drift(
    evidence_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_module, "_resolve_ipv4", lambda _: {"203.0.113.20"})

    with pytest.raises(InfrastructureAuditError, match=r"edge\.dns_exact_vips"):
        _run_audit(evidence_dir)


def test_private_report_is_exclusive_and_mode_0600(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    report_path = tmp_path / "report.json"

    write_private_report(report_path, {"result": "PASS"})

    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    with pytest.raises(InfrastructureAuditError, match=r"report\.output_absent"):
        write_private_report(report_path, {"result": "PASS"})


def test_audit_rejects_group_readable_evidence(evidence_dir: Path) -> None:
    evidence_path = evidence_dir / audit_module.EVIDENCE_FILES["health"]
    os.chmod(evidence_path, 0o640)

    with pytest.raises(InfrastructureAuditError, match=r"evidence\.health\.private_mode"):
        _run_audit(evidence_dir)


def test_audit_rejects_ip_address_as_api_host(evidence_dir: Path) -> None:
    with pytest.raises(InfrastructureAuditError, match=r"input\.api_host_dns"):
        audit(
            evidence_dir,
            candidate_sha=SHA,
            expected_image=IMAGE,
            api_host=EDGE_VIP,
            api_base_url=f"https://{EDGE_VIP}",
            expected_clb_id=CLB_ID,
            expected_certificate_id=CERTIFICATE_ID,
            expected_kube_context=KUBE_CONTEXT,
        )
