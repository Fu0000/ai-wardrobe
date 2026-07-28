terraform {
  required_version = "= 1.15.8"

  required_providers {
    tencentcloud = {
      source  = "tencentcloudstack/tencentcloud"
      version = "= 1.83.11"
    }
  }
}
