import json
import os
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

import pytest
from launcher import diagnostics
from launcher.settings import LauncherSettings, save_settings

NOW = datetime(2026, 11, 24, 12)


def test_bundle_contents_and_token_redaction(tmp_path):
    token = "current-operator-secret"
    env_token = "environment-operator-secret"
    settings = LauncherSettings(
        network_token=token,
        extra_audio_args=[token, env_token, '/operator#token=old-fragment-secret"'],
    )
    save_settings(settings, tmp_path / "launcher.json")
    logs = tmp_path / "logs"
    logs.mkdir()
    for name in ("services", "sessions"):
        directory = logs / name
        directory.mkdir()
        for index in range(11):
            entry = directory / f"{index}.jsonl"
            entry.write_text(json.dumps({"message": token}) + "\n", encoding="utf-8")
            os.utime(entry, (index + 1, index + 1))
    (logs / "sessions" / "10.wav").write_bytes(b"private recording")
    (logs / "audio.log").write_text(
        '{"ToKeN" : "old-json-secret"}\nAuthorization: bEaReR old-bearer-secret\n', encoding="utf-8"
    )
    (logs / "sessions" / "10.jsonl").write_text(
        json.dumps({"token": token, "auth": f"Bearer {token}"}) + "\n", encoding="utf-8"
    )
    (logs / f"services/{token}.jsonl").write_text(
        json.dumps({"message": env_token}), encoding="utf-8"
    )

    with (logs / "server.log").open("ab") as live_log:
        live_log.write(
            f"{token}\n/operator?token={token}&other=yes\n/operator#token={token}\n"
            f"/operator?token=old-query-secret\n/operator#token=old-fragment-secret\n"
            f"{env_token}\nplan-only-secret\n".encode()
        )
        live_log.flush()
        path = diagnostics.build_bundle(
            tmp_path,
            logs,
            settings,
            logs / "diagnostics" / "bundle.zip",
            extra_info={"probe": token},
            now=NOW,
            env_token=env_token,
            plan_token="plan-only-secret",
        )

    with ZipFile(path) as archive:
        names = set(archive.namelist())
        assert {"server.log", "audio.log", "launcher.json", "diagnostics.json"} <= names
        assert sum(name.startswith("services/") for name in names) == 10
        assert sum(name.startswith("sessions/") for name in names) == 10
        assert not any(name.endswith(".wav") or name.endswith("/0.jsonl") for name in names)
        for name in names:
            for secret in (
                token,
                env_token,
                "old-query-secret",
                "old-fragment-secret",
                "plan-only-secret",
                "old-json-secret",
                "old-bearer-secret",
            ):
                assert secret not in name
                assert secret.encode() not in archive.read(name)
        saved = json.loads(archive.read("launcher.json"))
        assert saved["network_token"] == "[REDACTED]"
        assert saved["extra_audio_args"] == [
            "[REDACTED]",
            "[REDACTED]",
            '/operator#token=[REDACTED]"',
        ]
        assert b"?token=[REDACTED]&other=yes" in archive.read("server.log")
        assert b"#token=[REDACTED]" in archive.read("server.log")
        assert archive.read("audio.log").splitlines() == [
            b'{"ToKeN" : "[REDACTED]"}',
            b"Authorization: bEaReR [REDACTED]",
        ]
        assert json.loads(archive.read("sessions/10.jsonl")) == {
            "token": "[REDACTED]",
            "auth": "Bearer [REDACTED]",
        }
        report = json.loads(archive.read("diagnostics.json"))
        assert report["collected_at"] == NOW.isoformat()
        assert report["files"]["server.log"] == "included"
        assert report["files"]["sessions/0.jsonl"].startswith("skipped")


def test_bundle_tails_large_logs_and_skips_locked_files_and_oversized_jsonl(tmp_path, monkeypatch):
    (tmp_path / "server.log").write_bytes(b"prefix\n" + b"x" * (5 * 1024 * 1024) + b"\nlast\n")
    (tmp_path / "audio.log").touch()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "small.jsonl").write_bytes(b"{}\n")
    with (sessions / "large.jsonl").open("wb") as large:
        large.truncate(20 * 1024 * 1024 + 1)
    original_open = Path.open

    def open_file(self, *args, **kwargs):
        if self.name == "audio.log":
            raise PermissionError("file locked")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    path = diagnostics.build_bundle(
        tmp_path, tmp_path, LauncherSettings(), tmp_path / "bundle.zip", extra_info={}, now=NOW
    )
    with ZipFile(path) as archive:
        assert archive.read("server.log") == b"last\n"
        assert archive.read("sessions/small.jsonl") == b"{}\n"
        assert "audio.log" not in archive.namelist()
        assert "sessions/large.jsonl" not in archive.namelist()
        manifest = json.loads(archive.read("diagnostics.json"))["files"]
        assert manifest["server.log"].startswith("truncated")
        assert manifest["audio.log"] == "skipped: could not read"
        assert manifest["sessions/large.jsonl"] == "skipped: JSONL size limit"


def test_bundle_with_missing_source_directories(tmp_path):
    path = diagnostics.build_bundle(
        tmp_path / "data",
        tmp_path / "logs",
        LauncherSettings(),
        tmp_path / "bundle.zip",
        extra_info={},
        now=NOW,
    )
    with ZipFile(path) as archive:
        assert set(archive.namelist()) == {"launcher.json", "diagnostics.json"}
    again = diagnostics.build_bundle(
        tmp_path, tmp_path, LauncherSettings(), path, extra_info={"second": True}, now=NOW
    )
    assert again.name == "bundle-1.zip"
    with ZipFile(path) as first, ZipFile(again) as second:
        assert "second" not in json.loads(first.read("diagnostics.json"))
        assert json.loads(second.read("diagnostics.json"))["second"]


@pytest.mark.parametrize("locked_partial", [False, True])
def test_failed_write_only_exposes_partial(tmp_path, monkeypatch, locked_partial):
    final = tmp_path / "bundle.zip"
    partial = tmp_path / "bundle.zip.partial"

    def fail(*args, **kwargs):
        assert partial.exists() and not final.exists()
        raise OSError("disk full")

    monkeypatch.setattr(ZipFile, "writestr", fail)
    if locked_partial:

        def locked(*args, **kwargs):
            raise PermissionError("locked")

        monkeypatch.setattr(Path, "unlink", locked)
    with pytest.raises(OSError, match="disk full"):
        diagnostics.build_bundle(
            tmp_path, tmp_path, LauncherSettings(), final, extra_info={}, now=NOW
        )
    assert not final.exists()
    assert partial.exists() == locked_partial
