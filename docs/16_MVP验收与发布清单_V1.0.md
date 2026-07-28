# AI Wardrobe MVP 验收与发布清单 V1.0

## 一、目的与适用范围

本清单是 AI Wardrobe P0 MVP 进入 30～50 人封闭测试前的唯一 Go/No-Go 记录。它覆盖微信登录、私有图片、AI 诊断、Minimal Change Optimization、分享投票、异步任务、隐私删除、可观测性、恢复和发布。

代码完成不等于验收完成。所有依赖 Staging、腾讯云 COS、微信真机、真实 AI Provider 或授权样本的条目，必须附可复查证据后才能由 `NOT_RUN` 改为 `PASS`。测试照片必须取得明确授权，不得使用线上用户数据代替测试数据。

状态统一为：

- `PASS`：已执行且证据满足阈值。
- `FAIL`：已执行但未满足阈值，必须关联 Bug。
- `BLOCKED`：缺少环境、凭据、样本或外部依赖。
- `NOT_RUN`：具备条件但尚未执行。
- `N/A`：经产品、QA、技术负责人共同确认不适用。

## 二、职责与签署

| 角色 | 职责 | 发布前要求 |
|:---|:---|:---|
| 产品负责人 | MVP 范围、用户体验、已知问题和测试人群 | 对核心旅程和风险签署 |
| QA 负责人 | 用例、回归、Bug 分级、真机和证据归档 | 确认 Blocker/Critical 清零 |
| 技术负责人 | 架构、安全、迁移、容量和回滚 | 确认技术 Gate 与回滚可执行 |
| AI 负责人 | 质量、Fidelity、成本、模型版本和 Canary | 确认 Eval 与模型 Gate |
| DevOps/On-call | 部署、告警、备份、恢复和事件响应 | 确认值班表与告警可达 |

正式发布记录必须将角色替换为具体姓名，不接受“团队”作为签署人。

## 三、Bug 分级与处理规则

| 级别 | 判定示例 | 响应与发布规则 |
|:---|:---|:---|
| Blocker | 数据丢失或串用户；私有图片泄露；删除无法完成；Secret 泄露；核心链路完全不可用；展示未通过 Critic 的高风险图片 | 立即停止发布和扩量，30 分钟内响应；发布前必须修复并完整回归，数量必须为 0 |
| Critical | 登录、上传、诊断或优化在正常输入下高频失败；额度错误扣减；任务永久卡住；回滚不可执行；关键告警不可达 | 2 小时内响应；`GO` 前必须为 0，不允许仅靠操作手册绕过 |
| Major | 存在可靠绕行方式的功能错误；明显性能退化；局部设备兼容、文案或埋点错误；非关键告警缺失 | 1 个工作日内定责；只允许 `CONDITIONAL GO`，必须有 Owner、截止日和影响范围 |
| Minor | 不影响完成任务的样式、轻微文案或低频边界问题 | 进入版本 Backlog；不单独阻断发布 |

以下问题无论频率高低至少按 Blocker 处理：跨用户访问、原图出现在公开分享、账号或照片删除后仍可访问、未授权图片进入 Eval、日志记录 Access Token/微信 OpenID/签名 URL/图片内容。

每个 Bug 必须记录：ID、环境、版本 SHA、严重级别、复现步骤、预期/实际、Request ID、Trace ID、截图或日志、Owner、修复版本、回归证据。

## 四、核心验收用例

### 4.1 环境、身份与私有资产

| ID | 场景与步骤 | 通过标准 | 必需证据 |
|:---|:---|:---|:---|
| ACC-001 | 新用户微信授权登录；重复登录 | 首次建用户，重复登录复用身份；失效 Code 返回可理解错误 | 真机录像、API 状态码、Trace ID |
| ACC-002 | Token 缺失、过期、伪造后访问 `/me` | 均返回 401，不暴露内部原因 | Contract 测试和 Staging 请求 |
| ACC-003 | 用户 A 枚举用户 B 的 Asset/Diagnosis/Optimization ID | 私有资源始终不可读写，响应不泄露资源细节 | 双账号隔离报告 |
| AST-001 | JPEG、PNG、WebP 直传并 Complete | 文件进入 READY，尺寸和 MIME 与真实内容一致 | COS Object、DB 记录、Trace |
| AST-002 | 超 20 MiB、伪造 MIME、坏图、超大 Dimensions | 在上传或 Complete 阶段拒绝；不进入 AI 队列 | API 证据、孤儿对象检查 |
| AST-003 | Signed URL 正常访问、过期后访问、跨用户获取 | 有效期内可用；过期或越权后不可用 | 时间戳、HTTP 状态、Bucket 权限 |
| INF-001 | API、PostgreSQL、Redis、COS 分别故障 | `/health/live` 保持进程语义；`/health/ready` 及时 503 并摘流 | 故障注入记录、告警 |

### 4.2 诊断与 Minimal Change Optimization

| ID | 场景与步骤 | 通过标准 | 必需证据 |
|:---|:---|:---|:---|
| DIA-001 | 授权照片完成上传 → 选择场景 → 诊断 | 结果 Schema 完整、文案可理解；成功率 ≥95%，P95 <30 秒 | 50+ Eval 报告、调用成本 |
| DIA-002 | 相同 Idempotency-Key 弱网重放 | 只创建一个 Job、只预留一次额度、返回同一结果 | DB/Quota/Job 对账 |
| DIA-003 | Primary 超时或 5xx | 按策略 Fallback；最终失败有正确提示且释放额度 | Invocation、Job、Quota 证据 |
| DIA-004 | 遮挡、弱光、无人像或多人物 | 返回输入质量提示，不臆造确定性结论 | Bad Case Eval |
| OPT-001 | 对已完成诊断创建 Level 1～3 优化 | Change Budget 不越级；结果关联 Prompt/Model/Schema 版本 | DB、AI Invocation、结果 |
| OPT-002 | Critic 拒绝首张图片后重试 | 最多执行配置次数；失败不展示图片、不消耗额度 | 两次 Invocation 和最终状态 |
| OPT-003 | 身份、服装、背景与最小变化盲评 | Critic First-pass ≥75%；身份和衣物保持满足 Fidelity Gate | 50+ 样本双人盲评 |
| OPT-004 | 用户退出小程序后重新进入 | Pending Job 可恢复；完成、超时和最终失败状态一致 | 真机录像、Job 时间线 |

### 4.3 分享、投票和隐私删除

| ID | 场景与步骤 | 通过标准 | 必需证据 |
|:---|:---|:---|:---|
| GRW-001 | 生成分享卡并发给微信好友 | 好友可见 P90 ≤3 秒；卡片仅使用独立派生图并标注 AI 编辑 | 双真机录像、COS Key |
| GRW-002 | 未登录好友打开 SceneCode 并投票 | 不需要访问原始私有资产；重复投票不重复计数，改票正确 | Vote 并发测试、DB 对账 |
| GRW-003 | Share → Open → Vote → Continue | 事件可按 SceneCode 和 Source 归因且去重 | 漏斗查询结果 |
| GRW-004 | 好友落地页二次转发，并从朋友圈打开 | 好友/朋友圈链接可打开同一卡片；`WECHAT_FRIEND` / `WECHAT_TIMELINE` 归因准确 | 双真机录像、事件对账 |
| GOV-001 | 删除一张原图并等待完成 | 原图、诊断、优化、分享图及关系闭包从 COS/DB 清理；重复请求幂等 | DeletionJob、COS 清单、DB 查询 |
| GOV-002 | 删除账号并在处理中并发创建任务 | 新任务被阻断；稳定扫描后所有用户数据和对象不可访问 | 竞态测试、删除步骤、Trace |
| GOV-003 | COS 暂时失败后恢复 | DeletionJob 可重试且告警；不得先宣告完成 | 故障注入、重试和告警证据 |

### 4.4 可靠性、性能与可观测性

| ID | 场景与步骤 | 通过标准 | 必需证据 |
|:---|:---|:---|:---|
| REL-001 | 执行 Staging 冒烟脚本 | 上传、诊断、优化、分享、投票、清理全部成功 | 脚本 JSON、版本 SHA |
| REL-002 | API → Outbox → Worker → Provider | 同一 Trace 可跨服务查询；日志无敏感内容 | Trace 截图、脱敏抽查 |
| REL-003 | 停止 Worker 后继续创建任务，再恢复 Worker | Outbox 不丢事件；队列恢复后任务最终完成 | Pending Age、Job 对账 |
| REL-004 | API 并发、诊断并发、图片队列积压压测 | 达到 NFR 容量且 API/Job P95、错误率和资源水位达标 | 压测报告、Dashboard |
| REL-005 | 触发 5xx、Provider、Outbox、Deletion 告警 | 告警在约定窗口到达 On-call，包含环境和定位入口 | 告警通知与响应记录 |
| REL-006 | 从备份恢复到隔离环境 | RPO/RTO 达标，记录数和关键关系校验一致 | 恢复报告与校验 SQL |
| REL-007 | 新模型 Canary 后触发回滚阈值 | 新 Job 切回旧模型，在途 Job 按策略快照完成 | 模型版本分布、演练记录 |

### 4.5 微信小程序真机矩阵

至少覆盖一台低端安卓、一台主流安卓和一台当前主流 iPhone；记录机型、系统、微信版本、网络和构建 SHA。

| ID | 场景 | 通过标准 |
|:---|:---|:---|
| WX-001 | 首次登录、隐私同意、拍照/相册上传 | 权限拒绝可恢复，授权后可继续 |
| WX-002 | 上传中切后台、断网、恢复网络 | 不重复建 Asset，草稿和进度可恢复 |
| WX-003 | 诊断/优化等待时退出再进入 | Pending Job 恢复并落到正确结果页 |
| WX-004 | Before/After 滑动、长图和小屏 | 无明显卡顿、遮挡、错位或误触 |
| WX-005 | 分享给另一微信账号并投票 | SceneCode 正确，私有页面和 Token 不外泄 |
| WX-006 | 单图删除和账号删除 | 二次确认、进度、失败重试和完成反馈完整 |

## 五、自动化执行入口

每个候选版本必须保存完整命令输出、版本 SHA 和执行时间：

```bash
make lint
make typecheck
make test
make security-audit
make build
make local-api-baseline
cd backend && uv run alembic upgrade head --sql
```

真实 Staging 部署后必须从当前 `develop` 候选 SHA 手动运行受保护的
`Staging Infrastructure Audit` 工作流。执行前应先完成同一 SHA 的只读 Terraform
Plan 审批。该工作流只读远程 State 白名单合同和 Kubernetes 运行状态，校验跨区私有
TKE、无节点公网 IP、不可变镜像、数据依赖 Readiness、固定 CLB/DNS、TLS/HSTS 与
HTTP 307。只允许归档脱敏 `report.json`；原始资源 ID、VIP、域名、Kube Context、
证书 ID、连接信息和 Secret 不得进入 Artifact。工作流代码完成不能替代真实运行证据，
首次 `PASS` 报告和对应 GitHub Run URL 产生前，INF-02 保持 `IN_PROGRESS`。

Staging 使用授权照片执行完整 API 冒烟；Access Token 只能通过环境变量注入：

```bash
export AIW_SMOKE_ACCESS_TOKEN='<staging access token>'
make staging-smoke \
  STAGING_API_BASE_URL='https://staging.example.com' \
  SMOKE_IMAGE='/absolute/path/to/authorized-photo.jpg'
unset AIW_SMOKE_ACCESS_TOKEN
```

脚本默认在验收结束后发起单图删除并等待闭包清理。JSON 报告不得包含 Access Token、签名 URL 或照片内容。

Staging 双账号资源隔离、Signed URL 真实过期与删除后旧 URL 失效必须通过独立安全
审计；两个 Token、四个 Owner 资源 ID 和一个不同的一次性删除 Asset ID 均只经环境
变量注入，命令与显式不可逆删除确认见 `infra/operations/README.md`：

```bash
make staging-security-audit
```

该命令会等待第一条 Signed URL 的服务器声明有效期结束，并删除第二个专用 Asset；
不得通过缩短本地时钟、复用同一 Asset 或 Mock 结果绕过。删除证据必须在旧 URL 仍有
有效期时证明 API 与对象均不可访问。报告不得包含 Token、资源 UUID、幂等键或签名 URL。

Staging Worker 停机与队列恢复使用独立演练入口。它会真实暂停 `ai_fast` Worker 并
产生 Provider 成本，只能在 Dashboard、On-call 和授权专用账号就绪的窗口执行；完整
变量和数据集保护要求见 `infra/performance/README.md`：

```bash
make staging-queue-recovery
```

脚本必须验证 Kubernetes Context、ConfigMap/API 的 Staging 身份和不可变镜像 SHA，
并通过 EXIT/信号陷阱恢复原副本数。脱敏报告需证明停机期间任务未执行、幂等重放未
重复建 Job、恢复后全部完成，并记录 Queue Drain Time；不得包含 Token、用户或资源 ID。

真实 AI 容量按 10→30→50 专用账号逐级执行；前一阶段未通过或 Dashboard、成本、
告警路由未确认时不得扩级。变量和私有数据集要求见 `infra/performance/README.md`：

```bash
make staging-ai-capacity
```

该入口必须固定 k6 镜像摘要并拒绝非 Staging 环境、错误 Context/镜像、非 `0600`
数据集和未就绪副本。每级报告须满足 Diagnosis 成功率 ≥95%、关联头 100%、
HTTP 失败率 <2%、P90 <20 秒、P95 <30 秒，且不得记录任何身份或资产数据。

Staging 告警送达使用两阶段人工确认入口。Webhook 与 Alertmanager Bearer 只允许从
Secret/环境变量注入；Critical/Warning 通知中的一次性 Ack Token 不得进入命令参数、
日志或报告。完整部署和操作要求见 `infra/operations/README.md`：

```bash
make staging-alert-drill ALERT_DRILL_ARGS='start --state /secure/alert-drill/state.json'
# On-call 分别从两条通知取得 Token 后执行 ack
make staging-alert-drill \
  ALERT_DRILL_ARGS='finish --state /secure/alert-drill/state.json --report /secure/alert-drill/report.json'
```

只有最终 `0600` 报告为 `PASSED` 且能关联真实通知/轮值记录时，REL-005 才能通过；
Mock、本地注入或仅验证配置不得替代人员送达证据。

## 六、发布 Gate

| Gate | `GO` 标准 | 当前状态（2026-07-28） |
|:---|:---|:---|
| Scope | MVP 范围冻结，非目标未进入版本 | PASS |
| Build | Lint、Type Check、Unit/Contract、微信构建、Migration 检查通过 | PASS（本地证据，需候选 SHA 重跑） |
| Supply Chain | Python 无已知漏洞；小程序无未批准 High/Critical；例外有 Owner 和到期日 | PASS（8 个 High 已修复；1 个 Windows Vite 开发服务器例外登记至 2026-08-09） |
| Security/Privacy | 双账号隔离、私有 URL、删除闭包和日志脱敏通过 | BLOCKED：42 个实库用例覆盖隔离与删除闭包，Staging 审计已强制 Signed URL 过期及删除后旧 URL/API 失效；仍缺真实 COS 执行与授权 Prompt Injection Eval |
| AI Quality | 50+ 授权样本达到诊断、Fidelity、延迟和成本阈值 | BLOCKED：Bundle/Runner 已在 Provider 调用前强制样本分布、授权/私有引用，以及全 Validation 双人盲评、版本绑定、分歧仲裁和人工标签一致性；仍缺真实授权数据、评审记录、基线与真实 Provider 证据 |
| Staging E2E | 微信登录、COS、全部 Worker、分享投票和冒烟通过 | BLOCKED：缺 Staging 与凭据 |
| Reliability | Outbox 恢复、告警路由、备份恢复、Canary/回滚演练通过 | IN_PROGRESS：本地依赖告警与空库恢复通过，Staging 告警 Secret 路由和双 Ack 演练入口已失败关闭；真实 On-call 送达、含数据恢复及 Staging 演练未完成 |
| Performance | 核心容量、P95、队列积压和低端安卓达标 | IN_PROGRESS：本地 5→20 req/s API 基线为 100% 成功、0% HTTP 失败、P95 14.99 ms；Staging AI/COS、队列恢复、资源水位与低端安卓未验收 |
| Operations | Dashboard、On-call、Runbook、反馈入口和状态沟通就位 | NOT_RUN |
| Defects | Blocker=0、Critical=0；Major 均有 Owner 和截止日 | PENDING |
| Sign-off | 产品、QA、技术、AI、DevOps 完成签署 | PENDING |

当前结论：`NO-GO`。原因是环境、真实 AI 质量、隐私删除、恢复、性能和真机 Gate 尚未获得执行证据；这不是对代码质量的否定，也不得改写为 `CONDITIONAL GO`。

## 七、Go/No-Go 会议流程

1. DevOps 冻结候选 SHA，确认迁移、镜像 provenance/SBOM 和部署记录。
2. QA 展示自动回归、Staging E2E、真机矩阵、性能、安全和隐私证据。
3. AI 负责人展示 Golden Dataset、盲评、Critic、延迟、成本和 Canary 结论。
4. On-call 展示 Dashboard、告警路由、备份恢复、回滚和应急联系人。
5. 产品逐项确认已知问题不会破坏核心假设、用户安全或数据权利。
6. 五个角色分别记录 `GO`、`CONDITIONAL GO` 或 `NO-GO`；任何 Blocker/Critical 或硬 Gate 失败均直接 `NO-GO`。

`CONDITIONAL GO` 只允许未影响安全、隐私、数据一致性和核心链路的 Major/Minor 问题，并必须写明 Owner、截止日、监控方式和回退条件。

## 八、发布与回滚检查

发布前：

- [ ] 候选 SHA 已冻结，CI 全绿，镜像使用不可变 SHA Tag。
- [ ] Secret 来自环境保护和平台密钥管理，仓库及日志中不存在明文。
- [ ] 生产依赖漏洞门禁通过；所有安全例外均未过期且影响边界未变化。
- [ ] Forward-only Migration Job 成功，应用兼容已迁移 Schema。
- [ ] Staging 完整冒烟和三类真机通过。
- [ ] Dashboard 正常，关键告警已真实送达 On-call。
- [ ] 备份恢复和应用/模型回滚演练通过。
- [ ] 版本说明、已知问题、隐私说明、反馈入口和值班表已发布。

发布后：

- [ ] 观察 30 分钟 API 5xx、P95、DB/Redis、Outbox、Queue 和 Provider。
- [ ] 观察首批 Diagnosis/Optimization 成功率、Critic、成本和删除任务。
- [ ] 分批邀请测试用户，不得一次性开放全部名额。
- [ ] 指标越过阈值或出现 Blocker/Critical 时暂停邀请并执行回滚。

应用回滚不回退数据库。迁移必须遵守 expand/migrate/contract；模型回滚只影响新 Job，在途 Job 按创建时的策略快照完成。

## 九、签署记录模板

| 字段 | 内容 |
|:---|:---|
| 候选版本 / Commit SHA | 待填写 |
| Staging 部署记录 | 待填写 |
| 测试报告与证据目录 | 待填写 |
| 未决 Major/Minor | 待填写 |
| 产品负责人结论 / 时间 | 待签署 |
| QA 负责人结论 / 时间 | 待签署 |
| 技术负责人结论 / 时间 | 待签署 |
| AI 负责人结论 / 时间 | 待签署 |
| DevOps/On-call 结论 / 时间 | 待签署 |
| 最终决策 | `NO-GO`（待所有硬 Gate 通过后复审） |
