.PHONY: help install install-audio install-dev test test-unit test-integration test-cov lint format typecheck clean dev frontend-install frontend-dev frontend-build frontend-lint frontend-format frontend-test check check-frontend

BACKEND := backend
export PYTHONPATH := $(CURDIR)/$(BACKEND):$(PYTHONPATH)

help:
	@echo "BibleLive Development Commands (monorepo: backend/ + frontend/)"
	@echo ""
	@echo "Setup:"
	@echo "  make install          Install backend core dependencies"
	@echo "  make install-audio    Install backend + audio dependencies"
	@echo "  make install-dev      Install all backend deps including dev tools"
	@echo "  make frontend-install Install frontend npm dependencies"
	@echo ""
	@echo "Quality:"
	@echo "  make lint             Run Ruff linter (backend)"
	@echo "  make format           Format code with Ruff (backend)"
	@echo "  make typecheck        Run mypy type checker (backend)"
	@echo ""
	@echo "Testing:"
	@echo "  make test             Run all backend tests"
	@echo "  make test-unit        Run unit tests only"
	@echo "  make test-integration Run integration tests only"
	@echo "  make test-cov         Run tests with coverage report"
	@echo ""
	@echo "Development:"
	@echo "  make dev              Start development server (with reload)"
	@echo "  make frontend-dev     Start frontend dev server"
	@echo "  make frontend-build   Build frontend for production"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean            Remove build artifacts and caches"

install:
	pip install -e ./$(BACKEND)

install-audio:
	pip install -e "./$(BACKEND)[audio]"

install-dev:
	pip install -e "./$(BACKEND)[dev,audio]"

lint:
	ruff check $(BACKEND)

format:
	ruff format $(BACKEND)

typecheck:
	mypy $(BACKEND)/core $(BACKEND)/audio --install-types --non-interactive

test:
	pytest $(BACKEND)/tests -v

test-unit:
	pytest $(BACKEND)/tests -v -m "unit"

test-integration:
	pytest $(BACKEND)/tests -v -m "integration"

test-cov:
	pytest $(BACKEND)/tests --cov=core --cov=audio --cov-report=html --cov-report=term

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-lint:
	cd frontend && npm run lint

frontend-format:
	cd frontend && npm run format

frontend-test:
	cd frontend && npm test

check: lint test
	ruff format --check $(BACKEND)

check-frontend: frontend-lint frontend-test

clean:
	find . -type d -name "__pycache__" -not -path "./frontend/*" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist htmlcov .coverage coverage.xml

dev:
	BIBLELIVE_RELOAD=true uvicorn --factory core.server.app:create_app --reload --host 0.0.0.0 --port 8000
