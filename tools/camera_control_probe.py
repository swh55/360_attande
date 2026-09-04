#!/usr/bin/env python3
"""PHASE 4 — Camera-side control probe (task doc §16-18). NEVER touches 7679.

Proves the camera CONTROL plane independently of live streaming:
  * Telnet 23 reachable (gear360_modding shell)
  * FTP 21 reachable + DCIM listable
  * HTTP 8888 reachable (mod's busybox httpd) + /cgi-bin/execute available
  * Take Photo: trigger `st key click s2` via HTTP 8888 (documented in the
    mod's own top.html) or Telnet, wait for SD write, detect the NEW file
    over FTP, download it into diagnostics/raw/photos/

A PASS here proves: camera boots, Wi-Fi, mod services, SD write, and the
`st` command engine all work — independent from the stream problem.
"""
from __future__ import annotations

import re
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g360_common import (RAW_DIR, ensure_dirs, fail, hr, info, ok,
                         save_evidence, verdict, warn)

PHOTOS_DIR = RAW_DIR / "photos"
_IAC = 0xFF


# ------------------------------------------------------------------ telnet
def telnet_command(host: str, port: int, command: str, timeout: float = 4.0,
                   wait_after: float = 1.2) -> str:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        buf = bytearray()

        def recv_until_idle(budget: float, idle: float = 0.35) -> None:
            t0 = time.time()
            sock.settimeout(idle)
            while time.time() - t0 < budget:
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    return
                except OSError:
                    return
                if not data:
                    return
                buf.extend(data)

        recv_until_idle(1.2)
        out = bytearray()
        i = 0
        while i < len(buf):
            b = buf[i]
            if b == _IAC and i + 2 < len(buf):
                cmd, opt = buf[i + 1], buf[i + 2]
                if cmd in (0xFD, 0xFB):
                    out += bytes([_IAC, 0xFE if cmd == 0xFD else 0xFC, opt])
                i += 3
                continue
            i += 1
        if out:
            sock.sendall(bytes(out))
        sock.sendall(command.encode("ascii", "replace") + b"\r\n")
        buf.clear()
        recv_until_idle(wait_after)
        return bytes(buf).decode("ascii", "replace")[-600:]


# --------------------------------------------------------------------- ftp
class MiniFtp:
    def __init__(self, host: str, port: int, timeout: float = 6.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.f = self.sock.makefile("rb")

    def _read(self) -> tuple[int, str]:
        line = self.f.readline()
        if not line:
            raise OSError("FTP closed control connection")
        text = line.decode("ascii", "replace").rstrip("\r\n")
        try:
            return int(text[:3]), text
        except ValueError:
            return 0, text

    def _cmd(self, cmd: str, expect=(200,)) -> str:
        self.sock.sendall((cmd + "\r\n").encode("ascii"))
        code, text = self._read()
        if code not in expect:
            raise OSError(f"{cmd.split()[0]} → {text}")
        return text

    def login(self) -> None:
        code, text = self._read()
        if code != 220:
            raise OSError(f"banner: {text}")
        self._cmd("USER anonymous", expect=(230, 331))
        self._cmd("PASS g360probe", expect=(230, 202))
        self._cmd("TYPE I")

    def cwd(self, path: str) -> bool:
        self.sock.sendall(("CWD " + path + "\r\n").encode("ascii"))
        code, _ = self._read()
        return code == 250

    def pasv(self) -> tuple[str, int]:
        text = self._cmd("PASV", expect=(227,))
        m = re.search(r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)", text)
        if not m:
            raise OSError(f"PASV parse: {text}")
        ip = ".".join(m.groups()[:4])
        return ip, int(m.groups()[4]) * 256 + int(m.groups()[5])

    def list(self) -> list[dict]:
        ip, dport = self.pasv()
        self.sock.sendall(b"LIST\r\n")
        entries = []
        with socket.create_connection((ip, dport), timeout=6) as ds:
            ds.settimeout(6)
            while True:
                chunk = ds.recv(8192)
                if not chunk:
                    break
                entries += [self._parse(l) for l in
                            chunk.decode("utf-8", "replace").splitlines() if l.strip()]
        while True:
            line = self.f.readline()
            if not line or line.startswith(b"226"):
                break
        return [e for e in entries if e]

    @staticmethod
    def _parse(line: str):
        if len(line) < 10 or line[0] not in "-d":
            return None
        parts = line.split()
        if len(parts) < 9:
            return None
        try:
            size = int(parts[4])
        except ValueError:
            return None
        return {"name": line.split(None, 8)[-1].strip(), "size": size,
                "is_dir": line[0] == "d"}

    def retr(self, path: str, out: Path) -> int:
        ip, dport = self.pasv()
        self.sock.sendall(("RETR " + path + "\r\n").encode("ascii"))
        code, txt = self._read()
        if code not in (125, 150):
            raise OSError(f"RETR refused: {txt}")
        n = 0
        with socket.create_connection((ip, dport), timeout=15) as ds:
            ds.settimeout(15)
            with open(out, "wb") as fh:
                while True:
                    chunk = ds.recv(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    n += len(chunk)
        while True:
            line = self.f.readline()
            if not line or line.startswith(b"226"):
                break
        return n

    def quit(self) -> None:
        try:
            self.sock.sendall(b"QUIT\r\n")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def newest_jpg(ftp: MiniFtp):
    name_re = re.compile(r"^(SAM|SEC|100)[_\-]?\d+\.jpe?g$", re.IGNORECASE)
    best = None
    for root in ("/dcim", "/DCIM"):
        if ftp.cwd(root):
            subs = [e for e in ftp.list() if e["is_dir"]] or [{"name": None}]
            for sd in subs:
                if sd["name"] and not ftp.cwd(sd["name"]):
                    continue
                for e in ftp.list():
                    if e["is_dir"] or e["size"] <= 0:
                        continue
                    if not name_re.match(e["name"]) and not e["name"].lower().endswith(".jpg"):
                        continue
                    key = e["name"].upper()
                    if best is None or key > best["name"].upper():
                        best = {**e, "path": (sd["name"] + "/" if sd["name"] else "") + e["name"]}
                if sd["name"]:
                    ftp.cwd("..")
            return best
    return best


def http_get(host: str, port: int, path: str, timeout: float = 5.0) -> dict:
    out = {"url": f"http://{host}:{port}{path}"}
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(f"GET {path} HTTP/1.0\r\n\r\n".encode())
            data = b""
            while len(data) < 65536:
                chunk = s.recv(8192)
                if not chunk:
                    break
                data += chunk
        head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1", "replace")
        out["status_line"] = head.split("\r\n")[0] if head else "(empty)"
        out["bytes"] = len(data)
        out["ok"] = data.startswith(b"HTTP")
    except Exception as exc:
        out["ok"] = False
        out["error"] = f"{exc.__class__.__name__}: {exc}"
    return out


def tcp_open(host: str, port: int, timeout: float = 2.5) -> dict:
    try:
        t0 = time.time()
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "ms": round((time.time() - t0) * 1000, 1)}
    except OSError as exc:
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Camera CONTROL probe (23/21/8888) — never touches 7679")
    ap.add_argument("--host", default="192.168.43.1")
    ap.add_argument("--telnet-port", type=int, default=23)
    ap.add_argument("--ftp-port", type=int, default=21)
    ap.add_argument("--http-port", type=int, default=8888)
    ap.add_argument("--photo", action="store_true",
                    help="actually trigger Take Photo (st key click s2) and download the new file")
    ap.add_argument("--via", choices=("http", "telnet"), default="http",
                    help="transport for the shutter command (default http = mod's execute CGI)")
    args = ap.parse_args()
    ensure_dirs()
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

    host = args.host
    ev: dict = {"host": host}
    hr(f"PHASE 4 — CAMERA CONTROL PROBE  ({host} — ports 23/21/8888, never 7679)")

    # 1) transports
    ev["telnet"] = tcp_open(host, args.telnet_port)
    ev["ftp"] = tcp_open(host, args.ftp_port)
    ev["http8888"] = tcp_open(host, args.http_port)
    ok(f"telnet 23 : {ev['telnet']}") if ev["telnet"]["ok"] else fail(f"telnet 23 : {ev['telnet']}")
    ok(f"ftp 21    : {ev['ftp']}") if ev["ftp"]["ok"] else fail(f"ftp 21    : {ev['ftp']}")
    ok(f"http 8888 : {ev['http8888']}") if ev["http8888"]["ok"] else fail(f"http 8888 : {ev['http8888']}")

    # 2) HTTP 8888 content (mod's www root + execute CGI)
    ev["http_root"] = http_get(host, args.http_port, "/")
    ev["http_exec"] = http_get(host, args.http_port,
                               "/cgi-bin/execute?rawcmd=echo%20g360probe-ok")
    info(f"8888 /            → {ev['http_root'].get('status_line', ev['http_root'].get('error'))}")
    info(f"8888 execute CGI  → {ev['http_exec'].get('status_line', ev['http_exec'].get('error'))} "
         f"({ev['http_exec'].get('bytes', 0)} B)")

    # 3) FTP DCIM listing
    baseline = None
    if ev["ftp"]["ok"]:
        try:
            ftp = MiniFtp(host, args.ftp_port)
            ftp.login()
            best = newest_jpg(ftp)
            baseline = best["path"] if best else None
            ftp.quit()
            ok(f"FTP DCIM listable — newest photo: {baseline or '(none found)'}")
            ev["ftp_baseline"] = baseline
        except Exception as exc:
            fail(f"FTP listing failed: {exc}")
            ev["ftp_list_error"] = str(exc)

    # 4) Take Photo (optional)
    if args.photo:
        hr("TAKE PHOTO (independent of live stream)")
        cmd = "st key click s2"
        try:
            if args.via == "http":
                import urllib.parse
                url = "/cgi-bin/execute?rawcmd=" + urllib.parse.quote(cmd)
                r = http_get(host, args.http_port, url, timeout=8.0)
                info(f"shutter via HTTP 8888: {r.get('status_line')} ({r.get('bytes', 0)} B)")
                ev["shutter_via"] = "http8888"
            else:
                tail = telnet_command(host, args.telnet_port, cmd)
                info(f"shutter via telnet; reply tail: {tail[-160:]!r}")
                ev["shutter_via"] = "telnet"
            ev["shutter_command"] = cmd
        except Exception as exc:
            fail(f"shutter command failed: {exc}")
            ev["shutter_error"] = str(exc)

        info("waiting 3s for SD write …")
        time.sleep(3.0)
        new_file = None
        deadline = time.time() + 12
        while time.time() < deadline and new_file is None:
            try:
                ftp = MiniFtp(host, args.ftp_port)
                ftp.login()
                best = newest_jpg(ftp)
                ftp.quit()
                if best and best["path"] != baseline:
                    new_file = best
            except Exception as exc:
                warn(f"scan retry: {exc}")
                time.sleep(1.0)
        if new_file is None:
            fail("no NEW photo appeared on SD — shutter command not executed by camera")
            ev["take_photo"] = {"ok": False, "baseline": baseline}
        else:
            local = PHOTOS_DIR / new_file["name"]
            try:
                ftp = MiniFtp(host, args.ftp_port)
                ftp.login()
                for part in new_file["path"].split("/")[:-1]:
                    ftp.cwd(part)
                n = ftp.retr(new_file["path"].split("/")[-1], local)
                ftp.quit()
                ok(f"photo downloaded: {local.name} ({n} B)")
                ev["take_photo"] = {"ok": n > 0, "file": new_file["path"],
                                    "bytes": n, "local": str(local)}
            except Exception as exc:
                fail(f"download failed: {exc}")
                ev["take_photo"] = {"ok": False, "file": new_file["path"],
                                    "error": str(exc)}
    # 5) verdict
    control_ok = ev["telnet"]["ok"] or ev["http8888"]["ok"]
    ftp_ok = ev["ftp"]["ok"]
    if args.photo:
        photo_ok = ev.get("take_photo", {}).get("ok", False)
        if control_ok and ftp_ok and photo_ok:
            verdict("CAMERA CONTROL FULLY WORKING (telnet/http + FTP + real photo)")
            ret = 0
        elif control_ok and ftp_ok:
            verdict("control transports OK but photo pipeline failed — see take_photo evidence")
            ret = 1
        else:
            verdict("camera control plane unavailable (mod services not running?)")
            ret = 1
    else:
        verdict(f"control plane: {'OK' if control_ok and ftp_ok else 'DEGRADED'} "
                f"(run with --photo to prove a real capture)")
        ret = 0 if control_ok and ftp_ok else 1
    save_evidence("camera_control_probe", ev)
    return ret


if __name__ == "__main__":
    sys.exit(main())
