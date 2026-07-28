# AI Wardrobe 技术方案设计 TDD V1.1

## 一、总体技术架构
采用 Modular Monolith + Independent Workers。

代码按领域拆模块，运行时按负载拆进程，不提前做微服务。

## 二、技术栈
### Client
uni-app + Vue 3 + TypeScript + Pinia

### Admin
Vue 3 + TypeScript + Vite + Element Plus

### Backend
Python 3.13 + FastAPI + Pydantic 2 + SQLAlchemy 2.x + Psycopg 3 + Alembic

### Data
PostgreSQL 18 + pgvector + pg_trgm + Redis

### Async
Celery + Redis Broker + Transactional Outbox

### AI
AI Gateway + Provider Adapter + Official SDK + Selective LangGraph

### Media
腾讯云 COS + Pillow + OpenCV，必要时 pyvips

### Infra
Docker + CLB/Nginx + Managed PostgreSQL + Managed Redis + COS + OpenTelemetry

## 三、后端模块
app/
- identity/
- assets/
- diagnosis/
- wardrobe/
- ingestion/
- styling/
- looks/
- personal_graph/
- visualization/（DCVS 业务域：Scene / Layout / Slot / Visual State）
- scene_rendering/（渲染基础设施：Layout + Assets → Snapshot）
- commerce/
- growth/
- governance/
- ai/
- jobs/
- events/
- infrastructure/
- shared/

一级模块：
- Universal Ingestion Service
- Scene Rendering Service

### 模块依赖规则
原则：写操作边界严格，查询可以受控同步。

每个模块三层：Public Application Service → Domain → Infrastructure。

允许：模块 A 通过模块 B 的 Public Service Interface 查询数据。
禁止：跨模块直接 Repository 写入、跨模块直接操作 ORM Entity、循环依赖。
Event 用于：异步副作用、跨模块状态派生、非强一致操作（如 LookActuallyWorn → StyleGraph 更新）。

visualization/ 与 scene_rendering/ 边界：
- visualization/：决定"展示什么"（Scene、Zone、Slot、Layout、Visual State）。
- scene_rendering/：决定"怎么渲染成图"（Layout + Assets → Snapshot）。

## 四、AI Gateway
业务代码禁止直接绑定具体模型。
接口：
- StructuredVisionProvider
- ImageGenerationProvider
- ImageEditProvider
- EmbeddingProvider

每个 Task 独立配置 primary、fallback、timeout、retry、cost ceiling、quality threshold。

## 五、Celery 与 LangGraph
Celery：队列、执行、重试、限流、并发。
LangGraph：多步骤 Agent 决策、条件分支、HITL。

适合 LangGraph：
- Stylist Commerce Agent
- 多轮 Personal Stylist
- 复杂 Wardrobe Gap Decision

不使用 LangGraph：
- 单次诊断
- Embedding
- 图像生成
- Garment Detection

## 六、异步队列
当前 P0 运行队列：
- ai_fast
- image_generation
- media_generation
- maintenance

`ingestion` 在 P0.5 进入对应能力时再启用，当前不创建空队列。

所有高成本任务支持：
- idempotency_key
- retry_count
- ai_invocations
- cost tracking

### Job 状态与失败 UX
统一 Job 状态：PENDING → QUEUED → PROCESSING → QUALITY_CHECKING → COMPLETED / FAILED_RETRYABLE / FAILED_FINAL / TIMED_OUT / CANCELLED。

客户端不暴露内部错误（如 OPENAI_RATE_LIMIT、COS_TIMEOUT），转换为用户可理解消息：
- AI 临时失败："这次生成没有成功，我们没有消耗你的次数。重新试一次。"
- 输入问题："这张照片里没有识别到完整穿搭，请换一张正面或全身照片。"
- 最终失败："AI 今天状态不太好，这次结果没有生成成功。"

Credits 采用 Ledger 模式：RESERVE → Job → SUCCESS 时 COMMIT / FAIL 时 RELEASE。
前端轮询上限后展示"任务中心 + 下次打开自动恢复"，不依赖微信服务通知。

### 任务 SLO 与 Worker Capacity
诊断任务 P50 ≤10 秒、P90 ≤20 秒；优化任务 P50 ≤30 秒、P90 ≤60 秒。
渐进展示：文字诊断先返回，图片生成后续推送。
Worker 数量根据并发用户和 SLO 目标配置，P0 阶段 2～4 Worker。

## 七、Transactional Outbox
业务数据与 outbox_event 同事务写入。
由 Dispatcher 可靠发布 Celery 事件。

关键事件：
GarmentResolved、StyleDiagnosisCompleted、LookSelected、LookActuallyWorn、ProductIngested、DisplayLayoutActivated。

### Outbox Dispatcher 可靠性
Dispatcher 作为独立进程，配置自动重启。
使用 SELECT ... FOR UPDATE SKIP LOCKED 实现 Worker 安全竞争。
Outbox 状态：PENDING → PROCESSING → PUBLISHED / FAILED。
增加 next_retry_at、last_error、attempt_count 字段。
监控指标：oldest_pending_age、pending_count、failed_count。
备用方案：定时任务每分钟扫描未处理的 Outbox 事件作为兜底。

## 八、图片上传
小程序请求 Upload Ticket
→ 直接上传 COS
→ Complete API
→ 创建 Asset
→ 异步处理。

API Server 不搬运大图。

### Upload Validation
请求体限制：JSON Body ≤ 1MB，Share Text ≤ 20KB，单图 Upload Ticket 默认 ≤ 20MB。
服务端必须校验：MIME Type、Magic Number、Image Dimensions，不能只相信客户端文件扩展名。

## 九、Universal Ingestion
POST /api/v1/ingestions

类型：
PHOTO、ITEM_PHOTO、PRODUCT_LINK、PRODUCT_SCREENSHOT、未来 ORDER_IMPORT。

Product Link：
分享文本/URL
→ Normalize
→ Source Resolve
→ 公开元数据/授权数据
→ Product Extraction
→ Screenshot Fallback
→ 用户确认 Variant
→ Product Snapshot
→ Entity Resolution。

不以绕验证码、反爬对抗为核心工程路线。

## 十、多源 Entity Resolution
综合：
- Category
- Structured Attributes
- Visual Embedding
- Semantic Embedding
- Recent Ingestion Context
- Time Window
- User Confirmation

输出：
RESOLVED / NEEDS_REVIEW / NEW_ENTITY。

## 十一、Display Asset Pipeline
Source/Merchant Image
→ Normalize
→ Background Removal
→ Standardize
→ Content Bounds
→ Anchor
→ QA
→ Asset Registry。

Geometry：
canvas、content_bounds、anchor_points、display_profile、visual_scale_factor。

## 十二、Progressive Asset Upgrade
入库立即可见，不等待最终 Display Asset。
质量梯度逐级升级。

## 十三、Digital Closet Runtime
默认：
Scene Snapshot + Hotspot Layer。

独立 Display Asset 仅在：
- Detail
- Edit
- AI View
- New Item Animation
按需加载。

## 十四、Scene Rendering Service
输入：Scene、Layout、Display Assets、Visual Token。
输出：
- Reality Snapshot
- AI Snapshot
- Share Snapshot

## 十五、Hit Testing
Scene 统一接受 Tap(x,y)。
根据 hit_region、z_index、visual_center、interaction_priority 命中物品。

## 十六、Personal Context Service
Agent 统一通过：
- get_available_wardrobe
- get_recent_wear_history
- get_style_profile
- resolve_garment_reference
- find_wardrobe_gap
- find_compatible_items

不直接拼数据库原始表。

### Context Cache 策略
采用 Versioned Cache，不使用简单固定 TTL。

Available Wardrobe：Redis 缓存，TTL 5～15 分钟，wardrobe 变更事件主动失效（wardrobeVersion + 1）。
Style Profile：Materialized Profile + Redis 缓存，WearEvent 触发 Async Recompute，不实时重新聚合所有 PreferenceSignal。
Compatible Items：当前实时计算，未来可按 item + wardrobeVersion + algorithmVersion 缓存。

## 十七、前端状态管理
Pinia Store 按领域拆分：
useAuthStore、useAppStore、useDiagnosisStore、useJobStore、useWardrobeStore、useSceneStore、useLookStore、useNetworkStore。

区分 Server State（后端是真相）和 Client UI State（筛选条件、选中状态、Scene Mode）。
不要把 Pinia 当本地数据库：Memory Store → Persistent Metadata Cache → Backend Source of Truth。

Scene Snapshot 采用 Local Asset Cache（sceneId + layoutVersion + renderVersion + localAssetPath + lastAccessedAt），LRU 按总磁盘占用 + 最近访问时间管理。

## 十八、API 版本策略
/api/v1/ 代表 Breaking Contract Version。
不升级版本：新增可选字段、新增 endpoint、新增 enum（客户端需具备 unknown fallback）、性能优化。
升级版本：删除字段、修改字段类型、修改核心语义、删除 endpoint。
兼容策略：至少支持 2 个客户端发布周期，且原则上不少于 90 天。
客户端 Header：X-Client-Version、X-Platform、X-App-Channel。
后端维护 minimum_supported_version 和 recommended_version，支持严重安全问题时的强制升级。

## 十九、性能
- Snapshot First
- Lazy Load
- Thumbnail/Detail 双分辨率
- P0.5 不做复杂自由拖拽
- 高耗时任务全部异步
- Polling First

## 二十、安全与隐私
- Internal User ID 与微信身份解耦
- 私有图片
- Signed URL
- Share Asset 与 Private Asset 分离
- DeletionJob
- Asset Registry

### 资源归属校验
所有资源操作端点必须校验资源归属。
Repository 查询直接使用 WHERE id = ? AND user_id = ?，不存在“先查出来再判断”的遗漏。
跨实体操作再校验关联链归属，形成查询范围与关联一致性的双层保护。
Slot 等间接资源通过关系链校验：Slot → Layout → Scene → user_id。

### Rate Limiting
三层限流：IP Layer、User Layer、Costly Action Layer。
初始配置：普通 API 120 req/min/user，AI 创建任务 10 req/min/user，Ingestion 30 req/hour。
Rate Limit 防攻击，Quota 防成本，两者是独立系统。
实现：Redis Token Bucket，返回 429 + Retry-After。

## 二十一、可观测性
System：API、CPU、DB、Redis。
Job：Queue Wait、Retry、Failure。
AI：Provider、Model、Latency、Cost、Critic Pass Rate。

## 二十二、Embedding 模型管理
不同模型和不同维度的向量绝对不能直接计算相似度。
新增 embedding_model_registry 管理模型生命周期：SHADOW → ACTIVE → DEPRECATED → RETIRED。
模型升级流程：New Model SHADOW → Backfill → Quality Eval → ACTIVE → Old Model DEPRECATED → Grace Period → RETIRED。

## 二十三、工程规范
Backend：pytest、ruff、mypy、Alembic。
Frontend：ESLint、TypeScript strict、Vitest。
API：OpenAPI 自动生成 TS Types。
CI/CD：PR Check → Test → Build → Staging → Production Approval。
