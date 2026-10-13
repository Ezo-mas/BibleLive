"""Tests for the server's language-control proxy (``/api/language``).

The audio pipeline's control API is stood in for by a tiny stdlib HTTP server, so
these run without the optional audio extras (importing ``audio.control`` loads
``audio.stt``) and nothing here is skipped.
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from core.config import Config, config

# NOTE: the ``client`` fixture comes from tests/conftest.py.


class _FakePipeline:
    """A stand-in for the pipeline's ``GET``/``POST /language`` control API."""

    def __init__(self, language: str = "en", token: str | None = None) -> None:
        self.language = language
        self.token = token
        self.supported = ("en", "lt")
        # Set to make the next replies deliberately wrong.
        self.raw_reply: bytes | None = None
        self.redirect_to: str | None = None
        self.seen_auth: list[str | None] = []
        self._httpd: ThreadingHTTPServer | None = None

    def start(self) -> str:
        pipeline = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status: int, payload: dict, headers: dict | None = None) -> None:
                body = pipeline.raw_reply or json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def _gate(self) -> bool:
                pipeline.seen_auth.append(self.headers.get("Authorization"))
                if pipeline.redirect_to:
                    self._reply(302, {}, {"Location": pipeline.redirect_to})
                    return False
                if pipeline.token and self.headers.get("Authorization") != (
                    f"Bearer {pipeline.token}"
                ):
                    self._reply(401, {"error": "unauthorized"})
                    return False
                return True

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if self._gate():
                    self._reply(200, {"language": pipeline.language})

            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                if not self._gate():
                    return
                length = int(self.headers.get("Content-Length", "0"))
                requested = json.loads(self.rfile.read(length))["language"]
                if requested not in pipeline.supported:
                    self._reply(400, {"error": f"Unsupported language '{requested}'"})
                    return
                pipeline.language = requested
                self._reply(200, {"language": requested})

            def log_message(self, *args) -> None:  # silence the test output
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()


@pytest.fixture
def pipeline():
    """A running fake pipeline, with the server configured to talk to it."""
    fake = _FakePipeline()
    original = (config.AUDIO_CONTROL_URL, config.AUDIO_CONTROL_TOKEN)
    config.AUDIO_CONTROL_URL = fake.start()
    config.AUDIO_CONTROL_TOKEN = None
    yield fake
    fake.stop()
    config.AUDIO_CONTROL_URL, config.AUDIO_CONTROL_TOKEN = original


@pytest.fixture
def auth_enabled():
    """Enable operator authentication for the duration of a test."""
    original = (config.REQUIRE_AUTH, config.OPERATOR_TOKEN)
    config.REQUIRE_AUTH = True
    config.OPERATOR_TOKEN = "operator-token-12345"
    yield "operator-token-12345"
    config.REQUIRE_AUTH, config.OPERATOR_TOKEN = original


def _unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_get_reports_the_pipelines_language(client, pipeline):
    pipeline.language = "lt"

    response = client.get("/api/language")

    assert response.status_code == 200
    assert response.json() == {"language": "lt"}


def test_post_switches_the_pipeline_and_returns_what_it_confirmed(client, pipeline):
    response = client.post("/api/language", json={"language": "lt"})

    assert response.status_code == 200
    assert response.json() == {"language": "lt"}
    # The switch happened on the pipeline, not just in the reply.
    assert pipeline.language == "lt"
    assert client.get("/api/language").json() == {"language": "lt"}


def test_an_unsupported_language_is_the_pipelines_own_400(client, pipeline):
    response = client.post("/api/language", json={"language": "xx"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported language 'xx'"
    assert pipeline.language == "en"


@pytest.mark.parametrize("body", [{}, {"language": ""}, {"language": 5}, {"language": "x" * 17}])
def test_a_malformed_request_is_rejected_before_it_reaches_the_pipeline(client, pipeline, body):
    response = client.post("/api/language", json=body)

    assert response.status_code == 422
    assert pipeline.seen_auth == []  # nothing was forwarded


def test_a_pipeline_that_is_not_running_is_a_503(client):
    original = config.AUDIO_CONTROL_URL
    config.AUDIO_CONTROL_URL = f"http://127.0.0.1:{_unused_port()}"
    try:
        get_response = client.get("/api/language")
        post_response = client.post("/api/language", json={"language": "lt"})
    finally:
        config.AUDIO_CONTROL_URL = original

    for response in (get_response, post_response):
        assert response.status_code == 503
        assert "not reachable" in response.json()["detail"]


def test_the_control_token_is_sent_when_configured(client, pipeline):
    pipeline.token = "control-secret"
    config.AUDIO_CONTROL_TOKEN = "control-secret"

    assert client.get("/api/language").status_code == 200
    assert pipeline.seen_auth[-1] == "Bearer control-secret"


def test_a_wrong_control_token_is_a_502_that_does_not_leak_it(client, pipeline):
    pipeline.token = "control-secret"
    config.AUDIO_CONTROL_TOKEN = "not-the-secret"

    response = client.get("/api/language")

    assert response.status_code == 502
    assert "BIBLELIVE_AUDIO_CONTROL_TOKEN" in response.json()["detail"]
    assert "not-the-secret" not in response.text
    assert "control-secret" not in response.text


def test_no_token_is_sent_when_none_is_configured(client, pipeline):
    client.get("/api/language")

    assert pipeline.seen_auth == [None]


def test_an_unreadable_reply_is_a_502(client, pipeline):
    pipeline.raw_reply = b"<html>not json</html>"

    response = client.get("/api/language")

    assert response.status_code == 502


def test_a_reply_without_a_language_is_a_502(client, pipeline):
    pipeline.raw_reply = json.dumps({"lang": "en"}).encode()

    response = client.get("/api/language")

    assert response.status_code == 502


def test_a_redirect_is_not_followed(client, pipeline):
    """The token must only ever go where it was aimed."""
    elsewhere = _FakePipeline()
    elsewhere_url = elsewhere.start()
    try:
        pipeline.redirect_to = f"{elsewhere_url}/language"
        config.AUDIO_CONTROL_TOKEN = "control-secret"

        response = client.get("/api/language")

        assert response.status_code == 502
        assert elsewhere.seen_auth == []  # never contacted
    finally:
        elsewhere.stop()


def test_the_operator_token_guards_both_endpoints(client, pipeline, auth_enabled):
    assert client.get("/api/language").status_code == 401
    assert client.post("/api/language", json={"language": "lt"}).status_code == 401
    assert pipeline.language == "en"

    headers = {"Authorization": f"Bearer {auth_enabled}"}
    assert client.get("/api/language", headers=headers).status_code == 200
    assert client.post("/api/language", json={"language": "lt"}, headers=headers).status_code == 200
    assert pipeline.language == "lt"


def test_the_operator_token_is_never_forwarded_to_the_pipeline(client, pipeline, auth_enabled):
    client.get("/api/language", headers={"Authorization": f"Bearer {auth_enabled}"})

    assert pipeline.seen_auth == [None]


class TestControlUrlSetting:
    def test_it_defaults_to_the_pipelines_own_default(self):
        assert Config(_env_file=None).AUDIO_CONTROL_URL == "http://127.0.0.1:8001"

    def test_a_trailing_slash_is_dropped(self):
        assert Config(
            AUDIO_CONTROL_URL="http://127.0.0.1:9000/", _env_file=None
        ).AUDIO_CONTROL_URL == ("http://127.0.0.1:9000")

    @pytest.mark.parametrize("value", ["ftp://127.0.0.1:8001", "127.0.0.1:8001", "file:///etc/x"])
    def test_only_http_urls_are_accepted(self, value):
        with pytest.raises(ValidationError):
            Config(AUDIO_CONTROL_URL=value, _env_file=None)
