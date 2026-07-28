# Staging Terraform contract

本根模块只允许创建 AI Wardrobe Staging 资源，不能通过变量切换到 Production。当前批次
覆盖 INF-02 的网络与 AST-01 的私有对象存储代码合同；真实腾讯云资源、连通性和安全
审计在取得专用账号、远程状态与审批后才可验收。

## 安全边界

- VPC 只有应用与数据私有子网，不创建公网 IP。
- API 8000 只接受显式私网 CIDR；PostgreSQL 5432 和 Redis 6379 只接受应用安全组。
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
4. 执行：

```bash
terraform init -reconfigure -backend-config=config/backend.local.hcl
terraform plan \
  -input=false \
  -out=staging.tfplan \
  -var-file=config/staging.local.tfvars
terraform apply staging.tfplan
```

Apply 前必须由另一位 Reviewer 核对计划、月成本、CAM Role、CIDR、CORS Origin 和远程
状态配置。禁止使用 `-auto-approve`，禁止把 `*.tfplan`、状态、凭据或实际 Bucket 名
提交到 Git。

## 当前不代表完成

`terraform validate` 只证明语法和 Provider Schema 合法。INF-02 在 API、PostgreSQL、
Redis、COS 与 TKE 真正部署并完成连通性/故障注入后才能转为 `DONE`；AST-01 在真实
私有 Bucket 上完成上传、读取隔离、Signed URL 过期与删除后旧 URL 失效审计后才能解除
`BLOCKED`。
