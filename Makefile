.PHONY: help install install-audio install-dev test test-unit test-integration lint format typecheck clean dev build

help:
	@echo "BibleLive Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install          Install core dependencies"
	@echo "  make install-audio    Install core + audio dependencies"
	@echo "  make install-dev      Install all dependencies including dev tools"
	@echo ""
	@echo "Quality:"
	@echo "  make lint             Run Ruff linter"
	@echo "  make format           Format code with Ruff"
	@echo "  make typecheck        Run mypy type checker"
	@echo ""
	@echo "Testing:"
	@echo "  make test             Run all tests"
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
	pip install -e .

install-audio:
	pip install -e ".[audio]"

install-dev:
	pip install -e ".[dev,audio]"

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy core/ audio/ --install-types --non-interactive

test:
	pytest tests/ -v

test-unit:
	pytest tests/ -v -m "unit"

test-integration:
	pytest tests/ -v -m "integration"

test-cov:
	pytest tests/ --cov=core --cov=audio --cov-report=html --cov-report=term

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist htmlcov .coverage coverage.xml

dev:
	BIBLELIVE_RELOAD=true uvicorn core.biblelive_server:app --reload --host 0.0.0.0 --port 8000

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
