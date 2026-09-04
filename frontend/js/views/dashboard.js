// Dashboard — camera status hero + control services + main actions (§30).
import { t } from "../i18n.js";
import { api } from "../api.js";
import { el, toast, setStatePill } from "../app.js";

export async function renderDashboard(container) {
  container.innerHTML = `
  <div class="card hero" id="hero">
    <div class="cam-state"><span class="dot"></span><span id="heroState">${t("common.loading")}</span></div>
    <div class="ip" id="heroIp">—</div>
    <div class="svc-grid" id="svcGrid"></div>
    <div class="actions">
      <button class="btn primary" id="btnPhoto">📸 ${t("dash.take_photo")}</button>
      <button class="btn" id="btnWeb">🌐 ${t("dash.open_web")}</button>
      <button class="btn" id="btnFiles">🗂 ${t("dash.files")}</button>
      <button class="btn" id="btnTest">🧪 ${t("dash.test")}</button>
    </div>
  </div>
  <div class="card" id="testCard" hidden>
    <h3>🧪 ${t("test.title")}</h3>
    <p class="sub">${t("test.note")}</p>
    <div id="testBody"></div>
  </div>`;

  const svcRow = (name, key, port, ok, latency) => `
    <div class="svc ${ok ? "ok" : "err"}">
      <div><div class="svc-name">${t(key)}</div><div class="svc-port">${port}</div></div>
      <div class="svc-mark">${ok ? "✓" : "✕"}</div>
    </div>`;

  async function refresh() {
    try {
      const st = await api.status();
      setStatePill(st.state);
      const hero = container.querySelector("#hero");
      hero.className = `card hero ${st.state === "CONTROL_READY" ? "ok" : st.state === "REACHABLE" ? "warn" : "err"}`;
      container.querySelector("#heroState").textContent = t("state." + st.state, st.state);
      container.querySelector("#heroIp").textContent =
        `${t("cam.ip")}: ${st.host} · ${st.camera_web_url}`;
      const s = st.services;
      container.querySelector("#svcGrid").innerHTML =
        svcRow("http", "svc.http", `HTTP ${s.http.port}`, s.http.ok, s.http.latency_ms) +
        svcRow("ftp", "svc.ftp", `FTP ${s.ftp.port}`, s.ftp.ok, s.ftp.latency_ms) +
        svcRow("telnet", "svc.telnet", `Telnet ${s.telnet.port}`, s.telnet.ok, s.telnet.latency_ms);
      return st;
    } catch (err) {
      setStatePill("OFFLINE");
      const hero = container.querySelector("#hero");
      hero.className = "card hero err";
      container.querySelector("#heroState").textContent = t("cam.offline");
      container.querySelector("#svcGrid").innerHTML = "";
      return null;
    }
  }

  container.querySelector("#btnPhoto").onclick = () => { location.hash = "#/photo"; };
  container.querySelector("#btnFiles").onclick = () => { location.hash = "#/files"; };
  container.querySelector("#btnWeb").onclick = async () => {
    try {
      const w = await api.cameraWeb();
      window.open(w.url, "_blank", "noopener");
    } catch (err) { toast(err.message, "err"); }
  };
  container.querySelector("#btnTest").onclick = async () => {
    const card = container.querySelector("#testCard");
    const body = container.querySelector("#testBody");
    card.hidden = false;
    body.innerHTML = `<div class="empty">${t("test.running")}</div>`;
    try {
      const r = await api.test();
      const row = (label, r2) => `
        <div class="svc ${r2.ok ? "ok" : "err"}">
          <div><div class="svc-name">${label}</div>
            <div class="svc-port">${r2.ok ? (t("test.latency") + ": " + (r2.latency_ms ?? "—") + " ms")
              : (r2.error || "").slice(0, 90)}</div></div>
          <div class="svc-mark">${r2.ok ? "✓" : "✕"}</div>
        </div>`;
      body.innerHTML = `
        <div class="svc-grid">
          ${row(t("svc.http"), r.http)}
          ${row(t("svc.ftp"), r.ftp)}
          ${row(t("svc.telnet"), r.telnet)}
        </div>
        <p class="sub">${r.http.ok ? "✓ " + t("test.ok_all") : "✕ " + t("test.http_down")}</p>`;
    } catch (err) {
      body.innerHTML = `<div class="empty">✕ ${err.message}</div>`;
    }
    refresh();
  };

  await refresh();
  const timer = setInterval(refresh, 8000);
  return () => clearInterval(timer);
}
