.PHONY: install backend-install frontend-install dev-api dev-miniapp worker beat test lint typecheck structure-check docs-check security-audit build infra-up infra-observability-up infra-down migrate local-db-drill local-alert-drill local-api-baseline event-funnel-audit staging-smoke

install: backend-install frontend-install

backend-install:
	./scripts/setup.sh backend

frontend-install:
	./scripts/setup.sh frontend

dev-api:
	./scripts/dev.sh api

dev-miniapp:
	./scripts/dev.sh miniapp

worker:
	./scripts/dev.sh worker

beat:
	./scripts/dev.sh beat

test:
	./scripts/quality.sh test

lint:
	./scripts/quality.sh lint

typecheck:
	./scripts/quality.sh typecheck

structure-check:
	./scripts/quality.sh structure

docs-check:
	./scripts/quality.sh docs

security-audit:
	./scripts/quality.sh security-audit

build:
	./scripts/quality.sh build

infra-up:
	./scripts/infra.sh up

infra-observability-up:
	./scripts/infra.sh observability-up

infra-down:
	./scripts/infra.sh down

migrate:
	./scripts/infra.sh migrate

local-db-drill:
	./scripts/local-db-drill.sh

local-alert-drill:
	./scripts/ops/local-alert-drill.sh

local-api-baseline:
	./scripts/performance/local-api-baseline.sh

event-funnel-audit:
	cd backend && uv run python scripts/event_funnel_audit.py $(EVENT_AUDIT_ARGS)

staging-smoke:
	STAGING_API_BASE_URL="$(STAGING_API_BASE_URL)" SMOKE_IMAGE="$(SMOKE_IMAGE)" ./scripts/staging-smoke.sh
