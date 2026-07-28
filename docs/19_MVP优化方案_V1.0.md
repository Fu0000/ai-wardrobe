# AI Wardrobe MVP 优化方案 V1.0

- 文档状态：实现基线
- 适用范围：P0 Hardening
- 基线快照：2026-07-26，Commit `938fd77`
- 上游依据：`docs/12`、`docs/13`、`docs/14`、`docs/15`、`docs/16`

## 一、目的与边界

本文件是 P0 Hardening 阶段的唯一优化执行清单，用于把「代码已写完」推进到「可通过封闭测试 Go/No-Go」。

每一条优化项都必须满足：

- 有 `file:line` 级别的现状证据，不接受推测。
- 有明确的验收口径，可被 `docs/16` 的 Gate 引用。
- 归属 P0 Hardening，不夹带 P0.5 及以后的能力。

本文件不重复各上游文档已定义的契约，只记录「文档要求」与「代码现状」之间的差额。冲突时按 `AGENTS.md` 第 2 节的决策优先级处理。

## 二、现状基线

复核基于当前工作树，不使用历史结论。

| 维度 | 现状 |
|---|---|
| 后端测试 | 38 个文件，145 个测试函数，`ruff` 与 `mypy --strict`（88 文件）全通过 |
| 集成测试 | `tests/integration/platform/test_infrastructure.py` 已存在，3 个测试，默认 `skip` |
| 小程序测试 | 5 个文件，13 个用例，零页面测试、零 services 测试 |
| 数据库迁移 | 10 版 Alembic，CI 执行空库升级、模型漂移检查与离线 SQL 渲染 |
| 埋点事件 | `docs/15` 定义 19 个，已实现 4 个 |
| Eval 数据集 | 诊断与优化各 1 行示例，`docs/12` 要求各 50+ |
| 发布状态 | `docs/16` 最终决策为 `NO-GO` |

工程基础是可靠的：所有权校验在 Repository 层强制、幂等键覆盖全部写入口、执行租约与 Token Fencing 已实现、Outbox 已落地、生产配置校验完整。本文件记录的是这套基础之上的缺口，不是对基础的否定。

已在本轮复核前修复、不再列入的历史问题：`Dockerfile` 的 `--forwarded-allow-ips=*`（已改为 `--no-proxy-headers` 并引入 `trusted_proxy_cidrs`）、`identity_encryption_key` 缺生产校验（已补 Fernet 格式与本地默认值双重校验）。

## 三、优先级定义

| 级别 | 含义 | 处理规则 |
|---|---|---|
| P0 | 数据正确性、隐私合规、用户资产损失 | 封测前必须清零，不接受操作手册绕行 |
| P1 | 阻断 `docs/16` 某个 Gate 转为 `PASS` | 封测前必须完成或取得书面例外 |
| P2 | 架构债，影响后续迭代速度与回归成本 | 允许排期到封测期间并行推进 |

## 四、P0 优化项

### FIX-01 账号注销遗漏 `beta_feedback`

**证据**：`backend/app/modules/governance/deletion_repository.py:281-336` 的 `purge_account` 清理了 13 类实体，未包含 `BetaFeedback`；该文件 import 块未引入 `app.modules.feedback.models`。`BetaFeedback.user_id` 虽为 `ondelete="CASCADE"`（`backend/app/modules/feedback/models.py:42-45`），但 `purge_account` 只把用户状态置为 `DELETED`（第 334-336 行），不删除 `users` 行，级联永不触发。

**影响**：`BetaFeedback.message` 为最长 2000 字的用户自由文本，另含 `trace_id`、`platform`、`system_version`。注销后完整留存，而 `deletion_executor.py` 仍上报 `BUSINESS_DATA_PURGED`、API 回复「账户数据已删除」，构成不实的合规声明。属 `docs/16` 第三节「删除后仍可访问」的 Blocker 定义。

**方案**：

1. 在 `purge_account` 中补 `delete(BetaFeedback).where(BetaFeedback.user_id == user_id)`，置于 `UserProfile` 删除之前。
2. 新增反射式回归测试：遍历 `Base.metadata` 中所有含 `user_id` 列的表，断言 `purge_account` 后该用户零残留。

第 2 步是本条的核心。当前没有任何结构性机制强制新增的用户表注册进删除闭包，`feedback` 模块的遗漏就是该缺失的直接结果。补一次数据不解决问题，补一个会自动发现下一次遗漏的测试才解决。

**验收**：新增表未注册进 `purge_account` 时，该测试必须失败。

### FIX-02 无过期任务回收，配额永久占用

**证据**：`JobStatus.TIMED_OUT` 定义于 `backend/app/modules/jobs/models.py:32`，`state_machine.py:59` 将其列为终态。全仓库对它的 7 处引用全部是读取型判定集合（`optimization/executor.py:410`、`growth/executor.py:188`、`diagnosis/executor.py:245`、`governance/deletion_executor.py:164`），**无任何写入点**。`backend/app/worker/celery_app.py:37-42` 的 `beat_schedule` 只有 `dispatch-outbox` 一条。

**影响**：各 Executor 的 `_prepare` 确实有租约恢复能力，但它仅在同一 Job 被重新投递时才生效。Broker 为 Redis，若 Worker 被 OOM 终止且消息丢失，Job 永久停留 `PROCESSING`，`QuotaRepository.release` 永不被调用，用户免费额度被静默扣除且无法恢复。前端表现为进度条永久停在中途。

**方案**：

1. 新增 `ai_wardrobe.reap_stale_jobs` Celery Beat 任务，扫描 `status in {QUEUED, PROCESSING, QUALITY_CHECKING}` 且 `execution_lease_expires_at < now()` 超过宽限期的 Job。
2. 通过状态机转入 `TIMED_OUT`，同时释放配额预留、写入 Outbox 事件。
3. 宽限期取该任务类型 `execution_lease_seconds` 的 2 倍，避免与正常的租约续期竞争。

**验收**：集成测试构造一个租约过期的 `PROCESSING` Job，执行回收后断言状态为 `TIMED_OUT` 且配额计数已回滚。

### FIX-03 Outbox 无死信，毒事件永久重试

**证据**：`OutboxStatus` 只有 `PENDING / PROCESSING / PUBLISHED / FAILED` 四值（`backend/app/modules/events/models.py:13-17`）。`mark_failed`（`backend/app/modules/events/repository.py:89-106`）无条件递增 `attempt_count` 并重排 `next_retry_at`，无上限判断；`retry_delay` 封顶 900 秒。`claim_batch`（第 27-49 行）按 `next_retry_at <= now` 无差别捞回，不过滤 `attempt_count`。

**影响**：`CeleryEventPublisher` 对 `UNSUPPORTED_JOB_TYPE` / `UNSUPPORTED_EVENT_TYPE` 这类永久性坏载荷抛出的异常，与 Broker 瞬时故障走同一条路径，每 15 分钟重试一次直至永远。更严重的是 `metrics()` 把全部 `FAILED` 计入 `failed_count`，经 `record_outbox_backlog` 送入告警指标 —— 一条毒事件会让 Outbox 告警永久处于触发态，等同于关闭该告警。

**方案**：

1. `OutboxStatus` 增加 `DEAD_LETTER`。
2. `mark_failed` 在 `attempt_count` 超过阈值（建议 10 次，约覆盖 2.5 小时重试窗口）时转入 `DEAD_LETTER`，不再排程。
3. `metrics()` 将 `dead_letter_count` 与 `failed_count` 分离上报，`infra/observability/alerts.yml` 为前者单列告警规则。

**验收**：`tests/events/test_outbox_dispatcher.py` 补充用例，断言不可发布事件在达到阈值后停止重试且不再计入 `failed_count`。

### FIX-04 资料页加载失败会静默撤销 AI 授权

**证据**：`miniapp/src/pages/profile/index.vue:11` 的 `hasConsent` 初值为 `false`；第 40-49 行 `loadProfile` 的 catch 分支只写 `errorMessage`，不阻断后续操作；第 52-75 行 `save()` 无任何守卫，第 63 行无条件提交 `has_ai_processing_consent: hasConsent.value`。

**影响**：网络抖动或 5xx 导致资料加载失败后，用户点击「保存设置」即把服务端真实为 `true` 的授权状态降级为 `false`；且第 64 行的条件展开会让 `consent_version` 一并被置空（后端 `identity/api.py:227-229`）。由于 `AI_CONSENT_REQUIRED` 门禁全部 AI 能力，用户会在无任何提示的情况下失去核心功能。

**方案**：`loadProfile` 失败时置 `loadFailed` 标志，模板对该标志渲染重试态而非表单，`save()` 入口处直接拒绝。表单只有在成功读到服务端真值之后才允许提交。

**验收**：新增 store/组件测试，模拟加载失败后调用保存，断言未发出请求。

### FIX-05 上传取消后仍可能自行完成

**证据**：`miniapp/src/stores/assets.ts:114-129`。`cancelled` 守卫只出现在第 124 行的 catch 分支；第 112 行在直传完成后已将 `abortCurrentUpload` 置 `null`，因此进入 `completing` 阶段后取消无中断能力；`completeUpload` 正常返回时第 121 行无条件执行 `setPhase("ready")`。

**影响**：用户已看到「已暂停上传」，状态却被复活为 `ready`，随后可继续进入诊断流程 —— 用户主观上已撤回的照片仍被提交给 AI 处理。这是授权语义问题，不只是状态机瑕疵。

**方案**：在第 120 行之前补 `if (this.phase === "cancelled") { return; }`，与 catch 分支对称。若已产生 `assetId`，同时触发单图删除以免留下孤儿资产。

**验收**：`stores/core-flow.test.ts` 补充用例，在 `completing` 阶段调用 `cancelUpload()` 后断言终态为 `cancelled`。

### FIX-06 注销后 Store 内存态残留

**证据**：`miniapp/src/stores/deletion.ts:145-169` 的 `clearPrivateLocalState` 只处理 `useAssetStore().clearDraft()`（第 146-150 行）、`aiw:` 前缀的持久化存储（第 152-160 行）与 `useAuthStore().clear()`（第 165 行）。该文件 import 块未引入 `useDiagnosisStore`、`useOptimizationStore`、`useShareStore`、`usePhotoDeletionStore`、`useJobStore`。

**影响**：这些 Store 在 `App.vue` 的 `onLaunch` 中 hydrate 后常驻内存。清除 storage 不清除 Pinia state，因此注销当次会话内仍可读到诊断结论、优化前后图、分享记录与追踪中的 Job ID。违反 `docs/14` 第 4.12 节。

**方案**：为每个持有用户数据的 Store 提供统一的 `reset()`，由 `clearPrivateLocalState` 集中调用。命名与调用点集中在一处，避免下次新增 Store 时再次遗漏 —— 与 FIX-01 是同一类结构性问题。

**验收**：`stores/deletion.test.ts` 补充用例，注销后断言各 Store 关键字段回到初值。

### FIX-07 Token 失效后陷入永久失败循环

**证据**：全仓库对 401 / 403 零判断（`grep` 命中仅 410、404、200 三处）。`miniapp/src/stores/auth.ts:72-73` 的 `authenticate()` 仅按本地 `expiresAt > Date.now() + 30_000` 判定有效性。

**影响**：服务端因密钥轮换、账号状态变更或时钟偏移判定 Token 无效时，前端仍认为本地有效，不会重新 `wx.login`，全部请求持续 401 直到本地过期时间自然到达。期间用户无任何可自救的操作路径。

**方案**：在 `services/api.ts` 统一拦截 401 —— 清除本地凭据、重新 `wx.login` 换取 Token、对原请求做一次性重试；重试仍失败则上抛。必须限定单次重试，避免与服务端故障形成放大。同时补 `apiRequest` 缺失的 `timeout`（`services/api.ts:45-75` 无该字段，见 FIX-08）。

**验收**：`services` 层新增测试，断言 401 触发一次重新登录与一次重试，且第二次 401 不再重试。

### FIX-08 `apiRequest` 无超时配置

**证据**：`miniapp/src/services/api.ts:45-75` 的 `uni.request` 配置无 `timeout` 字段，`ApiRequestOptions` 接口（第 21-27 行）也未暴露该参数。对比 `services/assets.ts:130` 的直传已设 60 秒。

**影响**：依赖微信默认 60 秒。在 6 个页面的轮询场景下，慢请求会与后续轮询叠加堆积。

**方案**：`apiRequest` 设置默认 15 秒并允许按调用点覆盖；轮询类请求取更短值。与 FIX-07 同批修改，避免两次触碰同一文件。

### FIX-09 Staging 运行环境被错误标记为 Production

状态：**已完成。**

**证据**：`infra/k8s/base/configmap.yaml` 原先设置
`AIW_ENVIRONMENT=production`，但同一套清单和工作流明确用于 Staging。这会让日志、
Trace、指标和 `user_events.environment` 全部错误进入 Production 口径，导致
GATE-01 的环境隔离与 Staging 证据不可成立。

**修复**：清单改为 `staging`；配置校验把 Staging 与 Production 统一视为部署环境，
继续强制非本地密钥、私有 COS、Rate Limit、Trusted Proxy、Provider HTTPS 和
OpenTelemetry，避免修正标签后意外降低安全基线。Staging 同时启用 HSTS。

**验收**：配置测试验证 Staging 仍执行部署安全校验，并锁定清单只能声明
`AIW_ENVIRONMENT=staging`。

## 五、P1 优化项（发布 Gate 阻断）

### GATE-01 埋点体系（阻断全部功能的 Done 判定）

**当前证据（2026-07-28）**：`docs/15` 第四节实际列出 20 个核心事件，现已全部接入。
后端已提供 `POST /api/v1/client-events` 批量端点、事件 ID 幂等、关联实体 Ownership
校验、20 条批量上限与严格判别联合 Schema；`user_events` 已补齐版本、发生时间、
环境、Trace、Request、HMAC 用户哈希、Session、客户端版本、平台和渠道。小程序已
实现 100 条持久队列、20 条批量、指数退避、单飞发送、回执校验、永久坏事件二分
隔离和首屏去重，并接入上传、诊断结果/CTA、优化对比页。登录、上传完成、诊断/优化
创建与终态、Growth、删除请求与完成均在服务端事务中幂等记录；真实 PostgreSQL
集成测试覆盖公共字段、跨用户关联拒绝、重复上报、Worker 重复完成和账号清理后的
完成事件保留。

`make event-funnel-audit` 已提供 20 事件覆盖、三条漏斗、环境隔离、公共字段和禁止数据
审计；本地证据 `infra/operations/evidence/2026-07-28_local_event_funnel_audit.md`
为 `BASELINE_NO_DATA`，无违规但无真实旅程流量。因此代码侧已完成，GATE-01 仍为
`IN_PROGRESS`：必须在 Staging 用授权测试账号跑通三条旅程，并以
`--require-complete --require-correlated-context` 得到 `PASSED` 报告后才能关闭。

**剩余影响**：代码链路不再断裂，但缺少 Staging 授权流量时无法证明事件可达性、
环境隔离和真实转化口径；按 `docs/15` 第八节仍不能把相关功能判为 Done。

**已实施方案**：

1. 后端新增 `POST /client-events` 批量接收端点，按 `event_id` 幂等去重，服务端补全 `trace_id`、`request_id`、`environment`、`user_id_hash`。
2. `user_events` 迁移补齐公共字段。
3. 前端新增 `services/telemetry.ts`，提供 `track(name, props)`，本地缓冲、批量上报、失败重试。
4. 按 `docs/15` 表格逐个接线，纯客户端事件（`diagnosis.result.viewed`、`optimization.before_after.viewed`、`share.wechat.invoked`）优先。
5. 服务端事件优先经 Outbox 产生，满足第七节一致性要求。

**验收**：三条漏斗在 Staging 可完整查询；重试不产生重复计数。

### GATE-02 Eval 数据集与回归对比

**当前证据（2026-07-28）**：诊断与优化 Eval Runner 均已支持 `--baseline`、质量绝对
降幅、P95 延迟和平均成本相对涨幅门槛，以及 `--enforce-release-gates`；固定样本 ID
集合或 `dataset_version` 不一致时拒绝比较，Schema 通过率不允许退化。无效基线在调用
Provider 前失败，候选质量超阈值时先原子落报告再返回非零退出码，基线仅以 SHA-256
进入报告。相关边界和失败关闭行为已有自动测试。

`evals/style_diagnosis/manifest.example.jsonl` 与
`evals/style_optimization/manifest.example.jsonl` 仍各只有 1 行示例。`docs/12` 要求
各 50+ 授权样本，且诊断集需覆盖 6 类场景、含 10 张以上低质量输入与 5 张以上
Prompt Injection 样本；真实基线报告与 AI Canary 的 Staging 运行证据尚待完成。

AI Canary 工作流代码现已在任何非零放量前下载并校验不可变私有 Eval Bundle，按候选
类型执行对应 Runner，归档脱敏报告，并仅在门禁通过后修改 Staging ConfigMap；`0%`
紧急回滚不依赖 Eval。Runner 同时断言实际诊断/Critic 模型和 Optimization
`production_image_model`，避免 fallback 或错误候选数据导致假通过。待办缩小为配置
受保护环境密钥、提供真实授权 Bundle 并留存首次 Staging 运行证据。

**影响**：`docs/13` 第 9.2 节 M1 Gate 的 `Diagnosis Success Rate ≥95%`、`P95 < 30s`、`Critic First-pass ≥75%` 四个数值**没有任何数据来源**，AI Quality Gate 无法脱离 `BLOCKED`。

**方案**：

1. 立即启动授权样本采集 —— 这是唯一有外部前置周期的事项，不能排到最后。
2. ~~Eval Runner 增加 `--baseline` 与退化阈值判定，超阈值返回非零退出码。~~
   （代码完成，待授权数据实测）
3. ~~将该判定接入 AI Canary 工作流，作为模型变更的硬门禁。~~
   （代码完成，待 Staging 实跑）

**验收**：两个数据集达到 `docs/12` 的样本量与分布要求；基线报告归档并可被 `docs/16` 引用。

### GATE-03 告警可达性与依赖覆盖

**当前证据（2026-07-28）**：`infra/observability/alerts.yml` 已有 14 条规则；独立
PostgreSQL/Redis Exporter 与五条依赖规则覆盖可用性、连接使用率和 Redis 内存水位。
`make local-alert-drill` 已验证三个 Scrape Target、规则健康、合成告警注入和解除，API
Readiness 指标也已通过 OTLP 在 Prometheus 查询。证据归档于
`infra/operations/evidence/2026-07-28_local_alert_drill.md`。但 `alertmanager.yml`
唯一 receiver 仍是 `local-ui-only`，尚无真实外发路由、环境标签和 On-call 响应记录。

**剩余方案**：通过平台密钥管理配置真实通知路由；在 Staging 注入 critical/warning
故障，验证环境/服务/定位入口、抑制与重复通知策略，并由指定 On-call 确认、解除和留存
响应记录。不得把本地 Alertmanager API 注入等同于人员送达。

**验收**：`docs/16` 的 REL-005 转为 `PASS`。

### GATE-04 测试能力补齐

状态：**已完成。**

**证据**：

- `tests/integration/conftest.py` 已提供事务回滚、真实 Database 与失败后强制清理 Fixture。
- CI 已监听 `main`、`develop` 与 Pull Request，启动 PostgreSQL、Redis，并设置
  `AIW_RUN_INTEGRATION_TESTS=1`；当前 42 个集成测试可实际执行。远端
  [CI run 30350191465](https://github.com/Fu0000/ai-wardrobe/actions/runs/30350191465)
  已在 `develop@a031018` 实际通过 Backend 42 个集成测试和 Miniapp 73 个测试。
- `DiagnosisExecutor` 已在真实 PostgreSQL 上覆盖有效租约不可抢占、Token Fencing 与重试
  耗尽退款；`OptimizationExecutor` 覆盖成功幂等事件和失败退款，`ShareAssetExecutor`
  覆盖派生资产/事件事务完成、幂等和失败 Token Fencing，`DeletionExecutor` 覆盖账号及
  单图删除闭包、完成事件与重复执行隔离。
- 四个 Celery 业务任务已有 9 个单元测试，覆盖有界退避、重试耗尽、同一执行 Token 传递、
  late ack、Worker 丢失重投和队列隔离配置。
- `QuotaRepository.reserve/commit/release` 已有 12 个真实实现测试；账号删除闭包、过期任务
  回收与端点级 401 拒绝也已覆盖。
- 42 个 PostgreSQL/Redis 集成测试已在本地容器全量实际执行并通过；新增 6 个真实 API
  矩阵用例验证外来 Asset/Job/Diagnosis/Optimization 与随机不存在资源返回同一 404，
  外来写引用不产生 Job/Deletion/Feedback 副作用，Owner 正向访问与反馈列表隔离仍可用。
- 小程序已使用 `@vue/test-utils`、Happy DOM 与真实 Pinia 挂载反馈、账号删除和任务中心
  三个高风险页面，覆盖反馈最小化上下文、双重删除确认与三类任务恢复路由；services
  契约测试覆盖 Asset、Diagnosis、Optimization、Job、Profile、Deletion、Feedback、
  Share 与 Telemetry。当前共 16 个测试文件、73 个用例。

**影响**：原审计识别的后端事务和小程序页面/service 自动化缺口已关闭。微信运行时
菜单、授权弹窗和真机渲染差异仍属于 TST-02 的 Staging/真机 E2E，不用 DOM 单测冒充。

**方案**：

1. DB Fixture、CI integration job、Quota、purge、回收、401 与 Celery 重试测试已落地。
2. ~~继续覆盖 Optimization、Share 与 Deletion Executor 的领域直接行为。~~
   （已完成）
3. ~~小程序引入组件测试能力并覆盖关键页面与 services。~~
   （使用既有 `@vue/test-utils`、测试文件级 Happy DOM 和真实 Pinia 完成，无需增加只为
   包装 Pinia 的测试依赖）

**验收**：本地与远端门禁均满足，CI 已实际执行 42 个集成测试及 73 个小程序测试。

### GATE-05 迁移与孤儿资产

**证据**：CI 已增加 PostgreSQL 空库真实升级与 `alembic check` 模型漂移验证。本地
Docker 实库验证曾发现 `20260726_0004` 已创建
`ix_generation_jobs_status_lease`，但 ORM 元数据未声明；现已对齐并增加结构回归测试。
`OrphanUploadCleaner` 现按 TTL 扫描 `UPLOADING`，通过 `UPLOAD_CLEANING` 租约与
`updated_at` fencing version 删除 COS 对象和 DB 行；存储故障转入 `UPLOAD_EXPIRED`
等待重试，Worker 崩溃后的过期认领也可恢复。上传 Complete 使用条件更新，无法把已被
清理器认领的对象重新标记为 `READY`。Beat 已按 maintenance 队列周期调度。
清理成功、可重试失败和陈旧认领均上报低基数指标，可重试失败已有独立告警。

**影响**：迁移漂移和孤儿上传的无界留存风险均已在代码层关闭。剩余发布证据是 Staging
真实 COS 上的对象删除与孤儿计数核对。

**方案**：空库真实升级、模型漂移检查、可恢复的孤儿上传清理任务与完成侧竞态保护均已落地。

**验收**：本地 PostgreSQL 集成测试覆盖过期选择、近期/READY 豁免、对象删除、存储失败
重试与崩溃租约恢复；上线前仍需在真实 COS 执行 `docs/16` AST-002 的孤儿对象检查。

### GATE-06 契约一致性修补

状态：**本地代码与自动化验证已完成；微信真机分享行为仍归入 W5 验收。**

| 项 | 落地结果 | 验证证据 |
|---|---|---|
| 路径参数缺校验 | 抽取统一 `SceneCodePath`，分享读取、投票结果、分享触发和 Continue 四条路径均限制 16～64 位安全字符 | OpenAPI 回归逐条断言 `minLength`、`maxLength` 与 `pattern` |
| 反馈列表无分页 | `GET /me/feedback` 改为 `{items,next_cursor}`；游标用版本化 Base64URL 封装 `created_at + id`，按用户做稳定键集分页，`limit` 限制 1～50 | 单元测试覆盖多页、无重复和坏游标；真实 PostgreSQL 测试覆盖排序及跨用户隔离 |
| 授权变更无审计 | 仅在授权布尔值或已接受版本真实变化时，同事务写入 `consent.ai.accepted` / `consent.ai.revoked`；不记录姓名等自由文本 | 测试覆盖同意、撤回、姓名变更与同版本重复提交 |
| 诊断结果页反馈入口未接线 | “这份建议不准确”跳转现有反馈页，并透传来源页及关联 Diagnosis Job | 纯函数测试锁定 URL 编码与上下文参数 |
| 分享链路断裂 | 分享确认页和落地页均接入好友转发与朋友圈；链接携带受限渠道，Open 事件按链接做首触达归因；新增幂等 `share.wechat.invoked` 记录 | 服务测试覆盖渠道归因和用户/渠道去重；小程序测试锁定好友与朋友圈链接 |
| 首页假指示器 | 红点只在 `useJobStore.hasPendingJobs` 为真时显示，首页恢复时刷新任务 | 既有 Job Store 测试覆盖恢复任务与终态消除 |
| 死代码 | 删除无读方的 `stores/app.ts` 及启动接线 | 全仓库引用扫描为零；小程序类型检查和构建通过 |

门禁结果：后端 Ruff、严格 Mypy、Alembic 模型漂移检查通过；后端 250 个单元/
契约/真实 PostgreSQL/Redis 测试通过；小程序 ESLint、类型检查、61 个
测试与微信构建通过。朋友圈菜单与好友二次转发仍需在微信开发者工具和真机按
`docs/16` 的 Growth 用例留存证据。

### GATE-07 性能与容量证据

状态：**本地 API 基线已完成；Staging AI/COS、队列恢复和低端安卓仍待验收。**

`make local-api-baseline` 现使用固定摘要的 k6 2.1.0 镜像，只允许本地环境与回环
PostgreSQL，自动创建权限最小的临时性能用户并在成功或失败退出时清理。压测报告原子
落盘且不包含 Token、用户 ID 或私有资产引用；CI 同时校验 k6 与 Shell 语法。原脚本
错误使用不存在的 `GET /api/v1/me/profile`，以及在本地 OTel 关闭时硬要求
`X-Trace-ID` 导致的假失败均已修复，现检查真实 `GET /api/v1/me` 与必有的
`X-Request-ID`。

`develop@f6a44dc` 的 5 → 20 req/s 正式本地基线完成 4,650 次业务请求，成功率
100%、HTTP 失败率 0%、P95 14.99 ms、P99 20.73 ms；PostgreSQL 连接峰值 3/100，
Redis 内存峰值 1,735,560/268,435,456 bytes，临时账号与目录残留均为 0。完整脱敏
证据位于 `infra/operations/evidence/2026-07-28_local_api_performance.md`。

该结果不替代 REL-004：本地 API 为单个 Uvicorn `--reload` 进程，数据库和 Redis
没有生产资源限制，且未触发真实 Provider/COS。剩余验收必须在 Staging 覆盖真实
Diagnosis 并发、图片队列积压与恢复、Queue Drain Time、额度/Job/DB/COS 对账、
Dashboard 资源水位和低端安卓体验后，才能将 `TST-03` 与 Performance Gate 转为
`DONE/PASS`。

`make staging-queue-recovery` 已将 REL-003 的 Worker 停机恢复固化为失败关闭入口：
仅在 Kubernetes Context、ConfigMap、API 均确认 Staging 且 API/`ai_fast` 镜像匹配
不可变 SHA 时，才把该 Worker 缩容为 0；每个授权专用账号创建一次 Diagnosis 并做幂等重放，
确认停机期间 Job 保持 Pending 后，通过 EXIT/信号陷阱恢复原副本数并等待全部完成。
数据集必须是权限 `0600`、每条使用不同 Token 的 `*.local.json`；临时状态与最终报告
均为 `0600`，报告仅保留聚合状态、恢复分位数和 Queue Drain Time。4 个
MockTransport/文件安全测试覆盖脱敏成功链路、错误停机控制、私有数据集与状态完整性。
真实 Staging 尚未执行，因此 REL-003/TST-03 状态不提前转为 `DONE`。

### GATE-08 Staging Security/Privacy 审计

状态：**自动化入口已完成；真实 COS 执行仍受 Staging 与双账号凭据阻塞。**

`make staging-security-audit` 使用两个仅经环境变量注入且必须不同的 Token，对 Owner
的 Asset、Job、Diagnosis、Optimization 执行正向 200 控制，并验证 Attacker 读取与
随机不存在资源返回完全一致的资源级 404。所有响应必须包含 Request ID 与 Trace ID。
Asset Access URL 还需满足 HTTPS、精确 COS Host 白名单、5～900 秒 TTL，在有效期内
可读，并在服务器声明过期时间加宽限后返回 401/403/404；脚本显式要求等待确认且最长
等待受限，不能以本地 Mock 冒充真实过期。

输出只包含检查名、状态码、耗时、TTL 和关联头布尔值，不记录 Token、资源 UUID、
Staging URL、COS Host 或 Signed URL。3 个 MockTransport 测试覆盖安全来源/Host
拒绝、完整成功链路与 Attacker 可读时失败关闭；Shell 入口语法也进入 CI。

剩余验收是用专用 Staging Owner/Attacker 和真实私有 COS 运行并归档脱敏输出，同时
完成对象删除后的不可访问检查及授权 Prompt Injection Eval。未取得这些证据前，
AST-06、TST-04 与 Security/Privacy Release Gate 不转为 `DONE/PASS`。

## 六、P2 优化项（架构债）

### ARCH-01 抽取 Job 执行骨架

状态：**已完成。**

`app/modules/jobs/execution.py` 已提供唯一的 `JobExecutionHarness`，统一处理：

- PENDING / QUEUED / FAILED_RETRYABLE 到 PROCESSING 的租约认领。
- 有效租约拒绝抢占、过期租约恢复和新执行令牌生成。
- PROCESSING / QUALITY_CHECKING 的令牌隔离和可重试失败转换。
- 携带令牌与无令牌 finalizer 的不同安全边界。
- COMPLETED / FAILED_FINAL / TIMED_OUT / CANCELLED 终态不可复活或重写。

Diagnosis、Optimization、Share、Deletion 四个 Executor 只保留领域状态、Quota 和资源
清理钩子，源文件中不再直接读写租约到期字段，也不再各自实现
`STALE_EXECUTION_RECOVERED`。四个 Executor 合计 1546 行，公共安全逻辑 154 行；
总体行数与 1700 行基线相同，收益来自消除四份发散实现，而非追求表面减行。

自动化验证包括租约认领、有效租约拒绝、过期恢复、四类终态、Token Fencing、
Retryable 转换和 `TIMED_OUT` 迟到 finalizer；另有结构约束测试防止四个 Executor
重新写回租约逻辑。后端严格 Mypy、Ruff、246 个单元/契约/真实 PostgreSQL/Redis
测试和 Alembic 漂移检查全部通过。

### ARCH-02 拆解 `OptimizationExecutor.run`

状态：**已完成。**

`OptimizationExecutor.run` 现只负责准备、网关生命周期与错误路由；生成尝试、图片验证、
Critic 评审、结果持久化和质量拒绝分别进入独立方法。`CriticReview` 显式绑定通过 Schema
校验的输出与模型版本，不再依赖内层循环变量在外层隐式存活。

已按故障域收窄异常语义：

- Provider 终态拒绝、Provider 瞬时不可用、Critic 响应非法、存储不可用、图片处理失败和
  数据库不可用各自使用明确错误码。
- 无效生成图仍计入本次有界生成尝试，并进入质量报告，不误判为基础设施故障。
- `TypeError` 等非预期编程异常先按当前执行 Token 终态失败并释放额度，随后原样抛出，
  由 Worker/Trace 暴露真实缺陷；不再转为 `OPTIMIZATION_TEMPORARY_FAILURE`。
- 关闭 Provider 放在统一 `finally`，所有已准备执行分支均释放客户端资源。

新增 4 个单元测试，覆盖 Critic 先非法后合法时采用正确模型版本、连续两次非法响应、
无效生成图报告，以及编程异常终态退款并显式失败。后端 Ruff、严格 Mypy、250 个
单元/契约/真实 PostgreSQL/Redis 测试与 Alembic 模型漂移检查全部通过；远端
[CI run 30336655337](https://github.com/Fu0000/ai-wardrobe/actions/runs/30336655337)
的 Backend 与 Miniapp Job 均为 `success`。

### ARCH-03 小程序抽取组件与 Composable

状态：**已完成。**

- `composables/useJobPolling.ts` 已统一诊断、优化、任务中心、账号删除、照片删除和分享
  确认六个页面的显示/隐藏/卸载生命周期、单定时器退避、进度变化重置、在途请求恢复与
  旧代次结果隔离。4 个确定性测试覆盖隐藏后停止、恢复后续轮询、旧请求完成竞态与退避。
- `StateCard.vue` 已在真实页面覆盖 loading / error / empty 三态；
  `ProgressTrack.vue` 统一紧凑、标准和突出进度语义，并提供可访问的 progressbar 属性。
  `PrimaryAction.vue` 与 `PrivacyNote.vue` 进一步收敛主页和结果页的重复交互样式。
- `createJobBackedResourceStore` 已统一 diagnoses / optimizations / shares 的恢复、
  接受、任务映射上限与持久化骨架，领域创建、错误与投票逻辑仍留在各自 Store。
- `pages/index/index.vue` 从 854 行降到 789 行；对 `miniapp/src` 的 Vue/TypeScript
  源文件扫描已无超过 800 行的文件，脚本职责未被无意义拆散。

自动化验证：小程序 13 个测试文件、61 个用例全部通过，ESLint、`vue-tsc`、微信小程序
生产构建与 High 级生产依赖漏洞门禁通过。远端
[CI run 30338199750](https://github.com/Fu0000/ai-wardrobe/actions/runs/30338199750)
在 `develop@db08463` 上完成，Backend 与 Miniapp Job 均为 `success`。

### ARCH-04 规约对齐

状态：**已完成。**

| 规约 | 落地结果 | 自动化证据 |
|---|---|---|
| 启停统一走 `scripts/*.sh` | Makefile 只保留稳定命令名，安装、开发、质量、基础设施与 Staging 冒烟进入 5 个分组脚本；公共命令发现和退出码处理归入 `scripts/lib/common.sh` | `bash -n` 与 Make 干运行通过；新入口实际跑通 lint、类型检查、构建和双端审计 |
| 日志输出到 `logs/` | 所有分组脚本通过 `tee` 同步输出到终端和带时间/PID 的本地日志；支持 `AIW_LOG_DIR` 覆盖，日志正文被 Git 忽略 | 各质量命令均生成独立日志且保持原命令退出码 |
| 单文件不超过 800 行 | 最大文件 `pages/index/index.vue` 为 789 行 | ARCH-03 已解决 |
| 每层目录不超过 8 个文件 | 小程序 Store/Service 的测试与共享基础设施分层；Job 执行/恢复进入 `jobs/runtime`；后端 45 个单元测试和 7 个集成测试按领域组织 | `check-structure.sh` 检查 71 个工程目录并接入 `make lint` 与 CI；文档序列和 Alembic 迁移作为有序注册表明确例外 |
| 项目级 `CLAUDE.md` 与 `docs/agent/` | `CLAUDE.md` 73 行，`docs/agent/README.md` 66 行，只提供执行路由与清单，不复制产品契约 | `wc -l` 与结构门禁通过 |

本地验证包括 Ruff、格式检查、严格 Mypy（157 个文件）、226 个单元/契约测试、
24 个真实 PostgreSQL/Redis 集成测试、小程序 61 个测试、ESLint、`vue-tsc`、微信构建
与双端生产依赖审计。远端
[CI run 30339243334](https://github.com/Fu0000/ai-wardrobe/actions/runs/30339243334)
在 `develop@8d4af16` 上完成，Backend 与 Miniapp Job 均为 `success`。

### ARCH-05 文档一致性

状态：**已完成。**

| 问题 | 落地结果 |
|---|---|
| 文件名与正文版本不符 | 正文为 V1.1 的 00～08 文档已统一重命名为 `_V1.1.md`，`AGENTS.md` 与 Agent 指引引用同步；后续要求文件名和标题同时升级 |
| 任务数不符 | 复核证明旧结论是审计误报：WBS 实有 80 个唯一任务，状态为 DONE 20、IN_REVIEW 46、IN_PROGRESS 8、BLOCKED 5、NOT_STARTED 1，总和正好为 80，未删除或虚构任务 |
| 队列名失同步 | P0 文档统一为 `ai_fast`、`image_generation`、`media_generation`、`maintenance`；`ingestion` 明确推迟到 P0.5 |
| 抽象不存在 | 不再把 `OwnershipGuard` / `ScopedRepository` 当作已实现类名，统一描述代码真实执行的查询范围与关联归属双层校验 |
| 任务名含未实现内容 | INF-04 改为 OpenTelemetry、TraceID 与结构化日志；托管日志后端及腾讯云 CLS 候选明确留待 INF-02 / Staging 真实验收 |
| 模块路径不符 | P0 模块树与 Agent 指引统一为真实的 `app/modules/*`，并补充 `api`、`core`、`database`、`evaluation`、`worker` 平台目录 |

`scripts/check-docs.sh` 已强制校验编号文档文件名/标题版本、WBS ID 唯一性、任务与状态
汇总、P0 队列、资源归属术语、INF-04 名称和核心文档引用；同时接入 `make docs-check`、
`make lint` 和 GitHub CI。脚本只依赖 Bash 与系统基础工具，并在最小 PATH 和 Ubuntu
Runner 上通过。远端
[CI run 30339862467](https://github.com/Fu0000/ai-wardrobe/actions/runs/30339862467)
在 `develop@4944b0b` 上完成，Backend 与 Miniapp Job 均为 `success`。

## 七、实施批次

批次划分依据是依赖关系与外部前置周期，不是简单的优先级排序。

| 批次 | 内容 | 前置 | 说明 |
|---|---|---|---|
| 第 0 批 | GATE-02 的样本采集启动 | 无 | 唯一有外部前置周期的事项，必须最先启动并与后续并行 |
| 第 1 批 | FIX-01 至 FIX-08 | 无 | 均为局部改动，可并行；FIX-07 与 FIX-08 同批触碰 `api.ts` |
| 第 2 批 | GATE-04 的 `conftest.py` 与 CI integration job | 第 1 批 | 后续所有测试类工作的解锁点 |
| 第 3 批 | GATE-01 埋点全链路 | 第 2 批 | 工作量最大，需前后端同步推进 |
| 第 4 批 | GATE-03、GATE-05、GATE-06 | 第 2 批 | 可与第 3 批并行 |
| 第 5 批 | ARCH-01 至 ARCH-05 | 第 2 批 | 允许排到封测期间并行推进 |

FIX-01、FIX-06 与 GATE-04 之间存在一条隐含主线：三者都指向「新增用户数据时没有机制强制它进入删除闭包与测试闭包」。修数据只解决当次，补机制才解决下一次。实施时应优先落地机制部分。

## 八、验收口径

本方案的完成判定与 `docs/16` 对齐，不新增独立标准：

- P0 全部条目关闭，且每条有对应的自动化测试防止回归。
- `docs/16` 第六节的 Security/Privacy、AI Quality、Reliability、Performance 四个 Gate 具备转为 `PASS` 的数据条件。
- `docs/15` 第八节的埋点验收对每个 MVP 功能成立。
- CI 中集成测试实际执行而非 skip。

P2 条目不阻断封测；ARCH-01 至 ARCH-05 已全部完成。封测仍受真实 Staging、COS、
微信/OpenAI、授权样本、性能质量数据和 Go/No-Go 签署等 P0/P1 外部门禁约束。

## 九、明确不做

遵循 `AGENTS.md` 第三节的阶段边界，本轮不引入：

- 任何 P0.5 及以后的能力（渐进建库、数字孪生、Display Asset、Visual Shoe Wall）。
- 微服务拆分。当前 Modular Monolith 边界清晰，ARCH-01 是抽取共性而非拆分服务。
- i18n。MVP 面向微信国内版，`docs/01` 与 `docs/14` 均未提出多语言要求。短期只需把散落在 Store 与页面两层的用户文案集中到单一模块，顺带解决错误文案泄漏。
- 分包与体积优化。11 个页面全在主包，但封测规模下不构成阻断，留待封测后依据真实启动耗时决策。
- 无障碍的全面整改。`docs/14` 第六节的字号与点击区域存在量化偏差（37 处字号低于下限、部分按钮低于 88rpx），列为封测期间批量修复项。
