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
  app_subnet_cidr  = cidrsubnet(var.vpc_cidr, 4, 0)
  data_subnet_cidr = cidrsubnet(var.vpc_cidr, 8, 16)

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
  }
}
