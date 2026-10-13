"""How `python -m core.server` hands the app to uvicorn."""

import pytest

import core.server.app as server_app
from core.config import config


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
