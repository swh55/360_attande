"""CameraControl: real Take Photo through the gear360_modding services.

The camera-side mod (ottokiksmaler/gear360_modding family) exposes:
  * Telnet on port 23  — busybox shell; the documented capture trigger is
    the key-emulation command  `st key click s2`  (s2 = shutter).
  * FTP on port 21     — anonymous access to the SD card (DCIM tree).

Take Photo pipeline (independent from Live Preview / FrameBus):
  Hub → Telnet `st key click s2` → camera captures → file written on SD
  → Hub lists DCIM over FTP, detects the NEW file → downloads it →
  saved into the photo library → Gallery shows it.

Design rules:
  * no invented protocols — only the mod's documented services;
  * structured errors (never fake success): the UI shows exactly which
    stage failed (transport / command / detection / download);
  * the live stream session is NOT touched (Telnet/FTP are separate
    single-session services from the 7679 stream).
"""
from __future__ import annotations

import re
import socket
import time
from pathlib import Path
from typing import Optional

from ..core import database as db
from ..core import settings as settings_svc
from ..core.bus import bus as event_bus
from ..core.logging_setup import get
from ..core.paths import DIRS

LOG = get("camera.control")

# structured error codes surfaced to the UI
E_TRANSPORT = "CAMERA_CONTROL_UNAVAILABLE"
E_COMMAND = "CAMERA_COMMAND_FAILED"
E_NOT_FOUND = "PHOTO_NOT_FOUND_ON_SD"
E_DOWNLOAD = "PHOTO_DOWNLOAD_FAILED"
E_DISABLED = "CAMERA_CONTROL_DISABLED"


class ControlError(RuntimeError):
    """Carries a machine-readable code for the structured API error body."""

    def __init__(self, code: str, message: str, **evidence):
        super().__init__(message)
        self.code = code
        self.evidence = dict(evidence)


# ---------------------------------------------------------------- telnet
_IAC = 0xFF


def _telnet_command(host: str, port: int, command: str,
                    timeout: float = 4.0, wait_after: float = 1.0) -> str:
    """Open a raw telnet session, absorb IAC negotiation, run one command.

    Returns everything the server echoed back (tail, bounded). Raises
    ControlError(E_TRANSPORT) when the shell is not reachable.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            buf = bytearray()

            def recv_until_idle(budget_s: float, idle: float = 0.35) -> None:
                """Read whatever arrives until the line goes quiet."""
                t0 = time.time()
                sock.settimeout(idle)
                while time.time() - t0 < budget_s:
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        return
                    except OSError:
                        return
                    if not data:
                        return
                    buf.extend(data)

            # absorb banner + IAC option negotiation (respond WONT to all)
            recv_until_idle(1.2)
            out = bytearray()
            i = 0
            while i < len(buf):
                b = buf[i]
                if b == _IAC and i + 2 < len(buf):
                    cmd, opt = buf[i + 1], buf[i + 2]
                    if cmd in (0xFD, 0xFB):        # DO / WILL -> answer WONT/DONT
                        out += bytes([_IAC, 0xFE if cmd == 0xFD else 0xFC, opt])
                    i += 3
                    continue
                i += 1
            if out:
                sock.sendall(bytes(out))
            # send the capture command
            sock.sendall(command.encode("ascii", "replace") + b"\r\n")
            LOG.info("CONTROL: telnet %s:%s sent %r", host, port, command)
            buf.clear()
            recv_until_idle(wait_after)
            tail = bytes(buf).decode("ascii", "replace")[-400:]
            LOG.info("CONTROL: telnet reply tail: %s", tail.replace("\n", " | ")[:200])
            return tail
    except ControlError:
        raise
    except OSError as exc:
        raise ControlError(
            E_TRANSPORT,
            f"Telnet shell on {host}:{port} is not reachable ({exc.__class__.__name__}: {exc}). "
            "The gear360_modding services must be installed and the camera on its Wi-Fi.",
            host=host, port=port, stage="telnet") from exc


# ------------------------------------------------------------------ ftp
class _FtpSession:
    """Minimal FTP client (anonymous) — just enough to list + fetch files.

    Supports: USER/PASS, TYPE I, PWD, CWD, LIST, PASV+RETR. Implemented on a
    raw socket on purpose: the camera runs busybox ftpd and this avoids
    adding dependencies or ftplib edge-case behaviour differences.
    """

    def __init__(self, host: str, port: int, timeout: float = 6.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.f = self.sock.makefile("rb")
        self.host = host
        self.port = port

    def _read(self) -> tuple[int, str]:
        line = self.f.readline()
        if not line:
            raise ControlError(E_TRANSPORT, "FTP closed the control connection",
                               stage="ftp")
        text = line.decode("ascii", "replace").rstrip("\r\n")
        try:
            code = int(text[:3])
        except ValueError:
            code = 0
        return code, text

    def _cmd(self, cmd: str, expect: tuple = (200,)) -> str:
        self.sock.sendall((cmd + "\r\n").encode("ascii"))
        code, text = self._read()
        if code not in expect:
            raise ControlError(E_COMMAND, f"FTP command {cmd.split(' ')[0]} "
                               f"failed: {text}", stage="ftp", ftp_code=code)
        return text

    def login(self) -> None:
        code, text = self._read()
        if code != 220:
            raise ControlError(E_TRANSPORT, f"FTP banner unexpected: {text}", stage="ftp")
        self._cmd("USER anonymous", expect=(230, 331))
        self._cmd("PASS gear360hub", expect=(230, 202))
        self._cmd("TYPE I", expect=(200,))

    def cwd(self, path: str) -> bool:
        """Change dir; return False when it does not exist (550)."""
        self.sock.sendall(("CWD " + path + "\r\n").encode("ascii"))
        code, _ = self._read()
        return code == 250

    def list(self) -> list[dict]:
        """LIST current dir → [{name, size, is_dir}] (busybox unix listing)."""
        code, text = self._cmd("PASV", expect=(227,))
        m = re.search(r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)", text)
        if not m:
            raise ControlError(E_TRANSPORT, f"PASV parse failed: {text}", stage="ftp")
        ip = ".".join(m.groups()[:4])
        dport = int(m.groups()[4]) * 256 + int(m.groups()[5])
        self.sock.sendall(b"LIST\r\n")
        entries: list[dict] = []
        with socket.create_connection((ip, dport), timeout=6) as dsock:
            dsock.settimeout(6)
            while True:
                chunk = dsock.recv(8192)
                if not chunk:
                    break
                entries.extend(_parse_list_line(l) for l in
                               chunk.decode("utf-8", "replace").splitlines()
                               if l.strip())
        while True:
            line = self.f.readline()
            if not line or line.startswith(b"226"):
                break
        return [e for e in entries if e]

    def retr(self, path: str, out: Path) -> int:
        """RETR a file into out; returns bytes written."""
        code, text = self._cmd("PASV", expect=(227,))
        m = re.search(r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)", text)
        if not m:
            raise ControlError(E_TRANSPORT, f"PASV parse failed: {text}", stage="ftp")
        ip = ".".join(m.groups()[:4])
        dport = int(m.groups()[4]) * 256 + int(m.groups()[5])
        self.sock.sendall(("RETR " + path + "\r\n").encode("ascii"))
        code, txt = self._read()
        if code not in (125, 150):
            raise ControlError(E_DOWNLOAD, f"RETR {path} refused: {txt}",
                               stage="ftp", file=path)
        total = 0
        with socket.create_connection((ip, dport), timeout=15) as dsock:
            dsock.settimeout(15)
            with open(out, "wb") as fh:
                while True:
                    chunk = dsock.recv(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
        while True:
            line = self.f.readline()
            if not line or line.startswith(b"226"):
                break
        return total

    def quit(self) -> None:
        try:
            self.sock.sendall(b"QUIT\r\n")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _parse_list_line(line: str) -> Optional[dict]:
    """busybox unix LIST line → entry. Example:
    -rwxr-xr-x 1 root root  3456789 Jan 01 00:11 SAM_0001.JPG"""
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
    return {"name": name, "size": size, "is_dir": line[0] == "d"}


def _find_newest_jpg(ftp: _FtpSession) -> Optional[dict]:
    """Walk the DCIM tree (1 level of subdirs, typical 100PHOTO) and return
    the newest JPG by name order (SAM_xxxx / 100_xxxx numbers increase)."""
    best: dict = {}
    name_re = re.compile(r"^(SAM|SEC|100)[_\-]?\d+\.jpe?g$", re.IGNORECASE)

    def consider(entry: dict, path: str) -> None:
        if entry["is_dir"] or entry["size"] <= 0:
            return
        if not name_re.match(entry["name"]) and not entry["name"].lower().endswith(".jpg"):
            return
        key = entry["name"].upper()
        if not best or key > best["path"].upper().split("/")[-1]:
            best.update({"path": path, "size": entry["size"], "name": entry["name"]})

    roots = ["/dcim", "/DCIM"]
    for root in roots:
        if ftp.cwd(root):
            subs = [e for e in ftp.list() if e["is_dir"]]
            subdirs = [e["name"] for e in subs] or [None]
            for sd in subdirs:
                if sd and not ftp.cwd(sd):
                    continue
                for e in ftp.list():
                    consider(e, (sd + "/" if sd else "") + e["name"])
                if sd:
                    ftp.cwd("..")
            return best or None
    return None


# ------------------------------------------------------------- take photo
def control_cfg() -> dict:
    return {
        "enabled": bool(settings_svc.get("camera.control.enabled", True)),
        "host": settings_svc.get("camera.host"),
        "telnet_port": int(settings_svc.get("camera.control.telnet_port", 23)),
        "ftp_port": int(settings_svc.get("camera.control.ftp_port", 21)),
        "command": str(settings_svc.get("camera.control.command", "st key click s2")),
        "settle_ms": int(settings_svc.get("camera.control.settle_ms", 2500)),
    }


def take_photo() -> dict:
    """Trigger a REAL camera capture and sync the new file into the gallery.

    Returns {id, filename, path, size, source:"camera_sd"} on success.
    Raises ControlError with a structured code on any failure.
    """
    cfg = control_cfg()
    if not cfg["enabled"]:
        raise ControlError(E_DISABLED, "Camera control is disabled in Settings.")
    host, tport, fport = cfg["host"], cfg["telnet_port"], cfg["ftp_port"]

    # 1) baseline: newest photo currently on the SD (so we detect the NEW file)
    baseline: Optional[str] = None
    ftp: Optional[_FtpSession] = None
    try:
        ftp = _FtpSession(host, fport)
        ftp.login()
        found = _find_newest_jpg(ftp)
        baseline = found["path"] if found else None
    except (ControlError, OSError) as exc:
        # baseline is best-effort: FTP may be down while telnet works — the
        # structured transport error (if any) surfaces at the command stage
        LOG.warning("CONTROL: baseline FTP scan failed: %s", exc)
    finally:
        if ftp is not None:
            ftp.quit()
            ftp = None

    # 2) trigger the shutter via the mod's key-emulation command
    _telnet_command(host, tport, cfg["command"], wait_after=1.0)

    # 3) give the camera time to write the file to the SD
    settle = max(0.5, cfg["settle_ms"] / 1000.0)
    LOG.info("CONTROL: waiting %.1fs for the camera to write the file", settle)
    time.sleep(settle)

    # 4) locate the new file over FTP (prefer a file newer than baseline)
    deadline = time.time() + 8.0
    new_file: Optional[dict] = None
    last_err: Optional[str] = None
    while time.time() < deadline and new_file is None:
        try:
            ftp = _FtpSession(host, fport)
            ftp.login()
            newest = _find_newest_jpg(ftp)
            if newest and newest["path"] != baseline:
                new_file = newest
            elif newest and baseline is None:
                # no baseline (scan failed) — take the newest file as best effort
                new_file = newest
        except (ControlError, OSError) as exc:
            last_err = str(exc)
            LOG.warning("CONTROL: SD scan retry failed: %s", exc)
        finally:
            if ftp is not None:
                ftp.quit()
                ftp = None
        if new_file is None:
            time.sleep(1.0)
    if new_file is None:
        raise ControlError(
            E_NOT_FOUND,
            "Capture command was sent, but no new photo appeared on the SD card. "
            "The camera may be in a mode that ignores the shutter command "
            "(e.g. playback) or the SD write failed.",
            baseline=baseline, last_error=last_err or "", stage="detect")

    # 5) download it
    now = time.localtime()
    rel_dir = Path("photos") / "camera" / time.strftime("%Y%m%d", now)
    abs_dir = DIRS["base"] / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    local = abs_dir / new_file["name"]
    try:
        ftp = _FtpSession(host, fport)
        ftp.login()
        ftp.cwd("/dcim" if ftp.cwd("/dcim") else "/DCIM")
        subdir = new_file["path"].split("/")[0]
        if "/" in new_file["path"] and not ftp.cwd(subdir):
            raise ControlError(E_DOWNLOAD, f"DCIM subdir {subdir} vanished", file=new_file["path"])
        n = ftp.retr(new_file["path"].split("/")[-1], local)
    except ControlError:
        raise
    except OSError as exc:
        raise ControlError(E_DOWNLOAD, f"FTP download failed: {exc}",
                           file=new_file["path"], stage="download") from exc
    finally:
        if ftp is not None:
            ftp.quit()
    if n <= 0 or n != new_file["size"] and n < 1024:
        raise ControlError(E_DOWNLOAD, f"Downloaded {n} bytes, expected {new_file['size']}",
                           file=new_file["path"], stage="download")

    # 6) register in the photo library (Gallery) + thumbnail
    import cv2
    img = cv2.imread(str(local))
    h = img.shape[0] if img is not None else None
    w = img.shape[1] if img is not None else None
    thumb_path = ""
    if img is not None:
        th_dir = DIRS["thumbs"]
        thumb_path = str(th_dir / f"cam_{int(time.time() * 1000)}.jpg")
        cv2.imwrite(thumb_path, cv2.resize(img, (640, 360)),
                    [cv2.IMWRITE_JPEG_QUALITY, 75])
    photo_id = db.execute(
        """INSERT INTO photos (path, thumb_path, filename, created_at, size_bytes,
           width, height, is_360, projection, note)
           VALUES (?, ?, ?, datetime('now','localtime'), ?, ?, ?, 1, 'raw',
                   'camera capture (SD sync)')""",
        (str(local), thumb_path, new_file["name"], n, w, h),
    )
    LOG.info("CONTROL: Take Photo OK — %s (%d bytes) → gallery id=%s",
             local, n, photo_id)
    event_bus.publish("photo_taken", {"id": photo_id, "path": str(local),
                                      "projection": "camera"})
    return {"id": photo_id, "filename": new_file["name"], "path": str(local),
            "size": n, "source": "camera_sd", "baseline": baseline,
            "note": "captured by the camera (st key click s2) and synced from the SD"}


def status() -> dict:
    """Availability probe of the control transports (telnet/ftp) — used by
    the UI to show whether Take Photo can work right now. Never touches the
    live stream endpoint."""
    cfg = control_cfg()
    out = {"enabled": cfg["enabled"], "host": cfg["host"], "command": cfg["command"],
           "telnet": {"port": cfg["telnet_port"], "ok": False},
           "ftp": {"port": cfg["ftp_port"], "ok": False}}
    for key in ("telnet", "ftp"):
        port = out[key]["port"]
        try:
            with socket.create_connection((cfg["host"], port), timeout=2.0):
                out[key]["ok"] = True
        except OSError as exc:
            out[key]["error"] = f"{exc.__class__.__name__}: {exc}"
    out["available"] = bool(cfg["enabled"] and out["telnet"]["ok"] and out["ftp"]["ok"])
    return out
