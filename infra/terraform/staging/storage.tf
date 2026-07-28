resource "tencentcloud_kms_key" "assets" {
  alias                         = "${local.name_prefix}-assets"
  description                   = "AI Wardrobe Staging private asset encryption"
  key_usage                     = "ENCRYPT_DECRYPT"
  key_rotation_enabled          = true
  is_enabled                    = true
  pending_delete_window_in_days = 30
  tags                          = local.common_tags

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.guardrails]
}

resource "tencentcloud_cos_bucket" "assets" {
  bucket               = local.cos_bucket_name
  acl                  = "private"
  encryption_algorithm = "KMS"
  kms_id               = tencentcloud_kms_key.assets.id
  versioning_enable    = false
  acceleration_enable  = false
  force_clean          = false
  cors_response_vary   = true
  tags                 = local.common_tags

  cors_rules {
    allowed_origins = var.cos_allowed_origins
    allowed_methods = ["PUT"]
    allowed_headers = [
      "Authorization",
      "Content-Type",
      "x-cos-security-token",
      "x-cos-meta-content-sha256",
    ]
    expose_headers  = ["ETag", "x-cos-request-id"]
    max_age_seconds = 300
  }

  lifecycle_rules {
    id            = "abort-incomplete-uploads"
    filter_prefix = "private/"

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "tencentcloud_cam_policy" "cos_upload" {
  name        = "${local.name_prefix}-cos-upload"
  description = "Write-only upload permission for AI Wardrobe Staging"
  tags        = local.common_tags
  document = jsonencode({
    version = "2.0"
    statement = [
      {
        effect   = "allow"
        action   = ["name/cos:PutObject"]
        resource = ["${local.cos_bucket_qcs}/private/*/uploads/*"]
      },
    ]
  })
}

resource "tencentcloud_cam_policy" "cos_runtime" {
  name        = "${local.name_prefix}-cos-runtime"
  description = "Private asset runtime permission for AI Wardrobe Staging"
  tags        = local.common_tags
  document = jsonencode({
    version = "2.0"
    statement = [
      {
        effect = "allow"
        action = [
          "name/cos:GetBucketLocation",
          "name/cos:HeadBucket",
        ]
        resource = [local.cos_bucket_qcs]
      },
      {
        effect = "allow"
        action = [
          "name/cos:DeleteObject",
          "name/cos:GetObject",
          "name/cos:HeadObject",
        ]
        resource = ["${local.cos_bucket_qcs}/private/*"]
      },
      {
        effect = "allow"
        action = [
          "name/cos:DeleteObject",
          "name/cos:GetObject",
          "name/cos:HeadObject",
          "name/cos:PutObject",
        ]
        resource = [
          "${local.cos_bucket_qcs}/private/*/optimizations/*",
          "${local.cos_bucket_qcs}/share-derivatives/*",
        ]
      },
    ]
  })
}

resource "tencentcloud_cam_role_policy_attachment" "cos_upload" {
  role_id   = var.cos_upload_role_id
  policy_id = tencentcloud_cam_policy.cos_upload.id
}

resource "tencentcloud_cam_role_policy_attachment" "cos_runtime" {
  role_id   = var.cos_runtime_role_id
  policy_id = tencentcloud_cam_policy.cos_runtime.id
}
