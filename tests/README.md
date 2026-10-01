# BibleLive Tests

This directory contains the test suite for BibleLive.

## Test Organization

```
tests/
├── unit/              # Fast, isolated unit tests
│   ├── test_verse_parser.py
│   └── test_bible_lookup.py
├── integration/       # Tests requiring database/external resources
│   ├── test_api_validation.py
│   └── test_bible_integration.py
├── websocket/         # WebSocket-specific tests
│   ├── test_websocket_approval.py
│   └── test_reference_id_protocol.py
└── conftest.py        # Shared pytest fixtures
```

## Running Tests

### All tests
```bash
pytest
```

### By category
```bash
pytest tests/unit/              # Unit tests only
pytest tests/integration/       # Integration tests
pytest tests/websocket/         # WebSocket tests
pytest -m unit                  # By marker
```

### With coverage
```bash
pytest --cov=core --cov=audio --cov-report=html
```

### Specific test file
```bash
pytest tests/unit/test_verse_parser.py -v
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
from verse_parser import ReferenceParser


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
from bible_lookup import BibleLookup


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

Demo and simulation scripts have been moved to `/scripts/`:
- These are not pytest tests
- Used for manual testing and demonstration
- May require a running server or audio hardware
