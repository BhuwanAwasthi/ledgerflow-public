# Runs on Windows (GNU make from ezwinports + PowerShell/cmd) and Linux: plain uv/npm recipes only.
BACKEND := uv --directory backend
FRONTEND := $(wildcard frontend/package.json)

.PHONY: setup dev test test-live lint check frontend-check fixtures seed smoke-deploy

setup:
	$(BACKEND) sync
ifneq ($(FRONTEND),)
	npm --prefix frontend ci
endif

dev:
	$(BACKEND) run python scripts/dev.py

test:
	$(BACKEND) run pytest

test-live:  # make test-live ARGS="-k extraction -s"
	$(BACKEND) run pytest -m live $(ARGS)

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

seed:  # against Supabase (PowerShell): $env:DATABASE_URL="<session pooler URI>"; make seed
	$(BACKEND) run python -m app.seed

smoke-deploy:  # make smoke-deploy URL=https://ledgerflow-api.onrender.com
	$(BACKEND) run python -m scripts.smoke_deploy $(URL) $(ARGS)
