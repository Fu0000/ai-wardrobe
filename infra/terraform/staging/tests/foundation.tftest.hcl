mock_provider "tencentcloud" {}

override_data {
  target = data.tencentcloud_user_info.current
  values = {
    app_id    = "1250000000"
    owner_uin = "100000000001"
    uin       = "100000000002"
  }
}

run "staging_foundation_contract" {
  command = plan

  variables {
    availability_zone = "ap-guangzhou-6"
    api_ingress_source_cidrs = [
      "10.32.0.0/20",
    ]
    cos_allowed_origins = [
      "https://servicewechat.com",
      "https://staging.example.com",
    ]
    cos_upload_role_id  = "4611686018427000001"
    cos_runtime_role_id = "4611686018427000002"
  }

  assert {
    condition     = tencentcloud_vpc.staging.cidr_block == "10.32.0.0/16"
    error_message = "Staging VPC CIDR changed without updating the network contract."
  }

  assert {
    condition = (
      tencentcloud_cos_bucket.assets.acl == "private" &&
      tencentcloud_cos_bucket.assets.encryption_algorithm == "KMS" &&
      tencentcloud_cos_bucket.assets.versioning_enable == false &&
      tencentcloud_cos_bucket.assets.force_clean == false
    )
    error_message = "The asset bucket must remain private, KMS encrypted and deletion-safe."
  }

  assert {
    condition = alltrue([
      for origin in tencentcloud_cos_bucket.assets.cors_rules[0].allowed_origins :
      startswith(origin, "https://") && origin != "*"
    ])
    error_message = "COS CORS must contain only explicit HTTPS origins."
  }

  assert {
    condition = (
      strcontains(
        tencentcloud_cam_policy.cos_upload.document,
        "/private/*/uploads/*",
      ) &&
      !strcontains(
        tencentcloud_cam_policy.cos_upload.document,
        "GetObject",
      )
    )
    error_message = "The upload identity must stay write-only under the real upload prefix."
  }

  assert {
    condition = alltrue([
      for prefix in [
        "/private/*",
        "/private/*/optimizations/*",
        "/share-derivatives/*",
      ] :
      strcontains(tencentcloud_cam_policy.cos_runtime.document, prefix)
    ])
    error_message = "Runtime COS permissions no longer cover the application object-key contract."
  }

  assert {
    condition = (
      tencentcloud_cam_role_policy_attachment.cos_upload.role_id !=
      tencentcloud_cam_role_policy_attachment.cos_runtime.role_id
    )
    error_message = "Upload and runtime permissions must be attached to different identities."
  }
}
