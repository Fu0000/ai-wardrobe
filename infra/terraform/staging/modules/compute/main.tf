resource "tencentcloud_cls_logset" "tke" {
  logset_name = "${var.name_prefix}-tke"
  tags        = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "tencentcloud_cls_topic" "tke_audit" {
  topic_name           = "${var.name_prefix}-tke-audit"
  logset_id            = tencentcloud_cls_logset.tke.id
  auto_split           = true
  max_split_partitions = 20
  partition_count      = 1
  period               = 15
  storage_type         = "hot"
  describes            = "AI Wardrobe Staging TKE audit log"
  encryption           = 1
  tags                 = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "tencentcloud_cls_topic" "tke_event" {
  topic_name           = "${var.name_prefix}-tke-event"
  logset_id            = tencentcloud_cls_logset.tke.id
  auto_split           = true
  max_split_partitions = 20
  partition_count      = 1
  period               = 15
  storage_type         = "hot"
  describes            = "AI Wardrobe Staging TKE event log"
  encryption           = 1
  tags                 = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "tencentcloud_eip" "nat" {
  name                       = "${var.name_prefix}-nat"
  type                       = "EIP"
  internet_charge_type       = "TRAFFIC_POSTPAID_BY_HOUR"
  internet_max_bandwidth_out = var.nat_eip_bandwidth_mbps
  tags                       = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "tencentcloud_nat_gateway" "app" {
  name                = "${var.name_prefix}-nat"
  vpc_id              = var.vpc_id
  nat_product_version = 2
  subnet_id           = var.app_subnet_ids[0]
  zone                = var.primary_availability_zone
  assigned_eip_set    = [tencentcloud_eip.nat.public_ip]
  tags                = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "tencentcloud_route_table" "app" {
  name   = "${var.name_prefix}-app-egress"
  vpc_id = var.vpc_id
  tags   = var.tags
}

resource "tencentcloud_route_table_entry" "app_default_egress" {
  route_table_id         = tencentcloud_route_table.app.id
  destination_cidr_block = "0.0.0.0/0"
  next_type              = "NAT"
  next_hub               = tencentcloud_nat_gateway.app.id
  description            = "Application subnet egress through the managed NAT gateway"
}

resource "tencentcloud_route_table_association" "app" {
  for_each = {
    primary = var.app_subnet_ids[0]
    standby = var.app_subnet_ids[1]
  }

  route_table_id = tencentcloud_route_table.app.id
  subnet_id      = each.value

  depends_on = [tencentcloud_route_table_entry.app_default_egress]
}

resource "tencentcloud_security_group" "tke_endpoint" {
  name        = "${var.name_prefix}-tke-endpoint"
  description = "AI Wardrobe Staging private TKE API endpoint"
  tags        = var.tags
}

resource "tencentcloud_security_group_rule_set" "tke_endpoint" {
  security_group_id = tencentcloud_security_group.tke_endpoint.id

  dynamic "ingress" {
    for_each = var.app_subnet_cidrs
    content {
      action      = "ACCEPT"
      cidr_block  = ingress.value
      protocol    = "TCP"
      port        = "443"
      description = "Allow Kubernetes API only from an application subnet"
    }
  }

  ingress {
    action      = "DROP"
    cidr_block  = "0.0.0.0/0"
    protocol    = "ALL"
    port        = "ALL"
    description = "Deny every other control-plane inbound connection"
  }

  egress {
    action      = "ACCEPT"
    cidr_block  = "0.0.0.0/0"
    protocol    = "ALL"
    port        = "ALL"
    description = "Allow the managed endpoint to communicate with cluster nodes"
  }
}

resource "tencentcloud_kubernetes_cluster" "staging" {
  cluster_name            = "${var.name_prefix}-tke"
  cluster_desc            = "AI Wardrobe Staging managed private cluster"
  cluster_deploy_type     = "MANAGED_CLUSTER"
  cluster_version         = var.cluster_version
  cluster_os              = "tlinux3.1x86_64"
  cluster_cidr            = var.cluster_cidr
  service_cidr            = var.service_cidr
  cluster_max_pod_num     = 64
  cluster_max_service_num = 256
  cluster_internet        = false
  network_type            = "GR"
  container_runtime       = "containerd"
  instance_delete_mode    = "terminate"
  deletion_protection     = true
  vpc_id                  = var.vpc_id
  tags                    = var.tags

  auth_options {
    use_tke_default                      = true
    auto_create_discovery_anonymous_auth = false
  }

  log_agent {
    enabled = true
  }

  cluster_audit {
    enabled                    = true
    log_set_id                 = tencentcloud_cls_logset.tke.id
    topic_id                   = tencentcloud_cls_topic.tke_audit.id
    delete_audit_log_and_topic = false
  }

  event_persistence {
    enabled                    = true
    log_set_id                 = tencentcloud_cls_logset.tke.id
    topic_id                   = tencentcloud_cls_topic.tke_event.id
    delete_event_log_and_topic = false
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "tencentcloud_kubernetes_node_pool" "system" {
  name                     = "${var.name_prefix}-system"
  cluster_id               = tencentcloud_kubernetes_cluster.staging.id
  vpc_id                   = var.vpc_id
  subnet_ids               = var.app_subnet_ids
  min_size                 = 2
  max_size                 = 4
  desired_capacity         = 2
  enable_auto_scale        = true
  multi_zone_subnet_policy = "EQUALITY"
  retry_policy             = "INCREMENTAL_INTERVALS"
  node_os                  = "tlinux3.1x86_64"
  deletion_protection      = true
  delete_keep_instance     = false

  auto_scaling_config {
    instance_type              = var.node_instance_type
    backup_instance_types      = var.backup_instance_types
    system_disk_type           = "CLOUD_SSD"
    system_disk_size           = 50
    orderly_security_group_ids = [var.app_security_group_id]
    instance_charge_type       = "POSTPAID_BY_HOUR"
    internet_charge_type       = "TRAFFIC_POSTPAID_BY_HOUR"
    internet_max_bandwidth_out = 0
    public_ip_assigned         = false
    key_ids                    = var.ssh_key_ids
    enhanced_security_service  = true
    enhanced_monitor_service   = true
  }

  labels = {
    "ai-wardrobe/environment" = "staging"
    "ai-wardrobe/node-pool"   = "system"
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [
    tencentcloud_route_table_association.app,
    tencentcloud_security_group_rule_set.tke_endpoint,
  ]
}

resource "tencentcloud_kubernetes_cluster_endpoint" "private" {
  cluster_id                      = tencentcloud_kubernetes_cluster.staging.id
  cluster_internet                = false
  cluster_intranet                = true
  cluster_intranet_subnet_id      = var.app_subnet_ids[0]
  cluster_intranet_security_group = tencentcloud_security_group.tke_endpoint.id

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [tencentcloud_kubernetes_node_pool.system]
}
