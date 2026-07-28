mock_provider "tencentcloud" {}

override_data {
  target = data.tencentcloud_user_info.current
  values = {
    app_id    = "1250000000"
    owner_uin = "100000000001"
    uin       = "100000000002"
  }
}

override_data {
  target = module.data_services.data.tencentcloud_postgresql_db_versions.required
  values = {
    version_set = [
      {
        db_major_version         = "18"
        db_version               = "18.0"
        db_kernel_version        = "v18.0_r1.0"
        db_engine                = "postgresql"
        status                   = "AVAILABLE"
        supported_feature_names  = ["TDE"]
        available_upgrade_target = []
      },
    ]
  }
}

override_data {
  target = module.data_services.data.tencentcloud_postgresql_specinfos.required
  values = {
    list = [
      {
        id                  = "pg-spec-test"
        cpu                 = 2
        memory              = 4
        engine_version      = "18.0"
        engine_version_name = "PostgreSQL 18"
        qps                 = 1000
        storage_min         = 20
        storage_max         = 1000
      },
    ]
  }
}

override_data {
  target = module.data_services.data.tencentcloud_redis_zone_config.required
  values = {
    list = [
      {
        zone                = "ap-guangzhou-6"
        type_id             = 17
        type                = "master_slave_redis"
        version             = "Redis 7.0"
        shard_memories      = [1024]
        mem_sizes           = [1024]
        redis_shard_nums    = [1]
        redis_replicas_nums = [2]
      },
    ]
  }
}

run "staging_foundation_contract" {
  command = plan

  variables {
    availability_zone         = "ap-guangzhou-6"
    standby_availability_zone = "ap-guangzhou-7"
    api_ingress_source_cidrs = [
      "10.32.0.0/20",
    ]
    cos_allowed_origins = [
      "https://servicewechat.com",
      "https://staging.example.com",
    ]
    cos_upload_role_id       = "4611686018427000001"
    cos_runtime_role_id      = "4611686018427000002"
    postgresql_root_password = "PgStaging#2026Strong"
    redis_password           = "Redis#2026Aa"
    redis_replica_zone_ids   = [100006, 100007]
    tke_cluster_version      = "1.30.0"
    tke_node_instance_type   = "SA5.MEDIUM4"
    tke_backup_instance_types = [
      "S5.MEDIUM4",
    ]
    tke_ssh_key_ids = [
      "skey-test000001",
    ]
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

  assert {
    condition = (
      output.data_service_contract.postgresql_major_version == "18" &&
      output.data_service_contract.postgresql_tde_enabled &&
      output.data_service_contract.postgresql_tls_enabled &&
      output.data_service_contract.postgresql_public_access == false
    )
    error_message = "PostgreSQL must remain version 18, private, TLS-enabled and TDE-enabled."
  }

  assert {
    condition = (
      output.data_service_contract.redis_version == "7.0" &&
      output.data_service_contract.redis_tls_enabled &&
      output.data_service_contract.redis_public_access == false &&
      output.data_service_contract.redis_replicas == 2
    )
    error_message = "Redis must remain version 7.0, private, TLS-enabled and replicated."
  }

  assert {
    condition = (
      output.compute_contract.cluster_private_only &&
      output.compute_contract.cluster_deletion_protection &&
      output.compute_contract.node_public_ip == false &&
      output.compute_contract.node_min_size == 2 &&
      output.compute_contract.node_max_size == 4 &&
      output.compute_contract.cross_zone_subnet_count == 2 &&
      output.compute_contract.audit_enabled &&
      output.compute_contract.event_persistence_enabled &&
      output.compute_contract.audit_retention_days == 15 &&
      output.compute_contract.edge_access_retention_days == 15 &&
      output.compute_contract.edge_clb_delete_protection &&
      output.compute_contract.edge_clb_cross_zone &&
      output.compute_contract.edge_clb_public_ipv4 &&
      output.compute_contract.edge_clb_pass_to_target &&
      output.compute_contract.edge_clb_bandwidth_mbps == 20 &&
      output.compute_contract.edge_public_ports == [80, 443]
    )
    error_message = "TKE must remain private, deletion-protected, cross-zone and audit-enabled."
  }

  assert {
    condition = (
      output.compute_contract.pod_cidr == "172.20.0.0/16" &&
      output.compute_contract.service_cidr == "172.21.0.0/20" &&
      output.compute_contract.nat_product_version == 2
    )
    error_message = "TKE network ranges and managed NAT contract changed without review."
  }
}
