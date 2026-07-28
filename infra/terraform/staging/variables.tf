variable "environment" {
  description = "部署环境。当前根模块故意只允许 Staging。"
  type        = string
  default     = "staging"

  validation {
    condition     = var.environment == "staging"
    error_message = "此根模块只允许部署到 staging，禁止复用到 Production。"
  }
}

variable "project_name" {
  description = "资源名称与标签使用的项目标识。"
  type        = string
  default     = "ai-wardrobe"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.project_name))
    error_message = "project_name 必须是 3～32 位小写字母、数字或连字符。"
  }
}

variable "region" {
  description = "腾讯云地域，例如 ap-guangzhou。"
  type        = string
  default     = "ap-guangzhou"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+$", var.region))
    error_message = "region 必须使用腾讯云地域标识，例如 ap-guangzhou。"
  }
}

variable "availability_zone" {
  description = "Staging 主可用区，例如 ap-guangzhou-6。"
  type        = string

  validation {
    condition     = startswith(var.availability_zone, "${var.region}-")
    error_message = "availability_zone 必须属于选定的 region。"
  }
}

variable "standby_availability_zone" {
  description = "Managed PostgreSQL Standby 使用的不同可用区。"
  type        = string

  validation {
    condition     = startswith(var.standby_availability_zone, "${var.region}-")
    error_message = "standby_availability_zone 必须属于选定的 region。"
  }
}

variable "vpc_cidr" {
  description = "Staging VPC 私网地址段；当前拓扑固定，变更必须经过网络评审。"
  type        = string
  default     = "10.32.0.0/16"

  validation {
    condition     = var.vpc_cidr == "10.32.0.0/16"
    error_message = "当前 Staging 拓扑固定使用 10.32.0.0/16；变更需更新并评审网络合同。"
  }
}

variable "api_ingress_source_cidrs" {
  description = "允许访问 API 8000 端口的精确私网入口/负载均衡地址段。"
  type        = list(string)
  default     = ["10.32.0.0/16"]

  validation {
    condition = (
      length(var.api_ingress_source_cidrs) > 0 &&
      alltrue([
        for cidr in var.api_ingress_source_cidrs :
        can(cidrhost(cidr, 0)) &&
        can(regex("^10\\.32\\.[0-9]{1,3}\\.[0-9]{1,3}/(?:1[6-9]|2[0-9]|3[0-2])$", cidr)) &&
        cidr != "0.0.0.0/0"
      ])
    )
    error_message = "api_ingress_source_cidrs 必须是 10.32.0.0/16 内的非空 CIDR 列表。"
  }
}

variable "cos_bucket_prefix" {
  description = "COS Bucket 前缀；账号 AppID 会自动追加。"
  type        = string
  default     = "ai-wardrobe-staging-assets"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{5,38}$", var.cos_bucket_prefix))
    error_message = "cos_bucket_prefix 必须是 6～39 位小写字母、数字或连字符。"
  }
}

variable "cos_allowed_origins" {
  description = "允许直传的精确 HTTPS Origin；禁止通配符与路径。"
  type        = list(string)

  validation {
    condition = (
      length(var.cos_allowed_origins) > 0 &&
      alltrue([
        for origin in var.cos_allowed_origins :
        origin != "*" &&
        can(regex("^https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?$", origin))
      ])
    )
    error_message = "cos_allowed_origins 必须是非空的精确 HTTPS Origin 列表，禁止 * 和路径。"
  }
}

variable "cos_upload_role_id" {
  description = "仅获 private/<user>/uploads/ PutObject 策略的既有 CAM Role ID。"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{10,32}$", var.cos_upload_role_id))
    error_message = "cos_upload_role_id 必须是 10～32 位数字 CAM Role ID。"
  }
}

variable "cos_runtime_role_id" {
  description = "获私有资产读、派生图写和删除策略的既有 CAM Role ID。"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{10,32}$", var.cos_runtime_role_id))
    error_message = "cos_runtime_role_id 必须是 10～32 位数字 CAM Role ID。"
  }
}

variable "postgresql_root_password" {
  description = "PostgreSQL 管理账号密码；仅通过 TF_VAR 环境变量或受控 Secret 注入。"
  type        = string
  sensitive   = true

  validation {
    condition = (
      length(var.postgresql_root_password) >= 16 &&
      length(var.postgresql_root_password) <= 32 &&
      can(regex("[a-z]", var.postgresql_root_password)) &&
      can(regex("[A-Z]", var.postgresql_root_password)) &&
      can(regex("[0-9]", var.postgresql_root_password)) &&
      can(regex("[^A-Za-z0-9]", var.postgresql_root_password))
    )
    error_message = "postgresql_root_password 必须为 16～32 位，并包含大小写字母、数字和特殊字符。"
  }
}

variable "redis_password" {
  description = "Redis 密码；仅通过 TF_VAR 环境变量或受控 Secret 注入。"
  type        = string
  sensitive   = true

  validation {
    condition = (
      length(var.redis_password) >= 12 &&
      length(var.redis_password) <= 16 &&
      can(regex("[a-z]", var.redis_password)) &&
      can(regex("[A-Z]", var.redis_password)) &&
      can(regex("[0-9]", var.redis_password)) &&
      can(regex("[^A-Za-z0-9]", var.redis_password))
    )
    error_message = "redis_password 必须为 12～16 位，并包含大小写字母、数字和特殊字符。"
  }
}

variable "redis_replica_zone_ids" {
  description = "Redis 两个副本所在可用区的数字 ID，必须来自目标地域容量查询。"
  type        = list(number)

  validation {
    condition = (
      length(var.redis_replica_zone_ids) == 2 &&
      length(distinct(var.redis_replica_zone_ids)) == 2 &&
      alltrue([for zone_id in var.redis_replica_zone_ids : zone_id > 0])
    )
    error_message = "redis_replica_zone_ids 必须包含两个不同的正数可用区 ID。"
  }
}

variable "extra_tags" {
  description = "附加非敏感资源标签。"
  type        = map(string)
  default     = {}
}
