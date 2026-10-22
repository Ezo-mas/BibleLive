"""The idle-screen picture: upload checks, serving, fit mode, clear, and persistence.

The picture is operator-controlled and lives beside the settings file, so these tests
run against the per-test settings store from conftest (a temporary directory).
"""

import asyncio
import json
import logging

import pytest

from core.config import config
from core.server.idle_screen import (
    MAX_IDLE_IMAGE_BYTES,
    IdleImage,
    IdleScreenError,
    read_upload,
    sniff_image_kind,
)
from core.server.settings_store import SettingsStore
from core.server.state import AppState

TOKEN = "idle-screen-test-token"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBPVP8 " + b"\x00" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>x</script></svg>'

AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def auth_enabled(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_AUTH", True)
    monkeypatch.setattr(config, "OPERATOR_TOKEN", TOKEN)
    return AUTH


def _stored_files(app_state) -> set[str]:
    """The names in the settings directory (the picture file, the settings file, strays)."""
    return {p.name for p in app_state.settings_store.directory.iterdir()}


def _upload(client, data: bytes, headers: dict | None = None, params: dict | None = None):
    return client.post(
        "/api/idle-screen",
        content=data,
        headers=AUTH if headers is None else headers,
        params=params or {},
    )


class _StreamingRequest:
    """Stands in for a Starlette request: its body is handed out chunk by chunk and counted."""

    def __init__(self, chunks, headers: dict | None = None) -> None:
        self.headers = headers or {}
        self._chunks = iter(chunks)
        self.chunks_read = 0

    async def stream(self):
        for chunk in self._chunks:
            self.chunks_read += 1
            yield chunk


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (PNG, "png"),
        (JPEG, "jpeg"),
        (WEBP, "webp"),
        (SVG, None),
        (b"GIF89a" + b"\x00" * 20, None),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", None),
        (b"\x89PNG", None),
        (b"<html><body>not a picture</body></html>", None),
        (b"", None),
    ],
)
def test_format_is_read_from_the_magic_bytes(data, kind):
    assert sniff_image_kind(data) == kind


@pytest.mark.parametrize(
    ("data", "content_type", "served_as"),
    [
        (PNG, "image/png", "image/png"),
        (JPEG, "image/jpeg", "image/jpeg"),
        (WEBP, "image/webp", "image/webp"),
    ],
)
def test_each_accepted_format_is_stored_and_served_with_its_own_type(
    client, auth_enabled, data, content_type, served_as
):
    response = _upload(client, data, headers={**AUTH, "Content-Type": content_type})

    assert response.status_code == 200
    assert response.json()["image"]["content_type"] == served_as

    served = client.get("/api/idle-screen/image")
    assert served.status_code == 200
    assert served.content == data
    assert served.headers["content-type"] == served_as
    assert served.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-the-token"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
    ],
)
def test_upload_needs_the_operator_token(client, app_state, auth_enabled, headers):
    response = _upload(client, PNG, headers=headers)

    assert response.status_code == 401
    assert app_state.idle_image is None
    assert "idle_screen.png" not in _stored_files(app_state)


def test_svg_is_refused(client, app_state, auth_enabled):
    response = _upload(client, SVG, headers={**AUTH, "Content-Type": "image/svg+xml"})

    assert response.status_code == 415
    assert app_state.idle_image is None
    assert not any(name.startswith("idle_screen") for name in _stored_files(app_state))


def test_svg_claimed_as_png_is_refused(client, app_state, auth_enabled):
    response = _upload(client, SVG, headers={**AUTH, "Content-Type": "image/png"})

    assert response.status_code == 415
    assert app_state.idle_image is None


def test_wrong_magic_bytes_are_refused_even_with_an_image_content_type(
    client, app_state, auth_enabled
):
    response = _upload(
        client, b"GIF89a" + b"\x00" * 40, headers={**AUTH, "Content-Type": "image/png"}
    )

    assert response.status_code == 415
    assert app_state.idle_image is None


def test_empty_body_is_refused(client, app_state, auth_enabled):
    response = _upload(client, b"")

    assert response.status_code == 415
    assert app_state.idle_image is None


def test_client_content_type_does_not_change_what_is_served(client, auth_enabled):
    response = _upload(client, PNG, headers={**AUTH, "Content-Type": "image/svg+xml"})

    assert response.status_code == 200
    served = client.get("/api/idle-screen/image")
    assert served.headers["content-type"] == "image/png"


def test_client_filename_and_path_never_reach_the_disk(client, app_state, auth_enabled, tmp_path):
    headers = {
        **AUTH,
        "Content-Disposition": 'attachment; filename="../../escape.png"',
        "X-Filename": "../../../etc/passwd",
    }
    response = _upload(client, PNG, headers=headers)

    assert response.status_code == 200
    assert _stored_files(app_state) == {"idle_screen.png", "server_settings.json"}
    assert not (tmp_path.parent / "escape.png").exists()


def test_fit_query_cannot_name_a_path(client, app_state, auth_enabled):
    response = _upload(client, PNG, params={"fit": "../../escape"})

    assert response.status_code == 422
    assert app_state.idle_image is None


def test_oversize_body_is_refused_and_nothing_is_stored(client, app_state, auth_enabled):
    too_big = PNG + b"\x00" * (MAX_IDLE_IMAGE_BYTES - len(PNG) + 1)

    response = _upload(client, too_big)

    assert response.status_code == 413
    assert app_state.idle_image is None
    assert "idle_screen.png" not in _stored_files(app_state)


def test_chunked_oversize_body_is_cut_off_without_being_read_whole():
    chunk = b"\x00" * (256 * 1024)
    request = _StreamingRequest([chunk] * 1000)  # 250 MB if it were all read

    with pytest.raises(IdleScreenError) as refused:
        asyncio.run(read_upload(request))

    assert refused.value.status_code == 413
    # The cap is 8 MB (32 chunks of 256 KiB): one more chunk is read to find it passed.
    assert request.chunks_read <= 33


def test_declared_oversize_length_is_refused_before_any_byte_is_read():
    request = _StreamingRequest(
        [b"\x00" * 1024], headers={"content-length": str(MAX_IDLE_IMAGE_BYTES + 1)}
    )

    with pytest.raises(IdleScreenError) as refused:
        asyncio.run(read_upload(request))

    assert refused.value.status_code == 413
    assert request.chunks_read == 0


def test_picture_at_exactly_the_cap_is_accepted(client, auth_enabled):
    at_cap = PNG + b"\x00" * (MAX_IDLE_IMAGE_BYTES - len(PNG))

    response = _upload(client, at_cap)

    assert response.status_code == 200


def test_fit_query_sets_the_fit_with_the_upload(client, app_state, auth_enabled):
    response = _upload(client, PNG, params={"fit": "fill"})

    assert response.status_code == 200
    assert response.json()["fit"] == "fill"
    assert app_state.idle_screen_fit == "fill"


def test_replacing_with_another_format_removes_the_old_file(client, app_state, auth_enabled):
    assert _upload(client, PNG).status_code == 200
    assert _upload(client, JPEG).status_code == 200

    assert _stored_files(app_state) == {"idle_screen.jpg", "server_settings.json"}
    assert client.get("/api/idle-screen/image").headers["content-type"] == "image/jpeg"


def test_failed_save_is_a_server_error_and_leaves_no_picture(
    client, app_state, auth_enabled, monkeypatch
):
    monkeypatch.setattr(app_state.settings_store, "save_idle_screen_image", lambda image: False)

    response = _upload(client, PNG)

    assert response.status_code == 500
    assert app_state.idle_image is None
    assert client.get("/api/idle-screen/image").status_code == 404


def test_picture_is_served_without_a_token_when_auth_is_on(client, auth_enabled):
    assert _upload(client, PNG).status_code == 200

    # The display page has no token, so the picture is public like /display.
    assert client.get("/api/idle-screen/image").status_code == 200


def test_no_picture_is_a_404(client, auth_enabled):
    assert client.get("/api/idle-screen/image").status_code == 404


def test_state_reports_fit_and_version_but_no_path(client, auth_enabled):
    assert _upload(client, PNG).status_code == 200

    state = client.get("/api/idle-screen", headers=AUTH).json()

    assert set(state) == {"fit", "image"}
    assert set(state["image"]) == {"version", "content_type"}
    assert state["image"]["content_type"] == "image/png"
    assert "idle_screen" not in json.dumps(state)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/idle-screen", None),
        ("POST", "/api/idle-screen", PNG),
        ("PUT", "/api/idle-screen/fit", {"fit": "fill"}),
        ("DELETE", "/api/idle-screen", None),
    ],
)
def test_operator_routes_refuse_a_missing_token(client, auth_enabled, method, path, body):
    kwargs = {}
    if isinstance(body, bytes):
        kwargs["content"] = body
    elif body is not None:
        kwargs["json"] = body

    response = client.request(method, path, **kwargs)

    assert response.status_code == 401


def test_openapi_describes_the_idle_screen_routes(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert {"get", "post", "delete"} <= set(paths["/api/idle-screen"])
    assert "put" in paths["/api/idle-screen/fit"]
    assert "get" in paths["/api/idle-screen/image"]


def test_fit_defaults_to_logo(client, auth_enabled):
    state = client.get("/api/idle-screen", headers=AUTH).json()

    assert state == {"fit": "logo", "image": None}


def test_fit_change_is_applied_and_bad_values_are_refused(client, app_state, auth_enabled):
    response = client.put("/api/idle-screen/fit", json={"fit": "fill"}, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["fit"] == "fill"
    assert app_state.idle_screen_fit == "fill"

    bad = client.put("/api/idle-screen/fit", json={"fit": "stretch"}, headers=AUTH)
    assert bad.status_code == 422
    assert app_state.idle_screen_fit == "fill"


def test_fit_change_without_a_picture_is_kept_for_the_next_one(client, app_state, auth_enabled):
    client.put("/api/idle-screen/fit", json={"fit": "fill"}, headers=AUTH)
    assert _upload(client, PNG).status_code == 200

    assert app_state.idle_screen_fit == "fill"


def test_delete_clears_the_picture_and_its_file(client, app_state, auth_enabled):
    assert _upload(client, PNG).status_code == 200

    response = client.delete("/api/idle-screen", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["image"] is None
    assert app_state.idle_image is None
    assert "idle_screen.png" not in _stored_files(app_state)
    assert client.get("/api/idle-screen/image").status_code == 404


def test_delete_with_a_wrong_token_keeps_the_picture(client, app_state, auth_enabled):
    assert _upload(client, PNG).status_code == 200

    response = client.delete("/api/idle-screen", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401
    assert client.get("/api/idle-screen/image").status_code == 200


def test_clear_on_a_blank_screen_is_harmless(client, auth_enabled):
    response = client.delete("/api/idle-screen", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["image"] is None


def test_picture_and_fit_survive_a_restart(client, app_state, auth_enabled):
    assert _upload(client, JPEG, params={"fit": "fill"}).status_code == 200
    before = app_state.idle_image

    recreated = AppState(app_state.bible_lookup, SettingsStore(app_state.settings_store.path))

    assert recreated.idle_image == before
    assert recreated.idle_screen_fit == "fill"
    served = (app_state.settings_store.directory / "idle_screen.jpg").read_bytes()
    assert served == JPEG


def test_overlapping_uploads_leave_the_recorded_picture_in_place(app_state, tmp_path):
    # A fresh state, so its lock is not bound to the event loop of some other test.
    state = AppState(app_state.bible_lookup, SettingsStore(tmp_path / "server_settings.json"))

    async def upload_png_and_jpeg_together():
        await asyncio.gather(state.set_idle_image("png", PNG), state.set_idle_image("jpeg", JPEG))

    asyncio.run(upload_png_and_jpeg_together())

    picture = state.idle_image
    assert picture is not None
    # The last upload wins; the file it recorded must still be there, with its bytes.
    assert (tmp_path / picture.filename).read_bytes() == JPEG
    assert {p.name for p in tmp_path.iterdir()} == {"idle_screen.jpg", "server_settings.json"}
    reloaded = AppState(app_state.bible_lookup, SettingsStore(tmp_path / "server_settings.json"))
    assert reloaded.idle_image == picture


def test_cleared_picture_stays_cleared_after_a_restart(client, app_state, auth_enabled):
    assert _upload(client, PNG).status_code == 200
    assert client.delete("/api/idle-screen", headers=AUTH).status_code == 200

    recreated = AppState(app_state.bible_lookup, SettingsStore(app_state.settings_store.path))

    assert recreated.idle_image is None


def test_record_without_its_file_is_ignored_on_start(client, app_state, auth_enabled, caplog):
    assert _upload(client, PNG).status_code == 200
    (app_state.settings_store.directory / "idle_screen.png").unlink()

    with caplog.at_level(logging.WARNING):
        recreated = AppState(app_state.bible_lookup, SettingsStore(app_state.settings_store.path))

    assert recreated.idle_image is None
    assert "idle_screen.png is missing" in caplog.text


def test_malformed_saved_record_is_ignored_on_start(app_state, tmp_path, caplog):
    path = tmp_path / "server_settings.json"
    path.write_text(
        json.dumps(
            {
                "idle_screen_image": {"kind": "svg", "version": "0123456789abcdef"},
                "idle_screen_fit": "stretch",
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        recreated = AppState(app_state.bible_lookup, SettingsStore(path))

    assert recreated.idle_image is None
    assert recreated.idle_screen_fit == "logo"
    assert "invalid idle-screen picture record" in caplog.text


@pytest.mark.parametrize("version", ["../../etc", "XYZ", "0123", 12345])
def test_saved_version_must_look_like_a_version_tag(app_state, tmp_path, version):
    path = tmp_path / "server_settings.json"
    path.write_text(
        json.dumps({"idle_screen_image": {"kind": "png", "version": version}}), encoding="utf-8"
    )

    recreated = AppState(app_state.bible_lookup, SettingsStore(path))

    assert recreated.idle_image is None


def test_saving_the_picture_keeps_other_settings(client, app_state, auth_enabled):
    assert app_state.settings_store.save_auto_mode("high")

    assert _upload(client, PNG).status_code == 200

    saved = json.loads(app_state.settings_store.path.read_text(encoding="utf-8"))
    assert saved["auto_mode"] == "high"
    assert saved["idle_screen_image"]["kind"] == "png"


def test_stored_record_is_a_kind_and_a_version_only(client, app_state, auth_enabled):
    assert _upload(client, PNG).status_code == 200

    saved = json.loads(app_state.settings_store.path.read_text(encoding="utf-8"))
    assert set(saved["idle_screen_image"]) == {"kind", "version"}
    assert app_state.idle_image == IdleImage(
        kind="png", version=saved["idle_screen_image"]["version"]
    )


def _connect_display(client):
    """Open a display socket and read its initial clear and display settings."""
    context = client.websocket_connect("/ws/display")
    websocket = context.__enter__()
    assert websocket.receive_json()["type"] == "clear"
    return context, websocket, websocket.receive_json()


def test_display_settings_carry_the_picture_to_connected_displays(client, app_state, auth_enabled):
    context, websocket, initial = _connect_display(client)
    try:
        assert initial["type"] == "display_settings"
        assert initial["data"]["idle_image"] is None

        assert _upload(client, PNG, params={"fit": "fill"}).status_code == 200
        uploaded = websocket.receive_json()
        assert uploaded["type"] == "display_settings"
        assert uploaded["data"]["idle_image"] == {
            "version": app_state.idle_image.version,
            "fit": "fill",
        }

        response = client.put("/api/idle-screen/fit", json={"fit": "logo"}, headers=AUTH)
        assert response.status_code == 200
        assert websocket.receive_json()["data"]["idle_image"] == {
            "version": app_state.idle_image.version,
            "fit": "logo",
        }

        assert client.delete("/api/idle-screen", headers=AUTH).status_code == 200
        assert websocket.receive_json()["data"]["idle_image"] is None
    finally:
        context.__exit__(None, None, None)


def test_a_failed_upload_after_a_fit_change_still_tells_the_displays(
    client, app_state, auth_enabled, monkeypatch
):
    async def fail(kind, data):
        raise IdleScreenError(500, "The idle-screen picture could not be saved.")

    monkeypatch.setattr(app_state, "set_idle_image", fail)
    context, websocket, _initial = _connect_display(client)
    try:
        response = _upload(client, PNG, params={"fit": "fill"})

        assert response.status_code == 500
        assert app_state.idle_screen_fit == "fill"
        assert websocket.receive_json()["type"] == "display_settings"
    finally:
        context.__exit__(None, None, None)


def test_a_display_that_connects_later_gets_the_picture(client, app_state, auth_enabled):
    assert _upload(client, JPEG).status_code == 200

    context, _websocket, settings = _connect_display(client)
    try:
        assert settings["data"]["idle_image"] == {
            "version": app_state.idle_image.version,
            "fit": "logo",
        }
    finally:
        context.__exit__(None, None, None)


def test_changing_the_theme_keeps_the_picture(client, app_state, auth_enabled):
    assert _upload(client, PNG).status_code == 200

    asyncio.run(app_state.set_display_theme("light"))

    picture = app_state.display_settings.idle_image
    assert picture is not None
    assert picture.version == app_state.idle_image.version


def test_a_saved_picture_is_in_the_display_settings_after_a_restart(
    client, app_state, auth_enabled
):
    assert _upload(client, WEBP, params={"fit": "fill"}).status_code == 200

    recreated = AppState(app_state.bible_lookup, SettingsStore(app_state.settings_store.path))

    picture = recreated.display_settings.idle_image
    assert picture is not None
    assert (picture.version, picture.fit) == (app_state.idle_image.version, "fill")


def test_display_settings_without_the_field_are_still_valid():
    from core.server.models import DisplaySettings

    settings = DisplaySettings.model_validate({"verse_limit": 3, "theme": "dark"})

    assert settings.idle_image is None
