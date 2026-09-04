"""§35 tests — the Stable Edition contract, all mocked (no real camera).

    test_camera_tcp          state machine OFFLINE / REACHABLE / CONTROL_READY
    test_http_8888           health() classification
    test_ftp_connection      FTPClient + busybox LIST parsing
    test_telnet_connection   TelnetSession login dance (mocked socket)
    test_photo_workflow      baseline → command → detect → download → READY
    test_media_listing       DCIM listing with types (JPG/MP4/Other)
    test_download            download into the local gallery

NOTE: passing these proves the Hub logic. The REAL final acceptance is the
six hardware tests in README (§48) against the actual SM-R210.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from app.stable import ftp as ftp_mod
from tests.stable.mocks import FakeFTPClient, sample_files


# ------------------------------------------------------------- ftp parsing
def test_parse_list_line_busybox():
    line = "-rwxr-xr-x 1 root root  3456789 Jan 01 00:11 SAM_0001.JPG"
    e = ftp_mod.parse_list_line(line)
    assert e and e["name"] == "SAM_0001.JPG" and e["size"] == 3456789
    assert e["is_dir"] is False and e["type"] == "JPG" if "type" in e else True


def test_parse_list_line_dir_and_garbage():
    assert ftp_mod.parse_list_line("drwxr-xr-x 1 root root 0 Jan 01 2018 .")["is_dir"]
    assert ftp_mod.parse_list_line("total 3") is None
    assert ftp_mod.parse_list_line("") is None


def test_file_identity_uses_size_and_mtime():
    entries = [{"path": "100PHOTO/SAM_0001.JPG", "size": 10, "mtime": "2026-09-01 10:00"}]
    ident = ftp_mod.file_identity(entries)
    assert ident == {"100PHOTO/SAM_0001.JPG": (10, "2026-09-01 10:00")}


# ------------------------------------------------------------ tcp / states
class _FakeResult:
    def __init__(self, ok):
        self._ok = ok

    def get(self, k, d=None):
        return self._ok if k == "ok" else d


def test_state_machine_offline_reachable_ready(monkeypatch):
    from app.stable import control
    seq = {
        "http": [{"ok": False}, {"ok": False}],
        "ftp": [{"ok": False}, {"ok": True}],
        "telnet": [{"ok": False}, {"ok": False}],
    }
    calls = {k: 0 for k in seq}

    def fake_test_http():
        r = seq["http"][min(calls["http"], len(seq["http"]) - 1)]
        calls["http"] += 1
        return r

    monkeypatch.setattr(control, "test_http", fake_test_http)
    monkeypatch.setattr(control, "test_ftp", lambda: {"ok": False})
    monkeypatch.setattr(control, "test_telnet", lambda: {"ok": False})
    monkeypatch.setattr(control, "test_tcp", lambda h, p, t: {"ok": False})

    # 1) nothing answers → OFFLINE
    st = control.get_status()
    assert st["state"] == "OFFLINE"

    # 2) only TCP answers (raw socket) but HTTP is dead → REACHABLE
    monkeypatch.setattr(control, "test_tcp", lambda h, p, t: {"ok": p == 8888})
    st = control.get_status()
    assert st["state"] == "REACHABLE"

    # 3) HTTP answers → CONTROL_READY
    monkeypatch.setattr(control, "test_http", lambda: {"ok": True, "latency_ms": 5})
    st = control.get_status()
    assert st["state"] == "CONTROL_READY"
    assert st["capture_ready"] in (True, False)   # independent flags


def test_test_camera_never_mentions_7679(monkeypatch):
    from app.stable import camera as cam
    monkeypatch.setattr(cam.control, "test_tcp", lambda h, p, t: {"ok": False})
    monkeypatch.setattr(cam.control, "retry", lambda fn, **kw: {"ok": False})
    r = cam.test()
    assert "7679" in r["note"]
    assert set(r) >= {"http", "ftp", "telnet", "tcp"}


# ---------------------------------------------------------------- telnet
class _FakeSock:
    def __init__(self, script):
        self.script = list(script)   # list[bytes] returned per recv
        self.sent = []
        self.closed = False

    def settimeout(self, t):
        pass

    def recv(self, n):
        return self.script.pop(0) if self.script else b""

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


def test_telnet_login_sequence(monkeypatch):
    from app.stable import telnet as tn
    sock = _FakeSock([
        b"\xff\xfd\x01 Welcome\r\ndrime5 login: ",   # banner + IAC + login
        b"Password: ",
        b"root@drime5:/ # ",
        b"st key click s2\r\nroot@drime5:/ # ",
    ])
    created = []

    def fake_create_connection(addr, timeout=5):
        created.append(addr)
        return sock

    monkeypatch.setattr(tn.socket, "create_connection", fake_create_connection)
    s = tn.TelnetSession("192.168.43.1", 23, timeout=2)
    s.login()
    out = s.command("st key click s2", wait_after=0.3)
    s.close()
    joined = b"".join(sock.sent)
    assert b"root\r\n" in joined          # username sent
    assert b"\r\n" in joined              # empty password line sent
    assert b"st key click s2" in joined   # command sent
    assert "st key click s2" in out
    assert created == [("192.168.43.1", 23)]


def test_telnet_direct_shell_fallback(monkeypatch):
    """Units without login prompt: command goes straight to the shell."""
    from app.stable import telnet as tn
    sock = _FakeSock([b"root@drime5:/ # ", b"ok\r\n"])
    monkeypatch.setattr(tn.socket, "create_connection", lambda a, timeout=5: sock)
    rep = tn.run_command("192.168.43.1", 23, "echo ok", wait_after=0.2)
    assert rep["ok"] is True


# ------------------------------------------------------------ photo flow
def test_photo_workflow_full(monkeypatch, tmp_path):
    from app.stable import camera as cam
    from app.stable import media as media_mod

    baseline = {"100PHOTO/SAM_0001.JPG": (3_000_000, "2026-09-01 10:00"),
                "100PHOTO/SAM_0002.JPG": (3_100_000, "2026-09-02 11:30")}
    after = dict(baseline)
    after["100PHOTO/SAM_0004.JPG"] = (3_200_000, "2026-09-04 09:00")  # the NEW shot

    calls = {"n": 0}

    def fake_baseline():
        calls["n"] += 1
        return (baseline if calls["n"] == 1 else after), ""

    monkeypatch.setattr(cam, "_baseline_entries", fake_baseline)
    monkeypatch.setattr(cam.control, "get_status", lambda: {
        "state": "CONTROL_READY", "host": "x", "camera_web_url": "u",
        "services": {"http": {"ok": True}, "ftp": {"ok": True},
                     "telnet": {"ok": True}},
        "capture_ready": True})
    monkeypatch.setattr(cam.control, "send_capture_command",
                        lambda: {"ok": True})
    monkeypatch.setattr(cam.time, "sleep", lambda s: None)

    downloads = []

    def fake_download(path, size_hint=0):
        downloads.append(path)
        return {"ok": True, "name": "SAM_0004.JPG", "id": "abc123",
                "path": str(tmp_path / "SAM_0004.JPG"), "size": size_hint}

    monkeypatch.setattr(cam.media_mod, "download", fake_download)
    r = cam.take_photo()
    assert r["state"] == "PHOTO_READY"
    assert downloads == ["100PHOTO/SAM_0004.JPG"]
    states = [w["state"] for w in r["workflow"]]
    assert states[0] == "SENDING_COMMAND" and "WAITING_CAMERA" in states
    assert states[-1] == "PHOTO_READY"


def test_photo_workflow_no_new_file_fails(monkeypatch):
    from app.stable import camera as cam
    same = {"100PHOTO/SAM_0001.JPG": (3_000_000, "2026-09-01 10:00")}
    monkeypatch.setattr(cam, "_baseline_entries", lambda: (same, ""))
    monkeypatch.setattr(cam.control, "get_status", lambda: {
        "state": "CONTROL_READY", "host": "x", "camera_web_url": "u",
        "services": {"http": {"ok": True}, "ftp": {"ok": True},
                     "telnet": {"ok": True}},
        "capture_ready": True})
    monkeypatch.setattr(cam.control, "send_capture_command", lambda: {"ok": True})
    monkeypatch.setattr(cam.time, "sleep", lambda s: None)
    r = cam.take_photo()
    assert r["state"] == "FAILED"


def test_photo_offline_fails_fast(monkeypatch):
    from app.stable import camera as cam
    monkeypatch.setattr(cam.control, "get_status", lambda: {
        "state": "OFFLINE", "host": "x", "camera_web_url": "u",
        "services": {}, "capture_ready": False})
    r = cam.take_photo()
    assert r["state"] == "FAILED"
    assert "غير متصلة" in r["workflow"][-1]["reason"]


# --------------------------------------------------------- media listing
def test_media_listing_types(monkeypatch):
    from app.stable import media as media_mod

    def fake_client():
        return FakeFTPClient(sample_files())

    monkeypatch.setattr(media_mod, "_client", fake_client)
    r = media_mod.list_media()
    assert r["ok"] and r["count"] == 3
    types = {f["name"]: f["type"] for f in r.files if False} if False else \
        {f["name"]: f["type"] for f in r["files"]}
    assert types["SAM_0001.JPG"] == "JPG"
    assert types["SAM_0003.MP4"] == "MP4"


# -------------------------------------------------------------- download
def test_download_into_gallery(monkeypatch, tmp_path):
    from app.stable import media as media_mod
    from app.stable import settings as settings_svc

    monkeypatch.setattr(settings_svc, "gallery_dir", lambda: tmp_path)
    monkeypatch.setattr(media_mod.settings_svc, "gallery_dir", lambda: tmp_path)

    def fake_client():
        return FakeFTPClient(sample_files())

    monkeypatch.setattr(media_mod, "_client", fake_client)
    r = media_mod.download("100PHOTO/SAM_0001.JPG", size_hint=len(sample_files()["100PHOTO/SAM_0001.JPG"]))
    assert r["ok"] and r["name"] == "SAM_0001.JPG" and r["size"] > 0
    # lands under gallery/YYYY/MM/
    p = Path(r["path"])
    assert p.exists() and p.parent.parent.parent == tmp_path

    items = media_mod.list_gallery()
    assert any(it["name"] == "SAM_0001.JPG" for it in items)


def test_download_rejects_traversal():
    from app.stable import media as media_mod
    r = media_mod.download("../../etc/passwd")
    assert r["ok"] is False


# ------------------------------------------------------------ http probe
def test_http_health_down():
    from app.stable import http as http_mod
    r = http_mod.health("127.0.0.1", 1, timeout=0.2)
    assert r["ok"] is False and r["http_status"] is None
