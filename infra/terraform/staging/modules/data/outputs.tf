output "postgresql_connection" {
  value = {
    host   = tencentcloud_postgresql_instance.staging.private_access_ip
    port   = tencentcloud_postgresql_instance.staging.private_access_port
    ca_url = tencentcloud_postgresql_instance_ssl_config.staging.ca_url
  }
  sensitive = true
}

output "redis_instance_id" {
  value     = tencentcloud_redis_instance.staging.id
  sensitive = true
}

output "contract" {
  value = {
    postgresql_major_version = local.postgresql_major_version
    postgresql_tde_enabled   = true
    postgresql_tls_enabled   = true
    postgresql_public_access = false
    postgresql_backup_days   = 14
    redis_version            = "7.0"
    redis_tls_enabled        = true
    redis_public_access      = false
    redis_replicas           = local.redis_replicas
  }
}
