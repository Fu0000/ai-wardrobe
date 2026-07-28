variable "name_prefix" {
  type = string
}

variable "region" {
  type = string
}

variable "primary_availability_zone" {
  type = string
}

variable "standby_availability_zone" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "postgresql_security_group_id" {
  type = string
}

variable "redis_security_group_id" {
  type = string
}

variable "kms_key_id" {
  type = string
}

variable "postgresql_root_password" {
  type      = string
  sensitive = true
}

variable "redis_password" {
  type      = string
  sensitive = true
}

variable "redis_replica_zone_ids" {
  type = list(number)
}

variable "tags" {
  type = map(string)
}
