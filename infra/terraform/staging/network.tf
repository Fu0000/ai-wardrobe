resource "tencentcloud_vpc" "staging" {
  name         = "${local.name_prefix}-vpc"
  cidr_block   = var.vpc_cidr
  is_multicast = false
  tags         = local.common_tags

  depends_on = [terraform_data.guardrails]
}

resource "tencentcloud_subnet" "app" {
  name              = "${local.name_prefix}-app"
  vpc_id            = tencentcloud_vpc.staging.id
  cidr_block        = local.app_subnet_cidr
  availability_zone = var.availability_zone
  is_multicast      = false
  tags              = local.common_tags
}

resource "tencentcloud_subnet" "data" {
  name              = "${local.name_prefix}-data"
  vpc_id            = tencentcloud_vpc.staging.id
  cidr_block        = local.data_subnet_cidr
  availability_zone = var.availability_zone
  is_multicast      = false
  tags              = local.common_tags
}

resource "tencentcloud_security_group" "app" {
  name        = "${local.name_prefix}-app"
  description = "AI Wardrobe Staging API and worker network identity"
  tags        = local.common_tags
}

resource "tencentcloud_security_group_rule_set" "app" {
  security_group_id = tencentcloud_security_group.app.id

  dynamic "ingress" {
    for_each = var.api_ingress_source_cidrs
    content {
      action      = "ACCEPT"
      cidr_block  = ingress.value
      protocol    = "TCP"
      port        = "8000"
      description = "Allow API only from an approved private ingress range"
    }
  }

  ingress {
    action      = "DROP"
    cidr_block  = "0.0.0.0/0"
    protocol    = "ALL"
    port        = "ALL"
    description = "Deny every other inbound connection"
  }

  egress {
    action      = "ACCEPT"
    cidr_block  = "0.0.0.0/0"
    protocol    = "ALL"
    port        = "ALL"
    description = "Provider, registry and Tencent Cloud API access through managed egress"
  }
}

resource "tencentcloud_security_group" "postgresql" {
  name        = "${local.name_prefix}-postgresql"
  description = "AI Wardrobe Staging PostgreSQL private access"
  tags        = local.common_tags
}

resource "tencentcloud_security_group_rule_set" "postgresql" {
  security_group_id = tencentcloud_security_group.postgresql.id

  ingress {
    action             = "ACCEPT"
    source_security_id = tencentcloud_security_group.app.id
    protocol           = "TCP"
    port               = "5432"
    description        = "Allow PostgreSQL only from the application security group"
  }

  ingress {
    action      = "DROP"
    cidr_block  = "0.0.0.0/0"
    protocol    = "ALL"
    port        = "ALL"
    description = "Deny every other PostgreSQL inbound connection"
  }
}

resource "tencentcloud_security_group" "redis" {
  name        = "${local.name_prefix}-redis"
  description = "AI Wardrobe Staging Redis private access"
  tags        = local.common_tags
}

resource "tencentcloud_security_group_rule_set" "redis" {
  security_group_id = tencentcloud_security_group.redis.id

  ingress {
    action             = "ACCEPT"
    source_security_id = tencentcloud_security_group.app.id
    protocol           = "TCP"
    port               = "6379"
    description        = "Allow Redis only from the application security group"
  }

  ingress {
    action      = "DROP"
    cidr_block  = "0.0.0.0/0"
    protocol    = "ALL"
    port        = "ALL"
    description = "Deny every other Redis inbound connection"
  }
}
