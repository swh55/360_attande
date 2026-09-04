// Dashboard view: camera status, live preview, activity, storage, health, recent events.
import { t, fmt } from "../i18n.js";
import { api, feed } from "../api.js";
import { el, toast, state } from "../app.js";
import { GL360, attachInteraction } from "../gl360.js";

export async function renderDashboard(container) {
  const [cam, stats, today, health, events] = await Promise.all([
    api.camStatus(), api.galleryStats(), api.eventsToday(),
    api.health(), api.events("?limit=6"),
  ]);

  container.innerHTML = `
  <div class="grid cols-2" style="grid-template-columns: 2fr 1fr;">
    <div class="card" style="padding:0; overflow:hidden;">
      <div class="viewer-wrap" id="dashViewer" style="border-radius:12px; min-height:380px;">
        <canvas id="dashCanvas"></canvas>
        <div class="viewer-tag" id="dashTag"></div>
        <div class="viewer-ctl">
          <button class="vbtn" id="dashOpen">◉ ${t("dash.open_live")}</button>
        </div>
      </div>
    </div>
    <div style="display:flex; flex-direction:column; gap:16px;">
      <div class="card">
        <h3>📷 ${t("dash.camera")}</h3>
        <div class="kv"><span>${t("common.state")}</span><b id="dashState">—</b></div>
        <div class="kv"><span>${t("cam.ip")}</span><b>${cam.config?.host || "—"}</b></div>
        <div class="kv"><span>${t("cam.stream_url")}</span><b style="font-size:11px">${cam.stream_url}</b></div>
        <div class="kv"><span>FPS</span><b id="dashFps">—</b></div>
        <div class="kv"><span>${t("cam.codec")}</span><b>${cam.stream?.codec || "—"}</b></div>
      </div>
      <div class="card">
        <h3>✦ ${t("dash.activity")}</h3>
        <div class="grid cols-2" style="gap:10px">
          <div class="kpi"><span class="num" id="kpiPersons">${fmt.num(0)}</span><span class="lbl">${t("dash.people_detected")}</span></div>
          <div class="kpi"><span class="num" id="kpiFaces">${fmt.num(0)}</span><span class="lbl">${t("dash.faces_detected")}</span></div>
          <div class="kpi"><span class="num" id="kpiKnown">${fmt.num(today.known || 0)}</span><span class="lbl">${t("dash.known")}</span></div>
          <div class="kpi"><span class="num" id="kpiUnknown">${fmt.num(today.unknown || 0)}</span><span class="lbl">${t("dash.unknown")}</span></div>
        </div>
      </div>
    </div>
  </div>

  <div class="grid cols-3" style="margin-top:16px">
    <div class="card">
      <h3>▦ ${t("dash.storage")}</h3>
      <div class="grid cols-2" style="gap:10px">
        <div class="kpi"><span class="num">${fmt.num(stats.today.videos)}</span><span class="lbl">${t("dash.videos_today")}</span></div>
        <div class="kpi"><span class="num">${fmt.num(stats.today.photos)}</span><span class="lbl">${t("dash.photos_today")}</span></div>
        <div class="kpi"><span class="num">${fmt.num(stats.today.events)}</span><span class="lbl">${t("dash.events_today")}</span></div>
        <div class="kpi"><span class="num">${fmt.bytes(stats.today.storage_bytes)}</span><span class="lbl">${t("dash.storage_today")}</span></div>
      </div>
    </div>
    <div class="card">
      <h3>❤ ${t("dash.health")}</h3>
      <div id="dashHealth"></div>
    </div>
    <div class="card">
      <h3>≡ ${t("dash.recent")}</h3>
      <div id="dashRecent" style="max-height:230px; overflow-y:auto"></div>
    </div>
  </div>`;

  // ---- health list ----
  const healthBox = container.querySelector("#dashHealth");
  const labels = { camera: t("dash.camera"), stream: "Stream", decoder: "Decoder",
    ffmpeg: "FFmpeg", ai: t("nav.ai"), storage: t("dash.storage"), database: "DB" };
  healthBox.innerHTML = Object.entries(health)
    .filter(([k]) => k !== "frame_age")
    .map(([k, v]) => `<div class="health-item"><span class="hdot ${v.status}"></span>
      <span>${labels[k] || k}</span><span class="d">${v.detail || ""}</span></div>`).join("");

  // ---- recent events ----
  const recent = container.querySelector("#dashRecent");
  if (!events.events.length) {
    recent.innerHTML = `<div style="color:var(--text-2)">${t("ev.empty")}</div>`;
  } else {
    for (const ev of events.events) {
      recent.appendChild(el(`<div class="tl-event">
        <span class="time">${fmt.time(ev.ts)}</span>
        <div class="body"><div class="m">${t("ev.type." + ev.type, ev.type)}</div>
        <div class="x">${ev.message || ""}</div></div></div>`));
    }
  }

  // ---- mini live viewer ----
  const canvas = container.querySelector("#dashCanvas");
  const tag = container.querySelector("#dashTag");
  let gl = null, img = null, raf = 0, cleanupWS = () => {};
  try {
    gl = new GL360(canvas);
    img = new Image();
    img.src = "/api/mjpeg?w=960";
    img.addEventListener("load", () => gl.markStale());
    gl.setSource(img);
    gl.setMode("fisheye");
    attachInteraction(gl, canvas);
    const loop = () => { gl.render(); raf = requestAnimationFrame(loop); };
    loop();
    cleanupWS = feed.on("state", (msg) => {
      tag.hidden = false;
      const demo = msg.mode === "demo";
      tag.className = `viewer-tag ${demo ? "demo" : ""}`;
      tag.textContent = demo ? "DEMO MODE" : `${t("state." + msg.mode, msg.mode)}`;
      container.querySelector("#dashFps").textContent = fmt.num(Math.round(msg.stream?.fps || 0));
      const st = msg.camera?.state;
      container.querySelector("#dashState").textContent = demo ? t("state.demo") : t("state." + st, st);
      const c = msg.ai?.counts;
      if (c) {
        container.querySelector("#kpiPersons").textContent = fmt.num(c.persons);
        container.querySelector("#kpiFaces").textContent = fmt.num(c.faces);
      }
    });
  } catch (err) {
    toast("WebGL: " + err.message, "err");
  }
  container.querySelector("#dashOpen").onclick = () => { location.hash = "#/live"; };

  return () => {
    cancelAnimationFrame(raf);
    cleanupWS();
    gl?.destroy();
    img?.remove();
  };
}
