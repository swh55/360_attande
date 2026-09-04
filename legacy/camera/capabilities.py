"""Capability Center — every Hub feature ranked by GUARANTEE of working.

Grounded EXCLUSIVELY in field evidence (ROOT-CAUSE-MAP.md RC-6/RC-7,
E13/E15/E16/E17 — the user's "camera stops and freezes" symptom):

  * The SM-R210 streamer (port 7679) is SINGLE-SHOT per arming: client #1
    receives a continuous ~9 Mbps body; every later client receives 206
    headers with ZERO body (E13, LEVEL 0).
  * There is NO self-heal (51 s window tested), NO newest-wins steal, NO
    late re-arm (E16, verdict NO_REARM_FOUND, LEVEL 0).
  * Repeated connection attempts push the camera through a TWO-STAGE
    wedge cascade (E17, LEVEL 0):
        stage 1  streamer wedged, control plane (23/21/8888) STILL alive
        stage 2  host wedged — even 8888 unreachable (WinError 10065);
                 only a physical power cycle recovers.

Therefore this module runs ONLY the non-invasive ladder:
  N1  control-plane ping        GET http://<host>:8888/     (harmless —
      proven alive even in stage 1, 11:57:59 evidence)
  N2  camera-side inspector     exec-CGI reads /proc/net/tcp and counts
      LISTEN + ESTABLISHED sockets on 7679  (pure file read on camera)
  N3  session broker status     in-process state, zero network

and classifies the camera:

  OFFLINE           N1 failed                      -> stage 2 (or powered
                       off / Wi-Fi gone). Nothing remote can help.
  SESSION_ACTIVE    broker holds a session OR an ESTABLISHED 7679 socket
                       exists on the camera -> live session running;
                       touching 7679 is FORBIDDEN (RC-5 rule).
  STREAMER_WEDGED   N1 ok + no 7679 LISTEN         -> stage 1: streamer
                       process gone/wedged; re-arm ladder may apply.
  IDLE_READY        N1 ok + LISTEN present + no established + broker free
                       -> idle. The arming state (fresh vs spent) is
                       UNKNOWABLE without the decisive probe, which
                       CONSUMES the arming if fresh — hence user-gated.

The tier manifest below is the single source of truth for the web UI
"Capability Center": features ordered by guarantee, each with live
status, evidence references and a safe action.
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.request

from ..core import settings as settings_svc
from .control import status as control_status
from .rearm import _cgi_exec, _root_ping
from .session_broker import get_broker
from ..core.logging_setup import get

LOG = get("camera.capabilities")

_STREAM_PORT_HEX = "1DFF"  # 7679

# exec-CGI command: list LISTEN + ESTABLISHED rows for port 7679 from
# /proc/net/tcp (state 0A = LISTEN, 01 = ESTABLISHED). Read-only, harmless.
_INSPECT_CMD = (
    "awk 'NR>1 && $2 ~ /:" + _STREAM_PORT_HEX + "$/ {print $4}' /proc/net/tcp | "
    "sort | uniq -c; echo done"
)

# camera classes
OFFLINE = "OFFLINE"
SESSION_ACTIVE = "SESSION_ACTIVE"
STREAMER_WEDGED = "STREAMER_WEDGED"
IDLE_READY = "IDLE_READY"

# guarantee levels (the user-facing ordering key)
G_ALWAYS = "always"            # works whenever the camera is powered
G_SESSION = "while_session"    # guaranteed while the one session is alive
G_CONTROL = "control_plane"    # high confidence; independent of the stream
G_BLOCKED = "blocked"          # firmware-blocked, evidence-backed


def _inspect_session_sockets(host: str) -> dict:
    """Count 7679 LISTEN/ESTABLISHED sockets ON THE CAMERA via exec-CGI."""
    out = {"ok": False, "listen": None, "established": None}
    r = _cgi_exec(host, _INSPECT_CMD, timeout=10)
    if not r.get("ok"):
        out["error"] = r.get("error", "exec failed")
        return out
    out["ok"] = True
    text = r.get("output", "")
    for line in text.splitlines():
        line = line.strip()
        # lines look like: "1 0A" / "3 01" (count, state hex)
        parts = line.split()
        if len(parts) == 2 and parts[1] in ("0A", "01"):
            n = int(parts[0]) if parts[0].isdigit() else 0
            if parts[1] == "0A":
                out["listen"] = n
            else:
                out["established"] = (out["established"] or 0) + n
    return out


def probe(minimal: bool = False) -> dict:
    """Run the non-invasive ladder and return the full Capability report.

    NEVER opens a connection to 7679. Safe to call repeatedly (auto-refresh).
    """
    host = settings_svc.get("camera.host")
    t0 = time.time()
    report: dict = {"host": host, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # N3 — broker (free, no network)
    broker = get_broker().status()
    report["broker"] = broker

    # N1 — control plane
    ctrl = _root_ping(host, timeout=4.0)
    ctrl_ms = round((time.time() - t0) * 1000, 1) if ctrl.get("ok") else None
    report["control_plane"] = {"ok": bool(ctrl.get("ok")), "latency_ms": ctrl_ms,
                               "error": ctrl.get("error")}

    # control transports (telnet/ftp) — only meaningful when the host answers
    report["control_transports"] = (control_status()
                                    if ctrl.get("ok") else
                                    {"available": False, "skipped": "host unreachable"})

    # N2 — camera-side socket inspector (harmless exec-CGI)
    insp = {"ok": False, "listen": None, "established": None,
            "skipped": "host unreachable"}
    if ctrl.get("ok"):
        insp = _inspect_session_sockets(host)
    report["camera_sockets"] = insp

    # ---- classify -------------------------------------------------------
    if not ctrl.get("ok"):
        klass = OFFLINE            # stage-2 wedge, powered off, or Wi-Fi gone
    elif broker.get("in_process_owner") or (insp.get("established") or 0) > 0:
        klass = SESSION_ACTIVE
    elif insp.get("ok") and insp.get("listen") == 0:
        klass = STREAMER_WEDGED    # stage 1 — streamer gone/wedged
    else:
        klass = IDLE_READY         # idle; arming freshness unknown
    report["camera_class"] = klass
    report["stage"] = ({OFFLINE: 2, STREAMER_WEDGED: 1}.get(klass, 0))
    report["elapsed_ms"] = round((time.time() - t0) * 1000, 1)

    # ---- decisive-probe gate --------------------------------------------
    report["decisive_probe"] = {
        "allowed": klass == IDLE_READY,
        "reason": {
            OFFLINE: "stage-2/host unreachable — a probe cannot even connect",
            SESSION_ACTIVE: "a live session is held — probing 7679 would "
                            "compete with it and waste the arming (RC-5)",
            STREAMER_WEDGED: "streamer has no listener — already spent/wedged",
            IDLE_READY: "idle — probe permitted; WARNING: a fresh arming is "
                        "CONSUMED by this test (single-shot streamer)",
        }[klass],
    }

    if not minimal:
        report["features"] = feature_statuses(report)
    return report


def feature_statuses(report: dict) -> list[dict]:
    """Resolve the tier-ordered feature manifest against a probe report."""
    klass = report.get("camera_class", OFFLINE)
    ctrl_ok = bool(report.get("control_plane", {}).get("ok"))
    trans = report.get("control_transports") or {}
    photo_ok = bool(trans.get("available"))
    broker_owner = report.get("broker", {}).get("in_process_owner")
    wedged_hint = klass in (STREAMER_WEDGED, OFFLINE)

    def st(ok: bool, active: str, inactive: str) -> str:
        return active if ok else inactive

    features: list[dict] = [
        # ---------------- TIER 0 — guaranteed whenever powered --------------
        {
            "id": "F0.1", "tier": 0, "guarantee": G_ALWAYS,
            "title": {"ar": "مراقب مستوى التحكم (8888)", "en": "Control-plane monitor (8888)"},
            "desc": {"ar": "فحص دوري لخادم الـmod على المنفذ 8888 مع قياس زمن الاستجابة. لا يمس البث إطلاقاً — أُثبت أنه يبقى حياً حتى في المرحلة الأولى من التعليق.",
                     "en": "Periodic probe of the mod's 8888 server with latency. Never touches the stream — proven alive even in wedge stage 1."},
            "evidence": "E15 · E17",
            "status": st(ctrl_ok, "up", "down"),
            "detail": f"{report['control_plane'].get('latency_ms') or '—'} ms",
            "action": None,
        },
        {
            "id": "F0.2", "tier": 0, "guarantee": G_ALWAYS,
            "title": {"ar": "فاحص جلسات الكاميرا (من الداخل)", "en": "Camera-side session inspector"},
            "desc": {"ar": "يقرأ /proc/net/tcp داخل الكاميرا عبر exec-CGI لعدّ مستمعات واتصالات المنفذ 7679 — الحقيقة من جهة الكاميرا دون أي لمسة للبث.",
                     "en": "Reads /proc/net/tcp inside the camera via exec-CGI to count 7679 listeners/connections — camera-side truth with zero stream impact."},
            "evidence": "E15 · E17",
            "status": st(report.get("camera_sockets", {}).get("ok"), "up", "down"),
            "detail": (f"LISTEN={report.get('camera_sockets', {}).get('listen')} · "
                       f"ESTABLISHED={report.get('camera_sockets', {}).get('established')}"),
            "action": None,
        },
        {
            "id": "F0.3", "tier": 0, "guarantee": G_ALWAYS,
            "title": {"ar": "مصنّف مرحلة التعليق", "en": "Wedge-stage classifier"},
            "desc": {"ar": "يصنّف حالة الكاميرا: سليمة / جلسة نشطة / مرحلة 1 (البث معلّق ومستوى التحكم حي) / مرحلة 2 (الكاميرا غاربة عن الشبكة).",
                     "en": "Classifies the camera: healthy / session active / stage 1 (streamer wedged, control alive) / stage 2 (host off the network)."},
            "evidence": "E16 · E17 · RC-7",
            "status": "up",
            "detail": {"IDLE_READY": "stage 0 — healthy/idle",
                       "SESSION_ACTIVE": "session active",
                       "STREAMER_WEDGED": "stage 1 — streamer wedged",
                       "OFFLINE": "stage 2 — host unreachable"}[klass],
            "action": None,
        },
        {
            "id": "F0.4", "tier": 0, "guarantee": G_ALWAYS,
            "title": {"ar": "مرشد إعادة التشغيل الفيزيائي", "en": "Power-cycle wizard"},
            "desc": {"ar": "الطريق الوحيد خارج المرحلة 2 (إثبات: NO_REARM_FOUND). خطوات إرشادية ثم زر إعادة فحص بعد إعادة التشغيل.",
                     "en": "The ONLY way out of stage 2 (proof: NO_REARM_FOUND). Guided steps, then a re-check button after restart."},
            "evidence": "E16 · RC-7",
            "status": "up",
            "detail": {"OFFLINE": "required now", "STREAMER_WEDGED": "fallback",
                       "SESSION_ACTIVE": "standby", "IDLE_READY": "standby"}[klass],
            "action": {"type": "powercycle"},
        },

        # ---------------- TIER 1 — guaranteed while session alive -----------
        {
            "id": "F1.1", "tier": 1, "guarantee": G_SESSION,
            "title": {"ar": "المعاينة الحية (جلسة لاصقة واحدة)", "en": "Live preview (one sticky session)"},
            "desc": {"ar": "اتصال واحد تحتفظ به الوسيط طوال الجلسة — بلا إعادة اتصال عمياء؛ عند نفاد التسليح يتوقف الأنبوب بشكل نهائي بدل ضرب الكاميرا.",
                     "en": "One broker-held connection kept for the whole session — no blind retries; on arming exhaustion the pipeline stops terminally instead of hammering the camera."},
            "evidence": "E13 · E16 · RC-5/RC-6",
            "status": st(klass == SESSION_ACTIVE and broker_owner, "live", "needs-session"),
            "detail": f"broker: {broker_owner or 'free'}",
            "action": {"type": "goto", "route": "#/live", "label": "live"},
        },
        {
            "id": "F1.2", "tier": 1, "guarantee": G_SESSION,
            "title": {"ar": "لقطة من الجلسة الحية", "en": "In-session snapshot"},
            "desc": {"ar": "يلتقط آخر إطار مفكوك من الذاكرة (FrameBus) — صفر اتصالات جديدة نحو الكاميرا.",
                     "en": "Captures the latest decoded frame from memory (FrameBus) — zero new connections to the camera."},
            "evidence": "E13 · RC-6",
            "status": st(klass == SESSION_ACTIVE, "ready", "needs-session"),
            "detail": "FrameBus · /api/stream/snapshot",
            "action": {"type": "goto", "route": "#/live", "label": "live"},
        },
        {
            "id": "F1.3", "tier": 1, "guarantee": G_SESSION,
            "title": {"ar": "التسجيل أثناء الجلسة", "en": "In-session recording"},
            "desc": {"ar": "يسجّل من إطارات الجلسة نفسها دون أي اتصال إضافي.",
                     "en": "Records from the same session's frames without any extra connection."},
            "evidence": "E13 · RC-6",
            "status": st(klass == SESSION_ACTIVE, "ready", "needs-session"),
            "detail": "/api/recording/start",
            "action": {"type": "goto", "route": "#/recordings", "label": "recordings"},
        },
        {
            "id": "F1.4", "tier": 1, "guarantee": G_SESSION,
            "title": {"ar": "قياسات خط الأنابيب الحي", "en": "Live pipeline telemetry"},
            "desc": {"ar": "جدول العدّادات من البايتات إلى الإطارات المنشورة — يحدد أوقفت الإطارات محلياً دون لمس الكاميرا.",
                     "en": "Byte-to-frame counter table — localises where frames stop without touching the camera."},
            "evidence": "§45 · RC-5",
            "status": "up",
            "detail": "/api/stream/counters",
            "action": {"type": "goto", "route": "#/camera", "label": "camera"},
        },

        # ---------------- TIER 2 — control plane, stream-independent --------
        {
            "id": "F2.1", "tier": 2, "guarantee": G_CONTROL,
            "title": {"ar": "التقاط صورة حقيقية (عبر الـmod)", "en": "Real Take Photo (via mod)"},
            "desc": {"ar": "أمر تصوير حقيقي: تيلنت ← SD ← مزامنة FTP ← المعرض. مستقل تماماً عن البث لكنه يعتمد على منفذي 23/21.",
                     "en": "Real camera capture: telnet command → SD → FTP sync → Gallery. Fully independent of the stream but depends on ports 23/21."},
            "evidence": "E12 · E15",
            "status": st(photo_ok, "ready", st(ctrl_ok, "transports-down", "down")),
            "detail": f"telnet {bool(trans.get('telnet', {}).get('ok'))} · ftp {bool(trans.get('ftp', {}).get('ok'))}",
            "action": {"type": "photo"},
        },
        {
            "id": "F2.2", "tier": 2, "guarantee": G_CONTROL,
            "title": {"ar": "سلم إعادة التسليح الآمن (S1–S3)", "en": "Safe re-arm ladder (S1–S3)"},
            "desc": {"ar": "أفعال غير مؤذية عبر 8888 (root ping / echo / ps) — نافذة المرحلة 1 فقط، وتُنفَّذ مرة واحدة بلا تكرار.",
                     "en": "Harmless 8888 actions (root ping / echo / ps) — stage-1 window only, executed once, never retried in a loop."},
            "evidence": "E15 · E16 · RC-7",
            "status": st(ctrl_ok and klass != SESSION_ACTIVE, st(klass == STREAMER_WEDGED, "recommended", "ready"), "down"),
            "detail": "stage-1 window",
            "action": {"type": "rearm", "apply": False},
        },
        {
            "id": "F2.3", "tier": 2, "guarantee": G_CONTROL,
            "title": {"ar": "إعادة تسليح جارفة (S4: قتل المُبثّ)", "en": "Invasive re-arm (S4: kill streamer)"},
            "desc": {"ar": "قتل عملية 7679 ليولدها init من جديد — آخر ورقة قبل الإطفاء الفيزيائي؛ استجابة init غير مضمونة على هذه الوحدة.",
                     "en": "Kills the 7679 process so init respawns it — last resort before a physical restart; init respawn is NOT guaranteed on this unit."},
            "evidence": "E15 · E16 (respawn unverified)",
            "status": st(ctrl_ok and klass != SESSION_ACTIVE, st(klass == STREAMER_WEDGED, "recommended", "ready"), "down"),
            "detail": "apply-gated",
            "action": {"type": "rearm", "apply": True},
        },
        {
            "id": "F2.4", "tier": 2, "guarantee": G_CONTROL,
            "title": {"ar": "وصول SD + صَدَف التنفيذ (8888)", "en": "SD access + exec shell (8888)"},
            "desc": {"ar": "تصفح/تنزيل ملفات SD عبر FTP وتنفيذ أوامر عبر exec-CGI — قنوات التحكم المستقلة عن البث.",
                     "en": "Browse/download SD files over FTP and run commands via exec-CGI — control channels independent of the stream."},
            "evidence": "E15",
            "status": st(ctrl_ok, "ready", "down"),
            "detail": "FTP 21 · CGI 8888",
            "action": None,
        },

        # ---------------- TIER 3 — firmware-blocked, evidence-backed --------
        {
            "id": "F3.1", "tier": 3, "guarantee": G_BLOCKED,
            "title": {"ar": "إعادة اتصال تلقائية بعد الانقطاع", "en": "Auto-reconnect after disconnect"},
            "desc": {"ar": "معطّلة بإثبات حاسم: لا شفاء ذاتي خلال 51 ثانية (headers-only → timeout → closed_by_camera). كل محاولة تدفع الكاميرا نحو المرحلة 2.",
                     "en": "Blocked by decisive proof: no self-heal within 51 s (headers-only → timeout → closed_by_camera). Every attempt pushes the camera toward stage 2."},
            "evidence": "E16 st1 · RC-7",
            "status": "blocked",
            "detail": "NO self-heal (51 s tested)",
            "action": None,
        },
        {
            "id": "F3.2", "tier": 3, "guarantee": G_BLOCKED,
            "title": {"ar": "مُشاهدون متعددون في آنٍ واحد", "en": "Concurrent viewers"},
            "desc": {"ar": "معطّلة: انضمام عميل ثانٍ يجمّد العميلين معاً (كلاهما timeout) — المُبثّ يخدم عميلاً واحداً فقط.",
                     "en": "Blocked: a second client stalls BOTH (timeouts on each) — the streamer serves exactly one client."},
            "evidence": "E16 st2",
            "status": "blocked",
            "detail": "both clients stall",
            "action": None,
        },
        {
            "id": "F3.3", "tier": 3, "guarantee": G_BLOCKED,
            "title": {"ar": "خطف/استئناف الجلسة", "en": "Session steal / resume"},
            "desc": {"ar": "معطّلة: لا يوجد newest-wins — العميل الجديد لا يخطف الجلسة ولا يستأنفها.",
                     "en": "Blocked: no newest-wins semantics — a new client neither steals nor resumes the session."},
            "evidence": "E16 st2",
            "status": "blocked",
            "detail": "no steal, no resume",
            "action": None,
        },
        {
            "id": "F3.4", "tier": 3, "guarantee": G_BLOCKED,
            "title": {"ar": "إنعاش عن بُعد من المرحلة 2", "en": "Remote recovery from stage 2"},
            "desc": {"ar": "معطّلة: بعد المرحلة 2 تصبح كل المنافذ (23/21/8888/7679) غير قابلة للوصول (WinError 10065) — لا يوجد أي مسار شبكي؛ فقط إعادة تشغيل فيزيائية.",
                     "en": "Blocked: in stage 2 every port (23/21/8888/7679) becomes unreachable (WinError 10065) — no network path at all; only a physical power cycle."},
            "evidence": "E16 st3 · RC-7",
            "status": "blocked",
            "detail": "NO_REARM_FOUND",
            "action": None,
        },
    ]
    return features


def decisive_arming_probe(read_seconds: float = 3.0) -> dict:
    """THE decisive test — opens ONE connection to 7679 and classifies it.

    WARNING (honoured by the UI gate): on a fresh arming this test CONSUMES
    the single serving window — that is unavoidable, it is what makes the
    result decisive. Refuses to run while a session is held (broker guard).
    """
    broker = get_broker()
    if broker.status().get("in_process_owner"):
        return {"allowed": False, "result": "REFUSED_SESSION_HELD",
                "detail": "a live session is held — probing would compete (RC-5)"}

    host = settings_svc.get("camera.host")
    url = f"http://{host}:7679/livestream_high.avi"
    out: dict = {"allowed": True, "host": host, "url": url,
                 "read_seconds": read_seconds}
    req = urllib.request.Request(url, headers={
        "User-Agent": "g360-arming-probe/1.0", "Range": "bytes=0-"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=read_seconds + 4) as resp:
            status = resp.status
            n = 0
            deadline = t0 + read_seconds
            while time.time() < deadline:
                chunk = resp.read(65536)
                if not chunk:
                    break
                n += len(chunk)
        elapsed = round(time.time() - t0, 2)
        out.update({"http_status": status, "bytes": n, "elapsed_s": elapsed})
        if n > 0:
            out["result"] = "ARMED_BODY_OK"
            out["detail"] = (f"body flowed ({n} B in {elapsed} s) — arming was "
                             "fresh and is NOW CONSUMED")
        else:
            out["result"] = "HEADERS_ONLY_WEDGED"
            out["detail"] = ("206/200 headers with zero body — the single-shot "
                             "arming is spent (stage 1)")
    except Exception as exc:  # noqa: BLE001 — classify everything
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        out["result"] = "UNREACHABLE_STAGE2"
        out["detail"] = ("connect/read failed before any header — host wedged "
                         "or busy; do NOT retry blindly (RC-7)")
    LOG.info("decisive probe -> %s", out.get("result"))
    return out
