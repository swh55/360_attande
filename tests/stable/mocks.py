"""Shared mocks for the Stable Edition test-suite.

No test ever talks to a real camera. The final acceptance, however, is the
real hardware (README §35): these mocks only prove the Hub-side logic.
"""
from __future__ import annotations

from app.stable import ftp as ftp_mod


class FakeFTPClient:
    """In-memory FTPClient double with the same surface the app uses.

    `files` maps DCIM-relative paths to bytes, e.g.
        {"100PHOTO/SAM_0001.JPG": b"..."} — listings (including subdirs)
    are derived from the map, so walk() logic is exercised for real.
    """

    def __init__(self, files: dict[str, bytes] | None = None, fail: bool = False):
        self.files = files or {}
        self.fail = fail
        self.sub = ""            # current subdir inside /DCIM ("" = root)
        self.in_root = False
        self.downloaded: dict[str, bytes] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cwd(self, path: str) -> bool:
        if self.fail:
            return False
        if path in ("/dcim", "/DCIM"):
            self.in_root = True
            self.sub = ""
            return True
        if path == "..":
            self.sub = ""
            return True
        if path == "/":
            self.in_root = False
            self.sub = ""
            return True
        if not self.in_root:
            return False
        if any(key.split("/")[0] == path for key in self.files):
            self.sub = path
            return True
        return False

    def list(self) -> list[dict]:
        if self.fail:
            raise ftp_mod.FTPError("connection lost")
        prefix = self.sub + "/" if self.sub else ""
        out: list[dict] = []
        dirs_seen: set[str] = set()
        for key, data in self.files.items():
            rest = key[len(prefix):] if self.sub and key.startswith(prefix) else \
                (key if not self.sub else None)
            if rest is None:
                continue
            if "/" in rest:
                d = rest.split("/")[0]
                if d not in dirs_seen:
                    dirs_seen.add(d)
                    out.append({"name": d, "size": 0, "is_dir": True,
                                "mtime": "2026-09-01 09:00"})
            else:
                out.append({"name": rest, "size": len(data), "is_dir": False,
                            "mtime": "2026-09-02 12:00"})
        return out

    def retr(self, remote_path: str, out) -> int:
        key = (self.sub + "/" if self.sub else "") + remote_path
        data = self.files.get(key)
        if data is None or self.fail:
            raise ftp_mod.FTPError(f"no such file: {key}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        self.downloaded[key] = data
        return len(data)

    def close(self):
        pass


def sample_files():
    """Typical DCIM: subdir 100PHOTO with two JPGs + one MP4."""
    return {
        "100PHOTO/SAM_0001.JPG": b"\xff\xd8" + b"A" * 100,
        "100PHOTO/SAM_0002.JPG": b"\xff\xd8" + b"B" * 200,
        "100PHOTO/SAM_0003.MP4": b"\x00\x00\x00\x18ftypisom" + b"C" * 500,
    }
