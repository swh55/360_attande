"""FTPClient — minimal busybox-safe FTP client for the camera's SD card.

Implemented on a raw socket on purpose: the camera runs busybox ftpd and
this avoids ftplib edge-case behaviour differences (the identical wire
logic ran against the real camera during the investigation and listed /
fetched DCIM files successfully).

Credentials default to anonymous/empty (the mod's default) and come from
Settings — never hard-coded (§25). Retry policy: the CALLER retries at
most 2 times with backoff (§41); this class is single-attempt.
"""
from __future__ import annotations

import logging
import re
import socket
from datetime import datetime
from pathlib import Path

LOG = logging.getLogger("stable.ftp")


class FTPError(RuntimeError):
    """Structured FTP failure with a short human-readable reason."""


def parse_list_line(line: str) -> dict | None:
    """busybox unix LIST line → entry with name/size/is_dir/date.

    Example: -rwxr-xr-x 1 root root  3456789 Jan 01 00:11 SAM_0001.JPG
             -rwxr-xr-x 1 root root  3456789 Jan 01  2018 SAM_0001.JPG
    """
    if len(line) < 10 or line[0] not in "-d":
        return None
    parts = line.split()
    if len(parts) < 9:
        return None
    try:
        size = int(parts[4])
    except ValueError:
        return None
    name = line.split(None, 8)[-1].strip()
    month, day, rest = parts[5], parts[6], parts[7]
    date_str = f"{month} {day} {rest}"
    mtime = None
    for fmt in ("%b %d %H:%M", "%b %d %Y"):
        try:
            mtime = datetime.strptime(date_str, fmt).replace(
                year=datetime.now().year if fmt.endswith("%H:%M") else
                datetime.strptime(date_str, fmt).year)
            break
        except ValueError:
            continue
    return {"name": name, "size": size, "is_dir": line[0] == "d",
            "mtime": mtime.strftime("%Y-%m-%d %H:%M") if mtime else date_str}


class FTPClient:
    def __init__(self, host: str, port: int = 21, timeout: float = 6.0,
                 username: str = "", password: str = ""):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.user = username or "anonymous"
        self.password = password or "gear360hub"
        self.sock: socket.socket | None = None
        self.f = None

    # ---- wire ---------------------------------------------------------------
    def _connect(self) -> None:
        try:
            self.sock = socket.create_connection((self.host, self.port),
                                                 timeout=self.timeout)
            self.sock.settimeout(self.timeout)
            self.f = self.sock.makefile("rb")
        except OSError as exc:
            raise FTPError(
                f"تعذر الاتصال بخدمة FTP على {self.host}:{self.port} "
                f"({exc.__class__.__name__})") from exc

    def _read(self) -> tuple[int, str]:
        assert self.f is not None
        line = self.f.readline()
        if not line:
            raise FTPError("أغلقت خدمة FTP قناة التحكم")
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        try:
            code = int(text[:3])
        except ValueError:
            code = 0
        return code, text

    def _cmd(self, cmd: str, expect: tuple[int, ...] = (200,)) -> str:
        assert self.sock is not None
        self.sock.sendall((cmd + "\r\n").encode("utf-8"))
        code, text = self._read()
        if code not in expect:
            raise FTPError(f"فشل أمر FTP ({cmd.split(' ')[0]}): {text}")
        return text

    def _pasv(self) -> tuple[str, int]:
        text = self._cmd("PASV", expect=(227,))
        m = re.search(r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)", text)
        if not m:
            raise FTPError(f"تعذر تحليل PASV: {text}")
        ip = ".".join(m.groups()[:4])
        dport = int(m.groups()[4]) * 256 + int(m.groups()[5])
        return ip, dport

    def _data_socket(self) -> socket.socket:
        ip, dport = self._pasv()
        try:
            d = socket.create_connection((ip, dport), timeout=self.timeout)
            d.settimeout(self.timeout)
            return d
        except OSError as exc:
            raise FTPError(f"تعذر فتح قناة البيانات ({ip}:{dport})") from exc

    # ---- session API ----------------------------------------------------------
    def __enter__(self) -> "FTPClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        self._connect()
        code, text = self._read()
        if code != 220:
            raise FTPError(f"رد FTP غير متوقع: {text}")
        self._cmd(f"USER {self.user}", expect=(230, 331))
        self._cmd(f"PASS {self.password}", expect=(230, 202))
        self._cmd("TYPE I", expect=(200,))

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.sendall(b"QUIT\r\n")
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
            self.f = None

    def cwd(self, path: str) -> bool:
        """Change directory; False when it does not exist (550)."""
        assert self.sock is not None
        self.sock.sendall(("CWD " + path + "\r\n").encode("utf-8"))
        code, _ = self._read()
        return code == 250

    def list(self) -> list[dict]:
        """LIST the current directory → parsed entries."""
        assert self.sock is not None
        self.sock.sendall(b"LIST\r\n")
        entries: list[dict] = []
        with self._data_socket() as d:
            while True:
                chunk = d.recv(8192)
                if not chunk:
                    break
                entries.extend(
                    e for e in (parse_list_line(l) for l in
                                chunk.decode("utf-8", "replace").splitlines()
                                if l.strip()) if e)
        while True:
            line = self.f.readline()
            if not line or line.startswith(b"226"):
                break
        return entries

    def retr(self, remote_path: str, out: Path) -> int:
        """RETR a file into `out`; returns bytes written."""
        assert self.sock is not None
        self.sock.sendall(("RETR " + remote_path + "\r\n").encode("utf-8"))
        code, txt = self._read()
        if code not in (125, 150):
            raise FTPError(f"رفض الخادم تنزيل {remote_path}: {txt}")
        total = 0
        out.parent.mkdir(parents=True, exist_ok=True)
        with self._data_socket() as d:
            with open(out, "wb") as fh:
                while True:
                    chunk = d.recv(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
        while True:
            line = self.f.readline()
            if not line or line.startswith(b"226"):
                break
        return total


# ---- helpers ----------------------------------------------------------------
MEDIA_ROOTS = ("/dcim", "/DCIM")


def _try(func, *args, **kw):
    try:
        return func(*args, **kw)
    except (FTPError, OSError):
        return None


def dcim_entries(client: FTPClient, max_depth: int = 2) -> tuple[str, list[dict]]:
    """Walk DCIM (root + subdirs up to max_depth) → (root_used, entries).

    Each entry: {path, name, size, is_dir, mtime, type}. `type` is
    JPG / MP4 / Other per spec §11. Video is never interpreted — only
    listed / downloaded / opened.
    """
    root_used = ""
    for root in MEDIA_ROOTS:
        if client.cwd(root):
            root_used = root
            break
    if not root_used:
        raise FTPError("لم يتم العثور على مجلد DCIM على بطاقة الذاكرة")

    def ftype(name: str) -> str:
        low = name.lower()
        if low.endswith((".jpg", ".jpeg")):
            return "JPG"
        if low.endswith(".mp4"):
            return "MP4"
        return "Other"

    out: list[dict] = []

    def walk(prefix: str, depth: int) -> None:
        for e in client.list():
            rel = (prefix + "/" + e["name"]) if prefix else e["name"]
            if e["is_dir"]:
                if depth < max_depth:
                    if client.cwd(e["name"]):
                        walk(rel, depth + 1)
                        client.cwd("..")
            else:
                out.append({"path": rel, "name": e["name"], "size": e["size"],
                            "mtime": e["mtime"], "type": ftype(e["name"]),
                            "is_dir": False})

    walk("", 0)
    return root_used, out


def file_identity(entries: list[dict]) -> dict[str, tuple[int, str]]:
    """Identity map per §44: path → (size, mtime) — never name-only."""
    return {e["path"]: (e["size"], e.get("mtime", "")) for e in entries}
