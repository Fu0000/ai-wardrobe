# GATE-01 本地事件与漏斗审计证据（2026-07-28）

## 范围

- 环境：`local`
- 查询窗口：最近 24 小时
- 数据源：本地 PostgreSQL `user_events`
- 事件契约：`docs/15_MVP埋点与指标规范_V1.0.md` 的 20 个核心事件

## 执行

```bash
make event-funnel-audit EVENT_AUDIT_ARGS='--environment local --window-hours 24 --report ../infra/operations/evidence/2026-07-28_local_event_funnel_audit.json'
```

结果文件：

- `infra/operations/evidence/2026-07-28_local_event_funnel_audit.json`
- 状态：`BASELINE_NO_DATA`
- 查询事件：0
- 公共上下文违规：0
- 禁止字段违规：0

本地库没有保留集成测试数据，因此该结果只证明审计器能安全查询空基线，不证明
20 个事件已在真实用户旅程中全部出现，也不能替代 Staging 漏斗验收。

## 自动化验证

- 20 个事件契约唯一且完整。
- 三条漏斗各包含 5 个步骤。
- 缺失事件在本地非严格模式报告为基线，不伪造通过。
- Staging 严格模式缺事件、Trace/Request 不可关联、用户哈希缺失或发现禁止字段时
  返回非零状态。
- 隐私违规报告只输出事件 ID 与字段路径，不回显疑似敏感值。
- 真实 PostgreSQL 已验证客户端、服务端及 Growth 事件的幂等写入和公共字段。

## Staging 最终命令

数据库凭据必须通过受控环境变量 `AIW_EVENT_AUDIT_DATABASE_URL` 注入，不得写入参数、
日志或证据文件。

```bash
make event-funnel-audit EVENT_AUDIT_ARGS='--environment staging --window-hours 24 --require-complete --require-correlated-context --report ../infra/operations/evidence/staging_event_funnel_audit.json'
```

只有在授权测试流量覆盖三条完整旅程、报告状态为 `PASSED`，并由产品/QA 核对
Dashboard 口径后，GATE-01 才能从 `IN_PROGRESS` 转为完成。
