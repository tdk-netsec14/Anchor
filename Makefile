# Anchor developer tasks.
#
# Windows users without `make` can run the commands directly — every target
# below is a one-liner.

PY      ?= .venv/Scripts/python.exe
PIP     ?= $(PY) -m pip
PYTEST  ?= $(PY) -m pytest
RUFF    ?= $(PY) -m ruff

.DEFAULT_GOAL := help
.PHONY: help setup venv install test unit integration security lint format \
        up down logs build ingest eval eval-pytest token health smoke e2e clean nuke

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	 awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Create the local virtualenv (Python 3.13)
	py -3.13 -m venv .venv || python3 -m venv .venv

install: ## Install runtime + dev dependencies
	$(PIP) install -r requirements-dev.txt

setup: venv install ## Bootstrap a machine from scratch (venv + deps + .env)
	@cp -n .env.example .env || (echo ".env already exists - leaving it alone")
	@echo "Anchor ready. Set JWT_SECRET in .env before starting."

test: ## Run the full test suite (tests/ and the evaluation graders)
	$(PYTEST) tests eval

unit: ## Run unit tests only
	$(PYTEST) tests/unit

integration: ## Run integration tests only
	$(PYTEST) tests/integration -m integration

security: ## Run adversarial / security tests only
	$(PYTEST) tests/security

lint: ## Static checks (ruff)
	$(RUFF) check .
	$(RUFF) format --check .

format: ## Auto-format
	$(RUFF) check --fix .
	$(RUFF) format .

up: ## Build and start the whole system
	docker compose up --build -d
	@echo "Agent API: http://localhost:8000  (docs at /docs)"

down: ## Stop the system (keeps volumes)
	docker compose down

logs: ## Tail service logs
	docker compose logs -f agent

build: ## Rebuild images without starting
	docker compose build

ingest: ## Batch-ingest everything in data/documents
	docker compose --profile batch run --rm ingestion

token: ## Mint a dev JWT (usage: make token ROLE=admin USER=alice)
	docker compose exec agent python -m agent.auth \
		$(or $(USER),demo) $(or $(ROLE),user)

health: ## Curl the local health endpoint
	curl -fsS http://localhost:8000/health

smoke: ## Check auth, RBAC and ingestion against a running API
	$(PY) scripts/smoke_http.py

e2e: ## Run the end-to-end scenario suite against a running API
	$(PY) scripts/e2e_audit.py

eval: ## Run the evaluation harness against the local API
	$(PY) eval/run_eval.py --base-url http://localhost:8000

eval-pytest: ## Run the evaluation suite as pytest regressions
	$(PYTEST) eval -m "not slow"

clean: ## Remove caches and test scratch dirs
	rm -rf .pytest_cache .ruff_cache .pytest_chroma .pytest_tickets
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

nuke: ## Remove containers AND data volumes
	docker compose down -v
