output "vpc_id" {
  description = "Staging VPC ID。"
  value       = tencentcloud_vpc.staging.id
}

output "app_subnet_id" {
  description = "API、Worker 与 TKE 节点私有子网 ID。"
  value       = tencentcloud_subnet.app.id
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
