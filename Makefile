# Common commands. Run `make help` for a list.
.DEFAULT_GOAL := help

PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin

.PHONY: help install install-dev migrate migrate-down run test test-unit \
        test-integration lint fmt up down db-up test-docker clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install runtime dependencies
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements.txt

install-dev: ## Create venv and install runtime + test/lint dependencies
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements-dev.txt

migrate: ## Apply database migrations
	$(BIN)/alembic upgrade head

migrate-down: ## Roll back the last migration
	$(BIN)/alembic downgrade -1

run: ## Start the API with auto-reload (needs Postgres + `make migrate`)
	$(BIN)/uvicorn app.main:app --reload --port 8000

test: ## Run the whole test suite (needs Postgres, see README)
	$(BIN)/pytest

test-unit: ## Run unit tests only (no database required)
	$(BIN)/pytest tests/unit

test-integration: ## Run integration tests only (needs Postgres)
	$(BIN)/pytest tests/integration

lint: ## Lint with ruff
	$(BIN)/ruff check .

fmt: ## Auto-fix lint issues and format code
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format app tests

up: ## Build and start app + Postgres in the background
	docker compose up --build -d

down: ## Stop and remove the compose stack
	docker compose down

db-up: ## Start only Postgres (for running the app/tests on the host)
	docker compose up -d db

test-docker: ## Run the test suite inside Docker against the compose DB
	docker compose --profile test run --rm test

clean: ## Remove caches and bytecode
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
