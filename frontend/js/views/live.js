// Live 360 Viewer: full projection modes, FOV, virtual cameras, AI director
// follow, snapshot, recording controls, calibration panel, recording playback.
import { t, fmt } from "../i18n.js";
import { api, feed } from "../api.js";
import { el, modal, toast, saveSetting } from "../app.js";
import { GL360, attachInteraction } from "../gl360.js";

const PROJECTIONS = ["raw", "equirect", "perspective", "fisheye", "tiny"];

export async function renderLive(container, params) {
  const [vcamList, info] = await Promise.all([api.vcams(), api.info()]);
  const playbackId = params.get("recording");

  container.innerHTML = `
  <div class="live-grid">
    <div>
      <div class="viewer-wrap" style="min-height:560px">
        <canvas id="liveCanvas"></canvas>
        <div class="viewer-tag" id="liveTag"></div>
        <div class="live-overlay" id="liveOverlay" hidden>
          <div class="lo-box">
            <div class="lo-title"><span class="lo-dot"></span><span id="loTitle"></span></div>
            <div class="lo-reason" id="loReason"></div>
          </div>
        </div>
        <div class="viewer-ctl">
          <select id="projSel" title="${t("live.projection")}">
            ${PROJECTIONS.map((p) => `<option value="${p}">${t("proj." + p)}</option>`).join("")}
          </select>
          <label style="color:#fff; font-size:12px; display:flex; gap:6px; align-items:center">
            ${t("live.fov")} <input type="range" id="fovRange" min="25" max="115" value="90" style="width:110px">
          </label>
          <button class="vbtn" id="btnReset">⌂ ${t("live.reset")}</button>
          <button class="vbtn" id="btnSnap">📷 ${t("live.snapshot")}</button>
          <button class="vbtn" id="btnRec">⏺ ${t("live.record")}</button>
          <button class="vbtn" id="btnFs">⛶</button>
          <label style="color:#fff; font-size:12px; display:flex; gap:6px; align-items:center">
            <input type="checkbox" id="followChk" style="width:auto"> ${t("live.follow")}
          </label>
        </div>
      </div>
      <div class="layer-chips" id="liveLayers" style="margin-top:10px"></div>
      <div class="card" style="margin-top:14px">
        <h3>🎯 ${t("live.calibration")}</h3>
        <div class="grid cols-3" style="gap:12px" id="calBox"></div>
      </div>
    </div>

    <div style="display:flex; flex-direction:column; gap:16px">
      <div class="card">
        <h3>◉ ${t("live.vcams")} <span class="grow"></span>
          <button class="btn sm primary" id="btnAddVcam">+</button></h3>
        <div id="vcamList"></div>
      </div>
      <div class="card">
        <h3>✦ AI Director</h3>
        <div class="kv"><span>${t("ai.best_view")}</span><b id="dirBest">—</b></div>
        <div class="kv"><span>${t("dash.people_detected")}</span><b id="dirPersons">0</b></div>
        <div class="kv"><span>${t("dash.faces_detected")}</span><b id="dirFaces">0</b></div>
        <div id="dirViews" style="margin-top:8px"></div>
      </div>
      <div class="card">
        <h3>ℹ ${t("live.source")}</h3>
        <div id="srcInfo" style="color:var(--text-2); font-size:12.5px">—</div>
      </div>
    </div>
  </div>`;

  const canvas = container.querySelector("#liveCanvas");
  const tag = container.querySelector("#liveTag");
  let gl, img, video, raf = 0;
  const cleanups = [];
  function esc(s) { const d = document.createElement("div"); d.textContent = String(s ?? ""); return d.innerHTML; }
  let following = false;

  // ---- source: recording playback or live MJPEG ----
  if (playbackId) {
    video = document.createElement("video");
    video.src = api.mediaFileUrl("recording", playbackId);
    video.loop = true; video.muted = true; video.playsInline = true;
    video.play().catch(() => {});
    gl = new GL360(canvas);
    gl.setSource(video);
    gl.setMode("perspective");
  } else {
    img = new Image();
    img.src = "/api/mjpeg";
    img.addEventListener("load", () => gl?.markStale());
    gl = new GL360(canvas);
    gl.setSource(img);
    gl.setMode("perspective");
  }
  const projSel = container.querySelector("#projSel");
  projSel.value = "perspective";

  // calibration from settings
  try {
    const s = await api.settings();
    const v = s.settings;
    gl.setCalibration({
      fovDeg: v["cal.fisheye_fov_deg"].value,
      radiusFactor: v["cal.radius_factor"].value,
      centerXOffset: v["cal.center_offset_x"].value,
      lens0AzimuthDeg: v["cal.lens0_azimuth_deg"].value,
      swapLenses: v["cal.swap_lenses"].value,
      rollDeg: v["cal.roll_deg"].value,
    });
  } catch {}

  attachInteraction(gl, canvas, { onDoubleClick: () => toggleFs() });

  const loop = () => { gl.render(); raf = requestAnimationFrame(loop); };
  loop();

  // ---- controls ----
  projSel.onchange = () => { gl.setMode(projSel.value); gl.resetView(); };
  const fovRange = container.querySelector("#fovRange");
  fovRange.oninput = () => { gl.setViewDeg(undefined, undefined, Number(fovRange.value)); };
  container.querySelector("#btnReset").onclick = () => gl.resetView();

  const toggleFs = () => {
    const wrap = canvas.closest(".viewer-wrap");
    if (document.fullscreenElement) document.exitFullscreen();
    else wrap.requestFullscreen?.();
  };
  container.querySelector("#btnFs").onclick = toggleFs;

  container.querySelector("#btnSnap").onclick = async () => {
    try {
      const q = `projection=${projSel.value}&yaw=${(gl.yaw * 180 / Math.PI).toFixed(1)}&fov=${(gl.fov * 180 / Math.PI).toFixed(0)}`;
      const out = await api.get("/api/stream/snapshot?" + q);
      toast(`✓ ${out.filename}`, "ok");
    } catch (err) {
      const p2 = err.payload;
      if (p2) {
        const body = el(`<div>
          <div class="rca conf-HIGH">
            <h4>${esc(p2.code || "ERROR")}</h4>
            <p>${esc(p2.message || "")}</p>
            <ul>${Object.entries(p2.evidence || {}).map(([k, v]) => `<li class="mono"><b>${esc(k)}</b>: ${esc(v)}</li>`).join("")}</ul>
            <div class="act">→ ${esc(p2.suggested_action || "")}</div>
          </div>
          <div class="btn-row"><button class="btn primary" id="goDiag">🩺 ${t("nav.diagnostics")}</button></div>
        </div>`);
        body.querySelector("#goDiag").onclick = () => { location.hash = "#/diagnostics"; m.close(); };
        const m = modal(t("live.snapshot"), body, true);
      } else {
        toast(err.message, "err");
      }
    }
  };

  const btnRec = container.querySelector("#btnRec");
  const setRecBtn = (active, paused) => {
    btnRec.textContent = active ? (paused ? "▶ " + t("live.resume") : "⏸ " + t("live.stop_rec"))
                                : "⏺ " + t("live.record");
    btnRec.style.background = active ? "rgba(220,38,38,.9)" : "";
  };
  setRecBtn(false);
  btnRec.onclick = async () => {
    try {
      const st = (await api.get("/api/recording/status")).recording;
      if (st?.active) {
        if (st.paused) { await api.post("/api/recording/pause"); setRecBtn(true, false); }
        else { await api.post("/api/recording/stop"); toast("⏹ ✓", "ok"); }
      } else {
        await api.post("/api/recording/start");
        toast("⏺ ✓", "ok");
      }
    } catch (err) { toast(err.message, "err"); }
  };

  // ---- virtual cameras ----
  const vcamBox = container.querySelector("#vcamList");
  function renderVcams(list) {
    vcamBox.innerHTML = "";
    if (!list.length) {
      vcamBox.innerHTML = `<div style="color:var(--text-2); font-size:12.5px">—</div>`;
      return;
    }
    for (const vc of list) {
      const row = el(`<div class="kv"><span>${vc.name}<br>
        <small style="color:var(--text-2)">yaw ${vc.yaw}° · fov ${vc.fov}°</small></span>
        <span style="display:flex; gap:6px">
        <button class="btn sm primary">◉</button><button class="btn sm">✕</button></span></div>`);
      const [goBtn, delBtn] = row.querySelectorAll("button");
      goBtn.onclick = () => {
        gl.setViewDeg(vc.yaw, vc.pitch, vc.fov);
        projSel.value = vc.mode in Object.fromEntries(PROJECTIONS.map((p) => [p, p])) ? vc.mode : "perspective";
        gl.setMode(projSel.value);
      };
      delBtn.onclick = async () => {
        await api.del(`/api/vcameras/${vc.id}`);
        renderVcams(await api.vcams());
      };
      vcamBox.appendChild(row);
    }
  }
  renderVcams(vcamList.virtual_cameras || []);
  container.querySelector("#btnAddVcam").onclick = async () => {
    const yaw = Math.round(gl.yaw * 180 / Math.PI);
    const pitch = Math.round(gl.pitch * 180 / Math.PI);
    const fov = Math.round(gl.fov * 180 / Math.PI);
    const name = `VC-${yaw}-${pitch}-${fov}`;
    await api.post("/api/vcameras", { name, mode: projSel.value, yaw, pitch, fov });
    renderVcams(await api.vcams());
    toast(`✓ ${name}`, "ok");
  };

  // ---- live state ----
  cleanups.push(feed.on("state", (msg) => {
    const demo = msg.mode === "demo";
    tag.hidden = false;
    tag.className = `viewer-tag ${demo ? "demo" : ""}`;
    tag.textContent = playbackId ? "▶ 360° " + t("gal.open360")
      : (demo ? "DEMO MODE" : `${t("state." + msg.mode, msg.mode)} · ${Math.round(msg.stream?.fps || 0)} FPS`);
    const ai = msg.ai || {};
    container.querySelector("#dirBest").textContent =
      ai.best_view !== undefined ? `View ${ai.best_view + 1}` : "—";
    const c = ai.counts || {};
    container.querySelector("#dirPersons").textContent = fmt.num(c.persons || 0);
    container.querySelector("#dirFaces").textContent = fmt.num(c.faces || 0);
    const rec = msg.recording;
    setRecBtn(!!rec?.active, !!rec?.paused);
    const srcInfo = container.querySelector("#srcInfo");
    if (msg.mode === "live") {
      const cam = msg.camera || {};
      const dec = cam.fps !== undefined ? `decoder ${cam.fps} fps` : "";
      const age = cam.last_frame_age !== undefined && cam.last_frame_age !== null
        ? ` · last frame ${cam.last_frame_age}s ago` : "";
      srcInfo.innerHTML = `${cam.stream?.codec || "—"} · ${cam.stream?.width || "?"}×${cam.stream?.height || "?"}`
        + `<br>${cam.stream_url || ""}`
        + `<br><small>${esc(String(cam.decoded_frames ?? 0))} decoded frames · ${esc(dec)}${esc(age)}</small>`;
    } else if (msg.mode === "demo") {
      srcInfo.textContent = "Demo source — 1280×720 · 15 FPS";
    } else {
      srcInfo.textContent = t("live.none");
    }
    // frame_ready gating: the viewer canvas stays covered until real frames
    // flow (or the pipeline errors — then show the reason, never fake live)
    const ov = container.querySelector("#liveOverlay");
    if (ov && !playbackId) {
      const pv = msg.pipeline || {};
      const live = msg.mode === "live";
      const ready = pv.state === "READY";
      if (live && !ready) {
        ov.hidden = false;
        container.querySelector("#loTitle").textContent = pv.state || "CONNECTING";
        container.querySelector("#loReason").textContent =
          pv.reason || "Waiting for the first decoded camera frame…";
      } else {
        ov.hidden = true;
      }
    }
    const lb = container.querySelector("#liveLayers");
    if (lb) {
      const pv = msg.pipeline || {};
      const layers = pv.layers || {};
      const keys = ["camera", "http", "stream", "parser", "metadata", "decoder", "frame"];
      lb.innerHTML = "";
      for (const key of keys) {
        const l = layers[key] || { state: "unknown", detail: "" };
        const chip = el(`<span class="layer-chip ${esc(l.state)}" title="${esc(l.detail || "")}"><span class="ld"></span>${t("diag.layer." + key, key)}</span>`);
        chip.onclick = () => { location.hash = "#/diagnostics"; };
        lb.appendChild(chip);
      }
      const fpsChip = el(`<span class="layer-chip ${pv.fps > 0 ? "ok" : "unknown"}"><span class="ld"></span>FPS ${pv.fps ?? 0}</span>`);
      fpsChip.onclick = () => { location.hash = "#/diagnostics"; };
      lb.appendChild(fpsChip);
    }
  }));

  // director follow
  const followChk = container.querySelector("#followChk");
  followChk.onchange = () => { following = followChk.checked; };
  let lastBest = -1;
  cleanups.push(feed.on("detections", (msg) => {
    if (following && msg.best_view !== lastBest && msg.best_view !== undefined) {
      lastBest = msg.best_view;
      const view = (msg.views || []).find((v) => v.view === msg.best_view);
      if (view) { gl.setViewDeg(view.yaw, view.pitch, 90); }
    }
    if (msg.views) {
      const dirViews = container.querySelector("#dirViews");
      dirViews.innerHTML = (msg.views || []).map((v) =>
        `<span class="badge ${v.view === msg.best_view ? "accent" : "muted"}" style="margin:2px">
          V${v.view + 1}: ${v.persons}P ${v.faces}F</span>`).join("");
    }
  }));

  // ---- calibration panel ----
  const calBox = container.querySelector("#calBox");
  const calFields = [
    ["cal.fisheye_fov_deg", "FOV °", 150, 230, 1],
    ["cal.radius_factor", "Radius ×", 0.7, 1.4, 0.01],
    ["cal.center_offset_x", "Offset ×", -0.1, 0.1, 0.005],
    ["cal.roll_deg", "Roll °", -30, 30, 1],
  ];
  for (const [key, label, min, max, step] of calFields) {
    const wrap = el(`<div class="field"><label>${label}</label>
      <input type="number" min="${min}" max="${max}" step="${step}"></div>`);
    const input = wrap.querySelector("input");
    input.value = gl.cal[{
      "cal.fisheye_fov_deg": "fovDeg", "cal.radius_factor": "radiusFactor",
      "cal.center_offset_x": "centerXOffset", "cal.roll_deg": "rollDeg",
    }[key]];
    input.onchange = async () => {
      const patch = {};
      patch[key] = Number(input.value);
      const obj = {
        "cal.fisheye_fov_deg": "fovDeg", "cal.radius_factor": "radiusFactor",
        "cal.center_offset_x": "centerXOffset", "cal.roll_deg": "rollDeg",
      };
      gl.setCalibration({ [obj[key]]: Number(input.value) });
      await saveSetting(key, Number(input.value)).catch(() => {});
    };
    calBox.appendChild(wrap);
  }

  return () => {
    cancelAnimationFrame(raf);
    cleanups.forEach((c) => c());
    gl?.destroy();
    if (video) { video.pause(); video.remove(); }
    img?.remove();
  };
}
