terraform {
  required_version = "= 1.15.8"

  required_providers {
    tencentcloud = {
      source  = "tencentcloudstack/tencentcloud"
      version = "= 1.83.11"
    }
  }

  # Staging apply 必须通过 config/backend.local.hcl 配置独立的远程状态。
  # 仓库中的校验命令使用 -backend=false，绝不会创建本地状态。
  backend "s3" {}
}

provider "tencentcloud" {
  region = var.region
}
