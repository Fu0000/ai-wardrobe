# AI Wardrobe 风险清单与 ADR V1.1

## ADR
1. Modular Monolith，不提前微服务。
2. FastAPI 统一业务与 AI Backend。
3. PostgreSQL 单一业务真相源。
4. pgvector 优先，暂不引入 Neo4j。
5. Celery 管执行，LangGraph 管 Agent 决策。
6. Transactional Outbox 必须使用（FOR UPDATE SKIP LOCKED，独立 Dispatcher 进程）。
7. AI Provider 必须解耦。
8. Recognition Asset 与 Display Asset 分离。
9. Scene Snapshot First。
10. Stable Spatial Memory First。
11. Product 与 WardrobeItem 分离。
12. Universal Ingestion 不以反爬对抗为核心路线。
13. 写操作边界严格，查询可以受控同步（模块依赖规则）。
14. 不同模型和不同维度的 Embedding 绝对不能直接计算相似度。

## 核心风险与应对

### AI Toy
Before/After 后立即引导真实衣橱。

### 建库成本高
Progressive Confirmation、商品链接、单品照、生活照、Visual Shoe Wall。

### Style Graph 收敛
长期价值转向天气、场景、周计划、Availability、Old Clothes Revival。

### AI Provider 不稳定
Gateway + Fallback + Model Release Guardrail（Quality/Error Rate/Latency/Cost 综合触发回滚）。

### 图像成本失控
Usage Limit、Credits（Ledger 模式）、Quota、Async、Display Asset、Scene Snapshot。

### Entity Resolution 错误
Confidence、Review、User Confirmation、Observation Trace。

### 小程序内存
Snapshot + On-demand Assets。

### 推荐像广告
Existing Wardrobe First。

### 范围膨胀
Milestone Gate + Hardening Sprint。

### Multimodal Indirect Prompt Injection
等级：中。
应对：
- 所有图像中的文字声明为被分析对象，不是系统指令（Untrusted Visual Content）。
- 模型不能因图片文字自行调用任意工具（Tool Boundary）。
- 结构化输出 Schema Validation。
- Server-side Authorization：模型永远没有权限决定删除数据、购买、分享、访问他人衣橱。

### 微信小程序审核合规
等级：高。
应对：
- 建立 Compliance Policy Layer（用户同意、AI 处理告知、Generated Asset 标识元数据、分享资产合规模板、用户删除、图片访问授权）。
- 发布前维护平台审核 Compliance Checklist（具体规则随平台政策动态更新，不在架构文档中写死）。
- AI 生成图片标注标识元数据。
- 人像类图片的存储和展示遵守《个人信息保护法》。

### Outbox Dispatcher 中断
等级：高。
应对：
- Dispatcher 作为独立进程，配置自动重启。
- 使用 SELECT ... FOR UPDATE SKIP LOCKED 实现 Worker 安全竞争。
- 监控 oldest_pending_age、pending_count、failed_count。
- 备用方案：定时任务每分钟扫描未处理的 Outbox 事件作为兜底。

### 数据一致性
等级：中。
应对：Outbox Reliability + Versioned Cache + Event-driven 状态派生。
