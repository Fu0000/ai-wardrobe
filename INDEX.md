# AI Wardrobe Workspace Index

## 必读入口

- 项目协作与架构约束：`AGENTS.md`
- 项目说明与本地启动：`README.md`
- 核心研发文档总目录：`docs/00_文档总目录_V1.1.md`
- MVP 实现进度：`docs/13_MVP实现方案与任务进度明细_V1.0.md`
- MVP 验收发布：`docs/16_MVP验收与发布清单_V1.0.md`
- MVP 封闭测试运行：`docs/17_MVP封闭测试运行手册_V1.0.md`

## 当前架构状态

- 已批准架构与风险：`docs/08_风险清单与ADR_V1.1.md`
- 当前技术方案：`docs/02_技术方案设计_TDD_V1.1.md`
- Node 后端迁移评估：`docs/20_后端Node技术栈迁移评估与任务清单_V1.0.md`
  - 状态：`PROPOSED / NOT APPROVED`
  - 注意：该文件是候选评估，不能覆盖当前 FastAPI/Celery 架构；实施前必须新增并批准正式 ADR。

## 代码与运行资产

- Python 后端：`backend/`
- 微信小程序：`miniapp/`
- 基础设施与部署：`infra/`
- 工程脚本：`scripts/`
- GitHub Actions：`.github/workflows/`

## 按需查阅

- 产品：`docs/01_产品需求文档_PRD_V1.1.md`
- 数据：`docs/03_数据架构与数据库设计_DDD_V1.1.md`
- API：`docs/04_API与外部集成方案_V1.1.md`
- 测试发布运维：`docs/07_测试发布与运维方案_V1.1.md`
- NFR/SLO：`docs/11_非功能性需求NFR与SLO_V1.0.md`
- AI 质量：`docs/12_AI评估与质量规范_V1.0.md`
- 会话上下文：`memory/`（按日期记录，只加载最近两天）
