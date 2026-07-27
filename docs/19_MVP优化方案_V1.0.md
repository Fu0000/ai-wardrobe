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
| 集成测试 | `tests/integration/test_infrastructure.py` 已存在，3 个测试，默认 `skip` |
| 小程序测试 | 5 个文件，13 个用例，零页面测试、零 services 测试 |
| 数据库迁移 | 9 版 Alembic，CI 执行空库升级、模型漂移检查与离线 SQL 渲染 |
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

**验收**：`tests/test_outbox_dispatcher.py` 补充用例，断言不可发布事件在达到阈值后停止重试且不再计入 `failed_count`。

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

## 五、P1 优化项（发布 Gate 阻断）

### GATE-01 埋点体系（阻断全部功能的 Done 判定）

**证据**：`docs/15` 第四节定义 19 个核心事件，代码仅实现 4 个（`share.asset.created`、`share.scene.opened`、`vote.choice.submitted`、`growth.continue.clicked`）。`miniapp/src` 无任何上报模块；`backend/app/modules/events/` 只有 `dispatcher.py`、`models.py`、`repository.py`，**无 `api.py`**，即无客户端事件接收端点。`user_events` 表缺 `docs/15` 第三节强制的 9 个公共字段。

**影响**：`docs/15` 第五节三条核心漏斗全部断裂，第八节验收口径下所有 MVP 功能均不满足 Done。这是当前工作量最大、阻断面最广的单项。

**方案**：

1. 后端新增 `POST /client-events` 批量接收端点，按 `event_id` 幂等去重，服务端补全 `trace_id`、`request_id`、`environment`、`user_id_hash`。
2. `user_events` 迁移补齐公共字段。
3. 前端新增 `services/telemetry.ts`，提供 `track(name, props)`，本地缓冲、批量上报、失败重试。
4. 按 `docs/15` 表格逐个接线，纯客户端事件（`diagnosis.result.viewed`、`optimization.before_after.viewed`、`share.wechat.invoked`）优先。
5. 服务端事件优先经 Outbox 产生，满足第七节一致性要求。

**验收**：三条漏斗在 Staging 可完整查询；重试不产生重复计数。

### GATE-02 Eval 数据集与回归对比

**证据**：`evals/style_diagnosis/manifest.example.jsonl` 与 `evals/style_optimization/manifest.example.jsonl` 各 1 行示例。`docs/12` 要求各 50+ 样本，且诊断集需覆盖 6 类场景、含 10 张以上低质量输入与 5 张以上 Prompt Injection 样本。`backend/app/evaluation/diagnosis.py` 的 CLI 无 `--baseline` 参数，无版本间退化判定。

**影响**：`docs/13` 第 9.2 节 M1 Gate 的 `Diagnosis Success Rate ≥95%`、`P95 < 30s`、`Critic First-pass ≥75%` 四个数值**没有任何数据来源**，AI Quality Gate 无法脱离 `BLOCKED`。

**方案**：

1. 立即启动授权样本采集 —— 这是唯一有外部前置周期的事项，不能排到最后。
2. Eval Runner 增加 `--baseline` 与退化阈值判定，超阈值返回非零退出码。
3. 将该判定接入 AI Canary 工作流，作为模型变更的硬门禁。

**验收**：两个数据集达到 `docs/12` 的样本量与分布要求；基线报告归档并可被 `docs/16` 引用。

### GATE-03 告警可达性与依赖覆盖

**证据**：`infra/observability/alerts.yml` 有 7 条规则，但 `alertmanager.yml` 唯一 receiver 是 `local-ui-only`，无外发路由；规则集中**无 PostgreSQL、无 Redis 告警**，而这是 `docs/13` 第 6.10 节的明确完成条件。

**方案**：通过平台密钥管理配置真实通知路由；补 DB 与 Redis 可用性、连接数、内存水位规则；完成一次告警送达演练并留存证据。

**验收**：`docs/16` 的 REL-005 转为 `PASS`。

### GATE-04 测试能力补齐

**证据**：

- `tests/integration/conftest.py` 已提供事务回滚、真实 Database 与失败后强制清理 Fixture。
- CI 已启动 PostgreSQL、Redis，并设置 `AIW_RUN_INTEGRATION_TESTS=1`；当前 20 个集成
  测试可实际执行。
- `DiagnosisExecutor` 已在真实 PostgreSQL 上覆盖有效租约不可抢占、Token Fencing 与重试
  耗尽退款；其余 Executor 的领域内直接行为仍待补齐。
- 四个 Celery 业务任务已有 9 个单元测试，覆盖有界退避、重试耗尽、同一执行 Token 传递、
  late ack、Worker 丢失重投和队列隔离配置。
- `QuotaRepository.reserve/commit/release` 已有 12 个真实实现测试；账号删除闭包、过期任务
  回收与端点级 401 拒绝也已覆盖。
- 剩余缺口是小程序页面与 services 组件测试。

**影响**：后端直接管额度的主路径已有防回归证据；剩余风险集中在 Optimization、Share、
Deletion Executor 的领域内状态转换，以及小程序页面交互回归。

**方案**：

1. DB Fixture、CI integration job、Quota、purge、回收、401 与 Celery 重试测试已落地。
2. 继续覆盖 Optimization、Share 与 Deletion Executor 的领域直接行为。
3. 小程序引入 `@vue/test-utils` 与 `@pinia/testing`（当前 `vitest.config.ts` 为
   `environment: "node"`，不具备组件测试能力）。

**验收**：CI 中集成测试实际执行而非 skip；上述四类关键路径均有覆盖。

### GATE-05 迁移与孤儿资产

**证据**：CI 已增加 PostgreSQL 空库真实升级与 `alembic check` 模型漂移验证。本地
Docker 实库验证曾发现 `20260726_0004` 已创建
`ix_generation_jobs_status_lease`，但 ORM 元数据未声明；现已对齐并增加结构回归测试。
`AssetStatus.UPLOADING` 仅在 `repository.py:25` 写入、`service.py:133` 校验，无任何按
`created_at` 扫描清理的逻辑，Beat 中亦无对应任务。

**影响**：迁移漂移现可在 CI 阻断。剩余风险是用户取得预签名 URL 后不调用 complete，DB
行与 COS 对象双双永久滞留 —— 既是无界成本增长，也是未引用用户照片长期留存的隐私暴露。

**方案**：空库真实 `upgrade head` 与模型漂移检查已落地；剩余工作是新增 Beat 任务，清理
超过 TTL 的 `UPLOADING` 资产及其 COS 对象。

**验收**：`docs/16` 的 AST-002「孤儿对象检查」可取得证据。

### GATE-06 契约一致性修补

以下为小改动，集中一批处理：

| 项 | 证据 | 方案 |
|---|---|---|
| 路径参数缺校验 | `growth/api.py:319`、`:332` 的 `scene_code` 为裸 `str`，而 `get_share`（`:258-262`）有完整 `Path` 约束；`:324` 直接函数调用不触发 FastAPI 校验 | 补齐 `min_length=16, max_length=64, pattern` |
| 反馈列表无分页 | `feedback/api.py:169-175` 无分页参数，`service.py:121-126` 硬编码 `limit=20`，客户端无法感知截断 | 改为游标信封，本项是首个列表端点，将成为后续先例 |
| 授权变更无审计 | `identity/api.py:213-232` 直接改写 `has_ai_processing_consent` 与 `consent_version`，无 `UserEvent` 记录 | 补写审计事件，与 GATE-01 同批 |
| 诊断结果页反馈入口未接线 | `diagnosis/result.vue:55-57` 的 `reportIssue()` 只弹 `showToast({ title: "已记录反馈入口需求" })`，把内部待办文案暴露给用户；feedback 全链路已存在但唯一入口在 `profile/index.vue:86` | 接入已有 feedback 服务或跳转反馈页 |
| 分享链路断裂 | `share/index.vue` 自身无 `onShareAppMessage`，访客无法二次转发；全仓库无 `onShareTimeline`，`WECHAT_TIMELINE` 归因源永不产生 | 补落地页转发与朋友圈分享 |
| 首页假指示器 | `pages/index/index.vue:147` 的 `task-link__dot` 无 `v-if`，且该类名在整个样式段无对应规则，是无效空节点 | 接入 `useJobStore` 做条件渲染，或删除 |
| 死代码 | `stores/app.ts` 的 `completeWelcome` 无调用方，`hasSeenWelcome` 无读取方，读写两端均未接线 | 接线或删除 |

## 六、P2 优化项（架构债）

### ARCH-01 抽取 Job 执行骨架

**证据**：四个 Executor 合计 1700 行（optimization 592 / growth 384 / diagnosis 381 / deletion 343），结构约 85% 同构。`_prepare` 的租约恢复阶梯在 `optimization/executor.py:406-432`、`growth/executor.py:183-210`、`diagnosis/executor.py:239-267` 三处逐字重复；`finalize_failure` 的 Token Fencing 在 `optimization/executor.py:357-376` 与 `growth/executor.py:143-161` 逐字相同。

**影响**：重复的恰是安全关键逻辑。在一处修好 Fencing 缺陷，另外三处会静默残留 —— 而它们零测试。这是本仓库风险最高的重复。

**方案**：抽取 `JobExecutionHarness`，统一承担租约获取、陈旧检测、终态转换与失败收敛；各 Executor 只保留领域逻辑。抽取后针对 Harness 集中补测，一次覆盖四条链路。

**依赖**：需 GATE-04 的 `conftest.py` 先行，否则无法为 Harness 写有意义的测试。

### ARCH-02 拆解 `OptimizationExecutor.run`

**证据**：`backend/app/modules/optimization/executor.py:76-338`，单方法 263 行，最深处约 6 层缩进（`try` → `for` → `try` / `for` → `try` → `except`），末尾第 321 行为裸 `except Exception`。

**影响**：裸捕获把代码缺陷（如 `TypeError`）与瞬时故障同等处理，一律转为 `OPTIMIZATION_TEMPORARY_FAILURE` 重试并退配额，真实缺陷因此不可见。另有隐式不变量：`critic_response` 在第 253 行被引用，但绑定于第 195 行的内层循环，仅因 `critic_output is not None` 才成立，需读者自行重建推理。

**方案**：随 ARCH-01 一并拆为「生成尝试」「Critic 评审」「结果落库」三个方法；收窄裸捕获范围，让非预期异常显式失败。

**约束**：`AGENTS.md` 与全局规约要求函数短小、超过三层缩进即设计错误。本条是该规约在仓库内最突出的偏离点。

### ARCH-03 小程序抽取组件与 Composable

**证据**：`miniapp/src` 下无 `components/` 与 `composables/` 目录，41 个源文件零可复用组件。轮询逻辑在 6 个页面各自实现（`diagnosis/index.vue`、`optimization/index.vue`、`tasks/index.vue`、`profile/deletion.vue`、`profile/photos.vue`、`share/confirm.vue`），每份都独立声明 `pollTimer`、`polling`、`pollAttempt` 三个模块级变量并手写递归 `setTimeout`；共享的仅有 `lib/job-progress.ts` 的退避函数。样式层面 `.progress-track`、`.state-card`、`.primary-action` 在 5 至 6 个页面各写一遍。

**方案**：

1. 抽 `composables/useJobPolling.ts`，统一生命周期与退避语义。
2. 抽 `components/StateCard.vue`（loading / error / empty 三态）与 `components/ProgressTrack.vue`。
3. 抽 `createJobBackedResourceStore` 工厂，收敛 `diagnoses` / `optimizations` / `shares` 三个同构 Store。

`pages/index/index.vue` 共 850 行，但 `<script setup>` 仅 130 行、样式占 535 行。它的问题不是职责过重，而是缺少共享样式层 —— 因此拆分从组件与样式入手，脚本层不必动。

### ARCH-04 规约对齐

| 规约 | 现状 | 处理 |
|---|---|---|
| 启停统一走 `scripts/*.sh` | 无 `scripts/` 目录，全部经 Makefile 直调 `uv` / `pnpm` | 补脚本层，Makefile 转为调用脚本 |
| 日志输出到 `logs/` | 无该目录 | 随脚本层一并建立 |
| 单文件不超过 800 行 | `pages/index/index.vue` 850 行 | 由 ARCH-03 解决 |
| 每层目录不超过 8 个文件 | 7 个目录超限，最多 13 个 | 随模块重组处理 |
| 项目级 `CLAUDE.md` 与 `docs/agent/` | 均缺失 | 补建，控制在 60 至 80 行 |

### ARCH-05 文档一致性

| 问题 | 现状 |
|---|---|
| 文件名与正文版本不符 | 前 9 篇文件名为 `V1.0`、正文标题为 `V1.1` |
| 任务数不符 | `docs/13` 自报 80 项，表格实际 57 项，差额 23 项无对应行 |
| 队列名失同步 | 文档为 `background` / `governance`，代码为 `media_generation` / `maintenance` |
| 抽象不存在 | `docs/13` 第 4.2 节声明的 `OwnershipGuard`、`ScopedRepository` 全仓库零命中，实际为约 25 处手写过滤 |
| 任务名含未实现内容 | `INF-04` 名称含腾讯云 CLS，无任何集成代码 |
| 模块路径不符 | 文档写 `app/identity/`，实际为 `app/modules/identity/` |

统一命名前，按 `AGENTS.md` 第 2 节以正文标题与章节内容为准。

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

P2 条目不阻断封测，但必须在封测结束前完成 ARCH-01，否则 Executor 的安全关键逻辑将在四处继续无测试地发散。

## 九、明确不做

遵循 `AGENTS.md` 第三节的阶段边界，本轮不引入：

- 任何 P0.5 及以后的能力（渐进建库、数字孪生、Display Asset、Visual Shoe Wall）。
- 微服务拆分。当前 Modular Monolith 边界清晰，ARCH-01 是抽取共性而非拆分服务。
- i18n。MVP 面向微信国内版，`docs/01` 与 `docs/14` 均未提出多语言要求。短期只需把散落在 Store 与页面两层的用户文案集中到单一模块，顺带解决错误文案泄漏。
- 分包与体积优化。11 个页面全在主包，但封测规模下不构成阻断，留待封测后依据真实启动耗时决策。
- 无障碍的全面整改。`docs/14` 第六节的字号与点击区域存在量化偏差（37 处字号低于下限、部分按钮低于 88rpx），列为封测期间批量修复项。
