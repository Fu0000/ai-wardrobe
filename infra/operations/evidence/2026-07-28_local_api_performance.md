# 2026-07-28 本地 API 性能基线证据

## 结论

本地认证读流量基线通过：5 → 20 iterations/s 阶梯下 4,650 次业务请求全部成功，
HTTP 失败率为 0%，P95 为 14.99 ms，P99 为 20.73 ms。该结果证明压测入口、门槛、
脱敏报告和临时账号清理可执行，不代表 Staging AI/COS、队列积压或生产容量已通过；
`TST-03` 与 Release Performance Gate 继续保持 `IN_PROGRESS`。

## 执行范围

- 应用提交：`f6a44dc8d66c529bdfb0874a40c482ed1cdbccf3`。
- 远端 CI：
  [run 30349155533](https://github.com/Fu0000/ai-wardrobe/actions/runs/30349155533)，
  Backend 与 Miniapp Job 均为 `success`。
- 命令：`make local-api-baseline`。
- 执行时间：2026-07-28 10:03:04～10:07:36 UTC。
- Load Generator：
  `grafana/k6:2.1.0@sha256:65c920dc067d5e2e00befbf982af6ad6ad0117034e8b1c65817c7975c52d4669`。
- 流量：1 分钟从 5 升至 20 iterations/s，20 iterations/s 保持 3 分钟，
  30 秒降至 0；20 个预分配 VU，最多允许 80 个。
- 目标：本机单个 Uvicorn `--reload` API 进程、本地 Docker PostgreSQL 18 与
  Redis 8.0.1；Apple M1 / arm64 / 16 GiB。
- PostgreSQL 与 Redis 容器没有设置 CPU/内存硬限制，因此本报告不用于生产规格推导。

## 结果

| 指标 | 结果 | 门槛 |
|:---|---:|:---|
| 场景业务请求 | 4,650 | 信息项 |
| 全部 HTTP 请求（含 2 次预检） | 4,652 | 信息项 |
| 平均吞吐（含升降载） | 17.22 req/s | 信息项 |
| 业务成功率 | 100.000% | >99% |
| HTTP 失败率 | 0.000% | <1% |
| P50 | 8.86 ms | 信息项 |
| P90 | 12.52 ms | 信息项 |
| P95 | 14.99 ms | <500 ms |
| P99 | 20.73 ms | <1,000 ms |
| Max | 63.52 ms | 信息项 |
| 中断 / 丢弃迭代 | 0 / 0 | =0 |
| 活跃 VU 峰值 | 1 / 20 预分配 | 信息项 |

同一观测窗口内：

- Prometheus 的 OTel、PostgreSQL、Redis 三个 Target 均为 `up`；
- PostgreSQL `ai_wardrobe` 连接峰值 3 / 100；
- Redis 客户端连接峰值 11，内存峰值 1,735,560 / 268,435,456 bytes；
- 压测结束后临时性能用户数为 0，临时目录残留为 0；
- JSON summary 与 Markdown report 不含 Access Token、用户 ID 或私有资产引用。

## 容量建议与剩余项

本地读路径可以按 20 req/s 作为 Staging 首轮 API 基线起点；只有在相同不可变 SHA、
生产形态 API/Worker 副本与资源限制下复验后才能提高流量或形成生产容量建议。

Release Gate 仍需：

- Staging 普通 API、Scene Snapshot 与 COS 加载压测及 Dashboard 资源水位；
- 10 个授权 Asset 起步的真实 Diagnosis 并发，确认成功率、P90/P95、成本、额度和
  Job 无丢失/卡死；
- 停止 Worker、制造图片队列积压并恢复，记录 Queue Drain Time；
- 30～50 个授权样本扩容、低端安卓体验与 PostgreSQL/COS 对账；
- 达到停止条件时验证能立即终止并触发真实 On-call。

因此当前整体发布结论仍为 `NO-GO`。
