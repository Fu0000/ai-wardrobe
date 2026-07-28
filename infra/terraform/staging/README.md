# Staging Terraform contract

本根模块只允许创建 AI Wardrobe Staging 资源，不能通过变量切换到 Production。当前批次
覆盖 INF-02 的网络、TKE、Managed PostgreSQL/Redis、CLS 与 AST-01 的私有对象存储代码合同；
真实腾讯云资源、连通性和安全审计在取得专用账号、远程状态与审批后才可验收。

## 安全边界

- VPC 包含跨可用区的两个应用私有子网和单一数据私有子网。TKE 节点不分配公网 IP，
  仅共享一个受 Terraform 保护的标准 NAT EIP 出站。
- API 8000 只接受显式私网 CIDR；PostgreSQL 5432 和 Redis 6379 只接受应用安全组。
- TKE 使用 Global Router 隔离的 Pod/Service CIDR、两个跨区节点起步并允许扩至四个；
  控制面关闭公网 Endpoint，私网 443 仅接受两个应用子网，Cluster 与 Node Pool 均启用
  删除保护。
- TKE 审计和 Kubernetes Event 分别写入 KMS 加密 CLS Topic，并保留 15 天；匿名
  OIDC Discovery RBAC 不会自动创建。
- PostgreSQL 固定主版本 18，真实 Plan 动态检查目标地域的版本/规格可用性；Primary 与
  Standby 跨可用区，启用 TDE、TLS、删除保护以及 14 天物理/日志备份。
- Redis 固定 7.0 标准架构、1 GiB、两个跨区副本，启用认证、TLS、每日备份和维护窗口，
  关闭公网地址与强制删除。
- COS 强制私有 ACL、KMS 加密、精确 HTTPS CORS Origin、非通配请求头和禁止加速。
- Bucket 不启用版本控制，避免当前删除闭包遗漏历史对象版本；应用层删除仍需在真实 COS
  运行 `make staging-security-audit` 验收。
- `force_clean=false` 且 Bucket/KMS 均启用 `prevent_destroy`，普通 `destroy` 不能清空
  用户资产或销毁密钥。
- 上传 Role 只能写 `private/<user>/uploads/`；运行时 Role 才能读取私有资产、写入
  `private/<user>/optimizations/` 与 `share-derivatives/` 并执行删除。两个 Role ID
  必须不同，且本模块不创建长期访问密钥。
- 后端使用独立 COS 客户端签发上传 PUT 和执行运行时 GET/PUT/DELETE；临时 STS
  Credential 的 Security Token 可由平台密钥管理注入，禁止把 Role 凭据静态写入镜像。
- Provider 凭据只允许通过 `TENCENTCLOUD_SECRET_ID`、`TENCENTCLOUD_SECRET_KEY` 和
  可选的 `TENCENTCLOUD_SECURITY_TOKEN` 环境变量传入。
- TKE 节点仅使用既有 SSH Key，不配置密码；主/备用 CVM 机型必须在真实 Plan 前确认
  两个目标可用区均有库存。TKE CAM 服务角色必须已获写入指定 CLS Topic 的权限。
- PostgreSQL 与 Redis 密码分别只通过 `TF_VAR_postgresql_root_password` 和
  `TF_VAR_redis_password` 注入；敏感变量会进入 Terraform State，因此远程状态必须
  加密、最小授权、审计并禁止下载到个人设备。

## 本地与 CI 校验

```bash
make terraform-staging-validate
```

该命令使用固定摘要的 Terraform 1.15.8 镜像、锁定的 TencentCloud Provider 1.83.11，
依次执行格式检查、`init -backend=false`、`validate` 和 Mock Plan 合同测试。它不读取
云凭据、不创建状态，也不会访问腾讯云账号。

## 经审批的 Plan / Apply

1. 在专用腾讯云账号中预先创建独立远程状态 Bucket，启用加密、版本保留、审计和受控
   状态锁；它不能由本根模块自举。
2. 将 `config/backend.hcl.example` 复制为被 `.gitignore` 排除的
   `config/backend.local.hcl`，将 `config/staging.tfvars.example` 复制为
   `config/staging.local.tfvars`，填入非敏感资源标识。
3. 使用短期联合身份或受控子账号导出 Provider 环境变量，不要把凭据写入文件或命令行。
4. 从目标地域获取真实 Redis Zone ID、TKE 版本、跨可用区 CVM 主/备用机型和既有
   `skey-*` ID；不得直接复制示例值。
5. 首选从 `develop` 手动运行受保护的 `Terraform Staging Plan` 工作流。环境
   `staging-infrastructure-plan` 必须配置至少一名独立 Reviewer，并注入：

   - `STAGING_TERRAFORM_BACKEND_CONFIG_B64`、`STAGING_TERRAFORM_TFVARS_B64`
   - Provider 的 `STAGING_TENCENTCLOUD_SECRET_*`
   - 仅能读写远程 State 的 `STAGING_TERRAFORM_STATE_*`
   - `STAGING_POSTGRESQL_ROOT_PASSWORD`、`STAGING_REDIS_PASSWORD`

   工作流只允许精确确认短语，执行 `terraform plan -detailed-exitcode`，不包含 Apply
   入口，不上传含敏感值的 Plan，并在结束时删除临时文件。
6. 本地受控 Plan 也必须使用仓库外、权限为 `0700` 的输出目录以及权限为 `0600` 的
   Backend/tfvars 文件：

```bash
export AIW_TERRAFORM_PLAN_CONFIRMATION=I_ACKNOWLEDGE_STAGING_PLAN_READ_ONLY
export AIW_TERRAFORM_BACKEND_CONFIG="$PWD/infra/terraform/staging/config/backend.local.hcl"
export AIW_TERRAFORM_VAR_FILE="$PWD/infra/terraform/staging/config/staging.local.tfvars"
export AIW_TERRAFORM_PLAN_OUTPUT_DIR="$(mktemp -d)"
chmod 700 "$AIW_TERRAFORM_PLAN_OUTPUT_DIR"
export AIW_TERRAFORM_STATE_ACCESS_KEY_ID='<state-only access key>'
export AIW_TERRAFORM_STATE_SECRET_ACCESS_KEY='<state-only secret key>'
make terraform-staging-plan
```

Plan 后仍不自动 Apply。Apply 必须由另一位 Reviewer 核对 Plan SHA、月成本、TKE/CVM
区域容量、CAM Role、CIDR、CORS Origin 和远程状态配置，并确认应用连接串使用
PostgreSQL `sslmode=verify-full` 与 Redis `rediss://...?ssl_cert_reqs=required`。禁止
使用 `-auto-approve`，禁止把 `*.tfplan`、状态、凭据或实际 Bucket 名提交到 Git。

PostgreSQL SSL 资源会输出 CA 下载地址。平台 Secret 管理器必须把经官方来源校验的
PostgreSQL 与 Redis CA 分别写入 `ai-wardrobe-data-ca` 的 `postgresql-ca.pem` 和
`redis-ca.pem`；部署清单以只读方式挂载，缺少任一证书时发布流程失败。

TKE 私网 Kubeconfig 只能进入 VPC 内、带 `ai-wardrobe-staging` 标签的临时
self-hosted GitHub Runner。`Deploy Staging` 和 `AI Canary Staging` 不再允许公网
GitHub-hosted Runner 访问控制面；Runner 必须一次一实例、任务后销毁，不得与 Production
复用。建立该 Runner 和公网 HTTPS CLB/域名/证书仍需云账号、GitHub 注册凭据和审批，
不在本模块中静态保存。

## 当前不代表完成

`terraform validate` 只证明语法和 Provider Schema 合法。INF-02 在 API、PostgreSQL、
Redis、COS、CLS 与 TKE 真正部署，VPC 内临时 Runner 可部署，并完成连通性/故障注入后
才能转为 `DONE`；AST-01 在真实
私有 Bucket 上完成上传、读取隔离、Signed URL 过期与删除后旧 URL 失效审计后才能解除
`BLOCKED`。
