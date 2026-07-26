# AI Wardrobe

AI Wardrobe 是一个微信小程序优先的 Personal Style Agent。MVP 聚焦一条可验证的闭环：上传真实穿搭照片、获得文字诊断、生成 Minimal Change Before/After，并完成微信分享与 A/B 投票。

## 仓库结构

```text
backend/   FastAPI Modular Monolith 与独立 Worker
miniapp/   uni-app + Vue 3 微信小程序
infra/     本地 PostgreSQL、pgvector 与 Redis
docs/      产品、技术、质量和实施计划
```

## 环境要求

- Python 3.13
- uv 0.9+
- Node.js 22 LTS
- pnpm 11
- Docker Desktop（仅本地 PostgreSQL/Redis 集成开发需要）

本机没有 Docker 时，仍可运行不依赖外部服务的单元测试和前端检查。

## 初始化

```bash
cp .env.example .env
make install
make infra-up
make migrate
```

## 开发

```bash
make dev-api
make dev-miniapp
```

API 健康检查：

```text
GET http://localhost:8000/health/live
GET http://localhost:8000/health/ready
```

`live` 只判断进程存活；`ready` 会检查 PostgreSQL、Redis，并在启用 COS 时检查 Bucket。依赖异常时返回 503，供负载均衡摘流。

## 本地可观测性

需要本地 Collector 与 Prometheus 时：

```bash
make infra-observability-up
```

然后在 `.env` 中设置：

```text
AIW_OTEL_ENABLED=true
AIW_OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

Grafana 位于 `http://localhost:3000`，Prometheus 位于 `http://localhost:9090`，Alertmanager 位于 `http://localhost:9093`。API、Outbox、Celery Worker 与 AI Provider 共用 Trace 上下文；响应中的 `X-Trace-ID` 可关联结构化日志和调用链。预置 Dashboard 覆盖 API、Worker、AI、Outbox 和产品动作，`infra/observability/alerts.yml` 包含 5xx、P95、Worker、AI Provider、Outbox 和删除任务告警基线。

本地 Alertmanager 仅用于查看告警状态，不向外部发送通知。Staging/Production 必须通过平台密钥管理配置真实通知路由，并完成一次告警送达演练后才能通过发布 Gate。

## Staging 冒烟验收

使用一张已获得本人授权、且不包含无关人员的 JPEG、PNG 或 WebP 照片，执行上传 → 诊断 → 优化 → 分享 → 投票 → 数据清理的完整链路：

```bash
export AIW_SMOKE_ACCESS_TOKEN='<staging access token>'
make staging-smoke \
  STAGING_API_BASE_URL='https://staging.example.com' \
  SMOKE_IMAGE='/absolute/path/to/authorized-photo.jpg'
unset AIW_SMOKE_ACCESS_TOKEN
```

访问令牌只通过环境变量传入，不会进入命令行参数或报告。脚本默认删除本次生成的原图和全部派生数据，并输出 Request ID、Trace ID、状态码与耗时证据；仅在受控人工检查时使用 `--keep-data`。

## 质量检查

```bash
make lint
make typecheck
make test
make security-audit
make build
```

供应链扫描对未批准的 High/Critical 漏洞失败；临时例外必须登记在
`docs/18_供应链安全例外登记_V1.0.md`，包含影响边界、补偿控制、Owner
与到期日。项目范围、架构红线与 Definition of Done 以根目录
`AGENTS.md` 为准。
