// Settings — camera IP/ports, FTP credentials, timeouts + Test Connection.
import { t } from "../i18n.js";
import { api } from "../api.js";
import { toast, setStatePill } from "../app.js";

export async function renderSettings(container) {
  container.innerHTML = `
  <div class="card">
    <h2>⚙ ${t("settings.title")}</h2>
    <p class="sub">${t("settings.sub")}</p>
    <div class="form-grid" id="form"><div class="empty">${t("common.loading")}</div></div>
    <div class="actions" style="justify-content:flex-start">
      <button class="btn primary" id="btnSave">💾 ${t("settings.save")}</button>
      <button class="btn" id="btnTest">🧪 ${t("settings.test")}</button>
    </div>
    <div id="testOut" style="margin-top:14px"></div>
  </div>`;

  const form = container.querySelector("#form");
  let items = [];
  try {
    const r = await api.settings();
    items = r.settings || [];
  } catch (err) { toast(err.message, "err"); }

  form.innerHTML = items.map((s) => `
    <div class="field" data-key="${s.key}">
      <label>${s.label_ar}</label>
      <input type="${s.masked ? "password" : (s.type === "int" || s.type === "float") ? "number"
        : "text"}" step="any" value="${s.masked ? "" : String(s.value)}"
        placeholder="${s.masked ? t("settings.pw_hint") : ""}"
        data-key="${s.key}" data-type="${s.type}">
    </div>`).join("");

  container.querySelector("#btnSave").onclick = async () => {
    const flat = {};
    form.querySelectorAll("input").forEach((inp) => {
      if (inp.value === "" && inp.type === "password") return; // unchanged
      const ty = inp.dataset.type;
      flat[inp.dataset.key] = ty === "int" ? parseInt(inp.value, 10)
        : ty === "float" ? parseFloat(inp.value) : inp.value;
    });
    try {
      await api.saveSettings(flat);
      toast("✓ " + t("settings.saved"), "ok");
    } catch (err) { toast("✕ " + err.message, "err"); }
  };

  container.querySelector("#btnTest").onclick = async () => {
    const out = container.querySelector("#testOut");
    out.innerHTML = `<div class="empty">${t("test.running")}</div>`;
    try {
      const r = await api.test();
      setStatePill(r.http.ok ? "CONTROL_READY" : "REACHABLE");
      const row = (label, x) => `
        <div class="svc ${x.ok ? "ok" : "err"}">
          <div><div class="svc-name">${label}</div>
          <div class="svc-port">${x.ok ? (x.latency_ms ?? "—") + " ms" : (x.error || "").slice(0, 80)}</div></div>
          <div class="svc-mark">${x.ok ? "✓" : "✕"}</div></div>`;
      out.innerHTML = `<div class="svc-grid">
        ${row(t("svc.http"), r.http)}${row(t("svc.ftp"), r.ftp)}${row(t("svc.telnet"), r.telnet)}
      </div><p class="sub">${t("test.note")}</p>`;
    } catch (err) {
      out.innerHTML = `<div class="empty">✕ ${err.message}</div>`;
    }
  };

  return () => {};
}
