variable "name_prefix" {
  description = "资源名称前缀。"
  type        = string
}

variable "vpc_id" {
  description = "Staging VPC ID。"
  type        = string
}

variable "app_subnet_ids" {
  description = "位于不同可用区的两个应用私有子网 ID。"
  type        = list(string)

  validation {
    condition     = length(var.app_subnet_ids) == 2
    error_message = "app_subnet_ids 必须恰好包含两个跨可用区子网。"
  }
}

variable "app_subnet_cidrs" {
  description = "允许访问 TKE 私网控制面的两个应用子网 CIDR。"
  type        = list(string)

  validation {
    condition = (
      length(var.app_subnet_cidrs) == 2 &&
      alltrue([
        for cidr in var.app_subnet_cidrs :
        can(cidrhost(cidr, 0)) && cidr != "0.0.0.0/0"
      ])
    )
    error_message = "app_subnet_cidrs 必须恰好包含两个非全网 CIDR。"
  }
}

variable "app_security_group_id" {
  description = "TKE 节点使用的应用安全组 ID。"
  type        = string
}

variable "primary_availability_zone" {
  description = "标准 NAT 所在主可用区。"
  type        = string
}

variable "standby_availability_zone" {
  description = "公网 CLB 灾备可用区。"
  type        = string
}

variable "cluster_version" {
  description = "精确 TKE Kubernetes 版本。"
  type        = string
}

variable "cluster_cidr" {
  description = "Global Router Pod CIDR。"
  type        = string
}

variable "service_cidr" {
  description = "Kubernetes Service CIDR。"
  type        = string
}

variable "node_instance_type" {
  description = "节点主 CVM 机型。"
  type        = string
}

variable "backup_instance_types" {
  description = "节点备用 CVM 机型。"
  type        = list(string)
}

variable "ssh_key_ids" {
  description = "节点 SSH Key ID。"
  type        = list(string)
}

variable "nat_eip_bandwidth_mbps" {
  description = "共享 NAT EIP 出站带宽。"
  type        = number
}

variable "edge_clb_bandwidth_mbps" {
  description = "公网 CLB 出站带宽。"
  type        = number
}

variable "tags" {
  description = "统一资源标签。"
  type        = map(string)
}
