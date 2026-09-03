// Gear360 Hub shell: sidebar router, topbar live state, toasts, modals, helpers.
import { t, getLang, setLang, fmt } from "./i18n.js";
import { api, feed } from "./api.js";
import { renderDashboard } from "./views/dashboard.js";
import { renderLive } from "./views/live.js";
import { renderGallery } from "./views/gallery.js";
import { renderRecordings } from "./views/recordings.js";
import { renderEvents } from "./views/events.js";
import { renderAI } from "./views/ai.js";
import { renderPeople } from "./views/people.js";
import { renderCamera } from "./views/camera.js";
import { renderSettings } from "./views/settings.js";
import { renderDiagnostics } from "./views/diagnostics.js";

export const state = {
  last: null,           // last WS state snapshot
  camInfo: null,
  demo: false,
};

// ---------- tiny DOM helpers ----------
export function el(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html.trim();
  return tpl.content.firstElementChild;
}
export const $view = () => document.getElementById("view");

// ---------- toasts ----------
export function toast(msg, kind = "") {
  const box = document.getElementById("toasts");
  const n = el(`<div class="toast ${kind}"></div>`);
  n.textContent = msg;
  box.appendChild(n);
  setTimeout(() => n.remove(), 3800);
}

// ---------- modal ----------
export function modal(title, bodyEl, wide = false) {
  const root = document.getElementById("modalRoot");
  const back = el(`<div class="modal-back"></div>`);
  const m = el(`<div class="modal ${wide ? "wide" : ""}"><h3></h3><div class="mbody"></div>
    <div class="actions"><button class="btn" data-close></button></div></div>`);
  m.querySelector("h3").textContent = title;
  m.querySelector(".mbody").appendChild(bodyEl);
  const closeBtn = m.querySelector("[data-close]");
  closeBtn.textContent = t("common.close");
  closeBtn.onclick = () => back.remove();
  back.onclick = (e) => { if (e.target === back) back.remove(); };
  back.appendChild(m);
  root.appendChild(back);
  return { close: () => back.remove(), root: m };
}

// ---------- settings change helper ----------
export async function saveSetting(key, value) {
  const body = {};
  body[key] = value;
  await api.saveSettings(body);
}

// ---------- topbar ----------
const NAV = [
  ["dashboard", "dashboard", "▦"],
  ["live", "live", "◉"],
  ["recordings", "recordings", "⏺"],
  ["gallery", "gallery", "▣"],
  ["events", "events", "≡"],
  ["ai", "ai", "✦"],
  ["people", "people", "☺"],
  ["camera", "camera", "⚙"],
  ["diagnostics", "diagnostics", "🩺"],
  ["settings", "settings", "🔧"],
];

function buildNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = "";
  for (const [id, key, ico] of NAV) {
    const a = el(`<a href="#/${id}"><span class="ico">${ico}</span><span></span></a>`);
    a.lastElementChild.textContent = t(`nav.${key}`);
    a.dataset.route = id;
    nav.appendChild(a);
  }
}

function markActive(route) {
  document.querySelectorAll("#nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.route === route);
  });
}

// ---------- router ----------
const views = {
  dashboard: renderDashboard,
  live: renderLive,
  recordings: renderRecordings,
  gallery: renderGallery,
  events: renderEvents,
  ai: renderAI,
  people: renderPeople,
  camera: renderCamera,
  diagnostics: renderDiagnostics,
  settings: renderSettings,
};

let currentCleanup = null;
export async function route() {
  const hash = location.hash.replace(/^#\/?/, "") || "dashboard";
  const [routeName, query] = hash.split("?");
  const params = new URLSearchParams(query || "");
  const fn = views[routeName] || views.dashboard;
  markActive(routeName in views ? routeName : "dashboard");
  if (currentCleanup) { try { currentCleanup(); } catch {} currentCleanup = null; }
  const container = $view();
  container.innerHTML = `<div class="card">${t("common.loading")}</div>`;
  try {
    currentCleanup = await fn(container, params) || null;
  } catch (err) {
    console.error(err);
    container.innerHTML = "";
    container.appendChild(el(`<div class="card"><h3>⚠ ${err.message}</h3></div>`));
  }
}
window.addEventListener("hashchange", route);

// ---------- topbar live state ----------
function startClock() {
  const clock = document.getElementById("clock");
  const tick = () => {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    clock.textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  };
  tick();
  setInterval(tick, 1000);
}

function updateCamPill(snap) {
  const pill = document.getElementById("camPill");
  const dot = document.getElementById("camDot");
  const text = document.getElementById("camText");
  const modeBadge = document.getElementById("modeBadge");
  const cam = snap.camera || {};
  const mode = snap.mode;
  let label, cls;
  if (mode === "demo") { label = `DEMO · ${t("state.demo")}`; cls = "warn"; }
  else if (mode === "live") {
    const st = cam.state;
    label = `${t("state." + st, st)} · ${cam.config?.host || ""}`;
    cls = st === "streaming" ? "ok" : (st === "error" ? "err" : "warn");
  } else { label = t("state.idle"); cls = ""; }
  text.textContent = label;
  pill.className = `cam-pill ${cls}`;
  modeBadge.hidden = mode !== "demo";
  modeBadge.textContent = "DEMO MODE";

  const stats = document.getElementById("topStats");
  stats.hidden = !(snap.stream?.fps > 0);
  if (snap.stream?.fps > 0) {
    document.getElementById("stFps").textContent = fmt.num(Math.round(snap.stream.fps));
    document.getElementById("stRes").textContent =
      snap.stream.width ? `${snap.stream.width}×${snap.stream.height}` : "—";
  }
  // rec badge
  const rec = document.getElementById("recBadge");
  rec.hidden = !snap.recording?.active;
  if (snap.recording?.active) {
    document.getElementById("recTime").textContent = fmt.clock(snap.recording.elapsed);
  }
}

// ---------- language ----------
function setupLang() {
  const btn = document.getElementById("langToggle");
  const apply = () => {
    btn.textContent = getLang() === "ar" ? "EN" : "ع";
  };
  apply();
  btn.onclick = () => {
    setLang(getLang() === "ar" ? "en" : "ar");
    apply();
    buildNav();
    route();
  };
}

// ---------- mobile menu ----------
function setupMenu() {
  const sb = document.getElementById("sidebar");
  document.getElementById("menuToggle").onclick = () => sb.classList.toggle("open");
  document.addEventListener("click", (e) => {
    if (sb.classList.contains("open") && !sb.contains(e.target)
        && !e.target.closest("#menuToggle")) sb.classList.remove("open");
  });
}

// ---------- boot ----------
async function boot() {
  buildNav();
  setupLang();
  setupMenu();
  startClock();
  feed.connect();
  feed.on("state", (msg) => {
    state.last = msg;
    updateCamPill(msg);
  });
  feed.on("event", (msg) => {
    const ev = msg.event || {};
    if (ev.type) {
      const kind = ev.type.startsWith("attendance") ? "ok" :
        (ev.type === "recognition_unknown" || ev.type === "motion" ? "" : "ok");
      toast(`${t("ev.type." + ev.type, ev.type)} — ${ev.message || ""}`, kind);
    }
  });
  if (!location.hash) location.hash = "#/dashboard";
  route();
}

boot();
