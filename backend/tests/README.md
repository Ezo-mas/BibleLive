# BibleLive Tests

This directory contains the backend test suite for BibleLive
(`backend/tests/`, run from the repository root as `pytest` or `make test`).

## Test Organization

```
backend/tests/
├── test_verse_parser.py            # Parser (English) tests
├── test_verse_parser_lt.py         # Lithuanian parsing
├── test_api_validation.py          # HTTP schema/validation tests
├── test_authentication.py          # Operator token auth
├── test_websocket_approval.py      # WebSocket + HTTP approval flows
├── test_reference_id_protocol.py   # reference_id protocol
├── test_reference_sender.py        # Audio reference delivery (dedup/retry)
├── test_audio_logging.py           # Audio logging + capture accounting
├── test_bible_integration.py       # Imported-database integration tests
├── test_import_flat_format.py      # Flat e-Sword JSON import
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
pytest -m integration           # Database/server tests only
pytest -m "not audio"           # Skip tests needing the audio extras
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

- `@pytest.mark.integration` - Builds a database or exercises a server
- `@pytest.mark.audio` - Requires the optional audio extras (`pip install -e "./backend[audio]"`)

Unmarked tests are fast, isolated unit tests.

## Writing Tests

### Unit Test Example

```python
from core.verse_parser import ReferenceParser


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
def test_bible_lookup(bible_db):
    lookup = BibleLookup(str(bible_db))
    assert lookup.is_loaded()
```

## Test Fixtures

Common fixtures are defined in `conftest.py`:

- `sample_bible_data` - Minimal in-memory Bible data
- `bible_db` - Temporary SQLite database built from `sample_bible_data`
- `kjv_db` - Full KJV database built once per session from `data/kjv2.json`
- `app` / `app_state` - FastAPI app (session-scoped) backed by `kjv_db`
- `client` - FastAPI TestClient bound to the shared app

The app fixtures build their own database, so tests do not depend on a
pre-imported `bible_data/biblelive.sqlite` (which is gitignored).

## Isolation

Tests should:
- Use temporary databases (via `tmp_path` or the `bible_db`/`kjv_db` fixtures)
- Not modify files in the repository
- Not depend on external services (mock when needed)
- Clean up resources in teardown
- Be runnable in any order

## Demo Scripts

Demo and simulation scripts live in `backend/scripts/`:
- These are not pytest tests
- Used for manual testing and demonstration
- May require a running server or audio hardware
