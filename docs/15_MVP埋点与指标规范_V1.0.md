# AI Wardrobe MVP 埋点与指标规范 V1.0

- 文档状态：实现基线
- 适用范围：P0a、P0b、P0 Hardening

## 一、目标

埋点用于回答三个问题：

1. 用户是否快速获得第一次价值。
2. Minimal Change Optimization 是否带来信任与分享。
3. 系统质量、延迟和成本是否支持扩大测试。

埋点不得用于收集与上述目标无关的敏感数据。

## 二、事件命名

统一使用：

```text
domain.object.action
```

示例：

- `auth.wechat.succeeded`
- `asset.upload.completed`
- `diagnosis.job.created`
- `diagnosis.result.viewed`
- `optimization.request.created`
- `share.asset.created`
- `vote.choice.submitted`

事件名使用小写英文，已经上线的语义不得静默修改。

## 三、公共字段

所有服务端事件：

- `event_id`
- `event_name`
- `event_version`
- `occurred_at`
- `environment`
- `trace_id`
- `request_id`
- `user_id_hash`
- `session_id`
- `client_version`
- `platform`
- `app_channel`

Job 事件额外包含：

- `job_id`
- `task_type`
- `job_status`
- `queue`
- `queue_wait_ms`
- `attempt_count`

AI 调用额外包含：

- `invocation_id`
- `provider`
- `model`
- `prompt_version`
- `schema_version`
- `latency_ms`
- `input_tokens`
- `output_tokens`
- `estimated_cost_microunits`
- `quality_passed`
- `error_code`

禁止记录：

- 原始微信 OpenID 或 UnionID。
- 原始私人照片 URL。
- Prompt 全文。
- 图片识别到的完整文字。
- 用户自由文本原文，除非存在单独的受控研究授权。

## 四、核心产品事件

| 事件 | 触发点 | 关键属性 |
|:---|:---|:---|
| `auth.wechat.succeeded` | 登录完成 | `is_new_user` |
| `consent.ai.accepted` | 用户同意 AI 图片处理 | `consent_version` |
| `consent.ai.revoked` | 用户撤回 AI 图片处理授权 | `consent_version` |
| `asset.upload.started` | 获取 Ticket 后开始直传 | `asset_id`, `size_bucket` |
| `asset.upload.interrupted` | 上传因网络中断 | `reason`, `progress_bucket` |
| `asset.upload.completed` | Complete API 成功 | `asset_id`, `latency_ms` |
| `diagnosis.job.created` | 诊断事务提交 | `job_id`, `occasion` |
| `diagnosis.text.completed` | 文字结果可用 | `job_id`, `latency_ms` |
| `diagnosis.result.viewed` | 结果页首次完整展示 | `diagnosis_id`, `score_bucket` |
| `diagnosis.optimization.clicked` | 点击优化 CTA | `diagnosis_id` |
| `optimization.job.created` | 优化事务提交 | `job_id`, `change_level` |
| `optimization.result.completed` | Critic 通过 | `job_id`, `critic_attempts` |
| `optimization.before_after.viewed` | 对比页首次展示 | `optimization_id` |
| `share.asset.created` | Share Asset 可用 | `share_id`, `latency_ms` |
| `share.wechat.invoked` | 用户触发微信分享 | `share_id`, `attribution_source` |
| `share.scene.opened` | 好友打开分享 | `share_id`, `attribution_source` |
| `vote.choice.submitted` | 投票成功 | `share_id`, `choice` |
| `growth.continue.clicked` | 好友继续体验 | `share_id` |
| `privacy.deletion.requested` | 用户提交删除 | `deletion_type` |
| `privacy.deletion.completed` | 删除任务完成 | `deletion_type`, `latency_ms` |

## 五、核心漏斗

### 5.1 首次价值

```text
auth.wechat.succeeded
→ asset.upload.completed
→ diagnosis.job.created
→ diagnosis.text.completed
→ diagnosis.result.viewed
```

指标：

- Diagnosis Start Rate。
- Diagnosis Completion Rate。
- Time to First Text Value。
- Diagnosis Result View Rate。

### 5.2 Optimization

```text
diagnosis.result.viewed
→ diagnosis.optimization.clicked
→ optimization.job.created
→ optimization.result.completed
→ optimization.before_after.viewed
```

指标：

- Optimization Click Rate。
- Optimization Completion Rate。
- Critic First-pass Rate。
- Before/After View Rate。

### 5.3 Growth

```text
optimization.before_after.viewed
→ share.wechat.invoked
→ share.scene.opened
→ vote.choice.submitted
→ growth.continue.clicked
```

指标：

- Share Rate。
- Share Open Rate。
- Vote Participation Rate。
- Share-to-New-Experience Rate。

## 六、系统与质量指标

API：

- Request Count。
- Error Rate。
- P50/P90/P95 Latency。
- Rate Limit Rejection。

Job：

- Queue Wait。
- Completion Rate。
- Retry Rate。
- Timeout Rate。
- `oldest_pending_age`。

AI：

- Schema Pass Rate。
- Critic First-pass Rate。
- Provider Error Rate。
- Provider Fallback Rate。
- Cost per Diagnosis。
- Cost per Optimization。

资产与治理：

- Upload Success Rate。
- Signed URL Failure。
- Deletion Completion Rate。
- Orphan Asset Rate。

## 七、数据一致性

- 服务端业务事件优先通过 Transactional Outbox 产生。
- 客户端展示事件使用唯一 `event_id`，服务端按 ID 去重。
- 时间统一存储 UTC。
- Event Schema 必须有版本。
- 业务指标不得混合 Staging、测试和 Production 数据。
- 失败重试不能造成重复计数。

## 八、验收

每个 MVP 功能进入 Done 前必须：

- 列出对应事件。
- 验证正常和失败路径。
- 验证重试不会重复计数。
- 在 Staging Dashboard 中查询到事件。
- 确认不包含禁止字段。

如果无法通过事件回答该功能的产品假设或质量问题，埋点设计不算完成。
