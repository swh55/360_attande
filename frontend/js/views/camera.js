// Camera view — connection state, service details, open camera web.
import { t } from "../i18n.js";
import { api } from "../api.js";
import { el, toast, setStatePill } from "../app.js";

export async function renderCamera(container) {
  container.innerHTML = `
  <div class="card" id="camCard">
    <h2>📷 ${t("cam.title")}</h2>
    <p class="sub">${t("cam.stable_note")}</p>
    <div id="camBody"><div class="empty">${t("common.loading")}</div></div>
    <div class="actions" style="justify-content:flex-start">
      <button class="btn" id="btnRefresh">🔄 ${t("cam.refresh")}</button>
      <button class="btn primary" id="btnWeb">🌐 ${t("dash.open_web")}</button>
    </div>
  </div>`;

  async function refresh() {
    const body = container.querySelector("#camBody");
    try {
      const st = await api.status();
      setStatePill(st.state);
      const s = st.services;
      const svc = (key, r) => `
        <div class="kv"><span>${t(key)} (${r.port})</span>
          <b>${r.ok ? `✓ ${r.latency_ms ?? "—"} ms`
            : `✕ ${(r.error || "").slice(0, 70)}`}</b></div>`;
      body.innerHTML = `
        <div class="kv"><span>${t("cam.model")}</span><b>${t("about.camera")}</b></div>
        <div class="kv"><span>${t("cam.state")}</span>
          <b><span class="badge ${st.state === "CONTROL_READY" ? "ok" : st.state === "REACHABLE" ? "warn" : "err"}">
            ${t("state." + st.state, st.state)}</span></b></div>
        <div class="kv"><span>${t("cam.ip")}</span><b class="num">${st.host}</b></div>
        <div class="kv"><span>${t("cam.web_url")}</span><b class="num">${st.camera_web_url}</b></div>
        <div class="kv"><span>${t("cam.capture_ready")}</span><b>${st.capture_ready ? "✓ " + t("cam.capture_ok") : "✕ " + t("cam.capture_no")}</b></div>
        <h3 style="margin-top:16px">${t("cam.services")}</h3>
        ${svc("svc.http", s.http)}${svc("svc.ftp", s.ftp)}${svc("svc.telnet", s.telnet)}`;
    } catch (err) {
      setStatePill("OFFLINE");
      body.innerHTML = `<div class="empty">✕ ${t("cam.offline")}<br><small>${err.message}</small></div>`;
    }
  }

  container.querySelector("#btnRefresh").onclick = refresh;
  container.querySelector("#btnWeb").onclick = async () => {
    try {
      const w = await api.cameraWeb();
      window.open(w.url, "_blank", "noopener");
    } catch (err) { toast(err.message, "err"); }
  };

  await refresh();
  const timer = setInterval(refresh, 8000);
  return () => clearInterval(timer);
}
