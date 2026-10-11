# Runs on Windows (GNU make from ezwinports + PowerShell/cmd) and Linux: plain uv/npm recipes only.
BACKEND := uv --directory backend
FRONTEND := $(wildcard frontend/package.json)

.PHONY: setup dev test test-live lint check frontend-check fixtures seed

setup:
	$(BACKEND) sync
ifneq ($(FRONTEND),)
	npm --prefix frontend ci
endif

dev:
	$(BACKEND) run python scripts/dev.py

test:
	$(BACKEND) run pytest

test-live:
	$(BACKEND) run pytest -m live

lint:
	$(BACKEND) run ruff check .

frontend-check:
ifneq ($(FRONTEND),)
	npm --prefix frontend run typecheck
	npm --prefix frontend run build
else
	@echo "frontend/ not created yet (phase F1): skipping typecheck and build"
endif

check: lint test frontend-check

fixtures:
	$(BACKEND) run python -m scripts.generate_fixtures

seed:
	$(BACKEND) run python -m app.seed
