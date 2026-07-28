# AI Wardrobe Claude 开发入口

本文件是 Claude Code 的快速入口，不替代根目录 `AGENTS.md`。
开始任何任务前必须完整阅读 `AGENTS.md`，并按任务加载对应 `docs/` 文档。

## 当前阶段

- Phase：P0 Hardening。
- 目标：达到 30～50 人封闭测试的 Go/No-Go 标准。
- 主分支：`main`；日常开发分支：`develop`。
- 禁止提前实现 P0.5、P1 或 Phase 2 能力。
- 外部 Staging、COS、微信和 OpenAI 验收必须保留真实证据，不得用 Mock 冒充。

## 产品红线

- Existing Wardrobe First，优先减少决策成本。
- Observation 不等于 Truth；AI 结果必须保留来源、版本与置信度。
- PostgreSQL 是业务真相源，本地状态只能保存必要缓存和恢复元数据。
- 用户图片默认私有；分享资产与私有资产必须隔离。
- AI Provider 必须经 Gateway，结构化输出必须通过 Schema 校验。
- 失败任务不得消耗配额，删除任务必须覆盖 DB、COS、缓存和派生资产。

## 架构边界

- 保持 Modular Monolith，不为封测提前拆微服务。
- 跨模块写入只能经公开应用服务或事件，禁止直接修改其他模块 Repository。
- Celery 管执行，LangGraph 只用于真正的多步骤决策。
- API Server 不搬运图片，上传继续使用 Ticket → COS → Complete。
- 所有写请求考虑幂等、所有权、并发、重试和可观测性。
- 单个工程目录最多 8 个直接文件，运行 `make structure-check` 验证。
- 单个源码文件不得超过 800 行，超过时按真实职责拆分。

## 标准工作流

1. 检查 `git status`，保留用户已有改动。
2. 确认 Phase、Epic、Milestone、验收条件和明确不做项。
3. 按 `AGENTS.md` 的文档路由加载最小必要上下文。
4. 先写失败路径、安全边界和测试，再实现最小垂直切片。
5. 用 `apply_patch` 修改文件，避免无关格式化。
6. 运行受影响范围检查，再运行必要的实库或构建门禁。
7. 同步更新 API、ADR、运维或进度文档。
8. 每个独立变更立即提交并推送 `develop`。

## 常用命令

```bash
make install
make infra-up
make migrate
make dev-api
make worker
make dev-miniapp
make lint
make typecheck
make structure-check
make test
make security-audit
make build
```

Makefile 只提供稳定命令名，实际执行统一进入 `scripts/*.sh`。
命令输出同时写入本地 `logs/`；日志内容不得提交。

## 提交前检查

- Ruff、格式检查、严格 Mypy、ESLint 与 `vue-tsc` 通过。
- 受影响测试通过；涉及持久化时使用真实 PostgreSQL/Redis。
- OpenAPI、Alembic 漂移、微信构建和供应链门禁按影响范围执行。
- 日志不包含 Token、身份标识、私有对象地址、图片 Data URL 或 Prompt 全文。
- 配额释放、Token Fencing、Outbox、删除闭包和弱网恢复未被破坏。
- 报告已运行的检查、未运行原因、外部阻塞和剩余风险。

更多执行细节见 `docs/agent/README.md`；冲突时始终以 `AGENTS.md` 为准。
