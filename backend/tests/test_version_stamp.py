"""Version metadata, installer stamp, and the approved heartbeat HTTP contract."""

import pytest

from core import version


@pytest.fixture(autouse=True)
def clear_version_cache():
    version.version_string.cache_clear()
    yield
    version.version_string.cache_clear()


def test_installed_metadata_has_priority(monkeypatch, tmp_path):
    monkeypatch.setattr(version.metadata, "version", lambda name: "2.3.4")
    monkeypatch.setattr(version, "_PYPROJECT", tmp_path / "missing")
    assert version.package_version() == "2.3.4"


@pytest.mark.parametrize(
    "project,expected",
    [
        ('[project]\nversion = "1.2.3"\n', "1.2.3"),
        ("", "unknown"),
        ("invalid TOML =", "unknown"),
    ],
)
def test_source_metadata_fallback(monkeypatch, tmp_path, project, expected):
    def absent(name):
        raise version.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version.metadata, "version", absent)
    path = tmp_path / "pyproject.toml"
    monkeypatch.setattr(version, "_PYPROJECT", path)
    assert version.package_version() == "unknown"
    path.write_text(project)
    assert version.package_version() == expected


@pytest.mark.parametrize(
    "build_id,expected",
    [
        (None, "1.2.3"),
        ("", "1.2.3"),
        ("1.2.3+abc123.dirty\n", "1.2.3+abc123.dirty"),
        ("1.2.3", "1.2.3"),
    ],
)
def test_optional_build_id(monkeypatch, tmp_path, build_id, expected):
    monkeypatch.setattr(version, "package_version", lambda: "1.2.3")
    path = tmp_path / "build-id.txt"
    monkeypatch.setattr(version, "_BUILD_ID", path)
    if build_id is not None:
        path.write_text(build_id)
    assert version.version_string() == expected


@pytest.mark.parametrize("stamp", [None, "0.1.0+abc123"])
@pytest.mark.parametrize("active", [None, "ltb", "kjv"])
def test_heartbeat_returns_active_id_with_optional_stamp(
    client, app_state, monkeypatch, stamp, active
):
    monkeypatch.setattr(app_state, "active_bible_version", lambda: active)
    body = {"language": "en", "interval_s": 5}
    if stamp is not None:
        body["version"] = stamp
    response = client.post("/api/pipeline/heartbeat", json=body)
    assert response.status_code == 200
    assert response.json() == {"bible_version": active}


@pytest.mark.parametrize("stamp", ["", "x" * 129])
def test_heartbeat_stamp_is_bounded(client, stamp):
    assert (
        client.post(
            "/api/pipeline/heartbeat",
            json={
                "language": "en",
                "interval_s": 5,
                "version": stamp,
            },
        ).status_code
        == 422
    )


def test_healthz_contains_server_stamp(client):
    assert client.get("/healthz").json() == {
        "status": "healthy",
        "version": version.version_string(),
    }


def test_heartbeat_openapi_has_response_model(client):
    operation = client.get("/openapi.json").json()["paths"]["/api/pipeline/heartbeat"]["post"]
    assert "204" not in operation["responses"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PipelineHeartbeatResponse",
    }


def test_version_stamp_is_cached_until_cleared(monkeypatch, tmp_path):
    path = tmp_path / "build-id.txt"
    monkeypatch.setattr(version, "_BUILD_ID", path)
    path.write_text("1.2.3+first")
    assert version.version_string() == "1.2.3+first"
    path.write_text("1.2.3+second")
    assert version.version_string() == "1.2.3+first"
    version.version_string.cache_clear()
    assert version.version_string() == "1.2.3+second"


def test_invalid_utf8_stamp_uses_package_version(monkeypatch, tmp_path):
    path = tmp_path / "build-id.txt"
    path.write_bytes(b"\xff")
    monkeypatch.setattr(version, "_BUILD_ID", path)
    monkeypatch.setattr(version, "package_version", lambda: "1.2.3")
    assert version.version_string() == "1.2.3"
