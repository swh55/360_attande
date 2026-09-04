// Capability Center — every Hub feature ranked by GUARANTEE of working.
// Backend evidence: RC-6/RC-7 (single-shot streamer + two-stage wedge cascade).
// The view itself is non-invasive: it never opens a connection to 7679;
// the ONLY stream-touching action (decisive probe) is double-gated behind
// an explicit warning modal AND a backend broker guard.
import { t, getLang } from "../i18n.js";
import { api } from "../api.js";
import { el, toast, modal } from "../app.js";

const STATUS_CLS = {
  up: "ok", live: "ok", ready: "ok", recommended: "ok",
  "needs-session": "warn", "transports-down": "warn",
  down: "err", blocked: "muted",
};
const TIER_ICONS = ["🛡", "▶", "🎛", "⛔"];

function guaranteeBadge(f) {
  const g = f.guarantee;
  const cls = g === "always" ? "ok" : g === "while_session" ? "ok"
    : g === "control_plane" ? "warn" : "err";
  return `<span class="badge ${cls}">${t("cap.g." + g)}</span>`;
}

function statusPill(f) {
  const cls = STATUS_CLS[f.status] || "muted";
  const label = t("cap.s." + f.status, f.status);
  return `<span class="badge ${cls}">${label}</span>`;
}

function featureRow(f) {
  const lang = getLang();
  const title = f.title[lang] || f.title.en;
  const desc = f.desc[lang] || f.desc.en;
  const icon = TIER_ICONS[f.tier];
  const actionBtn = f.action ? `
    <button class="btn ${f.tier === 3 ? "" : "primary"}" data-act="${f.id}"
      ${f.tier === 3 ? "disabled" : ""} style="min-height:44px">
      ${f.action.type === "goto" ? t("cap.go") : t("cap.action_run")}</button>` : "";
  return `
  <div class="cap-feature" data-fid="${f.id}">
    <div class="cap-head">
      <span class="cap-ico">${icon}</span>
      <div class="cap-title">
        <b>${title}</b> ${guaranteeBadge(f)}
        <div class="cap-desc">${desc}</div>
      </div>
      <div class="cap-side">
        ${statusPill(f)}
        <small class="cap-detail">${f.detail || ""}</small>
        ${actionBtn}
      </div>
    </div>
    <div class="cap-ev">${t("cap.evidence")}: <code>${f.evidence}</code></div>
  </div>`;
}

function tierSection(tier, features) {
  const list = features.filter((f) => f.tier === tier);
  if (!list.length) return "";
  return `
  <div class="card cap-tier">
    <h3>${TIER_ICONS[tier]} ${t("cap.tier" + tier)}</h3>
    <div class="cap-list">${list.map(featureRow).join("")}</div>
  </div>`;
}

export async function renderCapabilities(container) {
  container.innerHTML = `
  <div style="display:flex; flex-direction:column; gap:16px">
    <div class="card cap-hero">
      <h3>🧭 ${t("cap.title")}</h3>
      <p class="cap-sub">${t("cap.subtitle")}</p>
      <div class="cap-hero-grid">
        <div class="kv"><span>${t("cap.state")}</span><b id="capClass">—</b></div>
        <div class="kv"><span>${t("cap.stage")}</span><b id="capStage">—</b></div>
        <div class="kv"><span>${t("cap.updated")}</span><b id="capUpdated">—</b></div>
      </div>
      <div class="btn-row" style="margin-top:10px">
        <button class="btn" id="capRefresh">🔄 ${t("cap.refresh")}</button>
        <button class="btn warn" id="capDecisive">🧪 ${t("cap.decisive")}</button>
        <span class="badge muted">${t("cap.autorefresh")}</span>
      </div>
      <div id="capProbeBox" class="cap-probe" hidden></div>
    </div>
    <div id="capTiers">${t("common.loading")}</div>
  </div>`;

  let lastReport = null;
  let timer = null;
  let stopped = false;

  const classMeta = (klass) => ({
    IDLE_READY: ["ok", t("cap.stage0")],
    SESSION_ACTIVE: ["ok", t("cap.session_active")],
    STREAMER_WEDGED: ["err", t("cap.stage1")],
    OFFLINE: ["err", t("cap.stage2")],
  }[klass] || ["muted", klass]);

  const powercycleWizard = () => {
    const body = el(`<div><pre style="white-space:pre-wrap; font-size:13px; line-height:1.8"></pre>
      <div class="btn-row"><button class="btn primary"></button></div></div>`);
    body.querySelector("pre").textContent = t("cap.powercycle_steps");
    const re = body.querySelector("button");
    re.textContent = `🔄 ${t("cap.powercycle_recheck")}`;
    re.onclick = () => { back.close(); refresh(); };
    const back = modal(t("cap.powercycle_title"), body);
  };

  const runRearm = async (apply) => {
    if (!confirm(t("cap.rearm_confirm"))) return;
    toast("🔧 …", "ok");
    try {
      await api.rearmCam(apply);
      toast("🔧 ✓ " + t("cap.rearm_done"), "ok");
    } catch (err) {
      toast("🔧 " + t("cap.rearm_fail") + " — " + err.message, "err");
    }
    refresh();
  };

  const runAction = (f) => {
    const a = f.action;
    if (!a) return;
    if (a.type === "goto") location.hash = a.route;
    else if (a.type === "powercycle") powercycleWizard();
    else if (a.type === "rearm") runRearm(a.apply);
    else if (a.type === "photo") location.hash = "#/camera";
  };

  const runDecisiveProbe = () => {
    const body = el(`<div><p style="line-height:1.8"></p>
      <div class="btn-row">
        <button class="btn warn"></button>
        <button class="btn" data-x></button>
      </div></div>`);
    body.querySelector("p").textContent = t("cap.decisive_warn");
    const go = body.querySelector(".btn.warn");
    go.textContent = t("cap.decisive_run");
    const cancel = body.querySelector("[data-x]");
    cancel.textContent = t("common.close");
    const back = modal(t("cap.decisive_warn_title"), body);
    cancel.onclick = () => back.close();
    go.onclick = async () => {
      back.close();
      const box = container.querySelector("#capProbeBox");
      box.hidden = false;
      box.innerHTML = `<div class="kv"><span>${t("cap.probe_result")}</span><b>…</b></div>`;
      try {
        const r = await api.probeArming();
        const key = "cap.probe_" + (r.result || "");
        box.innerHTML = `
          <div class="kv"><span>${t("cap.probe_result")}</span>
            <b class="badge ${r.result === "ARMED_BODY_OK" ? "ok"
              : r.result === "REFUSED_SESSION_HELD" ? "warn" : "err"}">
              ${t(key, r.result)}</b></div>
          <div class="kv"><span>${t("cap.detail")}</span><b>${r.detail || r.error || ""}</b></div>
          ${r.bytes != null ? `<div class="kv"><span>bytes</span><b>${r.bytes} B (${r.elapsed_s}s, HTTP ${r.http_status})</b></div>` : ""}`;
      } catch (err) {
        box.innerHTML = `<div class="kv"><span>${t("cap.probe_result")}</span>
          <b class="badge err">${err.message}</b></div>`;
      }
      refresh();
    };
  };

  async function refresh() {
    if (stopped) return;
    try {
      const r = await api.capabilities();
      lastReport = r;
      const [cls, clsText] = classMeta(r.camera_class);
      const classEl = container.querySelector("#capClass");
      classEl.innerHTML = `<span class="badge ${cls}">${r.camera_class}</span> ${clsText}`;
      container.querySelector("#capStage").textContent =
        String(r.stage ?? 0);
      container.querySelector("#capUpdated").textContent =
        `${r.ts} · ${r.elapsed_ms} ms`;
      const tiers = container.querySelector("#capTiers");
      tiers.innerHTML =
        tierSection(0, r.features) + tierSection(1, r.features) +
        tierSection(2, r.features) + tierSection(3, r.features);
      // wire actions
      tiers.querySelectorAll("[data-act]").forEach((btn) => {
        btn.onclick = () => {
          const f = (lastReport?.features || []).find((x) => x.id === btn.dataset.act);
          if (f) runAction(f);
        };
      });
    } catch (err) {
      container.querySelector("#capTiers").innerHTML =
        `<div class="card">⚠ ${err.message}</div>`;
    }
  }

  container.querySelector("#capRefresh").onclick = refresh;
  container.querySelector("#capDecisive").onclick = runDecisiveProbe;
  await refresh();
  timer = setInterval(refresh, 10000);

  return () => { stopped = true; if (timer) clearInterval(timer); };
}
