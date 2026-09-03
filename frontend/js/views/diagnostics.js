// Diagnostics view: real pipeline diagnostics — pipeline visual, full runner,
// stream inspector, hex viewer, VID0 parser debugger, endianness analyzer,
// structural validation, chunk scanner, decoder probes, frame & snapshot
// diagnostics, captures, metrics, timeline and report export.
import { t, fmt } from "../i18n.js";
import { api, feed } from "../api.js";
import { el, toast } from "../app.js";

const STAGES = ["camera", "network", "http", "container", "chunks",
  "frames", "decoder", "projection", "viewer", "snapshot"];
const ST_ICONS = { PASS: "✓", WARNING: "⚠", FAIL: "✕", NOT_TESTED: "○" };
const ST_KEY = { PASS: "pass", WARNING: "warn", FAIL: "fail", NOT_TESTED: "untested" };
const LAYERS = ["camera", "http", "stream", "parser", "metadata", "decoder", "frame"];

const COMPARE_ROWS = [
  ["VID0 header chunk", "tag(4) + size(4) + fixed fields", "same structure", "✓ identical"],
  ["codec offset", "+16", "+16 (was +20 ✕)", "fixed"],
  ["scale offset", "+32", "+32 (was +36 ✕ → read the VD00 subTag as 1447309360)", "fixed"],
  ["subTag check", "must be 'VD00' @ +36", "validated @ +36 (was not checked)", "now verified"],
  ["fps", "num@+44 / den@+48", "num@+44 / den@+48 (was +52/+56 ✕ → 0.00)", "fixed"],
  ["width/height", "+76 / +80", "+76 / +80 (was +88/+92 ✕ → height read 'LIST')", "fixed"],
  ["keyframe marker", "payload[4] ∈ {64, 38}", "payload[4] ∈ {64, 38}", "✓ identical"],
  ["00VD payload framing", "keep '00 00 00 01' + AU, drop last 5 bytes", "same rule (was: strip first 5 bytes ✕ → corrupt first NAL header)", "fixed"],
  ["chunk size convention", "movi chunks: trust size", "trust size + validate next-tag boundary", "improved"],
  ["desync recovery", "scan forward 4 bytes", "scan to nearest known tag + anomaly log", "improved"],
];

function esc(s) { const d = document.createElement("div"); d.textContent = String(s ?? ""); return d.innerHTML; }

export async function renderDiagnostics(container) {
  container.innerHTML = `
  <div class="card">
    <h3>🩺 ${t("diag.title")} <span class="grow"></span><span id="diagState" class="badge muted">—</span></h3>
    <p class="subtle">${t("diag.desc")}</p>
    <div class="btn-row">
      <button class="btn primary" id="btnRun">▶ ${t("diag.run")}</button>
      <button class="btn" id="btnExportJson">⬇ ${t("diag.export_json")}</button>
      <button class="btn" id="btnExportZip">🗜 ${t("diag.export_zip")}</button>
    </div>
    <div class="progress" id="diagProgress" hidden><div></div></div>
    <div class="subtle" id="diagSession" style="margin-top:6px"></div>
  </div>

  <div class="card" style="margin-top:16px">
    <h3>⛓ ${t("diag.pipeline")}</h3>
    <div class="diag-pipe" id="pipe"></div>
  </div>

  <div class="grid cols-2" style="margin-top:16px; align-items:start">
    <div style="display:flex; flex-direction:column; gap:16px">
      <div class="card"><h3>🔎 ${t("diag.root_cause")}</h3><div id="rca"><span class="subtle">${t("diag.never")}</span></div></div>
      <div class="card"><h3>📡 ${t("diag.live_layers")}</h3>
        <div class="layer-chips" id="layers"></div>
        <div class="subtle" id="stateInfo" style="margin-top:8px"></div></div>
      <div class="card"><h3>🧬 ${t("diag.inspector")}</h3><div id="inspector" class="scroll-y"><span class="subtle">—</span></div></div>
      <div class="card"><h3>🔢 ${t("diag.hex")}</h3>
        <div class="btn-row" style="margin-bottom:8px">
          <input type="number" id="hexOff" value="0" min="0" step="16" style="width:110px" title="${t("diag.offset")}">
          <select id="hexLen" style="width:110px"><option>256</option><option selected>1024</option><option>4096</option></select>
          <button class="btn sm" id="hexLoad">⟳</button>
          <button class="btn sm" id="hexCopy">⧉ ${t("diag.copy")}</button>
          <button class="btn sm" id="hexDl">⬇ ${t("diag.download")}</button>
        </div>
        <div class="hexbox" id="hexBox">—</div>
      </div>
      <div class="card"><h3>🗺 ${t("diag.structural")}</h3><div id="structure" class="scroll-y"><span class="subtle">—</span></div></div>
      <div class="card"><h3>🧲 ${t("diag.scanner")}</h3><div id="scan" class="scroll-y"><span class="subtle">—</span></div></div>
    </div>

    <div style="display:flex; flex-direction:column; gap:16px">
      <div class="card"><h3>🔬 ${t("diag.parser_dbg")}</h3><div id="vid0"><span class="subtle">—</span></div></div>
      <div class="card"><h3>⚖ ${t("diag.endianness")}</h3><div id="endian" class="scroll-y"><span class="subtle">—</span></div></div>
      <div class="card"><h3>📋 ${t("diag.compare")}</h3>
        <div class="table-wrap scroll-y"><table class="table"><thead><tr>
          <th>${t("diag.feature") || "Feature"}</th><th>Gear360App</th><th>Gear360 Hub</th><th>${t("diag.confidence")}</th>
        </tr></thead><tbody>
          ${COMPARE_ROWS.map((r) => `<tr><td><b>${esc(r[0])}</b></td><td>${esc(r[1])}</td><td>${esc(r[2])}</td><td>${esc(r[3])}</td></tr>`).join("")}
        </tbody></table></div>
      </div>
      <div class="card"><h3>🎬 ${t("diag.decoder")}</h3>
        <div id="decInfo"></div>
        <div class="btn-row" style="margin-top:8px">
          <button class="btn sm" id="btnFF">🧪 ${t("diag.test_ffmpeg")}</button>
          <button class="btn sm" id="btnDec">🧪 ${t("diag.test_decoder")}</button>
        </div>
        <pre class="mono ltr" id="decOut" style="font-size:11px; max-height:190px; overflow:auto; margin-top:8px; color:var(--text-2)"></pre>
      </div>
      <div class="card"><h3>🖼 ${t("diag.frame_dia")}</h3>
        <img class="frame-thumb" id="frameThumb" alt="latest frame">
        <div class="subtle" id="frameInfo" style="margin-top:6px">—</div>
      </div>
      <div class="card"><h3>📸 ${t("diag.snapshot_dia")}</h3>
        <button class="btn sm" id="btnSnap">📷 ${t("diag.snapshot_test")}</button>
        <div id="snapOut" style="margin-top:8px"></div>
      </div>
      <div class="card"><h3>💾 ${t("diag.capture_title")}</h3>
        <div class="btn-row">
          <input type="number" id="capMb" value="2" min="0.5" max="32" step="0.5" style="width:90px" title="${t("diag.size_mb")}">
          <button class="btn sm primary" id="btnCap">⏺ ${t("diag.capture_btn")}</button>
        </div>
        <div id="capList" class="scroll-y" style="margin-top:8px"></div>
      </div>
      <div class="card"><h3>📊 ${t("diag.metrics")}</h3><div class="diag-grid" id="metrics"></div></div>
      <div class="card"><h3>⏱ ${t("diag.timeline")}</h3><div id="timeline" class="scroll-y"><span class="subtle">—</span></div></div>
    </div>
  </div>`;

  const $ = (id) => container.querySelector("#" + id);
  const cleanups = [];
  let pollTimer = 0, metTimer = 0, thumbTimer = 0;

  // ---- pipeline visual ----
  function renderPipeline(stageStatus) {
    const pipe = $("pipe");
    pipe.innerHTML = "";
    for (const key of STAGES) {
      const st = (stageStatus && stageStatus[key]) || "NOT_TESTED";
      const d = el(`<div class="diag-stage ${st}" title="${key}">
        <span class="s-ico">${ST_ICONS[st] || "○"}</span>
        <span class="s-name">${t("diag.stage." + key, key)}</span>
        <span class="s-state">${t("diag.st." + (ST_KEY[st] || "untested"))}</span>
      </div>`);
      d.onclick = () => $("rca")?.scrollIntoView({ behavior: "smooth", block: "center" });
      pipe.appendChild(d);
    }
  }
  renderPipeline(null);

  // ---- RCA ----
  function renderRCA(findings) {
    const box = $("rca");
    if (!findings || !findings.length) {
      box.innerHTML = `<span class="subtle">${t("diag.never")}</span>`;
      return;
    }
    box.innerHTML = "";
    for (const f of findings) {
      box.appendChild(el(`<div class="rca conf-${esc(f.confidence)}">
        <h4>${esc(f.id)} · ${esc(f.title)}
          <span class="verdict-badge ${f.confidence === "HIGH" ? "IMPLAUSIBLE" : f.confidence === "MEDIUM" ? "INFO" : "OK"}">${t("diag.confidence")}: ${esc(f.confidence)}</span></h4>
        <ul>${(f.evidence || []).map((e) => `<li class="mono">${esc(e)}</li>`).join("")}</ul>
        ${f.suggested_action ? `<div class="act">→ ${esc(f.suggested_action)}</div>` : ""}
      </div>`));
    }
  }

  // ---- inspector ----
  async function loadInspector() {
    try {
      const r = await api.get("/api/diagnostics/inspector");
      const box = $("inspector");
      if (!r.inspector) { box.innerHTML = `<span class="subtle">${esc(r.note || "—")}</span>`; return; }
      const ins = r.inspector;
      const http = ins.http || {};
      const sigRows = (ins.signatures?.signatures || []).map((s) =>
        `<span class="badge accent mono" style="margin:2px">${esc(s.sig)} @0x${Number(s.offset).toString(16)}</span>`).join("");
      const hexRows = (ins.first_256 || []).slice(0, 8).map((row) =>
        `${String(row.offset).padStart(8, "0")}  ${row.hex}  |${row.ascii}|`).join("\n");
      box.innerHTML = `
        <div class="kv"><span>source</span><b class="mono">${esc(r.source)}</b></div>
        <div class="kv"><span>HTTP status</span><b>${esc(http.status ?? "—")}</b></div>
        <div class="kv"><span>Content-Type</span><b class="mono">${esc(http.content_type || "—")}</b></div>
        <div class="kv"><span>container</span><b>${esc(ins.signatures?.kind || "—")}</b></div>
        <div class="kv"><span>${t("diag.signatures")}</span><b>${sigRows || "—"}</b></div>
        <pre class="hexbox" style="max-height:150px; margin-top:8px">${esc(hexRows)}</pre>`;
    } catch (err) { /* silent */ }
  }

  // ---- hex viewer ----
  async function loadHex() {
    const off = Math.max(0, parseInt($("hexOff").value || "0", 10));
    const len = parseInt($("hexLen").value, 10) || 256;
    try {
      const r = await api.get(`/api/diagnostics/hexdump?offset=${off}&length=${len}`);
      const box = $("hexBox");
      const text = (r.rows || []).map((row) =>
        `${String(row.offset).padStart(8, "0")}  ${row.hex.padEnd(47)}  |${row.ascii}|`).join("\n");
      box.textContent = text || (r.note || "—");
      box.dataset.text = text;
    } catch (err) { toast(err.message, "err"); }
  }
  $("hexLoad").onclick = loadHex;
  $("hexCopy").onclick = async () => {
    try { await navigator.clipboard.writeText($("hexBox").dataset.text || ""); toast("✓", "ok"); }
    catch { toast("clipboard blocked", "err"); }
  };
  $("hexDl").onclick = () => {
    const blob = new Blob([$("hexBox").dataset.text || ""], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `gear360_hexdump_${Date.now()}.txt`;
    a.click();
  };

  // ---- VID0 parser debugger + endianness ----
  async function loadVid0() {
    try {
      const r = await api.get("/api/diagnostics/vid0");
      const box = $("vid0"), ebox = $("endian");
      if (!r.vid0) {
        box.innerHTML = `<span class="subtle">${esc(r.note || "—")}</span>`;
        ebox.innerHTML = `<span class="subtle">—</span>`;
        return;
      }
      const v = r.vid0;
      const rowHtml = (f) => `<tr class="${f.legacy ? "legacy-row" : ""}">
        <td><b>${esc(f.name)}</b>${f.legacy ? `<br><small class="subtle">${t("diag.legacy_slot")}</small>` : ""}</td>
        <td class="mono">+${f.offset}</td>
        <td class="mono ltr">${esc(f.raw_hex)}</td>
        <td class="mono ltr">${esc(f.ascii)}</td>
        <td class="mono">${f.value_be ?? "—"}</td>
        <td><span class="verdict-badge ${esc(f.verdict)}">${esc(f.verdict)}</span>
          ${f.note ? `<div class="subtle" style="margin-top:3px">${esc(f.note)}</div>` : ""}</td></tr>`;
      box.innerHTML = `
        <div class="kv"><span>source</span><b class="mono">${esc(r.source)}</b></div>
        <div class="kv"><span>verdict</span><b><span class="verdict-badge ${v.verdict === "VALID" ? "OK" : "IMPLAUSIBLE"}">${esc(v.verdict)}</span></b></div>
        <div class="table-wrap"><table class="table"><thead><tr>
          <th>${t("diag.field") || "Field"}</th><th>Off</th><th>Raw</th><th>ASCII</th><th>u32be</th><th>Verdict</th>
        </tr></thead><tbody>${v.fields.map(rowHtml).join("")}</tbody></table></div>
        <div class="subtle" style="margin-top:8px">${esc(v.summary)}</div>`;
      // endianness: main fields only
      const main = v.fields.filter((f) => !f.legacy);
      ebox.innerHTML = main.map((f) => `
        <div style="margin-bottom:10px">
          <b>${esc(f.name)}</b> <span class="subtle mono">@+${f.offset}</span>
          <table class="table"><tbody>${(f.interpretations || []).map((i2) => `
            <tr><td class="mono">${esc(i2.interp)}</td>
            <td class="mono ltr">${esc(i2.value)}</td>
            <td>${i2.plausible ? '<span class="plausible-yes">✓</span>' : '<span class="plausible-no">✕</span>'}
                ${i2.note ? `<span class="subtle"> ${esc(i2.note)}</span>` : ""}</td></tr>`).join("")}
          </tbody></table></div>`).join("");
    } catch (err) { /* silent */ }
  }

  // ---- structure + scan ----
  async function loadStructureScan() {
    try {
      const r = await api.get("/api/diagnostics/structure");
      const box = $("structure");
      if (!r.structure) { box.innerHTML = `<span class="subtle">${esc(r.note || "—")}</span>`; }
      else {
        const st = r.structure;
        const rows = (st.rows || []).slice(0, 60).map((row) => `<tr>
          <td class="mono">${esc(row.tag)}</td>
          <td class="mono ltr">0x${Number(row.offset).toString(16)}</td>
          <td class="mono">${row.declared_size ?? "—"}</td>
          <td class="mono ltr">${row.next_offset != null ? "0x" + Number(row.next_offset).toString(16) : "—"}</td>
          <td>${row.boundary_ok === true ? '<span class="plausible-yes">✓</span>' :
               row.boundary_ok === false ? `<span class="plausible-no">✕ ${esc(row.note || "invalid boundary")}</span>` : "—"}</td></tr>`).join("");
        box.innerHTML = `<div class="kv"><span>boundaries</span><b>${st.valid_rows} ✓ / ${st.invalid_rows} ✕</b></div>
          <table class="table"><thead><tr><th>Tag</th><th>Offset</th><th>Size</th><th>Next</th><th>OK</th></tr></thead><tbody>${rows}</tbody></table>
          ${st.truncated ? '<div class="subtle">…</div>' : ""}`;
      }
      const s = await api.get("/api/diagnostics/scan");
      const sbox = $("scan");
      if (!s.scan) { sbox.innerHTML = `<span class="subtle">${esc(s.note || "—")}</span>`; }
      else {
        const counts = Object.entries(s.scan.counts || {}).map(([k, v]) =>
          `<span class="badge accent mono" style="margin:2px">${esc(k)} × ${fmt.num(v)}</span>`).join("");
        const occ = (s.scan.occurrences || []).slice(0, 60).map((o) => `<tr>
          <td class="mono">${esc(o.tag)}</td><td class="mono ltr">0x${Number(o.offset).toString(16)}</td>
          <td>${o.aligned ? '<span class="plausible-yes">✓</span>' : '<span class="plausible-no">unaligned</span>'}</td></tr>`).join("");
        sbox.innerHTML = `<div class="kv"><span>total</span><b>${fmt.num(s.scan.total)}</b></div>
          <div style="margin:6px 0">${counts}</div>
          <table class="table"><thead><tr><th>Tag</th><th>Offset</th><th>Aligned</th></tr></thead><tbody>${occ}</tbody></table>`;
      }
    } catch (err) { /* silent */ }
  }

  // ---- decoder diagnostics ----
  async function loadDecoder() {
    try {
      const r = await api.get("/api/diagnostics/decoder");
      const box = $("decInfo");
      const ff = r.ffmpeg || {};
      const p = r.pipeline || {};
      const d = p.decoder;
      box.innerHTML = `
        <div class="kv"><span>FFmpeg</span><b>${ff.found ? `<span class="plausible-yes">✓</span> <span class="mono ltr">${esc(ff.path)}</span>` : `<span class="plausible-no">✕</span> ${esc(ff.error || "")}`}</b></div>
        <div class="kv"><span>version</span><b class="mono ltr">${esc(ff.version || "—")}</b></div>
        ${d ? `
        <div class="kv"><span>codec</span><b class="mono">${esc(d.codec)} ${d.width}×${d.height}</b></div>
        <div class="kv"><span>process</span><b>${d.running ? '<span class="plausible-yes">RUNNING</span>' : '<span class="plausible-no">STOPPED</span>'}</b></div>
        <div class="kv"><span>${t("diag.frames") || "Frames"}</span><b>${fmt.num(d.frames_out)} (dropped ${fmt.num(d.dropped)})</b></div>
        <div class="kv"><span>${t("diag.decode_fps") || "Decode FPS"}</span><b>${d.decode_fps}</b></div>
        <div class="kv"><span>${t("diag.last_stderr") || "Last stderr"}</span><b class="mono ltr" style="font-size:11px">${esc((d.stderr_tail || []).slice(-2).join(" | ") || d.last_error || "—")}</b></div>`
        : `<div class="kv"><span>decoder</span><b>${esc(p?.decoder_block_reason || "not running")}</b></div>`}`;
    } catch (err) { /* silent */ }
  }
  $("btnFF").onclick = async () => {
    $("decOut").textContent = "…";
    try { $("decOut").textContent = JSON.stringify(await api.post("/api/diagnostics/test/ffmpeg"), null, 2); }
    catch (err) { $("decOut").textContent = String(err); }
  };
  $("btnDec").onclick = async () => {
    $("decOut").textContent = "…";
    try { $("decOut").textContent = JSON.stringify(await api.post("/api/diagnostics/test/decoder"), null, 2); }
    catch (err) { $("decOut").textContent = String(err); }
  };

  // ---- frame diagnostics ----
  function refreshThumb() {
    const img = $("frameThumb");
    img.src = `/api/diagnostics/frame-thumb?ts=${Date.now()}`;
  }
  $("frameThumb").onerror = () => {
    $("frameInfo").textContent = "✕ " + (t("diag.st.fail") + " — no decoded frame");
  };
  $("frameThumb").onload = () => {
    $("frameInfo").textContent = `✓ ${$("frameThumb").naturalWidth}×${$("frameThumb").naturalHeight} · BGR24 → JPEG`;
  };
  refreshThumb();
  thumbTimer = setInterval(refreshThumb, 5000);

  // ---- snapshot diagnostics ----
  $("btnSnap").onclick = async () => {
    const out = $("snapOut");
    out.innerHTML = `<span class="subtle">…</span>`;
    try {
      const r = await api.get("/api/stream/snapshot?projection=equirect&yaw=0&fov=90");
      out.innerHTML = `<span class="plausible-yes">✓ ${esc(r.filename)}</span>
        <div class="subtle mono ltr">${esc(r.path || "")}</div>`;
    } catch (err) {
      const p2 = err.payload;
      if (p2) {
        out.innerHTML = `<div class="rca conf-HIGH">
          <h4>${esc(p2.code || "ERROR")}</h4>
          <p>${esc(p2.message || "")}</p>
          <ul>${Object.entries(p2.evidence || {}).map(([k, v]) => `<li class="mono"><b>${esc(k)}</b>: ${esc(v)}</li>`).join("")}</ul>
          <div class="act">→ ${esc(p2.suggested_action || "")}</div>
          <div class="btn-row" style="margin-top:8px"><button class="btn sm primary" id="goDiag2">🩺 ${t("nav.diagnostics")}</button></div>
        </div>`;
        out.querySelector("#goDiag2").onclick = () => { location.hash = "#/diagnostics"; };
      } else {
        out.innerHTML = `<span class="plausible-no">✕ ${esc(err.message)}</span>`;
      }
    }
  };

  // ---- captures ----
  async function loadCaptures() {
    try {
      const r = await api.get("/api/diagnostics/captures");
      const box = $("capList");
      if (!(r.captures || []).length) { box.innerHTML = `<span class="subtle">—</span>`; return; }
      box.innerHTML = "";
      for (const c of r.captures.slice(0, 12)) {
        const row = el(`<div class="kv"><span class="mono ltr" style="font-size:11.5px">${esc(c.name)}<br>
          <small class="subtle">${fmt.bytes(c.size)} · ${esc((c.meta && (c.meta.http_status + " · " + (c.meta.content_type || ""))) || "")}</small></span>
          <span style="display:flex; gap:6px"><a class="btn sm" href="/api/diagnostics/captures/${encodeURIComponent(c.name)}/download">⬇</a>
          <button class="btn sm">✕</button></span></div>`);
        row.querySelector("button").onclick = async () => {
          await api.del(`/api/diagnostics/captures/${encodeURIComponent(c.name)}`);
          loadCaptures();
        };
        box.appendChild(row);
      }
    } catch { /* silent */ }
  }
  $("btnCap").onclick = async () => {
    const mb = parseFloat($("capMb").value) || 2;
    toast("⏺ …", "");
    try {
      const r = await api.post(`/api/diagnostics/captures?mb=${mb}`);
      if (r.error) toast(r.error, "err");
      else toast(`✓ ${r.name} (${fmt.bytes(r.bytes)})`, "ok");
      loadCaptures();
      loadInspector(); loadStructureScan(); loadVid0(); loadHex();
    } catch (err) { toast(err.message, "err"); }
  };
  loadCaptures();

  // ---- metrics ----
  function renderMetrics(m) {
    const items = [
      [fmt.bytes(m.bytes_per_sec || 0) + "/s", t("diag.bytes_sec") || "Bytes/s"],
      [m.latency_ms != null ? m.latency_ms + " ms" : "—", t("diag.latency") || "Latency"],
      [m.queue_depth != null ? fmt.num(m.queue_depth) : "—", t("diag.queue") || "Queue depth"],
      [fmt.num(m.dropped || 0), t("diag.dropped") || "Dropped"],
      [String(m.fps ?? 0), "FPS"],
      [fmt.num(m.decoded_frames || 0), t("diag.frames") || "Decoded"],
      [m.cpu_percent != null ? m.cpu_percent + "%" : "—", t("diag.cpu") || "CPU"],
      [m.ram_mb != null ? m.ram_mb + " MB" : "—", t("diag.ram") || "RAM"],
    ];
    $("metrics").innerHTML = items.map(([v, k]) =>
      `<div class="metric"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`).join("");
  }
  async function loadMetrics() {
    try { renderMetrics(await api.get("/api/diagnostics/metrics")); } catch { /* silent */ }
  }
  loadMetrics();
  metTimer = setInterval(loadMetrics, 3000);

  // ---- live layers (WS) ----
  cleanups.push(feed.on("state", (msg) => {
    const pv = msg.pipeline || {};
    const box = $("layers");
    if (!box) return;
    const layers = pv.layers || {};
    box.innerHTML = "";
    for (const key of LAYERS) {
      const l = layers[key] || { state: "unknown", detail: "" };
      const chip = el(`<span class="layer-chip ${esc(l.state)}" title="${esc(l.detail || "")}">
        <span class="ld"></span>${t("diag.layer." + key, key)}</span>`);
      chip.onclick = () => $("rca")?.scrollIntoView({ behavior: "smooth" });
      box.appendChild(chip);
    }
    const fpsChip = el(`<span class="layer-chip ${pv.fps > 0 ? "ok" : "unknown"}"><span class="ld"></span>FPS ${pv.fps ?? 0}</span>`);
    fpsChip.onclick = () => { location.hash = "#/diagnostics"; };
    box.appendChild(fpsChip);
    $("stateInfo").textContent = `${pv.state || "—"} · ${pv.reason || ""}`;
  }));

  // ---- timeline ----
  function renderTimeline(steps) {
    const box = $("timeline");
    if (!steps || !steps.length) { box.innerHTML = `<span class="subtle">—</span>`; return; }
    box.innerHTML = steps.map((s) => `<div class="kv">
      <span><span class="mono">${esc(String(s.ts).slice(11, 19) || "")}</span> ${ST_ICONS[s.status] || "○"} ${esc(s.label || s.key)}
        ${s.detail ? `<br><small class="subtle mono">${esc(s.detail)}</small>` : ""}</span>
      <b>${fmt.num(Math.round(s.duration_ms || 0))} ms</b></div>`).join("");
  }

  // ---- session rendering ----
  function renderSession(session) {
    if (!session) return;
    $("diagSession").textContent =
      `${t("diag.session")}: ${session.id} · ${session.finished_at} · ${fmt.num(session.duration_ms)} ms` +
      (session.summary?.source ? ` · ${session.summary.source}` : "");
    renderPipeline(session.summary?.stage_status);
    renderRCA(session.findings);
    renderTimeline(session.steps);
    loadInspector(); loadVid0(); loadStructureScan(); loadDecoder(); loadHex();
  }

  // ---- run + poll ----
  async function runDiag() {
    try {
      const r = await api.post("/api/diagnostics/run");
      pollStatus();
    } catch (err) { toast(err.message, "err"); }
  }
  function pollStatus() {
    $("diagProgress").hidden = false;
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const st = await api.get("/api/diagnostics/status");
        const pr = st.progress || {};
        const badge = $("diagState");
        if (st.running) {
          badge.className = "badge warn";
          badge.textContent = `${t("diag.running")} ${pr.done || 0}/${pr.total || 19}`;
          $("diagProgress").querySelector("div").style.width =
            `${Math.round(100 * (pr.done || 0) / (pr.total || 19))}%`;
        } else {
          clearInterval(pollTimer);
          $("diagProgress").hidden = true;
          badge.className = "badge ok";
          badge.textContent = t("diag.done");
          const r = await api.get("/api/diagnostics/latest");
          renderSession(r.session);
          const s = st.summary;
          if (s?.summary) toast(`✓ ${t("diag.done")}: ${s.summary.passed}✓ ${s.summary.failed}✕ ${s.summary.warned}⚠ ${s.summary.untested}○`, s.summary.failed ? "" : "ok");
        }
      } catch { /* keep polling */ }
    }, 1200);
  }
  $("btnRun").onclick = runDiag;

  // ---- exports ----
  $("btnExportJson").onclick = async () => {
    try {
      const rep = await api.get("/api/diagnostics/report");
      const blob = new Blob([JSON.stringify(rep, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `gear360_diagnostic_${new Date().toISOString().replace(/[:T-]/g, "").slice(0, 15)}.json`;
      a.click();
    } catch (err) { toast(err.message, "err"); }
  };
  $("btnExportZip").onclick = () => { location.href = "/api/diagnostics/report/zip"; };

  // ---- boot ----
  (async () => {
    try {
      const st = await api.get("/api/diagnostics/status");
      if (st.latest_id) {
        const r = await api.get("/api/diagnostics/latest");
        renderSession(r.session);
        const badge = $("diagState");
        badge.className = "badge ok";
        badge.textContent = t("diag.done");
      } else if (!st.running) {
        runDiag();          // first ever visit: run automatically (read-only probes)
      } else {
        pollStatus();
      }
    } catch { /* backend older? fine */ }
  })();

  return () => {
    clearInterval(pollTimer);
    clearInterval(metTimer);
    clearInterval(thumbTimer);
    cleanups.forEach((c) => c());
  };
}
