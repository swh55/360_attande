"""TelnetSession — raw-socket telnet client for the camera's busybox shell.

Sequence (spec §23), each step with its own timeout:
    connect 23 → absorb banner + IAC negotiation → wait "login:" →
    send root → wait "Password:" → send empty line → wait shell prompt →
    send command → read echo → close

If the unit boots straight to a shell (no login prompt), the command is
sent directly — both variants of gear360_modding are supported.

The telnet transport NEVER touches port 7679 and is used exclusively for
the documented mod command (st key click s2 / read-only inspection).
"""
from __future__ import annotations

import logging
import socket
import time

LOG = logging.getLogger("stable.telnet")

_IAC = 0xFF
_IAC_WONT = 0xFE
_IAC_DONT = 0xFC

PROMPTS = ("#", "$", ">")
LOGIN_WORDS = ("login:", "Login:")
PASS_WORDS = ("Password:", "password:")


class TelnetError(RuntimeError):
    """Structured telnet failure: step + short human reason."""


class TelnetSession:
    def __init__(self, host: str, port: int = 23, timeout: float = 5.0,
                 username: str = "root", password: str = ""):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.username = username
        self.password = password
        self.sock: socket.socket | None = None
        self.log_tail = ""

    # ---- low level ---------------------------------------------------------
    def _connect(self) -> None:
        try:
            self.sock = socket.create_connection((self.host, self.port),
                                                 timeout=self.timeout)
            self.sock.settimeout(self.timeout)
        except OSError as exc:
            raise TelnetError(
                f"تعذر الاتصال بمنفذ Telnet {self.host}:{self.port} "
                f"({exc.__class__.__name__})") from exc

    def _recv_until(self, wanted: tuple[str, ...], budget_s: float,
                    idle: float = 0.4) -> str:
        """Read until one of `wanted` substrings appears or budget expires."""
        buf = bytearray()
        t0 = time.time()
        assert self.sock is not None
        while time.time() - t0 < budget_s:
            self.sock.settimeout(idle)
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            # strip IAC negotiation inline (answer WONT/DONT, keep text)
            clean = bytearray()
            i = 0
            while i < len(data):
                b = data[i]
                if b == _IAC and i + 2 < len(data):
                    cmd, opt = data[i + 1], data[i + 2]
                    if cmd in (0xFD, 0xFB):  # DO/WILL
                        self.sock.sendall(bytes(
                            [_IAC, _IAC_WONT if cmd == 0xFD else _IAC_DONT, opt]))
                    i += 3
                    continue
                clean.append(b)
                i += 1
            buf.extend(clean)
            text = buf.decode("utf-8", "replace")
            if any(w in text for w in wanted):
                return text
        return buf.decode("utf-8", "replace")

    def _send(self, line: str) -> None:
        assert self.sock is not None
        self.sock.sendall(line.encode("utf-8", "replace") + b"\r\n")

    # ---- session -----------------------------------------------------------
    def login(self) -> str:
        """Drive the login dance; returns everything seen so far."""
        self._connect()
        banner = self._recv_until(LOGIN_WORDS + PROMPTS, budget_s=2.5)
        if any(w in banner for w in LOGIN_WORDS):
            self._send(self.username or "root")
            echo = self._recv_until(PASS_WORDS, budget_s=2.5)
            banner += echo
            if any(w in echo for w in PASS_WORDS):
                self._send(self.password or "")   # mod default: empty password
                banner += self._recv_until(PROMPTS, budget_s=2.5)
        return banner

    def command(self, cmd: str, wait_after: float = 1.2) -> str:
        """Run one command on an already-logged-in session."""
        if self.sock is None:
            raise TelnetError("الجلسة غير مفتوحة — استدعِ login أولاً")
        self._send(cmd)
        out = self._recv_until(PROMPTS, budget_s=max(wait_after, 1.0) + 1.5)
        self.log_tail = out[-400:]
        LOG.info("telnet %s:%s cmd=%r tail=%r", self.host, self.port, cmd,
                 self.log_tail[-120:])
        return out

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.sendall(b"exit\r\n")
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def run_command(host: str, port: int, cmd: str, timeout: float = 5.0,
                username: str = "root", password: str = "",
                wait_after: float = 1.2) -> dict:
    """One-shot: connect → login → command → close. Returns a report."""
    session = TelnetSession(host, port, timeout, username, password)
    try:
        seen = session.login()
        out = session.command(cmd, wait_after=wait_after)
        return {"ok": True, "echo": (seen + out)[-600:],
                "tail": session.log_tail}
    except TelnetError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        session.close()
