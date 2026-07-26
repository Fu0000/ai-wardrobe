# AGENTS.md — AI Wardrobe 开发协作指南

本文件适用于本仓库内的所有人工开发者与 AI 编码 Agent。目标是在项目从文档阶段进入实现阶段后，持续守住产品定位、阶段范围、架构边界、数据语义和发布质量。

文中的“必须”“禁止”属于不可静默绕过的约束。如确需改变，应先取得负责人明确批准，并同步更新相关设计文档和 ADR。

## 1. 项目使命与北极星

AI Wardrobe 是一个以微信小程序为优先入口，围绕 Personal Wardrobe Graph、Personal Style Graph 和 Personal Decision Graph 构建的 Personal Style Agent。

核心价值链：

现实穿搭 → AI 看懂 → 最小优化 → 建立数字衣橱 → 记录真实选择 → 学习个人偏好 → 持续穿搭决策 → 必要时辅助购物决策。

开发时必须始终满足以下产品方向：

- 优先降低用户的穿搭与服装消费决策成本。
- 优先使用用户已经拥有的真实衣物（Existing Wardrobe First）。
- 即时体验必须逐步导向真实衣橱和长期决策价值。
- 不得把产品退化为单纯的电子衣橱、图片生成器、AI 试衣工具或导购平台。
- 行为信号比口头声明更可信，真实穿着是个人偏好的核心证据。
- 商业推荐仅在现有衣橱不能解决需求时介入，并且理由必须可解释。

项目级成功标准：

1. 用户能快速获得首次价值。
2. 用户愿意渐进建立数字衣橱。
3. 用户在第二周仍主动使用。
4. 产品出现真实付费意愿。
5. 商业建议不会被理解为纯广告。

## 2. 权威文档与冲突处理

开始任务前，按需阅读对应文档，不要只依赖本文件：

- 项目定义与阶段总览：`docs/00_文档总目录_V1.0.md`
- 产品范围与用户体验：`docs/01_产品需求文档_PRD_V1.0.md`
- 技术架构与模块边界：`docs/02_技术方案设计_TDD_V1.0.md`
- 数据语义与实体设计：`docs/03_数据架构与数据库设计_DDD_V1.0.md`
- API 契约与外部集成：`docs/04_API与外部集成方案_V1.0.md`
- 阶段计划与依赖：`docs/05_研发计划与WBS_V1.0.md`
- 里程碑与验收门禁：`docs/06_里程碑与验收标准_V1.0.md`
- 测试、发布与运维：`docs/07_测试发布与运维方案_V1.0.md`
- 已批准架构决策与风险：`docs/08_风险清单与ADR_V1.0.md`
- 成本与容量：`docs/09_成本预算与资源规划_V1.0.md`
- 术语定义：`docs/10_术语表与数据字典_V1.0.md`
- 非功能需求与 SLO：`docs/11_非功能性需求NFR与SLO_V1.0.md`
- AI 评估与发布质量：`docs/12_AI评估与质量规范_V1.0.md`
- MVP 实现计划与任务进度：`docs/13_MVP实现方案与任务进度明细_V1.0.md`
- MVP 交互与页面状态：`docs/14_MVP交互与页面状态规范_V1.0.md`
- MVP 埋点与指标：`docs/15_MVP埋点与指标规范_V1.0.md`
- MVP 验收与发布：`docs/16_MVP验收与发布清单_V1.0.md`
- MVP 封闭测试运行：`docs/17_MVP封闭测试运行手册_V1.0.md`
- 供应链安全例外：`docs/18_供应链安全例外登记_V1.0.md`

决策优先级：

1. 本次任务中由负责人明确批准的产品或架构变更。
2. 已批准的最新 ADR。
3. PRD 与里程碑中的产品范围、阶段门禁。
4. TDD、DDD 和 API 文档中的技术契约。
5. NFR、AI 质量规范、测试运维方案、WBS、成本规划和术语表。

冲突处理规则：

- 安全、隐私、数据完整性或 Release Gate 数值冲突时，默认采用更严格标准。
- 业务语义冲突时不得自行选择，应提出问题并通过 ADR 或文档修订明确。
- 经批准偏离现有设计时，代码和文档必须在同一任务中同步，避免形成隐性架构。
- 当前部分文件名仍为 `V1.0`，正文标题已为 `V1.1`。在统一命名前，以正文标题和具体章节内容为准，引用时同时写明文件路径与章节。
- 总目录中“十二条核心原则”实际列出 14 条，本文件按全部 14 条执行。

## 3. 阶段边界与范围控制

项目按以下顺序推进：

| 阶段 | 核心目标 | 对应里程碑 |
|:---|:---|:---|
| P0a | 基础工程、身份、资产、AI Job、Outbox、Offline AI Eval | M0 |
| P0b | AI 诊断、最小优化、Before/After、分享、投票 | M1、M2 |
| P0 Hardening | 修复、日志、Prompt 版本化、成本复盘、恢复演练、封闭测试 | Hardening Gate |
| P0.5 | 渐进建库、商品链接入库、数字孪生、Display Asset、Visual Shoe Wall | M3、M4 |
| P1 | Outfit、Look、WearEvent、PreferenceSignal、Style Graph | M5 |
| P1.5 | Visual Wardrobe、Laundry、Weekly Planning、旧衣唤醒 | M6 |
| Phase 2 | Wardrobe Gap、购买决策、商业推荐、CPS/B2B2C | M7 |

每个实现任务都应明确：

- 当前 Phase。
- 所属 Epic。
- 对应 Milestone 或 Release Gate。
- 本次验证的产品假设。
- 明确不做的相邻能力。

禁止因为“以后可能需要”而提前建设后续阶段能力。跨阶段开发必须得到明确批准。

P0 明确不做：

- 完整 3D。
- 微服务化。
- 闺蜜帮搭。
- 大型商品池。
- 真实尺码预测。
- 全相册扫描。
- 全平台电商抓取或反爬对抗。
- 复杂洗衣 ERP。
- 完整离线衣橱。
- 高级上传分片断点续传。

## 4. 不可违反的核心原则

1. Observation ≠ Truth。
2. Behavior > Declaration。
3. Personal Graph 是核心数据资产。
4. PostgreSQL 是业务真相源。
5. AI Provider 必须可替换。
6. Celery 管执行，LangGraph 管决策。
7. Modular Monolith 优先。
8. Recognition Asset 与 Display Asset 分离。
9. Product 与 WardrobeItem 分离。
10. Existing Wardrobe First。
11. Stable Spatial Memory First。
12. 用户数据可追溯、可删除。
13. 写操作边界严格，查询可以受控同步。
14. 不同模型、模型版本或维度的 Embedding 绝对不能混合计算。

任何实现若违反这些原则，必须停止并提出 ADR，而不是通过局部代码规避。

## 5. 技术栈与总体架构

既定技术栈：

- Client：uni-app + Vue 3 + TypeScript + Pinia。
- Admin：Vue 3 + TypeScript + Vite + Element Plus。
- Backend：Python 3.13 + FastAPI + Pydantic 2 + SQLAlchemy 2.x + Psycopg 3 + Alembic。
- Data：PostgreSQL 18 + pgvector + pg_trgm + Redis。
- Async：Celery + Redis Broker + Transactional Outbox。
- AI：AI Gateway + Provider Adapter + Official SDK + Selective LangGraph。
- Media：腾讯云 COS + Pillow + OpenCV，必要时使用 pyvips。
- Infra：Docker + CLB/Nginx + Managed PostgreSQL + Managed Redis + COS + OpenTelemetry。

总体架构采用 Modular Monolith + Independent Workers：

- 代码按领域模块拆分。
- 运行时可以按负载拆分 API、Worker 和 Dispatcher 进程。
- 不得在没有明确容量或组织理由时提前拆微服务。
- 新增框架、数据库、消息系统、向量库或 AI 编排层前必须提出 ADR。

## 6. 后端模块与依赖规则

规划模块：

```text
app/
├── identity/
├── assets/
├── diagnosis/
├── wardrobe/
├── ingestion/
├── styling/
├── looks/
├── personal_graph/
├── visualization/
├── scene_rendering/
├── commerce/
├── growth/
├── governance/
├── ai/
├── jobs/
├── events/
├── infrastructure/
└── shared/
```

每个业务模块采用三层结构：

```text
Public Application Service → Domain → Infrastructure
```

允许：

- 模块 A 通过模块 B 的 Public Application Service 查询数据。
- 使用事件处理异步副作用、跨模块派生状态和非强一致操作。
- 在接口明确、依赖单向的前提下进行受控同步查询。

禁止：

- 跨模块直接写入 Repository。
- 跨模块直接读取或修改 ORM Entity。
- 绕过领域服务直接修改其他模块的数据。
- 创建循环依赖。
- 把所有公共逻辑无边界地堆入 `shared/`。

可视化边界：

- `visualization/` 决定展示什么：Scene、Zone、Slot、Layout、Visual State。
- `scene_rendering/` 决定如何渲染：Layout + Assets → Snapshot。

## 7. 数据建模与一致性

统一建模方式：

```text
Entity + Observation + Evidence + State + Event
```

必须遵守：

- `WardrobeItem` 表示用户现实中拥有的一件具体物品，不等于电商 `Product`。
- AI 识别结果先作为 Observation 或 Evidence 保存，不能直接覆盖为事实。
- 属性必须保留来源、置信度、模型版本和时间。
- `ProductSourceSnapshot` 是某次解析结果的不可变快照。
- 同一衣物允许存在多个 Observation、Evidence 和 Source。
- Entity Resolution 统一输出 `RESOLVED / NEEDS_REVIEW / NEW_ENTITY`。
- 低置信度或高风险合并必须进入用户确认，不得强行自动合并。
- PreferenceSignal 保留原始权重，时间衰减在计算时应用。
- Outfit Agent 默认只能选择 `AVAILABLE` 衣物。
- Credits 使用 Ledger 模式，状态遵循 `RESERVE → COMMIT / RELEASE`。
- 业务数据和 `OutboxEvent` 必须在同一个数据库事务中提交。

Embedding 规则：

- visual、semantic、style、multimodal 向量独立存储。
- 每条向量必须记录模型、版本、类型和维度。
- 检索只允许比较相同 `model_version`、相同类型、相同维度的向量。
- 模型生命周期遵循 `SHADOW → ACTIVE → DEPRECATED → RETIRED`。

## 8. API、上传与异步任务

API 原则：

- REST First。
- Async Job First。
- Idempotency First。
- OpenAPI Contract。
- `/api/v1/` 表示破坏性契约版本。

兼容规则：

- 新增可选字段或 Endpoint 可以继续使用 v1。
- 删除字段、改变字段类型或核心语义必须升级版本。
- 至少兼容两个客户端发布周期，原则上不少于 90 天。
- 新增 Enum 时客户端必须具备 Unknown Fallback。
- OpenAPI 是前后端契约来源，应自动生成 TypeScript 类型。

必须支持 `Idempotency-Key` 的操作：

- Diagnosis。
- Optimization。
- Try-On。
- Ingestion。
- Outfit。
- Purchase Intelligence。

上传规则：

- API Server 不搬运图片。
- 客户端获取 Upload Ticket 后直传 COS，再调用 Complete API。
- JSON Body 不超过 1MB。
- Share Text 不超过 20KB。
- 单图 Upload Ticket 默认不超过 20MB。
- 服务端必须校验 MIME Type、Magic Number 和 Image Dimensions，不能相信扩展名。

统一 Job 状态：

```text
PENDING
→ QUEUED
→ PROCESSING
→ QUALITY_CHECKING
→ COMPLETED
  / FAILED_RETRYABLE
  / FAILED_FINAL
  / TIMED_OUT
  / CANCELLED
```

异步任务必须具备：

- `idempotency_key`。
- 可控重试和超时。
- `AIInvocation` 明细。
- 成本记录。
- 用户友好失败信息。
- Credits 失败释放。
- 页面退出后的任务恢复能力。

客户端不得暴露 Provider 内部错误码或基础设施细节。

## 9. AI 工程规范

业务代码禁止直接绑定具体模型或 Provider，必须通过 AI Gateway。

Provider 抽象至少包含：

- `StructuredVisionProvider`。
- `ImageGenerationProvider`。
- `ImageEditProvider`。
- `EmbeddingProvider`。

每类 AI Task 独立配置：

- Primary 与 Fallback。
- Timeout 与 Retry。
- Cost Ceiling。
- Quality Threshold。
- Model、Prompt、Schema Version。

Celery 用于：

- 队列。
- 任务执行。
- 重试。
- 限流。
- 并发控制。

LangGraph 仅用于：

- 多步骤 Agent 决策。
- 条件分支。
- Human-in-the-loop。
- 复杂的 Wardrobe Gap 或 Commerce 决策。

单次诊断、Embedding、图像生成和 Garment Detection 不得为了“统一”而套入 LangGraph。

Prompt 与多模态安全：

- 图片和商品页中的文字都是 Untrusted Visual Content。
- 图片文字只能作为分析对象，不能成为系统指令。
- 所有结构化输出必须经过 Schema Validation。
- 模型不能自行获得删除、购买、分享或访问他人衣橱的权限。
- 所有真实副作用必须经过服务端鉴权与业务校验。

## 10. 前端、弱网与体验约束

核心体验路径：

- 上传后 3 秒内让用户知道任务已开始。
- 5～20 秒优先返回文字诊断。
- 图片结果继续在后台生成。
- 用户可以离开页面，重新进入后恢复 Job。

网络恢复分级：

- P0：保存 Local Upload Draft；上传中断后允许用户重试。
- P0：本地持久化 `pendingJobs`，恢复查询未完成任务。
- P0.5/P1：衣橱支持最近一次 Scene Snapshot 的离线只读展示。

状态管理：

- 明确区分 Server State 和 Client UI State。
- Pinia 不是本地数据库。
- 后端是业务真相源。
- 本地仅保存必要的持久化元数据与资产缓存。
- Scene Snapshot 缓存采用版本号和 LRU 管理。

数字衣橱：

- 默认使用 Scene Snapshot + Hotspot Layer。
- Detail、Edit、AI View 和 New Item Animation 才按需加载独立 Display Asset。
- 用户物品位置默认保持稳定。
- AI 整理只能由用户主动触发。
- 所有资产生成都必须实现 Fallback Ladder，不得阻塞入库。

## 11. 安全、隐私与合规

身份和资源：

- 微信身份与 Internal User ID 解耦。
- 所有资源操作必须校验所有权。
- 查询时直接使用 `WHERE id = ? AND user_id = ?` 的 Scoped Repository。
- OwnershipGuard 作为第二层保护，不能替代查询范围约束。
- Slot 等间接资源必须沿 `Slot → Layout → Scene → user_id` 校验。

资产：

- 用户图片默认私有。
- 使用有有效期的 Signed URL。
- Share Asset 与 Private Asset 必须分离。
- 分享资产必须经过专用合规模板处理。

删除：

- 支持 ACCOUNT、WARDROBE、PHOTO、ASSET 删除任务。
- 删除流程覆盖 DB、COS、Embedding、Cache 和 Provider Temporary Data。
- 删除任务必须可查询、可重试、可审计。

日志：

- 不记录原始敏感 `user_id`。
- 不记录私人照片 URL。
- Prompt 全文默认不得进入普通日志。
- 必要调试信息必须进入受控渠道。

合规：

- 建立并维护微信小程序发布 Compliance Checklist。
- 明示 AI 处理、图片授权和用户同意。
- AI 生成图片应包含适用的标识元数据。
- 人像类数据处理必须满足适用的个人信息保护要求。
- 商品数据只使用公开元数据、授权 API、合法第三方服务或用户提供的截图。
- 不得将绕验证码或反爬对抗作为工程路线。

## 12. 限流、配额与成本

Rate Limit 防攻击，Quota 防止成本失控，两者必须独立。

P0 初始目标：

- 普通 API：120 req/min/user。
- AI 创建任务：10 req/min/user。
- Ingestion：30 req/hour/user。

实现建议：

- Redis Token Bucket。
- 返回 `429 Too Many Requests`。
- 设置 `Retry-After`。

成本记录：

- 每个 AIInvocation 记录 Provider、Model、Token、延迟和估算成本。
- 分别统计 Cost per Diagnosis、Optimization、Try-On、Active User 和 Paid User。
- 高成本任务必须设置成本上限。
- 免费配额与付费 Credits 不得混为同一套账务语义。
- Job 失败时不得消耗用户次数或 Credits。

存储成本：

- 监控总存储量、月增长、活跃用户平均存储量和孤儿资产率。
- AssetGCJob 定期清理过期 Scene Render 与被替换的 Display Asset。
- 有外部 ShareRecord 引用的分享资产不得提前删除。

## 13. 可靠性、缓存与可观测性

Outbox：

- Dispatcher 作为独立进程运行并自动重启。
- 使用 `SELECT ... FOR UPDATE SKIP LOCKED` 支持安全竞争。
- 状态为 `PENDING → PROCESSING → PUBLISHED / FAILED`。
- 记录 `next_retry_at`、`last_error` 和 `attempt_count`。
- 定时扫描未处理事件作为兜底。

缓存：

- 优先使用 Versioned Cache，不得仅依赖固定 TTL。
- Wardrobe 变化时递增 `wardrobeVersion` 并主动失效。
- Style Profile 使用物化结果和异步重算。
- 缓存不能成为业务真相源。

可观测性必须覆盖：

- System：API、CPU、DB、Redis、COS。
- Job：Queue Wait、Retry、Failure、Timeout。
- AI：Provider、Model、Latency、Cost、Critic Pass Rate。
- Product：Diagnosis、Optimization、Share、Wardrobe Activation、D7。
- Governance：DeletionJob、Outbox Pending Age、Orphan Asset。

标准日志字段至少包含：

```text
timestamp, level, service, environment, trace_id, request_id,
user_id_hash, job_id, endpoint, latency_ms, status_code, error_code
```

## 14. SLO 与关键验收门禁

P0 性能目标：

| 指标 | P50 | P90 | P95 |
|:---|---:|---:|---:|
| AI 诊断文字结果 | ≤10 秒 | ≤20 秒 | ≤30 秒 |
| Optimization 图片 | ≤30 秒 | ≤60 秒 | - |
| 普通 API | ≤100ms | ≤300ms | ≤500ms |
| Scene Snapshot 加载 | ≤1 秒 | ≤2 秒 | ≤3 秒 |
| Upload 完成确认 | ≤3 秒 | ≤5 秒 | - |

可靠性目标：

- API 月度可用性 ≥99.5%。
- 非用户输入问题导致的 Job 完成率 ≥95%。
- 数据丢失为 0。
- Outbox 事件丢失为 0。
- Outbox 事件处理延迟 P95 <30 秒。
- P0 备份 RPO ≤24 小时、RTO ≤4 小时。

关键 Release Gate：

- Diagnosis Success Rate ≥95%。
- AI Instant Experience 的 Diagnosis P95 <30 秒。
- Job 失败信息正确且 Credits 自动释放。
- Entity Resolution 误合并率采用跨文档中更严格的 ≤2%。
- Display Asset QA Pass ≥90%。
- Outfit 仅使用 AVAILABLE 衣物，正确率 100%。
- Hotspot 命中正确率 ≥95%。
- 低端设备 Scene Snapshot 加载 ≤2 秒且无闪退。
- P0 Blocker Bug 为 0。
- 模型或 Prompt 相比上一版本无显著质量退化。

这些数字在真实压测或用户测试后可以校准，但变更必须有数据依据并更新文档。

## 15. 测试策略

测试体系必须按风险覆盖：

- Unit Test。
- Integration Test。
- API Contract Test。
- AI Eval。
- Visual QA。
- E2E Test。
- Performance Test。
- Security Test。
- Privacy Test。

重点集成场景：

- 用户登录 → 上传 → Asset Complete。
- Diagnosis → Optimization → Job 恢复。
- 业务事务 → Outbox → Celery 执行。
- Job 失败 → Credit Release。
- 商品链接解析失败 → Screenshot Fallback。
- Observation → Entity Resolution → 用户确认。
- Wardrobe 状态变化 → Cache Invalidation。
- 账号删除 → DB/COS/Embedding/Cache 清理。
- 跨用户资源访问必须失败。

AI 质量：

- 每次模型或 Prompt 更新前运行 Golden Dataset。
- 自动评估与人工抽样结合。
- 每个典型 Bad Case 应进入回归数据集。
- 必须对比上一版本，不能只判断当前版本是否“看起来可用”。
- 模型发布遵循 `Regression → Shadow → Human Review → 10% Canary → 50% → 100%`。

## 16. 发布、迁移与回滚

标准发布流程：

```text
Feature Branch
→ PR
→ CI
→ Staging
→ Product QA
→ AI Regression
→ Migration Review
→ Canary
→ Production Approval
```

数据库：

- 只允许向前 Migration。
- 禁止在生产环境手工改表。
- 破坏性字段必须按“新增替代字段 → 双写/回填 → 切换读取 → 删除旧字段”分阶段下线。
- Migration 必须评估锁表、回填、回滚和版本兼容风险。

模型灰度回滚触发条件：

- Quality 下降 >10%。
- Error Rate 上涨 >5 个百分点。
- P95 Latency 上涨 >50%。
- Unit Cost 上涨 >30%。

触发时暂停灰度：

- 已创建 Job 按其 `model_policy_snapshot` 继续执行。
- 新 Job 路由回旧模型。
- 回滚决策记录到 ADR。

上线前必须至少完成一次数据库恢复演练。

## 17. 开发工作流

### 开始编码前

1. 阅读当前目录及父目录中的 `AGENTS.md`。
2. 检查工作区状态，保留用户已有改动。
3. 确认 Phase、Epic、Milestone 和产品假设。
4. 阅读与任务直接相关的 PRD、TDD、DDD、API、ADR 章节。
5. 明确模块 Owner、数据 Owner 和写入边界。
6. 列出验收条件、失败路径、安全要求和需要运行的测试。
7. 检查是否正在无意中引入后续阶段范围。

### 实现过程中

- 优先交付可运行、可验证的最小垂直切片。
- 不为未验证需求过度抽象。
- 外部 Provider、数据库、网络、时间和随机性必须可替换或可测试。
- 新增配置必须有安全默认值和环境变量说明。
- 所有高成本或可重试写操作必须考虑幂等。
- 所有用户资源端点必须实现 Ownership Guard。
- 所有失败路径必须有用户体验、日志和可观测性。
- AI 变更必须保留版本与评估证据。

### 合并前

1. 运行受影响模块的格式化、静态检查和测试。
2. 运行必要的集成、Contract、AI Eval 或隐私测试。
3. 检查 Migration、幂等、并发、所有权和缓存失效。
4. 检查日志中是否泄露敏感数据。
5. 检查成本统计、失败退款和降级路径。
6. 在 Staging 验证核心路径。
7. 更新 API、ADR、运维或产品文档。
8. 明确报告未运行的检查和剩余风险。

## 18. Definition of Done

一个任务只有同时满足以下条件才算完成：

- 代码已完成并通过 Review。
- 自动化测试通过。
- API/OpenAPI 契约已同步。
- 数据库 Migration 已审查。
- 埋点、日志、Trace 和告警已补齐。
- 错误处理、重试、幂等和降级路径完整。
- 用户友好失败信息已验证。
- 安全、所有权和隐私检查已完成。
- 成本记录和 Quota/Credits 行为正确。
- AI 相关改动通过回归评估。
- Staging 验证完成。
- 产品验收完成。
- 相关文档与 ADR 已更新。

不得把“主路径能运行”视为完成。

## 19. 必须记录 ADR 的变更

以下情况必须新增或更新 ADR：

- 改变总体架构、技术栈或部署边界。
- 新增微服务、数据库、消息系统、向量库或 Agent 框架。
- 改变模块依赖方向或写入边界。
- 改变核心实体、状态机或业务真相源。
- 进行 API 破坏性变更。
- 改变 AI Provider、模型路由或 Embedding 策略。
- 改变 SLO、Release Gate 或成本上限。
- 改变用户数据生命周期、权限或删除语义。
- 改变商品数据获取与外部集成方式。
- 接受会跨 Sprint 存在的重大技术债。

ADR 至少记录：背景、决策、替代方案、取舍、影响范围、迁移计划和回滚方案。

## 20. 仓库命令与环境说明

当前仓库以设计文档为主。代码骨架建立后，应在本节补充并持续维护：

- 后端依赖安装、开发启动、测试、Lint、类型检查和 Migration 命令。
- 小程序依赖安装、开发、构建、测试和类型检查命令。
- Admin 端开发和构建命令。
- Docker、本地 PostgreSQL、Redis、Worker 和 Outbox Dispatcher 启动命令。
- OpenAPI TypeScript 类型生成命令。
- AI Eval 和 Golden Dataset 回归命令。
- Staging 部署与烟雾测试命令。

在这些命令实际存在前：

- 从仓库配置文件中发现真实命令。
- 不得凭习惯虚构命令、端口、目录或环境变量。
- 无法运行某项检查时，在交付说明中明确写出原因。

## 21. 交付说明格式

每次开发交付应简要说明：

- 本次实现内容。
- 所属 Phase / Epic / Milestone。
- 修改的主要文件和接口。
- 数据迁移或配置变化。
- 已运行的测试及结果。
- 未运行的测试及原因。
- 对 SLO、成本、安全、隐私和 AI 质量的影响。
- 已知风险和后续工作。

目标不是让改动“看起来完成”，而是让下一位开发者能够验证、维护，并确认它没有偏离项目方向。
