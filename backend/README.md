# AI Wardrobe Backend

FastAPI Modular Monolith，负责同步 API、业务事务、AI Job 创建、Transactional Outbox 和 Worker 共享领域逻辑。

```bash
uv sync --all-groups
uv run uvicorn app.main:app --reload
uv run pytest
uv run ruff check .
uv run mypy app
```
