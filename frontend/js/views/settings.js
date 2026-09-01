// Settings: auto-generated forms from settings registry + FFmpeg test + About.
import { t, fmt } from "../i18n.js";
import { api } from "../api.js";
import { el, toast } from "../app.js";

const CATS = [
  ["camera", "set.cat.camera"], ["streaming", "set.cat.streaming"],
  ["recording", "set.cat.recording"], ["ai", "set.cat.ai"],
  ["storage", "set.cat.storage"], ["ffmpeg", "set.cat.ffmpeg"],
  ["network", "set.cat.network"], ["system", "set.cat.system"],
];

export async function renderSettings(container) {
  const data = await api.settings();
  const S = data.settings;
  const info = await api.info();

  // group by category
  const byCat = {};
  for (const [key, meta] of Object.entries(S)) {
    (byCat[meta.category] ||= []).push([key, meta]);
  }

  container.innerHTML = `
  <div class="set-grid">
    <nav class="set-nav card" id="setNav">
      ${CATS.map(([id, key], i) => `<a href="#set-${id}" class="${i === 0 ? "active" : ""}" data-cat="${id}">${t(key)}</a>`).join("")}
    </nav>
    <div id="setBody" style="display:flex; flex-direction:column; gap:16px">
      ${CATS.map(([id]) => `<div class="card" id="set-${id}"><h3>${t("set.cat." + id)}</h3><div></div></div>`).join("")}
      <div class="card" id="set-about">
        <h3>${t("about.title")}</h3>
        <div class="kv"><span>App</span><b>${info.app} ${info.version}</b></div>
        <div class="kv"><span>Python</span><b>${info.python}</b></div>
        <div class="kv"><span>Platform</span><b>${info.platform}</b></div>
        <div class="kv"><span>FFmpeg</span><b>${info.ffmpeg.found ? "✓ " + info.ffmpeg.path : "✕"}</b></div>
        <div class="kv"><span>DB schema</span><b>v${info.database.schema_version} · ${fmt.bytes(info.database.size_bytes)}</b></div>
        <div class="kv"><span>Face Recognition</span><b>${info.recognition_available ? "✓" : t("common.disabled")}</b></div>
        <p style="color:var(--text-2); font-size:12px">${t("set.restart_note")}</p>
      </div>
    </div>
  </div>`;

  for (const [cat, items] of Object.entries(byCat)) {
    const box = container.querySelector(`#set-${cat} > div`);
    if (!box) continue;
    box.innerHTML = "";
    for (const [key, meta] of items) {
      const row = el(`<div class="setting-row">
        <div class="info"><div class="n">${prettyName(key)}</div>
          <div class="d">${key}${String(meta.value) !== String(meta.default) ? " · default: " + meta.default : ""}</div></div>
        <div class="ctl"></div></div>`);
      const ctl = row.querySelector(".ctl");
      if (meta.type === "bool") {
        const sw = el(`<label class="switch"><input type="checkbox" ${meta.value ? "checked" : ""}><span class="track"></span></label>`);
        sw.querySelector("input").onchange = () => save(key, sw.querySelector("input").checked);
        ctl.appendChild(sw);
      } else if (meta.type === "enum") {
        const sel = el(`<select>${meta.options.map((o) => `<option value="${o}" ${meta.value === o ? "selected" : ""}>${o}</option>`).join("")}</select>`);
        sel.onchange = () => save(key, sel.value);
        ctl.appendChild(sel);
      } else {
        const inp = el(`<input type="${meta.type === "int" || meta.type === "float" ? "number" : "text"}"
          ${meta.min != null ? `min="${meta.min}"` : ""} ${meta.max != null ? `max="${meta.max}"` : ""}
          ${meta.step != null ? `step="${meta.step}"` : ""} value="${meta.value}">`);
        inp.onchange = () => save(key, meta.type === "int" ? parseInt(inp.value) : meta.type === "float" ? parseFloat(inp.value) : inp.value);
        ctl.appendChild(inp);
      }
      box.appendChild(row);
    }
    // FFmpeg extra: test button
    if (cat === "ffmpeg") {
      const row = el(`<div class="setting-row"><div class="info"><div class="n">${t("set.test_ffmpeg")}</div>
        <div class="d" id="ffTestOut">—</div></div><div class="ctl">
        <button class="btn" id="ffTestBtn">🧪 ${t("set.test_ffmpeg")}</button></div></div>`);
      row.querySelector("#ffTestBtn").onclick = async () => {
        const out = row.querySelector("#ffTestOut");
        out.textContent = "…";
        try {
          await api.saveSettings({ "ffmpeg.mode": S["ffmpeg.mode"].value });
          const i2 = await api.info();
          out.textContent = i2.ffmpeg.found ? `✓ ${i2.ffmpeg.version}` : "✕ not found";
        } catch (err) { out.textContent = err.message; }
      };
      box.appendChild(row);
    }
    if (cat === "system") {
      const demoRow = el(`<div class="setting-row"><div class="info">
        <div class="n">Demo Mode</div><div class="d">DEMO MODE badge — no camera needed</div></div>
        <div class="ctl"><label class="switch"><input type="checkbox" id="demoChk" ${S["demo.enabled"]?.value ? "checked" : ""}><span class="track"></span></label></div></div>`);
      demoRow.querySelector("#demoChk").onchange = async () => {
        await api.post(`/api/system/demo?enabled=${demoRow.querySelector("#demoChk").checked}`);
        toast(t("set.saved"), "ok");
      };
      box.appendChild(demoRow);
    }
  }

  // nav scroll
  container.querySelectorAll("#setNav a").forEach((a) => {
    a.onclick = () => {
      container.querySelectorAll("#setNav a").forEach((x) => x.classList.remove("active"));
      a.classList.add("active");
      container.querySelector("#set-" + a.dataset.cat)?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  });

  async function save(key, value) {
    try {
      await api.saveSettings({ [key]: value });
      toast(t("set.saved"), "ok");
    } catch (err) { toast(err.message, "err"); }
  }

  function prettyName(key) {
    const k = "settings." + key.replace(".", "_");
    const label = t(k, null);
    return label && label !== k ? label : key.split(".").slice(1).join(" · ");
  }

  return () => {};
}
