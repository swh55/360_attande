"""Transport-layer regression tests.

Background: diagnostic session DG-20260903-001 proved the camera can answer
HTTP 206 + headers and then stream ZERO body bytes (stalled session). These
tests lock the recovery behaviour:

  * http_probe distinguishes HEADERS_WITHOUT_BODY from hard failures;
  * http_probe_variants finds a style/endpoint that streams when one exists;
  * iter_camera_bytes aborts stalled attempts, rotates request styles and
    recovers data on a later attempt (stall -> retry -> data);
  * BackoffPolicy doubles the retry delay and resets after bytes flow;
  * the analyzer maps the all-stalled matrix to CAMERA_STREAM_STALLED (R11).

The synthetic servers emulate the two camera behaviours observed in the
field: a wedged session (headers, no body, connection held open) and a
healthy AVI byte feed.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.fixtures import make_synthetic_stream

# parse-only AUs (first byte = first NAL header byte; never decoded here)
_AUS = [bytes([38]) + f"au{i:04d}".encode() + b"\x11" * 48 for i in range(12)]
_STREAM = make_synthetic_stream(_AUS, codec=1, width=1920, height=1080)


class FakeGear360Server:
    """Threading HTTP server emulating the SM-R210 live endpoint.

    behaviour:
      * "stream": 206 + video/x-avi + full synthetic AVI body, close
      * "stall": 206 + Content-Length (huge) + NO body, connection held open
      * "stall_then_stream": the first `stall_first_n` connections stall,
        later connections stream (recovery-after-kick scenario)
      * "alt_streams": main path stalls, /livestream_low.avi streams
    """

    def __init__(self, behaviour: str, stall_first_n: int = 0,
                 stall_hold: float = 8.0):
        self.behaviour = behaviour
        self.stall_first_n = stall_first_n
        self.stall_hold = stall_hold
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.conn_count = 0
        self.requests: list[dict] = []
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._srv.daemon_threads = True
        self._srv.fake = self          # type: ignore[attr-defined]
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    # -- behaviour policy ---------------------------------------------------
    def _stalls(self, path: str, idx: int) -> bool:
        if self.behaviour == "stall":
            return True
        if self.behaviour == "stall_then_stream":
            return idx <= self.stall_first_n
        if self.behaviour == "alt_streams":
            return path == "/livestream_high.avi"
        return False

    def _make_handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):    # silence
                pass

            def do_GET(self):
                with fake.lock:
                    fake.conn_count += 1
                    idx = fake.conn_count
                    fake.requests.append({
                        "idx": idx, "path": self.path,
                        "range": self.headers.get("Range"),
                        "connection": self.headers.get("Connection"),
                        "ua": self.headers.get("User-Agent", ""),
                    })
                self.send_response(206)
                self.send_header("Content-Type", "video/x-avi ")
                if fake._stalls(self.path, idx):
                    self.send_header("Content-Length", "104857600")
                    self.end_headers()
                    # hold the socket open with a silent body (wedged session)
                    fake.stop_event.wait(fake.stall_hold)
                    return
                body = _STREAM
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                for i in range(0, len(body), 8192):
                    if fake.stop_event.is_set():
                        return
                    try:
                        self.wfile.write(body[i:i + 8192])
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    time.sleep(0.005)

        return H

    def close(self):
        self.stop_event.set()
        self._srv.shutdown()
        self._srv.server_close()


@pytest.fixture()
def fake_server():
    made = []

    def _make(behaviour: str, **kw):
        srv = FakeGear360Server(behaviour, **kw)
        made.append(srv)
        return srv

    yield _make
    for srv in made:
        srv.close()


# ---------------------------------------------------------------- unit tests
def test_backoff_policy_doubles_and_resets():
    from app.stream.parser import BackoffPolicy

    b = BackoffPolicy(base=1.0, factor=2.0, max_delay=15.0)
    assert [b.next_delay() for _ in range(6)] == [1, 2, 4, 8, 15, 15]
    b.reset()
    assert b.next_delay() == 1


def test_icmp_ping_never_crashes():
    """Regression for DG-20260903-001 step camera_ping:
    'module socket has no attribute getpid' crashed the whole step."""
    from app.diagnostics.probes import icmp_ping

    out = icmp_ping("127.0.0.1", timeout=0.5)
    assert isinstance(out, dict) and "ok" in out
    # on this sandbox raw sockets are usually permitted or unavailable —
    # either way the call must not raise and must explain itself
    assert "error" in out or "latency_ms" in out


# ------------------------------------------------------------ probe tests
def test_http_probe_reads_synthetic_stream(fake_server):
    from app.diagnostics.probes import http_probe

    srv = fake_server("stream")
    res = http_probe("127.0.0.1", srv.port, "/livestream_high.avi",
                     max_bytes=65_536, timeout=4.0)
    assert res["status"] == 206
    assert res["bytes_read"] > 0
    assert res["ok"] is True
    assert res["error"] == ""
    assert bytes.fromhex(res["first_bytes_hex"]).startswith(b"TTTS")


def test_http_probe_marks_headers_without_body(fake_server):
    from app.diagnostics.probes import http_probe

    srv = fake_server("stall", stall_hold=8.0)
    res = http_probe("127.0.0.1", srv.port, "/livestream_high.avi",
                     max_bytes=65_536, timeout=1.0)
    assert res["status"] == 206
    assert res["bytes_read"] == 0
    assert res.get("stalled") is True
    assert "HEADERS_WITHOUT_BODY" in res["error"]


def test_probe_variants_finds_alt_endpoint(fake_server):
    """If one style/endpoint streams while the main one stalls, the matrix
    must find it — that is the empirical evidence distinguishing a wedged
    session from a style-sensitive camera."""
    from app.diagnostics.probes import http_probe_variants

    srv = fake_server("alt_streams", stall_hold=4.0)
    res = http_probe_variants("127.0.0.1", srv.port, "/livestream_high.avi",
                              read_window=1.2)
    assert res["any_data"] is True
    assert res["all_stalled"] is False
    assert res["best"]["path"] == "/livestream_low.avi"
    assert res["best"]["bytes_read"] > 0


def test_probe_variants_all_stalled(fake_server):
    from app.diagnostics.probes import http_probe_variants

    srv = fake_server("stall", stall_hold=4.0)
    res = http_probe_variants("127.0.0.1", srv.port, "/livestream_high.avi",
                              read_window=1.0)
    assert res["any_data"] is False
    assert res["all_stalled"] is True
    assert len(res["variants"]) >= 4          # range/plain/close + alt path
    for v in res["variants"]:
        assert v["status"] == 206 and v["bytes_read"] == 0


def test_probe_variants_records_request_styles(fake_server):
    """The matrix must actually send the different styles (Range/plain/close)."""
    from app.diagnostics.probes import http_probe_variants

    srv = fake_server("stream")
    http_probe_variants("127.0.0.1", srv.port, "/livestream_high.avi",
                        alt_path="", read_window=3.0)
    ranges = [r["range"] for r in srv.requests]
    conns = [r["connection"] for r in srv.requests]
    assert "bytes=0-" in ranges                    # range style sent
    assert any(r is None for r in ranges)          # plain style sent
    assert any(c == "close" for c in conns)        # close style sent


# ------------------------------------------------- live reader recovery tests
def test_iter_camera_bytes_recovers_after_stall(fake_server):
    """THE regression for the reported outage: attempt 1 gets headers but no
    body; the reader must abort it, rotate the request style, and pull bytes
    from a later attempt instead of hanging forever."""
    from app.stream.parser import iter_camera_bytes

    srv = fake_server("stall_then_stream", stall_first_n=1, stall_hold=6.0)
    events: list[tuple] = []
    stop = threading.Event()
    cfg = {"host": "127.0.0.1", "port": srv.port, "path": "/livestream_high.avi",
           "request_timeout_seconds": 0.8, "reconnect_seconds": 0.2}

    gen = iter_camera_bytes(cfg, stop, on_event=lambda t, **d: events.append((t, d)))
    got = bytearray()
    try:
        for chunk in gen:
            got.extend(chunk)
            if len(got) >= 32_768:
                break
    finally:
        gen.close()
        stop.set()

    assert len(got) >= 32_768
    types = [t for t, _ in events]
    assert "stalled" in types, f"expected a stalled event, got {types}"
    assert "data" in types
    assert types.index("stalled") < types.index("data")
    # style rotated between the stalled attempt and the data attempt
    stalled_att = next(d["attempt"] for t, d in events if t == "stalled")
    data_att = next(d["attempt"] for t, d in events if t == "data")
    assert data_att > stalled_att
    # the reader actually sent the different styles
    styles = {r["range"] for r in srv.requests}
    assert "bytes=0-" in styles and None in styles


def test_iter_camera_bytes_reports_persistent_stall(fake_server):
    """After repeated stalled attempts the reader must emit stall_persist so
    the UI/diagnostics can tell the user WHY nothing moves."""
    from app.stream.parser import iter_camera_bytes

    srv = fake_server("stall", stall_hold=4.0)
    events: list[tuple] = []
    stop = threading.Event()
    cfg = {"host": "127.0.0.1", "port": srv.port, "path": "/livestream_high.avi",
           "request_timeout_seconds": 0.5, "reconnect_seconds": 0.2}

    gen = iter_camera_bytes(cfg, stop, on_event=lambda t, **d: events.append((t, d)))
    # consume in a helper thread so we can stop via stop_event (single-iterator
    # rule: never close() a generator owned by another thread)
    def run():
        try:
            for _ in gen:
                pass
        except Exception:
            pass
    th = threading.Thread(target=run, daemon=True)
    th.start()
    deadline = time.time() + 12
    while time.time() < deadline:
        if any(t == "stall_persist" for t, _ in events):
            break
        time.sleep(0.1)
    stop.set()
    th.join(timeout=3)

    persist = [d for t, d in events if t == "stall_persist"]
    assert persist, f"expected stall_persist, got {[t for t, _ in events]}"
    assert persist[0]["attempts"] >= 3


def test_iter_camera_bytes_backoff_grows_when_unreachable():
    """A refused connection must back off exponentially (old code hammered
    the camera every 1s — see stream.log 00:23-00:30)."""
    import socket as sockmod

    from app.stream.parser import iter_camera_bytes

    # find a closed port
    s = sockmod.socket()
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()

    events: list[tuple] = []
    stop = threading.Event()
    cfg = {"host": "127.0.0.1", "port": free_port, "path": "/x.avi",
           "request_timeout_seconds": 0.5, "reconnect_seconds": 0.2}
    gen = iter_camera_bytes(cfg, stop, on_event=lambda t, **d: events.append((t, d)))

    def run():
        try:
            for _ in gen:
                pass
        except Exception:
            pass
    th = threading.Thread(target=run, daemon=True)
    th.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        delays = [d["retry_in"] for t, d in events if t in ("interrupted", "stalled")]
        if len(delays) >= 3:
            break
        time.sleep(0.05)
    stop.set()
    th.join(timeout=3)

    delays = [d["retry_in"] for t, d in events if t in ("interrupted", "stalled")]
    assert len(delays) >= 3
    assert delays[1] > delays[0], f"backoff should grow: {delays}"


# --------------------------------------------------------- analyzer wiring
def test_analyzer_r11_camera_stream_stalled():
    from app.diagnostics.analyzer import analyze_findings

    ctx = {
        "tcp": {"ok": True, "host": "192.168.43.1", "port": 7679, "error": ""},
        "http": {"status": 206, "content_type": "video/x-avi ",
                 "bytes_read": 0, "variant": "range",
                 "error": "HEADERS_WITHOUT_BODY"},
        "http_variants": {
            "all_stalled": True, "any_data": False,
            "variants": [
                {"variant": "range", "path": "/livestream_high.avi", "status": 206,
                 "bytes_read": 0, "error": "HEADERS_WITHOUT_BODY"},
                {"variant": "plain", "path": "/livestream_high.avi", "status": 206,
                 "bytes_read": 0, "error": "HEADERS_WITHOUT_BODY"},
            ],
        },
    }
    findings = analyze_findings(ctx)
    r11 = next((f for f in findings if f["id"] == "R11"), None)
    assert r11 is not None
    assert r11["error_code"] == "CAMERA_STREAM_STALLED"
    assert r11["confidence"] == "HIGH"
    assert "power-cycle" in r11["suggested_action"].lower()
    # R2 still fires for transport-level evidence
    assert any(f["id"] == "R2" for f in findings)
