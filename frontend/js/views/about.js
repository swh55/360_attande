// About — Stable Edition identity (§33).
import { t } from "../i18n.js";
import { api } from "../api.js";

export async function renderAbout(container) {
  container.innerHTML = `<div class="card"><div class="empty">${t("common.loading")}</div></div>`;
  let a = {};
  try { a = await api.about(); } catch { a = {}; }
  container.innerHTML = `
  <div class="card hero">
    <div class="cam-state">📷 Gear360 Hub</div>
    <div class="ip">${t("about.edition")}</div>
    <div style="max-width:430px; margin:18px auto 0; text-align:start">
      <div class="kv"><span>${t("about.camera")}</span><b>SM-R210</b></div>
      <div class="kv"><span>${t("about.live_label")}</span><b>${t("about.live")}</b></div>
      <div class="kv"><span>${t("about.core_label")}</span><b>${t("about.core")}</b></div>
      <div class="kv"><span>Plane</span><b class="num">HTTP 8888 · FTP 21 · Telnet 23</b></div>
    </div>
    <p class="sub" style="margin-top:16px">${a.disabled ? t("about.disabled") : ""}</p>
  </div>`;
  return () => {};
}
