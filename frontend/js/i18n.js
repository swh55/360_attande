// Gear360 Hub i18n — Arabic (RTL) + English. Numbers stay Western (0-9).
export const LANGS = { ar: "العربية", en: "English" };

const dict = {
  ar: {
    "app.tagline": "منصة ذكية مفتوحة",
    "nav.dashboard": "لوحة التحكم", "nav.live": "البث المباشر 360°",
    "nav.recordings": "التسجيلات", "nav.gallery": "معرض الوسائط",
    "nav.events": "الأحداث", "nav.ai": "الذكاء الاصطناعي",
    "nav.people": "الأشخاص", "nav.camera": "الكاميرا", "nav.settings": "الإعدادات",
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
  },
  en: {
    "app.tagline": "Smart Open Platform",
    "nav.dashboard": "Dashboard", "nav.live": "Live 360° Viewer",
    "nav.recordings": "Recordings", "nav.gallery": "Gallery",
    "nav.events": "Events", "nav.ai": "AI Engine",
    "nav.people": "People", "nav.camera": "Camera", "nav.settings": "Settings",
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
