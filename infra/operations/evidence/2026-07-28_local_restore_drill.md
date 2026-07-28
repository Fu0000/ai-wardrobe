# 2026-07-28 本地数据库恢复演练证据

## 结论

本地实现检查通过。该结果证明逻辑备份、校验和、隔离空库恢复、迁移版本、行数和关系
不变量校验链路可执行；不代表 Staging 或 Production 的 OPS-01 已验收。

## 执行范围

- 源：本地 Compose PostgreSQL 18，脚本只执行元数据/行数查询与 `pg_dump`。
- 目标：独立 `--rm` 容器，数据目录使用 512 MiB tmpfs，不暴露宿主端口。
- 命令：`make local-db-drill`。
- 代码提交：`d31d5ee`。
- RPO 目标：≤86,400 秒。
- RTO 目标：≤14,400 秒。

## 结果

| 检查项 | 结果 |
|---|---:|
| 备份状态 | PASSED |
| 归档格式 | PostgreSQL custom / zstd |
| 归档大小 | 62,981 bytes |
| SHA-256 | `116af9cc3c71cd8fbfda5f36c4bd68d5ccb968895cf09f19fb2c960a017d09f6` |
| 恢复状态 | PASSED |
| 恢复耗时 | 0.263 秒 |
| 备份年龄 | 0.563 秒 |
| Alembic 版本 | `20260727_0010` |
| 行数比对 | 6 类一致 |
| 关系不变量 | 4 类均为 0 |

源库当时没有业务行，因此行数均为 0；本次证据主要覆盖 Schema 和恢复工具链。

## 安全与清理

- 备份/恢复凭据不进入命令参数、报告或 Git。
- 恢复目标的服务名和数据库名均含 `restore/drill`，并要求显式确认值。
- 恢复前验证目标 public schema 为空，恢复使用单事务和 `--exit-on-error`。
- 临时容器、tmpfs、备份、Manifest 和报告在命令结束后清理。
- 执行后确认不存在 `aiw-restore-drill-*` 或 `aiw-restore-probe-*` 容器。

## 未覆盖项

- Staging 含业务关系数据的行数与不变量恢复。
- 腾讯云托管备份保留策略、PITR 窗口与访问审计。
- COS 对象与恢复后数据库引用的一致性。
- DevOps、QA 和技术负责人签署。

因此 `OPS-01` 继续保持 `IN_PROGRESS`，Release Gate 继续保持 `NO-GO`。
