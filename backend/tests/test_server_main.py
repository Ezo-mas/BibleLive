"""How `python -m core.server` hands the app to uvicorn."""

import logging

import pytest
from uvicorn_logging_state import LOGGER_NAMES, restored_uvicorn_logging

import core.server.app as server_app
from core.config import config

# main() builds a uvicorn.Config, which applies uvicorn's logging setup.
pytestmark = pytest.mark.usefixtures("uvicorn_logging")


@pytest.fixture
def reload_setting():
    original = config.RELOAD
    yield
    config.RELOAD = original


def test_reload_runs_the_app_factory_by_import_string(monkeypatch, reload_setting):
    """uvicorn ignores reload for an app object; it needs the import string."""
    config.RELOAD = True
    calls = []
    monkeypatch.setattr(server_app.uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(server_app, "create_app", lambda: pytest.fail("built in the parent"))

    server_app.main()

    [(args, kwargs)] = calls
    assert args == ("core.server.app:create_app",)
    assert kwargs["factory"] is True
    assert kwargs["reload"] is True
    # Only the backend is watched, by absolute path, wherever the server starts.
    assert kwargs["reload_dirs"] == [str(config.backend_root)]
    assert (config.backend_root / "core" / "server" / "app.py").is_file()


def test_without_reload_the_app_is_served_directly(monkeypatch, reload_setting):
    config.RELOAD = False
    served = []

    class _Server:
        def __init__(self, uvicorn_config):
            served.append(uvicorn_config)

        async def serve(self):
            return None

    app = object()
    monkeypatch.setattr(server_app, "create_app", lambda: app)
    monkeypatch.setattr(server_app.uvicorn, "Server", _Server)
    monkeypatch.setattr(server_app.uvicorn, "run", lambda *a, **kw: pytest.fail("reload path"))

    server_app.main()

    [uvicorn_config] = served
    assert uvicorn_config.app is app
    assert uvicorn_config.reload is False


def _uvicorn_logger_state():
    return {
        name: (logger.handlers[:], logger.filters[:], logger.level, logger.propagate)
        for name in LOGGER_NAMES
        for logger in [logging.getLogger(name)]
    }


def test_uvicorn_logging_set_up_by_main_is_restored(monkeypatch, reload_setting):
    """main() leaves uvicorn's loggers not propagating; later tests must not inherit that.

    pytest attaches its capture handlers to every logger that does not propagate, so a
    later test that turns propagation back on for ``uvicorn.access`` captured each
    access record twice (seen with the suite in reverse order).
    """
    config.RELOAD = False

    class _Server:
        def __init__(self, uvicorn_config):
            pass

        async def serve(self):
            return None

    monkeypatch.setattr(server_app, "create_app", object)
    monkeypatch.setattr(server_app.uvicorn, "Server", _Server)
    before = _uvicorn_logger_state()

    with restored_uvicorn_logging():
        server_app.main()
        # The leak this guards against: uvicorn's dictConfig really ran.
        assert logging.getLogger("uvicorn.access").propagate is False

    assert _uvicorn_logger_state() == before
