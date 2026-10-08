"""Tests for the runtime language-control HTTP server (``audio.control``)."""

import json
import urllib.error
import urllib.request

import pytest

from audio.control import LanguageControlServer

# Requires the optional audio extras (importing audio.control loads audio.stt).
pytestmark = pytest.mark.audio


class _FakeLanguage:
    """Language holder that mirrors SpeechToText's validation."""

    def __init__(self, language: str = "en"):
        self.language = language

    def get(self) -> str:
        return self.language

    def set(self, language: str) -> None:
        if language not in ("en", "lt"):
            raise ValueError(f"Unsupported language '{language}'. Supported: en, lt")
        self.language = language


class _RunningServer:
    """Context manager that starts a control server on an ephemeral port."""

    def __init__(self, language: _FakeLanguage, **kwargs):
        self.language = language
        self.server = LanguageControlServer(
            language.get, language.set, host="127.0.0.1", port=0, **kwargs
        )

    def __enter__(self) -> "_RunningServer":
        self.server.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        self.server.stop()
        return False

    @property
    def port(self) -> int:
        address = self.server.address
        assert address is not None, "server should be started"
        return address[1]


def _encode_body(body) -> bytes | None:
    if body is None:
        return None
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    return json.dumps(body).encode("utf-8")


def _http(port: int, method: str, path: str = "/language", body=None, token=None):
    """Perform a request and return ``(status, parsed_json_body)``."""
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = _encode_body(body)
    if data is not None:
        headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        finally:
            error.close()


# ---------------------------------------------------------------------------
# GET / POST
# ---------------------------------------------------------------------------
def test_control_get_language():
    language = _FakeLanguage("en")
    with _RunningServer(language) as running:
        status, body = _http(running.port, "GET")

    assert status == 200
    assert body == {"language": "en"}


def test_control_post_language_switches():
    language = _FakeLanguage("en")
    with _RunningServer(language) as running:
        status, body = _http(running.port, "POST", body={"language": "lt"})
        assert status == 200
        assert body == {"language": "lt"}
        assert language.language == "lt"

        _, body = _http(running.port, "GET")
        assert body == {"language": "lt"}


def test_control_post_normalizes_case_and_whitespace():
    language = _FakeLanguage("en")
    with _RunningServer(language) as running:
        status, body = _http(running.port, "POST", body={"language": " LT "})

    assert status == 200
    assert body == {"language": "lt"}
    assert language.language == "lt"


# ---------------------------------------------------------------------------
# Invalid / malformed requests
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "body",
    [
        b"",  # empty body
        b"not json",  # malformed JSON
        b"{}",  # missing "language"
        b'{"language": 5}',  # wrong type
        b'{"language": ""}',  # empty value
        b'{"language": "fr"}',  # unsupported language
    ],
)
def test_control_post_rejects_malformed_requests(body):
    language = _FakeLanguage("en")
    with _RunningServer(language) as running:
        status, payload = _http(running.port, "POST", body=body)

    assert status == 400
    assert "error" in payload
    assert language.language == "en"  # a rejected request changes nothing


def test_control_unknown_path_returns_404():
    language = _FakeLanguage("en")
    with _RunningServer(language) as running:
        get_status, _ = _http(running.port, "GET", path="/nope")
        post_status, _ = _http(running.port, "POST", path="/nope", body={"language": "lt"})

    assert get_status == 404
    assert post_status == 404
    assert language.language == "en"


# ---------------------------------------------------------------------------
# Lifecycle / configuration
# ---------------------------------------------------------------------------
def test_control_server_start_stop_lifecycle():
    language = _FakeLanguage("en")
    server = LanguageControlServer(language.get, language.set, host="127.0.0.1", port=0)
    server.start()
    port = server.address[1]
    assert _http(port, "GET")[0] == 200

    server.stop()
    assert server.address is None

    with pytest.raises(urllib.error.URLError):
        _http(port, "GET")


def test_control_port_conflict_raises_on_start():
    language = _FakeLanguage("en")
    first = LanguageControlServer(language.get, language.set, host="127.0.0.1", port=0)
    first.start()
    try:
        port = first.address[1]
        second = LanguageControlServer(language.get, language.set, host="127.0.0.1", port=port)
        with pytest.raises(OSError):
            second.start()
        # The failed server stays stopped and can be cleaned up safely.
        assert second.address is None
        second.stop()
    finally:
        first.stop()


@pytest.mark.parametrize("token", ["", "   "])
def test_control_empty_token_is_treated_as_unset(token):
    """An empty token must not lock every request out with an unsatisfiable 401."""
    language = _FakeLanguage("en")
    with _RunningServer(language, token=token) as running:
        assert running.server.token is None
        status, body = _http(running.port, "GET")

    assert status == 200
    assert body == {"language": "en"}


def test_control_token_enforced_when_configured():
    language = _FakeLanguage("en")
    with _RunningServer(language, token="s3cret") as running:
        assert _http(running.port, "GET")[0] == 401
        assert _http(running.port, "GET", token="wrong")[0] == 401
        assert _http(running.port, "GET", token="s3cret")[0] == 200
