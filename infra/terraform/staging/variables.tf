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
  description = "腾讯云地域；必须同时支持 CLS KMS 加密。"
  type        = string
  default     = "ap-guangzhou"

  validation {
    condition = contains([
      "ap-bangkok",
      "ap-beijing",
      "ap-guangzhou",
      "ap-jakarta",
      "ap-seoul",
      "ap-shanghai",
      "ap-singapore",
      "ap-tokyo",
      "eu-frankfurt",
    ], var.region)
    error_message = "region 必须是当前 Provider 明确支持 CLS KMS 加密的地域。"
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

variable "tke_cluster_version" {
  description = "目标地域控制台当前支持的精确 TKE Kubernetes 版本，例如 1.30.0。"
  type        = string

  validation {
    condition     = can(regex("^1\\.(?:[2-9][0-9])\\.[0-9]+$", var.tke_cluster_version))
    error_message = "tke_cluster_version 必须是 1.x.y 形式的精确版本，且需在真实 Plan 前确认地域支持。"
  }
}

variable "tke_cluster_cidr" {
  description = "TKE Global Router Pod CIDR，不得与 VPC 或 Service CIDR 重叠。"
  type        = string
  default     = "172.20.0.0/16"

  validation {
    condition     = var.tke_cluster_cidr == "172.20.0.0/16"
    error_message = "Staging TKE Pod CIDR 固定为 172.20.0.0/16；变更需网络评审。"
  }
}

variable "tke_service_cidr" {
  description = "TKE Service CIDR，不得与 VPC 或 Pod CIDR 重叠。"
  type        = string
  default     = "172.21.0.0/20"

  validation {
    condition     = var.tke_service_cidr == "172.21.0.0/20"
    error_message = "Staging TKE Service CIDR 固定为 172.21.0.0/20；变更需网络评审。"
  }
}

variable "tke_node_instance_type" {
  description = "经目标地域售卖查询确认的 TKE 节点主 CVM 机型。"
  type        = string

  validation {
    condition     = can(regex("^[A-Z][A-Z0-9]*\\.[A-Z0-9]+[0-9]$", var.tke_node_instance_type))
    error_message = "tke_node_instance_type 必须是腾讯云 CVM 机型标识。"
  }
}

variable "tke_backup_instance_types" {
  description = "主机型无库存时使用的至少一个不同备用 CVM 机型。"
  type        = list(string)

  validation {
    condition = (
      length(var.tke_backup_instance_types) > 0 &&
      length(distinct(var.tke_backup_instance_types)) == length(var.tke_backup_instance_types) &&
      !contains(var.tke_backup_instance_types, var.tke_node_instance_type) &&
      alltrue([
        for instance_type in var.tke_backup_instance_types :
        can(regex("^[A-Z][A-Z0-9]*\\.[A-Z0-9]+[0-9]$", instance_type))
      ])
    )
    error_message = "tke_backup_instance_types 必须至少包含一个与主机型不同且不重复的合法机型。"
  }
}

variable "tke_ssh_key_ids" {
  description = "TKE 节点使用的既有 CVM SSH Key ID；禁止节点密码登录。"
  type        = list(string)

  validation {
    condition = (
      length(var.tke_ssh_key_ids) > 0 &&
      length(distinct(var.tke_ssh_key_ids)) == length(var.tke_ssh_key_ids) &&
      alltrue([
        for key_id in var.tke_ssh_key_ids :
        can(regex("^skey-[a-z0-9]{8,}$", key_id))
      ])
    )
    error_message = "tke_ssh_key_ids 必须是非空、不重复的腾讯云 skey-* ID 列表。"
  }
}

variable "nat_eip_bandwidth_mbps" {
  description = "TKE 节点共享 NAT EIP 的最大出站带宽，单位 Mbps。"
  type        = number
  default     = 20

  validation {
    condition     = contains([5, 10, 20, 50, 100], var.nat_eip_bandwidth_mbps)
    error_message = "nat_eip_bandwidth_mbps 必须是 5、10、20、50 或 100 Mbps。"
  }
}

variable "extra_tags" {
  description = "附加非敏感资源标签。"
  type        = map(string)
  default     = {}
}
