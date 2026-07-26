.PHONY: install backend-install frontend-install dev-api dev-miniapp worker beat test lint typecheck security-audit build infra-up infra-observability-up infra-down migrate staging-smoke

install: backend-install frontend-install

backend-install:
	cd backend && uv sync --all-groups

frontend-install:
	pnpm install --frozen-lockfile

dev-api:
	cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-miniapp:
	pnpm dev:miniapp

worker:
	cd backend && uv run celery -A app.worker.celery_app:celery_app worker -Q ai_fast,image_generation,media_generation,maintenance --loglevel=INFO

beat:
	cd backend && uv run celery -A app.worker.celery_app:celery_app beat --loglevel=INFO

test:
	cd backend && uv run pytest
	pnpm test

lint:
	cd backend && uv run ruff check .
	cd backend && uv run ruff format --check .
	pnpm lint

typecheck:
	cd backend && uv run mypy app tests scripts
	pnpm typecheck

security-audit:
	cd backend && uv export --frozen --no-dev --no-emit-project --format requirements-txt --quiet > /tmp/aiw-backend-requirements.txt
	cd backend && uv run pip-audit --requirement /tmp/aiw-backend-requirements.txt --disable-pip
	pnpm security:audit

build:
	pnpm build

infra-up:
	docker compose --env-file .env -f infra/compose.yaml up -d

infra-observability-up:
	docker compose --env-file .env -f infra/compose.yaml --profile observability up -d

infra-down:
	docker compose --env-file .env -f infra/compose.yaml down

migrate:
	cd backend && uv run alembic upgrade head

staging-smoke:
	test -n "$(STAGING_API_BASE_URL)"
	test -n "$(SMOKE_IMAGE)"
	test -n "$$AIW_SMOKE_ACCESS_TOKEN"
	cd backend && uv run python scripts/smoke_mvp.py --base-url "$(STAGING_API_BASE_URL)" --image "$(SMOKE_IMAGE)"
