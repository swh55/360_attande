// Stable Edition shell — hash router, topbar state, toasts, lightbox.
import { t, getLang, setLang } from "./i18n.js";
import { api } from "./api.js";
import { renderDashboard } from "./views/dashboard.js";
import { renderCamera } from "./views/camera.js";
import { renderFiles } from "./views/files.js";
import { renderPhoto } from "./views/photo.js";
import { renderSettings } from "./views/settings.js";
import { renderAbout } from "./views/about.js";

export function el(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html.trim();
  return tpl.content.firstElementChild;
}
export const $view = () => document.getElementById("view");

export function toast(msg, kind = "") {
  const box = document.getElementById("toasts");
  const n = el(`<div class="toast ${kind}"></div>`);
  n.textContent = msg;
  box.appendChild(n);
  setTimeout(() => n.remove(), 4200);
}

export function lightbox(src) {
  const lb = document.getElementById("lightbox");
  document.getElementById("lightboxImg").src = src;
  lb.hidden = false;
}
document.getElementById("lbClose").onclick = () => {
  document.getElementById("lightbox").hidden = true;
};
document.getElementById("lightbox").onclick = (e) => {
  if (e.target.id === "lightbox") e.target.hidden = true;
};

const NAV = [
  ["dashboard", "nav.dashboard"],
  ["photo", "nav.photo"],
  ["files", "nav.files"],
  ["camera", "nav.camera"],
  ["settings", "nav.settings"],
  ["about", "nav.about"],
];

function buildNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = "";
  for (const [id, key] of NAV) {
    const a = el(`<a href="#/${id}"></a>`);
    a.textContent = t(key);
    a.dataset.route = id;
    nav.appendChild(a);
  }
}

const views = {
  dashboard: renderDashboard,
  photo: renderPhoto,
  files: renderFiles,
  camera: renderCamera,
  settings: renderSettings,
  about: renderAbout,
};

let cleanup = null;
export async function route() {
  const name = (location.hash.replace(/^#\/?/, "") || "dashboard").split("?")[0];
  const fn = views[name] || views.dashboard;
  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.route === (name in views ? name : "dashboard")));
  if (cleanup) { try { cleanup(); } catch {} cleanup = null; }
  const c = $view();
  c.innerHTML = `<div class="card empty">${t("common.loading")}</div>`;
  try {
    cleanup = await fn(c) || null;
  } catch (err) {
    console.error(err);
    c.innerHTML = "";
    c.appendChild(el(`<div class="card"><h3>⚠ ${err.message}</h3></div>`));
  }
}
window.addEventListener("hashchange", route);

// topbar state pill (polled — REST only)
export function setStatePill(state) {
  const pill = document.getElementById("statePill");
  const text = document.getElementById("stateText");
  const cls = state === "CONTROL_READY" ? "ok" : state === "REACHABLE" ? "warn" : "err";
  pill.className = `state-pill ${cls}`;
  text.textContent = t("state." + state, state);
}

function setupLang() {
  const btn = document.getElementById("langToggle");
  const apply = () => { btn.textContent = getLang() === "ar" ? "EN" : "ع"; };
  apply();
  btn.onclick = () => {
    setLang(getLang() === "ar" ? "en" : "ar");
    apply();
    buildNav();
    route();
  };
}

async function pollState() {
  try {
    const st = await api.status();
    setStatePill(st.state);
  } catch {
    setStatePill("OFFLINE");
  }
}

async function boot() {
  buildNav();
  setupLang();
  if (!location.hash) location.hash = "#/dashboard";
  route();
  pollState();
  setInterval(pollState, 7000);
}

boot();
