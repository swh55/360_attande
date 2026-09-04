// AI Engine view: toggles, live tracking, director, attendance summary.
import { t, fmt } from "../i18n.js";
import { api, feed } from "../api.js";
import { el, toast, saveSetting } from "../app.js";

export async function renderAI(container) {
  const [ai, settings, attendance] = await Promise.all([
    api.aiStatus(), api.settings(),
    api.get("/api/attendance/summary"),
  ]);
  const S = settings.settings;

  container.innerHTML = `
  <div class="grid cols-2" style="align-items:start">
    <div style="display:flex; flex-direction:column; gap:16px">
      <div class="card">
        <h3>✦ ${t("ai.title")}</h3>
        <div class="setting-row">
          <div class="info"><div class="n">${t("ai.enable")}</div>
            <div class="d">${ai.available?.person ? t("ai.person_backend") + ": " + ai.available.person : "—"}
            ${ai.available?.recognition ? "" : `<br>⚠ ${t("ai.recognition_unavailable")}`}</div></div>
          <div class="ctl"><label class="switch"><input type="checkbox" id="aiEnabled" ${S["ai.enabled"].value ? "checked" : ""}><span class="track"></span></label></div>
        </div>
        <div class="setting-row">
          <div class="info"><div class="n">${t("ai.mode")}</div></div>
          <div class="ctl"><select id="aiMode">
            ${S["ai.mode"].options.map((o) => `<option value="${o}" ${S["ai.mode"].value === o ? "selected" : ""}>${o}</option>`).join("")}
          </select></div>
        </div>
        <div class="setting-row">
          <div class="info"><div class="n">Sample FPS</div></div>
          <div class="ctl"><input type="number" id="aiFps" min="0.5" max="15" step="0.5" value="${S["ai.sample_fps"].value}"></div>
        </div>
        ${["ai.motion.enabled", "ai.person.enabled", "ai.face.enabled", "ai.recognition.enabled",
           "ai.director.enabled", "ai.attendance.enabled"].map((key) => `
          <div class="setting-row">
            <div class="info"><div class="n">${t("set." + key.replace(".", "_"), key)}</div></div>
            <div class="ctl"><label class="switch"><input type="checkbox" data-key="${key}" ${S[key].value ? "checked" : ""}><span class="track"></span></label></div>
          </div>`).join("")}
        <div class="setting-row">
          <div class="info"><div class="n">Confidence</div></div>
          <div class="ctl"><input type="number" id="aiConf" min="0.1" max="0.95" step="0.05" value="${S["ai.confidence"].value}"></div>
        </div>
      </div>

      <div class="card">
        <h3>🕒 ${t("ai.attendance_summary")}</h3>
        ${attendance.rows?.length ? `<table class="table"><thead><tr>
            <th>${t("ppl.name")}</th><th>${t("ai.checkin")}</th><th>${t("ai.checkout")}</th>
          </tr></thead><tbody>
          ${attendance.rows.map((r) => `<tr><td>${r.name}</td><td>${fmt.time(r.check_in)}</td><td>${fmt.time(r.check_out)}</td></tr>`).join("")}
        </tbody></table>`
        : `<div style="color:var(--text-2)">—</div>`}
      </div>
    </div>

    <div style="display:flex; flex-direction:column; gap:16px">
      <div class="card">
        <h3>🎯 ${t("ai.director")}</h3>
        <div class="kv"><span>${t("ai.best_view")}</span><b id="aiBest">—</b></div>
        <div id="aiViews" style="margin-top:8px"></div>
      </div>
      <div class="card">
        <h3>👣 ${t("ai.tracks")}</h3>
        <div style="max-height:300px; overflow-y:auto" id="aiTracks">
          <div style="color:var(--text-2)">${t("ai.no_tracks")}</div>
        </div>
      </div>
      <div class="card">
        <h3>📊 Counters</h3>
        <div class="kv"><span>Frames processed</span><b id="aiFrames">0</b></div>
        <div class="kv"><span>Detections</span><b id="aiDet">0</b></div>
      </div>
    </div>
  </div>`;

  // wire controls
  const aiToggle = container.querySelector("#aiEnabled");
  aiToggle.onchange = async () => {
    await saveSetting("ai.enabled", aiToggle.checked).catch((e) => toast(e.message, "err"));
    toast(t("set.saved"), "ok");
  };
  container.querySelector("#aiMode").onchange = async (e) => {
    await saveSetting("ai.mode", e.target.value); toast(t("set.saved"), "ok");
  };
  container.querySelector("#aiFps").onchange = async (e) => {
    await saveSetting("ai.sample_fps", Number(e.target.value)); toast(t("set.saved"), "ok");
  };
  container.querySelector("#aiConf").onchange = async (e) => {
    await saveSetting("ai.confidence", Number(e.target.value)); toast(t("set.saved"), "ok");
  };
  container.querySelectorAll("input[data-key]").forEach((chk) => {
    chk.onchange = async () => {
      await saveSetting(chk.dataset.key, chk.checked).catch((e) => toast(e.message, "err"));
      toast(t("set.saved"), "ok");
    };
  });

  const unsub = feed.on("detections", (msg) => {
    const ai2 = msg;
    container.querySelector("#aiBest").textContent =
      msg.best_view !== undefined ? `View ${msg.best_view + 1}` : "—";
    container.querySelector("#aiViews").innerHTML = (msg.views || []).map((v) =>
      `<span class="badge ${v.view === msg.best_view ? "accent" : "muted"}" style="margin:2px">
        V${v.view + 1}: ${v.persons}P · ${v.faces}F</span>`).join("");
    const tracks = (msg.views || []).flatMap((v) =>
      (v.boxes || []).filter((b) => b.track_id).map((b) => ({ ...b, view: v.view })));
    const box = container.querySelector("#aiTracks");
    if (!tracks.length) {
      box.innerHTML = `<div style="color:var(--text-2)">${t("ai.no_tracks")}</div>`;
    } else {
      box.innerHTML = `<table class="table"><thead><tr><th>#</th><th>${t("gal.type")}</th>
        <th>View</th><th>Pos</th></tr></thead><tbody>
        ${tracks.map((tr2) => `<tr><td>#${tr2.track_id}</td><td>${tr2.person || tr2.label}</td>
        <td>V${tr2.view + 1}</td><td>${tr2.x?.toFixed(2)}, ${tr2.y?.toFixed(2)}</td></tr>`).join("")}
        </tbody></table>`;
    }
  });

  const stTimer = setInterval(async () => {
    try {
      const s = await api.aiStatus();
      container.querySelector("#aiFrames").textContent = fmt.num(s.frames_processed);
      container.querySelector("#aiDet").textContent = fmt.num(s.detections_total);
    } catch {}
  }, 2000);

  return () => { unsub(); clearInterval(stTimer); };
}
