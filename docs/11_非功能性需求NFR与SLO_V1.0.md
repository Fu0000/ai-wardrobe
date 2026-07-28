# AI Wardrobe 非功能性需求 NFR 与 SLO V1.0

## 一、Performance SLO

| 指标 | P50 目标 | P90 目标 | P95 目标 |
|:---|---:|---:|---:|
| AI 诊断（文字结果） | ≤10 秒 | ≤20 秒 | ≤30 秒 |
| Optimization 图片 | ≤30 秒 | ≤60 秒 | - |
| API 普通请求 | ≤100ms | ≤300ms | ≤500ms |
| Scene Snapshot 加载 | ≤1 秒 | ≤2 秒 | ≤3 秒 |
| Upload 完成确认 | ≤3 秒 | ≤5 秒 | - |

以上为 P0 Target SLO，通过真实压测和 30～50 人测试校准后定稿。

## 二、Availability

| 指标 | 目标 |
|:---|:---|
| API 可用性 | ≥ 99.5%（月度） |
| Job 完成率（非用户输入问题） | ≥ 95% |
| Outbox 事件处理延迟 | P95 < 30 秒 |

## 三、Reliability

| 指标 | 目标 |
|:---|:---|
| 数据丢失 | = 0 |
| Outbox 事件丢失 | = 0（同事务保证） |
| 备份 RPO | ≤ 24 小时（P0），PITR（P1） |
| 备份 RTO | ≤ 4 小时 |

## 四、Scalability

| 用户规模 | 预期并发 | Worker 配置 | DB 配置 |
|:---|:---|:---|:---|
| 50 人 | 5 | 2 Worker | 基础款 |
| 300 人 | 30 | 4 Worker | 基础款 |
| 1,000 人 | 100 | 6～8 Worker | 升级配置 |
| 10,000 人 | 500+ | 水平扩展 | 读写分离 |

## 五、Security

| 控制项 | 要求 |
|:---|:---|
| Rate Limiting | 普通 API 120 req/min/user，AI 任务 10 req/min/user |
| 资源归属校验 | 查询范围 + 关联链一致性双层校验 |
| Upload Validation | MIME + Magic Number + Dimensions 服务端校验 |
| 请求体大小 | JSON ≤ 1MB，图片 ≤ 20MB（COS 直传） |
| 跨用户隔离 | 所有资源查询带 user_id 条件 |
| Prompt 安全 | Untrusted Visual Content 声明 + Schema Validation |

## 六、Cost

| 指标 | 目标 |
|:---|:---|
| Cost per Diagnosis | P0 结束后测定基线 |
| Cost per Active User | P0 结束后测定基线 |
| COS 存储增长 | 监控 avg_storage_per_active_user |
| Orphan Asset Rate | < 5% |

## 七、说明
本文档中的具体数字为 V1.0 Target，通过真实压测、模型成本和用户测试校准后逐步固化为硬约束。
P0 阶段作为努力目标，P1 后根据数据提升为 Release Gate。
