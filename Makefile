.DEFAULT_GOAL := help
.PHONY: help preflight up bench report down teardown-check lint test

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

preflight: ## Read-only checks: tools, auth, quota, region, permissions, model, hourly cost
	@uv run python infra/preflight.py

up: ## PENDING (M1): create the cluster and deploy the stack
	@echo "PENDING: 'up' arrives in M1. Nothing was created."; exit 1

bench: ## PENDING (M4): run the benchmark matrix
	@echo "PENDING: 'bench' arrives in M4. Nothing was run."; exit 1

report: ## PENDING (M6): build charts and tables from results/
	@echo "PENDING: 'report' arrives in M6. Nothing was generated."; exit 1

down: ## PENDING (M1): destroy all billable resources
	@echo "PENDING: 'down' arrives in M1. Nothing was destroyed."; exit 1

teardown-check: ## PENDING (M1): confirm no billable resources remain
	@echo "PENDING: 'teardown-check' arrives in M1. Nothing was checked."; exit 1

lint: ## Run ruff lint and format checks
	@uv run ruff check .
	@uv run ruff format --check .

test: ## Run unit tests
	@uv run pytest -q
