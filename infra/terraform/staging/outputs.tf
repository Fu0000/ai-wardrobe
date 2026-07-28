output "vpc_id" {
  description = "Staging VPC ID。"
  value       = tencentcloud_vpc.staging.id
}

output "app_subnet_id" {
  description = "API、Worker 与 TKE 节点私有子网 ID。"
  value       = tencentcloud_subnet.app.id
}

output "app_standby_subnet_id" {
  description = "TKE 节点跨可用区使用的第二个应用私有子网 ID。"
  value       = tencentcloud_subnet.app_standby.id
}

output "data_subnet_id" {
  description = "Managed PostgreSQL 与 Redis 私有子网 ID。"
  value       = tencentcloud_subnet.data.id
}

output "app_security_group_id" {
  description = "应用网络身份安全组 ID。"
  value       = tencentcloud_security_group.app.id
}

output "postgresql_security_group_id" {
  description = "PostgreSQL 最小入口安全组 ID。"
  value       = tencentcloud_security_group.postgresql.id
}

output "redis_security_group_id" {
  description = "Redis 最小入口安全组 ID。"
  value       = tencentcloud_security_group.redis.id
}

output "cos_bucket_name" {
  description = "私有资产 Bucket 名；按敏感基础设施元数据处理。"
  value       = tencentcloud_cos_bucket.assets.id
  sensitive   = true
}

output "cos_kms_key_id" {
  description = "资产加密 KMS Key ID；按敏感基础设施元数据处理。"
  value       = tencentcloud_kms_key.assets.id
  sensitive   = true
}

output "postgresql_connection" {
  description = "PostgreSQL 私网连接与 CA 元数据；不得写入公开日志。"
  value       = module.data_services.postgresql_connection
  sensitive   = true
}

output "redis_instance_id" {
  description = "Redis 实例 ID；连接密码永不输出。"
  value       = module.data_services.redis_instance_id
  sensitive   = true
}

output "data_service_contract" {
  description = "不含凭据的数据层安全与版本合同。"
  value       = module.data_services.contract
}

output "tke_cluster_id" {
  description = "Staging TKE Cluster ID。"
  value       = module.compute.cluster_id
  sensitive   = true
}

output "tke_private_kubeconfig" {
  description = "仅可进入受控 Secret 并注入 VPC 内临时部署 Runner 的私网 Kubeconfig，禁止写入日志。"
  value       = module.compute.private_kubeconfig
  sensitive   = true
}

output "compute_contract" {
  description = "不含凭据的 TKE、NAT 与 CLS 安全合同。"
  value       = module.compute.contract
}
