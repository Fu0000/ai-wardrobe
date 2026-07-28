# 2026-07-28 本地告警链路演练证据

## 结论

本地实现检查通过。PostgreSQL/Redis 独立采集、Prometheus 规则加载、API Readiness
指标上报，以及 Alertmanager 合成告警注入和解除均可执行；该结果不代表真实 On-call
通知已送达，因此 `OBS-02` 继续保持 `IN_PROGRESS`，`REL-005` 尚未通过。

## 执行范围

- 代码提交：`5383f0a`。
- Docker 入口修复提交：`cbc8c4e`。
- 命令：`make infra-observability-up`、`make local-alert-drill`。
- Prometheus：`v3.13.1`，共加载 14 条规则。
- PostgreSQL exporter：`v0.19.1`。
- Redis exporter：`v1.88.0`。
- Alertmanager：`v0.32.1`，本地 receiver 为 `local-ui-only`。

## 结果

| 检查项 | 结果 |
|---|---:|
| Prometheus / Alertmanager Readiness | PASSED |
| Prometheus 配置与规则语法 | PASSED，14 条 |
| Prometheus Scrape Targets | OTel、PostgreSQL、Redis 均 `up` |
| PostgreSQL 可用性 | `pg_up=1` |
| PostgreSQL 活跃连接 / 上限 | `1 / 100` |
| Redis 可用性 | `redis_up=1` |
| Redis客户端连接 / 上限 | `10 / 10,000` |
| Redis 已用内存 / 上限 | `1,585,016 / 268,435,456 bytes` |
| 五条依赖规则状态 | `health=ok`、`state=inactive` |
| 合成告警注入 | PASSED |
| 合成告警解除与残留检查 | PASSED，活动残留 0 |
| API Readiness OTLP 指标 | database/object_storage/redis 均为 `1` |

五条依赖规则覆盖：

- PostgreSQL 不可用、连接使用率超过 80%；
- Redis 不可用、连接使用率超过 80%、内存使用率超过 80%。

完整质量回归同时通过：后端 227 个本地测试通过、24 个真实集成测试按本地默认配置
跳过，小程序 61 个测试通过；Ruff、Mypy、ESLint、`vue-tsc` 和微信构建通过。

## 安全与清理

- Exporter 仅在 Compose 内网暴露端口；Prometheus、Alertmanager 和 Grafana 仅绑定
  `127.0.0.1`。
- PostgreSQL exporter 的本地凭据来自 `.env`，未写入报告或 Git。
- 演练 ID 为一次性值，解除后 Alertmanager 活动告警中残留数为 0。
- 未停止或修改业务 PostgreSQL 数据；Redis 仅按 Compose 配置重建并保留命名卷。

## 未覆盖项

- Staging/Production 的真实通知 receiver、密钥托管、环境标签与路由抑制。
- 告警到达具体 On-call 人员、确认、升级和响应时长证据。
- PostgreSQL/Redis 故障与连接/内存阈值的 Staging 故障注入。
- 托管 PostgreSQL/Redis 平台指标与本项目规则的最终映射。

因此当前 Release Gate 继续保持 `NO-GO`。
