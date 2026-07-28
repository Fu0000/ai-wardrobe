# AI Wardrobe 数据架构与数据库设计 DDD V1.1

## 一、核心模型
Entity + Observation + Evidence + State + Event。

核心原则：Observation ≠ Truth。

## 二、Personal Graph
- Garment Graph：拥有什么
- Style Graph：喜欢什么
- Context Graph：何时何地需要什么
- Decision Graph：推荐、选择、真实穿着
- Commerce Graph：缺什么、看什么、买什么

## 三、核心实体组
用户：users、user_identities、user_profiles、user_style_profiles。
资产：user_assets、source_photos。
衣物：wardrobe_items、garment_observations、garment_attribute_evidence、garment_aliases、garment_state_events、wardrobe_item_sources。
商品来源：product_source_snapshots。
诊断：style_diagnoses、style_optimization_results。
穿搭：outfits、outfit_items、looks、look_items、wear_events、wear_feedback、preference_signals。
可视化：wardrobe_item_display_assets、display_scenes、display_zones、display_layout_versions、display_slots、display_scene_renders。
商业：products、wardrobe_gaps、recommendations、recommendation_candidates、commerce_events。
配额：quota_policies、usage_counters、credit_ledger。
平台：generation_jobs、ai_invocations、entity_embeddings、embedding_model_registry、outbox_events、share_records、user_events、deletion_jobs。

## 四、WardrobeItem
表示用户现实中拥有的一件具体物品，不等于 Product。

关键字段：
canonical_name、category、subcategory、primary_color、material、fit、silhouette、style_tags、availability_status、essential_score、wear_count、last_worn_at。

## 五、GarmentObservation
必须保存：
source_photo_id、bounding_box、detected_attributes、observation_confidence、resolved_item_id、resolution_status、resolution_method、resolution_confidence、resolver_version、resolved_at。

## 六、Attribute Evidence
字段：
attribute_name、attribute_value、source_type、source_id、confidence、model_version。

source_type：
VISION_AI / USER_CONFIRMATION / MERCHANT_DATA / WEAR_EVENT / MANUAL_EDIT。

## 七、Product Source Snapshot
不可变保存某次解析结果。
字段：
user_id、platform、source_url、external_product_id、variant_id、raw_title、brand、price_cents、structured_attributes、raw_images、parser_version、parsed_at。

## 八、WardrobeItemSource
一个衣物实体支持多个来源。
类型：
PRODUCT_LINK、PHOTO_OBSERVATION、MANUAL_INPUT、ORDER_IMPORT、USER_UPLOAD。

## 九、StyleDiagnosis
持久化：
source_photo、occasion、score、strengths、issues、primary_issue、optimization_plan、model_version、prompt_version、schema_version。

一个 Diagnosis 对应多个 OptimizationResult。

## 十、WearEvent
verification_status：
UNVERIFIED / USER_CONFIRMED / PHOTO_VERIFIED / AI_VERIFIED / REJECTED。

## 十一、PreferenceSignal
字段：
dimension、value、signal_type、base_weight、decay_profile、source、context、occurred_at。

时间衰减不修改原始权重。

## 十二、Essential Score
由穿着频率、搭配广度、场景覆盖、真实穿着率、Recency 共同计算。
is_essential 只是缓存标记。

## 十三、Display Asset
字段：
wardrobe_item_id、asset_id、view_type、display_profile、canvas_width、canvas_height、content_bounds、anchor_points、visual_scale_factor、asset_quality_tier、quality_report、is_primary。

## 十四、Display Scene
display_scenes：场景。
display_zones：逻辑区域。
display_layout_versions：布局版本。
display_slots：稳定空间位置。
display_scene_renders：Reality/AI/Share Snapshot。

DisplaySlot 坐标使用 0～1 Normalized Coordinate。

### Scene Render 生命周期
Render 状态：ACTIVE → SUPERSEDED → EXPIRED → DELETION_PENDING → DELETED。
保留策略：Reality Snapshot 保留 active + 最近 2 个历史版本；AI Snapshot 按算法版本和访问情况清理；Share Snapshot 按分享生命周期保留（有外部 ShareRecord 引用的资产不可删除）。
清理由 AssetGCJob 定期执行。DeletionJob 只负责用户主动删除。

## 十五、Slot Occupancy
OCCUPIED / HIDDEN / ADD_ENTRY / DECORATION / WARDROBE_GAP。

## 十六、Embedding
entity_embeddings 独立存储 visual、semantic、style、multimodal。
支持多模型并存。

### Embedding 模型管理
新增 embedding_model_registry 表：model_name、model_version、embedding_type、dimension、status、activated_at、deprecated_at。
状态：SHADOW → ACTIVE → DEPRECATED → RETIRED。
迁移流程：New Model SHADOW → Backfill → Quality Eval → ACTIVE → Old Model DEPRECATED → Grace Period → RETIRED。
核心原则：不同模型和不同维度的向量绝对不能直接计算相似度。检索时只使用同 model_version 的 Embedding。

## 十七、GenerationJob 与 AIInvocation
Job 表示用户层任务。
Invocation 表示真实 Provider 调用。
用于准确核算成本、延迟和重试。

Job 状态：PENDING → QUEUED → PROCESSING → QUALITY_CHECKING → COMPLETED / FAILED_RETRYABLE / FAILED_FINAL / TIMED_OUT / CANCELLED。

## 十八、OutboxEvent
业务表与 OutboxEvent 必须同事务提交。
Outbox 状态：PENDING → PROCESSING → PUBLISHED / FAILED。
增加字段：next_retry_at、last_error、attempt_count。

## 十九、DeletionJob
支持 ACCOUNT、WARDROBE、PHOTO、ASSET。
依次清理 DB、COS、Embedding、Cache、Provider Temporary Data。

## 二十、配额与 Credits
### quota_policies
定义产品规则：plan、quota_type（DIAGNOSIS / OPTIMIZATION / OUTFIT / INGESTION / STORAGE）、period（DAILY / MONTHLY / LIFETIME）、limit。

### usage_counters
高性能计数：user_id、quota_type、period_key、used、reserved。
P0 免费配额可先用 Redis Atomic Counter + PostgreSQL Usage Record。

### credit_ledger
涉及付费 AI Credits 时支持事件类型：GRANT、RESERVE、COMMIT、RELEASE、EXPIRE、PURCHASE。
采用 Ledger 模式保证并发安全和可靠退款。

## 二十一、数据飞轮
照片/链接
→ Observation/Evidence
→ Resolution
→ Garment Digital Twin
→ Wardrobe Graph
→ Outfit
→ Look
→ WearEvent
→ PreferenceSignal
→ StyleProfile
→ 更好的 Outfit。

## 二十二、数据生命周期（P0 预留，Phase 2 执行）
ai_invocations：超过 6 个月的记录适合归档。
outbox_events：已成功处理的事件短期保留后清理。
wear_events：Wear History 是 Personal Graph 的长期资产，不删除原始明细，未来按需聚合月度统计。
建表时预留 created_at 索引以支持范围分区。

## 二十三、阶段建表范围
P0：用户、资产、诊断、优化、Job、Invocation、Share、Outbox、quota_policies、usage_counters。
P0.5：Wardrobe、Observation、Evidence、Source、ProductSnapshot、DisplayAsset、Scene、Slot、Render、Embedding、embedding_model_registry。
P1：Outfit、Look、WearEvent、PreferenceSignal、StyleProfile、credit_ledger。
P2：Gap、Recommendation、Commerce。
