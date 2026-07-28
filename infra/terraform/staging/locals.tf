data "tencentcloud_user_info" "current" {}

locals {
  name_prefix = "${var.project_name}-${var.environment}"
  common_tags = merge(
    {
      environment = var.environment
      managed_by  = "terraform"
      project     = var.project_name
    },
    var.extra_tags,
  )

  # 子网从固定 VPC 确定性派生，避免调用方传入越界或重叠 CIDR。
  app_subnet_cidr         = cidrsubnet(var.vpc_cidr, 4, 0)
  data_subnet_cidr        = cidrsubnet(var.vpc_cidr, 8, 16)
  app_standby_subnet_cidr = cidrsubnet(var.vpc_cidr, 4, 2)

  cos_bucket_name = "${var.cos_bucket_prefix}-${data.tencentcloud_user_info.current.app_id}"
  cos_bucket_qcs  = "qcs::cos:${var.region}:uid/${data.tencentcloud_user_info.current.app_id}:${local.cos_bucket_name}"
}

resource "terraform_data" "guardrails" {
  input = {
    environment = var.environment
    region      = var.region
  }

  lifecycle {
    precondition {
      condition     = var.cos_upload_role_id != var.cos_runtime_role_id
      error_message = "上传与运行时 CAM Role 必须分离，不能复用同一身份。"
    }

    precondition {
      condition     = var.availability_zone != var.standby_availability_zone
      error_message = "PostgreSQL Primary 与 Standby 必须位于不同可用区。"
    }
  }
}

module "data_services" {
  source = "./modules/data"

  name_prefix                  = local.name_prefix
  region                       = var.region
  primary_availability_zone    = var.availability_zone
  standby_availability_zone    = var.standby_availability_zone
  vpc_id                       = tencentcloud_vpc.staging.id
  subnet_id                    = tencentcloud_subnet.data.id
  postgresql_security_group_id = tencentcloud_security_group.postgresql.id
  redis_security_group_id      = tencentcloud_security_group.redis.id
  kms_key_id                   = tencentcloud_kms_key.assets.id
  postgresql_root_password     = var.postgresql_root_password
  redis_password               = var.redis_password
  redis_replica_zone_ids       = var.redis_replica_zone_ids
  tags                         = local.common_tags

  depends_on = [terraform_data.guardrails]
}

module "compute" {
  source = "./modules/compute"

  name_prefix               = local.name_prefix
  vpc_id                    = tencentcloud_vpc.staging.id
  app_subnet_ids            = [tencentcloud_subnet.app.id, tencentcloud_subnet.app_standby.id]
  app_subnet_cidrs          = [local.app_subnet_cidr, local.app_standby_subnet_cidr]
  app_security_group_id     = tencentcloud_security_group.app.id
  primary_availability_zone = var.availability_zone
  cluster_version           = var.tke_cluster_version
  cluster_cidr              = var.tke_cluster_cidr
  service_cidr              = var.tke_service_cidr
  node_instance_type        = var.tke_node_instance_type
  backup_instance_types     = var.tke_backup_instance_types
  ssh_key_ids               = var.tke_ssh_key_ids
  nat_eip_bandwidth_mbps    = var.nat_eip_bandwidth_mbps
  tags                      = local.common_tags

  depends_on = [terraform_data.guardrails]
}
