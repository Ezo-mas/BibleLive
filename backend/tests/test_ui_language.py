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


def test_ui_language_needs_no_token_with_auth_on(client, monkeypatch, auth_enabled):
    # The display page (no token, projector browser) reads the language on load.
    monkeypatch.setattr(config, "UI_LANGUAGE", "lt")
    wrong = {"Authorization": "Bearer not-the-token"}

    assert client.get("/api/ui-language").json() == {"language": "lt"}
    assert client.get("/api/ui-language", headers=wrong).json() == {"language": "lt"}
    assert client.get("/api/ui-language", headers=auth_enabled).json() == {"language": "lt"}


def test_bible_version_still_requires_auth_with_auth_on(client, monkeypatch, auth_enabled):
    monkeypatch.setattr(config, "UI_LANGUAGE", "lt")
    wrong = {"Authorization": "Bearer not-the-token"}

    assert client.get("/api/bible-version").status_code == 401
    assert client.get("/api/bible-version", headers=wrong).status_code == 401
    assert client.get("/api/bible-version", headers=auth_enabled).status_code == 200


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
