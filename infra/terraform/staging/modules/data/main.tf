locals {
  postgresql_major_version = "18"
  postgresql_cpu           = 2
  postgresql_memory_gb     = 4
  postgresql_storage_gb    = 50
  postgresql_storage_type  = "CLOUD_SSD"
  redis_type_id            = 17
  redis_memory_mb          = 1024
  redis_replicas           = 2

  available_postgresql_versions = [
    for version in data.tencentcloud_postgresql_db_versions.required.version_set :
    version
    if version.status == "AVAILABLE" &&
    version.db_major_version == local.postgresql_major_version &&
    contains(version.supported_feature_names, "TDE")
  ]
  matching_postgresql_specs = [
    for spec in data.tencentcloud_postgresql_specinfos.required.list :
    spec
    if startswith(spec.engine_version, local.postgresql_major_version) &&
    spec.cpu == local.postgresql_cpu &&
    spec.memory == local.postgresql_memory_gb &&
    spec.storage_min <= local.postgresql_storage_gb &&
    spec.storage_max >= local.postgresql_storage_gb
  ]
  matching_redis_zones = [
    for config in data.tencentcloud_redis_zone_config.required.list :
    config
    if config.zone == var.primary_availability_zone &&
    contains(config.shard_memories, local.redis_memory_mb) &&
    contains(config.redis_replicas_nums, local.redis_replicas)
  ]
}

data "tencentcloud_postgresql_db_versions" "required" {
  db_major_version = local.postgresql_major_version
}

data "tencentcloud_postgresql_specinfos" "required" {
  availability_zone = var.primary_availability_zone
  storage_type      = local.postgresql_storage_type
}

data "tencentcloud_redis_zone_config" "required" {
  region  = var.region
  type_id = local.redis_type_id
}

resource "tencentcloud_postgresql_instance" "staging" {
  name                 = "${var.name_prefix}-postgresql"
  availability_zone    = var.primary_availability_zone
  charge_type          = "POSTPAID_BY_HOUR"
  vpc_id               = var.vpc_id
  subnet_id            = var.subnet_id
  db_major_version     = local.postgresql_major_version
  root_user            = "aiw_admin"
  root_password        = var.postgresql_root_password
  charset              = "UTF8"
  cpu                  = local.postgresql_cpu
  memory               = local.postgresql_memory_gb
  storage              = local.postgresql_storage_gb
  storage_type         = local.postgresql_storage_type
  public_access_switch = false
  security_groups      = [var.postgresql_security_group_id]
  need_support_tde     = 1
  kms_key_id           = var.kms_key_id
  kms_region           = var.region
  delete_protection    = true
  wait_switch          = 2
  tags                 = var.tags

  db_node_set {
    role = "Primary"
    zone = var.primary_availability_zone
  }

  db_node_set {
    role = "Standby"
    zone = var.standby_availability_zone
  }

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = length(local.available_postgresql_versions) > 0
      error_message = "目标地域没有 AVAILABLE 且支持 TDE 的 PostgreSQL 18，禁止静默降级或关闭加密。"
    }

    precondition {
      condition     = length(local.matching_postgresql_specs) > 0
      error_message = "目标可用区没有 PostgreSQL 18 的 2 vCPU/4 GiB/50 GiB CLOUD_SSD 规格。"
    }
  }

  timeouts {
    create = "60m"
    update = "60m"
  }
}

resource "tencentcloud_postgresql_backup_plan" "staging" {
  db_instance_id               = tencentcloud_postgresql_instance.staging.id
  plan_name                    = "${var.name_prefix}-daily"
  backup_period_type           = "month"
  backup_period                = [for day in range(1, 32) : tostring(day)]
  min_backup_start_time        = "03:00:00"
  max_backup_start_time        = "05:00:00"
  base_backup_retention_period = 14
  log_backup_retention_period  = 14
  backup_method                = "physical"
}

resource "tencentcloud_postgresql_instance_ssl_config" "staging" {
  db_instance_id  = tencentcloud_postgresql_instance.staging.id
  ssl_enabled     = true
  connect_address = tencentcloud_postgresql_instance.staging.private_access_ip
}

resource "tencentcloud_redis_instance" "staging" {
  name               = "${var.name_prefix}-redis"
  availability_zone  = var.primary_availability_zone
  type_id            = local.redis_type_id
  password           = var.redis_password
  mem_size           = local.redis_memory_mb
  redis_replicas_num = local.redis_replicas
  replica_zone_ids   = var.redis_replica_zone_ids
  replicas_read_only = false
  port               = 6379
  vpc_id             = var.vpc_id
  subnet_id          = var.subnet_id
  security_groups    = [var.redis_security_group_id]
  charge_type        = "POSTPAID"
  no_auth            = false
  wan_address_switch = "close"
  wait_switch        = 1
  force_delete       = false
  tags               = var.tags

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = length(local.matching_redis_zones) > 0
      error_message = "目标可用区不支持 Redis 7.0 标准版 1 GiB/2 副本规格。"
    }
  }
}

resource "tencentcloud_redis_ssl" "staging" {
  instance_id = tencentcloud_redis_instance.staging.id
  ssl_config  = "enabled"
}

resource "tencentcloud_redis_backup_config" "staging" {
  redis_id    = tencentcloud_redis_instance.staging.id
  backup_time = "04:00-05:00"
}

resource "tencentcloud_redis_maintenance_window" "staging" {
  instance_id = tencentcloud_redis_instance.staging.id
  start_time  = "02:00"
  end_time    = "04:00"
}
