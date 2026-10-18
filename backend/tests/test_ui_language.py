"""The operator page's interface language: ``GET /api/ui-language`` and its config."""

import pytest

from core.config import Config, config


@pytest.fixture
def auth_enabled(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_AUTH", True)
    monkeypatch.setattr(config, "OPERATOR_TOKEN", "ui-language-test-token")
    return {"Authorization": "Bearer ui-language-test-token"}


def test_ui_language_is_null_when_the_launcher_did_not_set_it(client, monkeypatch):
    monkeypatch.setattr(config, "UI_LANGUAGE", None)

    response = client.get("/api/ui-language")

    assert response.status_code == 200
    assert response.json() == {"language": None}


@pytest.mark.parametrize("language", ["lt", "en"])
def test_ui_language_is_what_the_config_says(client, monkeypatch, language):
    monkeypatch.setattr(config, "UI_LANGUAGE", language)

    response = client.get("/api/ui-language")

    assert response.status_code == 200
    assert response.json() == {"language": language}


def test_ui_language_requires_auth_like_bible_version(client, monkeypatch, auth_enabled):
    monkeypatch.setattr(config, "UI_LANGUAGE", "lt")
    wrong = {"Authorization": "Bearer not-the-token"}

    for path in ("/api/ui-language", "/api/bible-version"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers=wrong).status_code == 401, path
        assert client.get(path, headers=auth_enabled).status_code == 200, path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("lt", "lt"),
        ("LT", "lt"),
        (" En ", "en"),
        ("fr", None),
        ("auto", None),
        ("", None),
    ],
)
def test_config_normalises_the_ui_language(monkeypatch, raw, expected):
    monkeypatch.delenv("BIBLELIVE_UI_LANGUAGE", raising=False)

    assert expected == Config(UI_LANGUAGE=raw).UI_LANGUAGE


def test_config_reads_the_launcher_environment_variable(monkeypatch):
    monkeypatch.setenv("BIBLELIVE_UI_LANGUAGE", "lt")
    assert Config().UI_LANGUAGE == "lt"

    monkeypatch.setenv("BIBLELIVE_UI_LANGUAGE", "auto")
    assert Config().UI_LANGUAGE is None
