// Gear360 Hub i18n — Arabic (RTL) + English. Numbers stay Western (0-9).
export const LANGS = { ar: "العربية", en: "English" };

const dict = {
  ar: {
    "app.tagline": "منصة ذكية مفتوحة",
    "nav.dashboard": "لوحة التحكم", "nav.live": "البث المباشر 360°",
    "nav.recordings": "التسجيلات", "nav.gallery": "معرض الوسائط",
    "nav.events": "الأحداث", "nav.ai": "الذكاء الاصطناعي",
    "nav.people": "الأشخاص", "nav.camera": "الكاميرا", "nav.settings": "الإعدادات",
    "nav.diagnostics": "التشخيص",
    "rec.badge": "تسجيل",
    // dashboard
    "dash.title": "لوحة التحكم", "dash.subtitle": "نظرة عامة على النظام",
    "dash.camera": "الكاميرا", "dash.live": "المعاينة الحية", "dash.activity": "النشاط",
    "dash.storage": "التخزين", "dash.health": "صحة النظام", "dash.recent": "أحدث الأحداث",
    "dash.people_detected": "أشخاص مكتشفون", "dash.faces_detected": "وجوه مكتشفة",
    "dash.known": "معروفون", "dash.unknown": "مجهولون",
    "dash.videos_today": "فيديو اليوم", "dash.photos_today": "صور اليوم",
    "dash.events_today": "أحداث اليوم", "dash.storage_today": "حجم اليوم",
    "dash.open_live": "فتح البث المباشر",
    "state.idle": "خامل", "state.live": "مباشر", "state.demo": "وضع تجريبي",
    "state.connected": "متصل", "state.streaming": "بث حي", "state.connecting": "جاري الاتصال",
    "state.reconnecting": "إعادة اتصال", "state.error": "خطأ", "state.disconnected": "غير متصل",
    // live
    "live.title": "البث المباشر 360°", "live.snapshot": "لقطة", "live.record": "تسجيل",
    "live.stop_rec": "إيقاف التسجيل", "live.pause": "إيقاف مؤقت", "live.resume": "استئناف",
    "live.reset": "إعادة الضبط", "live.fullscreen": "ملء الشاشة", "live.projection": "الإسقاط",
    "live.fov": "مجال الرؤية", "live.follow": "اتباع المخرج الذكي", "live.vcams": "الكاميرات الافتراضية",
    "live.add_vcam": "حفظ الزاوية الحالية", "live.goto": "انتقال", "live.delete": "حذف",
    "live.source": "المصدر", "live.lens": "العدسة", "live.calibration": "معايرة العدسات",
    "live.front": "أمامية", "live.back": "خلفية", "live.none": "لا يوجد بث — شغّل Demo أو اتصل بالكاميرا",
    "proj.raw": "خام", "proj.equirect": "360° متساوي المستطيلات", "proj.perspective": "منظوري",
    "proj.fisheye": "عين السمكة", "proj.tiny": "الكوكب الصغير",
    // camera
    "cam.title": "الكاميرا", "cam.connect": "اتصال", "cam.disconnect": "قطع الاتصال",
    "cam.test": "اختبار الاتصال", "cam.ip": "عنوان IP", "cam.port": "المنفذ",
    "cam.stream_url": "رابط البث", "cam.latency": "زمن الاستجابة", "cam.reachable": "قابلة للوصول",
    "cam.yes": "نعم", "cam.no": "لا", "cam.errors": "الأخطاء", "cam.reconnects": "إعادة الاتصال",
    "cam.uptime": "مدة التشغيل", "cam.last_frame": "آخر إطار", "cam.codec": "الترميز",
    "cam.resolution": "الدقة", "cam.profiles": "الملفات التعريفية", "cam.never": "أبداً",
    "cam.restart": "إعادة تشغيل البث", "cam.restarting": "جارٍ إعادة تشغيل جلسة البث…",
    "cam.restart_ok": "بدأت جلسة بث جديدة — بانتظار الإطار الأول",
    "cam.take_photo": "التقاط صورة (الكاميرا)", "cam.photo_hint": "أمر حقيقي للكاميرا عبر الـmod: st key click s2 ← SD ← مزامنة FTP ← المعرض",
    "cam.photo_sending": "إرسال أمر التصوير إلى الكاميرا…",
    "cam.photo_done": "تمت المزامنة من بطاقة SD",
    "cam.phase": "مرحلة خط الأنابيب", "cam.counters": "عدّادات خط الأنابيب",
    "cam.counters_hint": "إذا توقف 00VD عند صفر فالمشكلة في المحلل/البث؛ إن كانت أكبر من صفر وFFmpeg عند صفر فالمشكلة في فك الترميز.",
    // gallery
    "gal.title": "معرض الوسائط", "gal.all": "الكل", "gal.photos": "صور",
    "gal.videos": "فيديو", "gal.favorites": "المفضلة", "gal.empty": "لا توجد وسائط بعد",
    "gal.download": "تنزيل", "gal.delete": "حذف", "gal.open360": "فتح في عارض 360°",
    "gal.size": "الحجم", "gal.duration": "المدة", "gal.type": "النوع",
    // recordings
    "recs.title": "التسجيلات",
    // events
    "ev.title": "الأحداث", "ev.timeline": "الخط الزمني", "ev.stats": "إحصائيات 24 ساعة",
    "ev.type.motion": "حركة", "ev.type.person": "شخص", "ev.type.face": "وجه",
    "ev.type.recognition_known": "تعرف عليه", "ev.type.recognition_unknown": "شخص مجهول",
    "ev.type.person_left": "مغادرة شخص", "ev.type.attendance_in": "حضور",
    "ev.type.attendance_out": "انصراف", "ev.empty": "لا أحداث بعد",
    "ev.confidence": "الثقة",
    // ai
    "ai.title": "الذكاء الاصطناعي", "ai.enable": "تشغيل محرك الذكاء الاصطناعي",
    "ai.mode": "الوضع", "ai.tracks": "التتبع الحي", "ai.director": "المخرج الذكي",
    "ai.best_view": "أفضل زاوية", "ai.attendance": "الحضور الذكي",
    "ai.attendance_summary": "ملخص الحضور اليوم", "ai.checkin": "دخول", "ai.checkout": "خروج",
    "ai.recognition_unavailable": "محرك التعرف غير مثبت — ثبّت requirements-ai.txt لتفعيله",
    "ai.person_backend": "كاشف الأشخاص", "ai.no_tracks": "لا تتبع نشط الآن",
    "ai.views": "المشاهد المحللة",
    // people
    "ppl.title": "الأشخاص", "ppl.add": "إضافة شخص", "ppl.name": "الاسم",
    "ppl.code": "المعرّف", "ppl.dept": "القسم", "ppl.notes": "ملاحظات",
    "ppl.photo": "صورة الوجه", "ppl.embeddings": "البصمات", "ppl.recognitions": "مرات التعرف",
    "ppl.edit": "تعديل", "ppl.empty": "لا أشخاص مسجلون",
    // settings
    "set.title": "الإعدادات", "set.saved": "تم الحفظ",
    "set.cat.camera": "الكاميرا", "set.cat.streaming": "البث", "set.cat.recording": "التسجيل",
    "set.cat.ai": "الذكاء الاصطناعي", "set.cat.storage": "التخزين", "set.cat.ffmpeg": "FFmpeg",
    "set.cat.network": "الشبكة", "set.cat.system": "النظام", "set.test_ffmpeg": "اختبار FFmpeg",
    "set.reset": "استعادة الافتراضي", "set.restart_note": "بعض التغييرات تحتاج إعادة تشغيل",
    "about.title": "حول التطبيق",
    // common
    "common.loading": "جاري التحميل…", "common.save": "حفظ", "common.cancel": "إلغاء",
    "common.confirm_delete": "تأكيد الحذف؟", "common.close": "إغلاق", "common.state": "الحالة", "common.enabled": "مفعل",
    "common.disabled": "معطل", "common.on": "يعمل", "common.off": "متوقف", "common.today": "اليوم",
    // diagnostics
    "diag.title": "تشخيص النظام", "diag.desc": "محرك تشخيص حقيقي: يفحص الكاميرا والبث والمحلل وفك الترميز طبقةً طبقة، ويحدد المرحلة التي فشل فيها النظام مع الإجراء المقترح.",
    "diag.run": "تشغيل التشخيص الكامل", "diag.running": "جاري التشخيص…", "diag.done": "اكتمل التشخيص",
    "diag.inspect_live": "فحص الجلسة الحية الحالية", "diag.inspect_hint": "يقرأ حالة خط الأنابيب الحالي — لا يفتح أي اتصال جديد بالكاميرا",
    "diag.session": "جلسة التشخيص", "diag.never": "لم يُشغَّل بعد — اضغط «تشغيل التشخيص الكامل»",
    "diag.pipeline": "خط الأنابيب", "diag.root_cause": "السبب الجذري", "diag.confidence": "الثقة",
    "diag.suggested_action": "الإجراء المقترح", "diag.live_layers": "طبقات البث الحي",
    "diag.inspector": "فاحص البث (Stream Inspector)", "diag.headers": "ترويسات HTTP",
    "diag.hex": "عارض Hex", "diag.offset": "الإزاحة", "diag.copy": "نسخ", "diag.download": "تنزيل",
    "diag.parser_dbg": "محلل الـParser — VID0", "diag.endianness": "محلل ترتيب البايتات (Endianness)",
    "diag.structural": "التحقق البنيوي للـChunks", "diag.scanner": "الماسح المستقل للتوقيعات",
    "diag.compare": "مقارنة مع المرجع Gear360App (Extractor.kt)", "diag.decoder": "تشخيص وحدة فك الترميز",
    "diag.test_ffmpeg": "اختبار FFmpeg", "diag.test_decoder": "اختبار فك الترميز على عينة",
    "diag.frame_dia": "تشخيص الإطار", "diag.snapshot_dia": "تشخيص اللقطات", "diag.snapshot_test": "اختبار اللقطة",
    "diag.capture_title": "أخذ عينة من البث", "diag.capture_btn": "التقاط عينة", "diag.size_mb": "الحجم (MB)",
    "diag.captures": "العينات الملتقطة", "diag.metrics": "مؤشرات الأداء", "diag.timeline": "الخط الزمني للجلسة",
    "diag.export_json": "تصدير التقرير JSON", "diag.export_zip": "تنزيل تقرير ZIP",
    "diag.legacy_slot": "إزاحة قديمة (خاطئة)",
    "diag.layer.camera": "الكاميرا", "diag.layer.http": "HTTP", "diag.layer.stream": "البث",
    "diag.layer.parser": "المحلل", "diag.layer.metadata": "البيانات الوصفية",
    "diag.layer.decoder": "فك الترميز", "diag.layer.frame": "الإطار",
    "diag.stage.camera": "الكاميرا", "diag.stage.network": "الشبكة", "diag.stage.http": "بث HTTP",
    "diag.stage.container": "الحاوية", "diag.stage.chunks": "محلل الـChunks", "diag.stage.frames": "الإطارات",
    "diag.stage.decoder": "فك الترميز", "diag.stage.projection": "الإسقاط", "diag.stage.viewer": "العارض",
    "diag.stage.snapshot": "اللقطة",
    "diag.st.pass": "نجاح", "diag.st.warn": "تحذير", "diag.st.fail": "فشل", "diag.st.untested": "لم يُختبر",
    "diag.signatures": "البصمات", "diag.field": "الحقل", "diag.feature": "الخاصية",
    "diag.frames": "الإطارات", "diag.decode_fps": "سرعة فك الترميز", "diag.last_stderr": "آخر stderr",
    "diag.bytes_sec": "بايت/ثانية", "diag.latency": "زمن الاستجابة", "diag.queue": "عمق الطابور",
    "diag.dropped": "إطارات مسقطة", "diag.cpu": "المعالج", "diag.ram": "الذاكرة",
  },
  en: {
    "app.tagline": "Smart Open Platform",
    "nav.dashboard": "Dashboard", "nav.live": "Live 360° Viewer",
    "nav.recordings": "Recordings", "nav.gallery": "Gallery",
    "nav.events": "Events", "nav.ai": "AI Engine",
    "nav.people": "People", "nav.camera": "Camera", "nav.settings": "Settings",
    "nav.diagnostics": "Diagnostics",
    "rec.badge": "REC",
    "dash.title": "Dashboard", "dash.subtitle": "System overview",
    "dash.camera": "Camera", "dash.live": "Live Preview", "dash.activity": "Activity",
    "dash.storage": "Storage", "dash.health": "System Health", "dash.recent": "Recent Events",
    "dash.people_detected": "People detected", "dash.faces_detected": "Faces detected",
    "dash.known": "Known", "dash.unknown": "Unknown",
    "dash.videos_today": "Videos today", "dash.photos_today": "Photos today",
    "dash.events_today": "Events today", "dash.storage_today": "Storage today",
    "dash.open_live": "Open Live Viewer",
    "state.idle": "Idle", "state.live": "Live", "state.demo": "Demo",
    "state.connected": "Connected", "state.streaming": "Streaming", "state.connecting": "Connecting",
    "state.reconnecting": "Reconnecting", "state.error": "Error", "state.disconnected": "Disconnected",
    "live.title": "Live 360° Viewer", "live.snapshot": "Snapshot", "live.record": "Record",
    "live.stop_rec": "Stop Recording", "live.pause": "Pause", "live.resume": "Resume",
    "live.reset": "Reset View", "live.fullscreen": "Fullscreen", "live.projection": "Projection",
    "live.fov": "FOV", "live.follow": "Follow AI Director", "live.vcams": "Virtual Cameras",
    "live.add_vcam": "Save current view", "live.goto": "Go", "live.delete": "Delete",
    "live.source": "Source", "live.lens": "Lens", "live.calibration": "Lens Calibration",
    "live.front": "Front", "live.back": "Back", "live.none": "No stream — enable Demo mode or connect the camera",
    "proj.raw": "Raw", "proj.equirect": "360° Equirectangular", "proj.perspective": "Perspective",
    "proj.fisheye": "Fisheye", "proj.tiny": "Tiny Planet",
    "cam.title": "Camera", "cam.connect": "Connect", "cam.disconnect": "Disconnect",
    "cam.test": "Test Connection", "cam.ip": "IP Address", "cam.port": "Port",
    "cam.stream_url": "Stream URL", "cam.latency": "Latency", "cam.reachable": "Reachable",
    "cam.yes": "Yes", "cam.no": "No", "cam.errors": "Errors", "cam.reconnects": "Reconnects",
    "cam.uptime": "Uptime", "cam.last_frame": "Last frame", "cam.codec": "Codec",
    "cam.resolution": "Resolution", "cam.profiles": "Profiles", "cam.never": "Never",
    "cam.restart": "Restart Stream", "cam.restarting": "Restarting live session…",
    "cam.restart_ok": "Fresh session started — waiting for the first frame",
    "cam.take_photo": "Take Photo (camera)", "cam.photo_hint": "Real camera command via the mod: st key click s2 → SD → FTP sync → Gallery",
    "cam.photo_sending": "Sending capture command to the camera…",
    "cam.photo_done": "synced from the SD card",
    "cam.phase": "Pipeline phase", "cam.counters": "Pipeline counters",
    "cam.counters_hint": "00VD = 0 → parser/stream problem; 00VD > 0 but FFmpeg input = 0 → decoder problem.",
    "gal.title": "Gallery", "gal.all": "All", "gal.photos": "Photos",
    "gal.videos": "Videos", "gal.favorites": "Favorites", "gal.empty": "No media yet",
    "gal.download": "Download", "gal.delete": "Delete", "gal.open360": "Open in 360 Viewer",
    "gal.size": "Size", "gal.duration": "Duration", "gal.type": "Type",
    "recs.title": "Recordings",
    "ev.title": "Events", "ev.timeline": "Timeline", "ev.stats": "Last 24 hours",
    "ev.type.motion": "Motion", "ev.type.person": "Person", "ev.type.face": "Face",
    "ev.type.recognition_known": "Recognized", "ev.type.recognition_unknown": "Unknown person",
    "ev.type.person_left": "Person left", "ev.type.attendance_in": "Check-in",
    "ev.type.attendance_out": "Check-out", "ev.empty": "No events yet",
    "ev.confidence": "Confidence",
    "ai.title": "AI Engine", "ai.enable": "Enable AI Engine",
    "ai.mode": "Mode", "ai.tracks": "Live Tracking", "ai.director": "AI Director",
    "ai.best_view": "Best view", "ai.attendance": "Smart Attendance",
    "ai.attendance_summary": "Today's Attendance", "ai.checkin": "Check-in", "ai.checkout": "Check-out",
    "ai.recognition_unavailable": "Recognition engine not installed — install requirements-ai.txt to enable",
    "ai.person_backend": "Person detector", "ai.no_tracks": "No active tracks",
    "ai.views": "Analyzed views",
    "ppl.title": "People", "ppl.add": "Add Person", "ppl.name": "Name",
    "ppl.code": "ID", "ppl.dept": "Department", "ppl.notes": "Notes",
    "ppl.photo": "Face photo", "ppl.embeddings": "Embeddings", "ppl.recognitions": "Recognitions",
    "ppl.edit": "Edit", "ppl.empty": "No people registered",
    "set.title": "Settings", "set.saved": "Saved",
    "set.cat.camera": "Camera", "set.cat.streaming": "Streaming", "set.cat.recording": "Recording",
    "set.cat.ai": "AI", "set.cat.storage": "Storage", "set.cat.ffmpeg": "FFmpeg",
    "set.cat.network": "Network", "set.cat.system": "System", "set.test_ffmpeg": "Test FFmpeg",
    "set.reset": "Reset to default", "set.restart_note": "Some changes require a restart",
    "about.title": "About",
    "common.loading": "Loading…", "common.save": "Save", "common.cancel": "Cancel",
    "common.confirm_delete": "Confirm delete?", "common.close": "Close", "common.state": "State", "common.enabled": "Enabled",
    "common.disabled": "Disabled", "common.on": "On", "common.off": "Off", "common.today": "Today",
    // diagnostics
    "diag.title": "Diagnostics", "diag.desc": "A real diagnostic engine: probes camera, stream, parser and decoder layer by layer, identifies the failing stage and suggests the fix.",
    "diag.run": "Run Full Diagnostic", "diag.running": "Running…", "diag.done": "Diagnostic complete",
    "diag.inspect_live": "Inspect Current Live Session", "diag.inspect_hint": "Reads the existing live pipeline state — never opens a new camera connection",
    "diag.session": "Diagnostic Session", "diag.never": "Not run yet — press “Run Full Diagnostic”",
    "diag.pipeline": "Pipeline", "diag.root_cause": "Root Cause", "diag.confidence": "Confidence",
    "diag.suggested_action": "Suggested action", "diag.live_layers": "Live Layers",
    "diag.inspector": "Stream Inspector", "diag.headers": "HTTP Headers",
    "diag.hex": "Hex Viewer", "diag.offset": "Offset", "diag.copy": "Copy", "diag.download": "Download",
    "diag.parser_dbg": "Parser Debugger — VID0", "diag.endianness": "Endianness Analyzer",
    "diag.structural": "Structural Validation", "diag.scanner": "Independent Chunk Scanner",
    "diag.compare": "Compare with Gear360App reference (Extractor.kt)", "diag.decoder": "Decoder Diagnostics",
    "diag.test_ffmpeg": "Test FFmpeg", "diag.test_decoder": "Test Decoder with sample",
    "diag.frame_dia": "Frame Diagnostics", "diag.snapshot_dia": "Snapshot Diagnostics", "diag.snapshot_test": "Test Snapshot",
    "diag.capture_title": "Capture Stream Sample", "diag.capture_btn": "Capture", "diag.size_mb": "Size (MB)",
    "diag.captures": "Captured samples", "diag.metrics": "Performance Metrics", "diag.timeline": "Session Timeline",
    "diag.export_json": "Export JSON Report", "diag.export_zip": "Download ZIP Report",
    "diag.legacy_slot": "legacy (buggy) offset",
    "diag.layer.camera": "CAMERA", "diag.layer.http": "HTTP", "diag.layer.stream": "STREAM",
    "diag.layer.parser": "PARSER", "diag.layer.metadata": "METADATA",
    "diag.layer.decoder": "DECODER", "diag.layer.frame": "FRAME",
    "diag.stage.camera": "Camera", "diag.stage.network": "Network", "diag.stage.http": "HTTP Stream",
    "diag.stage.container": "Container", "diag.stage.chunks": "Chunk Parser", "diag.stage.frames": "Frames",
    "diag.stage.decoder": "Decoder", "diag.stage.projection": "Projection", "diag.stage.viewer": "Viewer",
    "diag.stage.snapshot": "Snapshot",
    "diag.st.pass": "PASS", "diag.st.warn": "WARN", "diag.st.fail": "FAIL", "diag.st.untested": "NOT TESTED",
    "diag.signatures": "Signatures", "diag.field": "Field", "diag.feature": "Feature",
    "diag.frames": "Frames", "diag.decode_fps": "Decode FPS", "diag.last_stderr": "Last stderr",
    "diag.bytes_sec": "Bytes/s", "diag.latency": "Latency", "diag.queue": "Queue depth",
    "diag.dropped": "Dropped", "diag.cpu": "CPU", "diag.ram": "RAM",
  },
};

let lang = localStorage.getItem("hub.lang") || "ar";

export function t(key, fallback) {
  return dict[lang]?.[key] ?? dict.en?.[key] ?? fallback ?? key;
}
export function getLang() { return lang; }
export function setLang(l) {
  lang = l;
  localStorage.setItem("hub.lang", l);
  document.documentElement.lang = l;
  document.documentElement.dir = l === "ar" ? "rtl" : "ltr";
  document.body.style.fontFamily = l === "ar"
    ? '"Segoe UI", Tahoma, "Noto Sans Arabic", sans-serif'
    : '"Segoe UI", system-ui, sans-serif';
}
setLang(lang);

// ---- Western-numeral formatters ----
export const fmt = {
  num(n) { return Number(n ?? 0).toLocaleString("en-US"); },
  bytes(b) {
    b = Number(b ?? 0);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
    return `${b.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
  },
  dur(sec) {
    sec = Math.max(0, Math.round(Number(sec ?? 0)));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(s).padStart(2, "0")}`;
  },
  clock(sec) {
    sec = Math.max(0, Math.floor(Number(sec ?? 0)));
    const m = Math.floor(sec / 60), s = sec % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  },
  ts(ts) {
    if (!ts) return "—";
    const d = new Date(String(ts).replace(" ", "T"));
    if (isNaN(d)) return ts;
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  },
  time(ts) {
    if (!ts) return "—";
    const d = new Date(String(ts).replace(" ", "T"));
    if (isNaN(d)) return ts;
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  },
};
