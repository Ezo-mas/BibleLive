# BibleLive Tests

This directory contains the backend test suite for BibleLive
(`backend/tests/`, run from the repository root as `pytest` or `make test`).

## Test Organization

```
backend/tests/
├── test_verse_parser.py            # Parser unit tests
├── test_verse_parser_lt.py         # Lithuanian parsing
├── test_api_validation.py          # HTTP schema/validation tests
├── test_authentication.py          # Operator token auth
├── test_websocket_approval.py      # WebSocket approval flows
├── test_reference_id_protocol.py   # reference_id protocol
├── test_reference_sender.py        # Audio reference delivery (dedup/retry)
├── test_bible_integration.py      # Imported-database integration tests
├── test_working_references.py      # Reference validation against a real DB
└── conftest.py                     # Shared pytest fixtures
```

## Running Tests

### All tests
```bash
pytest
```

### By category
```bash
pytest backend/tests -q         # All backend tests
pytest -m unit                  # By marker
pytest -m "not audio and not integration"
```

### With coverage
```bash
pytest --cov=core --cov=audio --cov-report=html   # config lives in root pyproject.toml
```

### Specific test file
```bash
pytest backend/tests/test_verse_parser.py -v
```

## Test Markers

- `@pytest.mark.unit` - Fast, isolated tests
- `@pytest.mark.integration` - Requires database or external resources
- `@pytest.mark.audio` - Requires audio dependencies
- `@pytest.mark.websocket` - Requires running server or mocked WebSocket

## Writing Tests

### Unit Test Example

```python
import pytest
from core.verse_parser import ReferenceParser


@pytest.mark.unit
def test_parse_simple_reference():
    parser = ReferenceParser()
    refs = parser.parse("John 3:16")
    assert len(refs) == 1
    assert refs[0].book == "John"
    assert refs[0].chapter == 3
```

### Integration Test Example

```python
import pytest
from core.bible_lookup import BibleLookup


@pytest.mark.integration
def test_bible_lookup(tmp_path):
    db_path = tmp_path / "test.sqlite"
    lookup = BibleLookup(str(db_path))
    # Test with actual database operations
```

### WebSocket Test Example

```python
import pytest
from fastapi.testclient import TestClient


@pytest.mark.websocket
def test_display_connection(test_client):
    with test_client.websocket_connect("/ws/display") as ws:
        data = ws.receive_json()
        assert data["type"] == "state_snapshot"
```

## Test Fixtures

Common fixtures are defined in `conftest.py`:

- `tmp_db_path` - Temporary database path
- `test_client` - FastAPI TestClient instance
- `sample_bible_data` - Minimal Bible data for testing

## Isolation

Tests should:
- Use temporary databases (via `tmp_path` or `tmp_db_path`)
- Not modify files in the repository
- Not depend on external services (mock when needed)
- Clean up resources in teardown
- Be runnable in any order

## Demo Scripts

Demo and simulation scripts live in `backend/scripts/`:
- These are not pytest tests
- Used for manual testing and demonstration
- May require a running server or audio hardware
