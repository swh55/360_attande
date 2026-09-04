// Camera view: status details, connect/disconnect, test, profiles.
import { t, fmt } from "../i18n.js";
import { api, feed } from "../api.js";
import { el, toast } from "../app.js";

export async function renderCamera(container) {
  const [cam, profiles] = await Promise.all([api.camStatus(), api.cameraProfiles()]);
  const profileList = profiles.profiles || [];

  container.innerHTML = `
  <div class="grid cols-2" style="align-items:start">
    <div style="display:flex; flex-direction:column; gap:16px">
      <div class="card">
        <h3>📷 ${t("cam.title")} <span class="grow"></span><span id="camStateBadge"></span></h3>
        <div class="btn-row" style="margin-bottom:14px">
          <button class="btn primary" id="btnConnect">🔗 ${t("cam.connect")}</button>
          <button class="btn" id="btnDisconnect">⛔ ${t("cam.disconnect")}</button>
          <button class="btn" id="btnRestart">🔄 ${t("cam.restart")}</button>
          <button class="btn warn" id="btnRearm" ${cam.wedged ? "" : "hidden"}>🔧 ${t("cam.rearm")}</button>
          <button class="btn accent" id="btnPhoto">📸 ${t("cam.take_photo")}</button>
          <button class="btn" id="btnTest">🧪 ${t("cam.test")}</button>
        </div>
        <div class="kv"><span>${t("cam.phase")}</span><b id="camPhase">—</b></div>
        <div class="kv"><span>${t("cam.ip")}</span><b>${cam.config?.host}</b></div>
        <div class="kv"><span>${t("cam.port")}</span><b>${cam.config?.port}</b></div>
        <div class="kv"><span>${t("cam.stream_url")}</span><b style="font-size:11.5px; word-break:break-all">${cam.stream_url}</b></div>
        <div class="kv"><span>${t("cam.reachable")}</span><b id="camReach">—</b></div>
        <div class="kv"><span>${t("cam.latency")}</span><b id="camLatency">—</b></div>
        <div class="kv"><span>${t("cam.uptime")}</span><b id="camUptime">—</b></div>
        <div class="kv"><span>${t("cam.last_frame")}</span><b id="camLastFrame">—</b></div>
        <div class="kv"><span>${t("cam.errors")}</span><b id="camErrors">0</b></div>
        <div class="kv"><span>${t("cam.reconnects")}</span><b id="camReconn">0</b></div>
        <div class="kv"><span>${t("cam.codec")}</span><b id="camCodec">—</b></div>
        <div class="kv"><span>${t("cam.resolution")}</span><b id="camRes">—</b></div>
      </div>
      <div class="card" id="testResult" hidden><h3>🧪 ${t("cam.test")}</h3><div id="testBody"></div></div>
      <div class="card">
        <h3>📊 ${t("cam.counters")}</h3>
        <div id="counterTable" style="color:var(--text-2); font-size:12.5px">—</div>
        <p style="color:var(--text-2); font-size:11.5px">${t("cam.counters_hint")}</p>
      </div>
    </div>

    <div style="display:flex; flex-direction:column; gap:16px">
      <div class="card">
        <h3>📐 ${t("cam.profiles")}</h3>
        ${profileList.map((p) => `
          <div class="kv"><span><b>${p.name}</b><br>
            <small style="color:var(--text-2)">${p.host}:${p.port}${p.stream_path}</small></span>
            <span class="badge ${cam.config?.profile === p.id ? "ok" : "muted"}">${p.id}</span></div>`).join("")}
        <p style="color:var(--text-2); font-size:12px; line-height:1.6">
          ${profileList[0]?.notes || ""}</p>
      </div>
      <div class="card">
        <h3>🗒 Camera log</h3>
        <pre id="camLog" style="font-size:11px; max-height:260px; overflow-y:auto; white-space:pre-wrap; color:var(--text-2)"></pre>
      </div>
    </div>
  </div>`;

  const stateBadge = container.querySelector("#camStateBadge");
  const setState = (s, demo, wedged) => {
    stateBadge.innerHTML = `<span class="badge ${s === "streaming" ? "ok" : s === "error" ? "err" : s === "disconnected" || demo ? "muted" : "warn"}">
      ${demo ? t("state.demo") : wedged ? t("state.wedged") : t("state." + s, s)}</span>`;
  };
  setState(cam.state, false, cam.wedged);

  const phaseEl = container.querySelector("#camPhase");
  const setPhase = (ph) => { if (ph) phaseEl.textContent = ph; };
  setPhase(cam.phase);

  container.querySelector("#btnConnect").onclick = async () => {
    try { await api.connect(); toast("🔗 …", "ok"); }
    catch (err) { toast(err.message, "err"); }
  };
  container.querySelector("#btnDisconnect").onclick = async () => {
    await api.disconnect(); toast("⛔", "ok");
  };
  container.querySelector("#btnRestart").onclick = async () => {
    toast("🔄 " + t("cam.restarting"), "ok");
    try {
      await api.restartCam();
      toast("🔄 ✓ " + t("cam.restart_ok"), "ok");
    } catch (err) { toast(err.message, "err"); }
  };
  btnRearm.onclick = async () => {
    btnRearm.disabled = true;
    toast("🔧 " + t("cam.rearm_running"), "ok");
    try {
      const r = await api.rearmCam(true);   // invasive kill included — explicit user action
      const ra = r.rearm || {};
      const okActions = (ra.actions || []).filter((a) => a.ok).length;
      toast(`🔧 ${t("cam.rearm_ok")} (${okActions}/${(ra.actions || []).length})`, "ok");
    } catch (err) {
      toast("🔧 " + t("cam.rearm_fail") + " — " + err.message, "err");
    } finally { btnRearm.disabled = false; }
  };
  container.querySelector("#btnPhoto").onclick = async () => {
    const btn = container.querySelector("#btnPhoto");
    btn.disabled = true;
    toast("📸 " + t("cam.photo_sending"), "ok");
    try {
      const r = await api.takePhoto();
      toast(`📸 ✓ ${r.filename} (${fmt.num(r.size)} B) — ${t("cam.photo_done")}`, "ok");
    } catch (err) {
      const p2 = err.payload;
      if (p2) toast(`📸 ${p2.code}: ${p2.message}`, "err");
      else toast(err.message, "err");
    } finally { btn.disabled = false; }
  };
  container.querySelector("#btnTest").onclick = async () => {
    const box = container.querySelector("#testResult");
    const body = container.querySelector("#testBody");
    box.hidden = false;
    body.textContent = "…";
    try {
      const r = await api.testCam();
      body.innerHTML = `
        <div class="kv"><span>TCP ${r.host}:${r.port}</span><b>${r.tcp ? "✓" : "✕"}</b></div>
        ${r.latency_ms ? `<div class="kv"><span>${t("cam.latency")}</span><b>${r.latency_ms} ms</b></div>` : ""}
        <div class="kv"><span>HTTP stream</span><b>${r.http ? "✓ " + r.http_status : "✕"}</b></div>
        ${r.content_type ? `<div class="kv"><span>Content-Type</span><b>${r.content_type}</b></div>` : ""}
        ${r.bytes_received ? `<div class="kv"><span>Bytes received</span><b>${fmt.num(r.bytes_received)}</b></div>` : ""}
        ${r.error ? `<div class="kv"><span>Error</span><b style="color:var(--red)">${r.error}</b></div>` : ""}`;
    } catch (err) { body.textContent = err.message; }
  };

  // pipeline counters table — shows exactly WHERE frames stop
  const counterBox = container.querySelector("#counterTable");
  let lastCounters = "";
  function renderCounters(c) {
    if (!c) return;
    const rows = JSON.stringify(c);
    if (rows === lastCounters) return;
    lastCounters = rows;
    const items = [
      ["HTTP bytes", c.http_bytes], ["Chunks", c.chunks],
      ["00VD", c.chunks_00vd], ["H264 bytes", c.h264_bytes],
      ["AUs", c.aus], ["FFmpeg input", c.ffmpeg_input_bytes],
      ["Decoded frames", c.decoded_frames], ["FrameBus", c.published_frames],
      ["Viewer", c.viewer_frames],
    ];
    counterBox.innerHTML = items.map(([k, v]) =>
      `<div class="kv"><span>${k}</span><b>${fmt.num(v || 0)}</b></div>`).join("");
  }
  renderCounters(cam.counters);

  // live updates
  const unsub = feed.on("state", (msg) => {
    const cam2 = msg.camera || {};
    setState(cam2.state, msg.mode === "demo", cam2.wedged);
    setPhase(cam2.phase);
    renderCounters(msg.counters);
    container.querySelector("#camReach").textContent =
      cam2.camera_reachable ? t("cam.yes") : t("cam.no");
    container.querySelector("#camLatency").textContent =
      cam2.latency_ms != null ? cam2.latency_ms + " ms" : "—";
    container.querySelector("#camUptime").textContent =
      cam2.uptime_sec ? fmt.dur(cam2.uptime_sec) : "—";
    container.querySelector("#camLastFrame").textContent =
      cam2.last_frame_age != null ? cam2.last_frame_age + " s" : t("cam.never");
    container.querySelector("#camErrors").textContent = fmt.num(cam2.error_count || 0);
    container.querySelector("#camReconn").textContent = fmt.num(cam2.reconnects || 0);
    container.querySelector("#camCodec").textContent = cam2.stream?.codec || "—";
    container.querySelector("#camRes").textContent =
      cam2.stream?.width ? `${cam2.stream.width}×${cam2.stream.height}` : "—";
  });

  // camera log tail
  const logPre = container.querySelector("#camLog");
  const loadLog = async () => {
    try {
      const r = await api.get("/api/system/logs?domain=camera&tail=40");
      logPre.textContent = r.lines.join("\n") || "—";
      logPre.scrollTop = logPre.scrollHeight;
    } catch {}
  };
  loadLog();
  const logTimer = setInterval(loadLog, 4000);

  return () => { unsub(); clearInterval(logTimer); };
}
