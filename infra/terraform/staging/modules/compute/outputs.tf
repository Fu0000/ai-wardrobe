output "cluster_id" {
  description = "TKE Cluster ID。"
  value       = tencentcloud_kubernetes_cluster.staging.id
}

output "private_kubeconfig" {
  description = "仅可通过受控 Secret 供 VPC 内临时部署 Runner 使用的私网 Kubeconfig。"
  value       = tencentcloud_kubernetes_cluster_endpoint.private.kube_config_intranet
  sensitive   = true
}

output "edge_clb_id" {
  description = "供 TKE Ingress 复用的固定公网 CLB ID。"
  value       = tencentcloud_clb_instance.edge.id
}

output "edge_clb_vips" {
  description = "Staging API DNS 必须指向的固定公网 CLB VIP。"
  value       = tencentcloud_clb_instance.edge.clb_vips
}

output "contract" {
  description = "不含凭据的计算层安全与容量合同。"
  value = {
    cluster_private_only           = tencentcloud_kubernetes_cluster.staging.cluster_internet == false
    cluster_deletion_protection    = tencentcloud_kubernetes_cluster.staging.deletion_protection
    cluster_version                = tencentcloud_kubernetes_cluster.staging.cluster_version
    network_type                   = tencentcloud_kubernetes_cluster.staging.network_type
    pod_cidr                       = tencentcloud_kubernetes_cluster.staging.cluster_cidr
    service_cidr                   = tencentcloud_kubernetes_cluster.staging.service_cidr
    node_public_ip                 = tencentcloud_kubernetes_node_pool.system.auto_scaling_config[0].public_ip_assigned
    node_min_size                  = tencentcloud_kubernetes_node_pool.system.min_size
    node_max_size                  = tencentcloud_kubernetes_node_pool.system.max_size
    cross_zone_subnet_count        = length(var.app_subnet_ids)
    audit_enabled                  = tencentcloud_kubernetes_cluster.staging.cluster_audit[0].enabled
    event_persistence_enabled      = tencentcloud_kubernetes_cluster.staging.event_persistence[0].enabled
    audit_retention_days           = tencentcloud_cls_topic.tke_audit.period
    edge_access_retention_days     = tencentcloud_cls_topic.edge_access.period
    edge_clb_delete_protection     = tencentcloud_clb_instance.edge.delete_protect
    edge_clb_cross_zone            = tencentcloud_clb_instance.edge.master_zone_id != tencentcloud_clb_instance.edge.slave_zone_id
    edge_clb_public_ipv4           = tencentcloud_clb_instance.edge.network_type == "OPEN" && tencentcloud_clb_instance.edge.address_ip_version == "IPV4"
    edge_clb_pass_to_target        = tencentcloud_clb_instance.edge.load_balancer_pass_to_target
    edge_clb_bandwidth_mbps        = tencentcloud_clb_instance.edge.internet_bandwidth_max_out
    edge_public_ports              = [80, 443]
    nat_product_version            = tencentcloud_nat_gateway.app.nat_product_version
    shared_egress_eip              = true
    private_deployment_runner_only = true
  }
}
