// Take Photo — verified workflow with staged progress (§31).
// The stages animate while the server runs the REAL workflow; the final
// verdict always comes from the server (PHOTO_READY / FAILED + reason).
import { t } from "../i18n.js";
import { api } from "../api.js";
import { el, toast, lightbox } from "../app.js";

const STAGE_SEQ = [
  ["sending", "photo.sending"],
  ["waiting", "photo.waiting"],
  ["detected", "photo.detected"],
  ["transferring", "photo.transferring"],
];

export async function renderPhoto(container) {
  container.innerHTML = `
  <div class="card">
    <h2>📸 ${t("photo.title")}</h2>
    <p class="sub">${t("photo.sub")}</p>
    <div class="actions" style="justify-content:flex-start">
      <button class="btn primary" id="btnGo">📸 ${t("photo.start")}</button>
    </div>
    <div class="steps" id="steps" hidden></div>
    <div class="photo-result" id="result"></div>
  </div>`;

  const stepsEl = container.querySelector("#steps");
  const resultEl = container.querySelector("#result");
  const btn = container.querySelector("#btnGo");

  function renderSteps(activeIdx, failIdx = -1, doneUpTo = -1) {
    stepsEl.hidden = false;
    stepsEl.innerHTML = STAGE_SEQ.map(([id, key], i) => {
      let cls = "";
      let ico = `${i + 1}`;
      if (failIdx === i) { cls = "fail"; ico = "✕"; }
      else if (doneUpTo >= i && activeIdx > i) { cls = "done"; ico = "✓"; }
      else if (activeIdx === i) { cls = "active"; ico = "…"; }
      return `<div class="step ${cls}"><span class="ico">${ico}</span>${t(key)}</div>`;
    }).join("");
  }

  btn.onclick = async () => {
    btn.disabled = true;
    resultEl.innerHTML = "";
    let stage = 0;
    renderSteps(0);
    // animate the known workflow while the server works (honest: final
    // state comes from the server response, never assumed)
    const ticker = setInterval(() => {
      stage = Math.min(stage + 1, STAGE_SEQ.length - 1);
      renderSteps(stage);
    }, 6000);
    toast(t("photo.busy"), "ok");
    try {
      const r = await api.takePhoto();
      clearInterval(ticker);
      if (r.state === "PHOTO_READY") {
        renderSteps(STAGE_SEQ.length, -1, STAGE_SEQ.length);
        resultEl.innerHTML = `
          <div class="step done"><span class="ico">✓</span>${t("photo.ready")}</div>
          <div class="kv"><span>${t("files.name")}</span><b>${r.filename}</b></div>
          <div class="kv"><span>${t("files.size")}</span><b class="num">${r.size} B</b></div>
          <div class="kv"><span>SD</span><b class="num">${r.sd_path}</b></div>
          <div class="actions" style="justify-content:flex-start">
            <button class="btn primary" id="btnOpenP">🖼 ${t("photo.open")}</button>
            <button class="btn" id="btnAgain">📸 ${t("photo.again")}</button>
          </div>`;
        resultEl.querySelector("#btnOpenP").onclick = () =>
          lightbox(api.galleryFileUrl(r.id));
        resultEl.querySelector("#btnAgain").onclick = () => {
          resultEl.innerHTML = ""; stepsEl.hidden = true; btn.disabled = false;
        };
        toast("✓ " + t("photo.ready"), "ok");
      } else {
        renderSteps(-1, Math.max(0, (r.workflow || []).length - 1), -1);
        const reason = (r.workflow || []).filter((w) => w.state === "FAILED").at(-1)?.reason
          || t("photo.failed");
        resultEl.innerHTML = `
          <div class="step fail"><span class="ico">✕</span>${t("photo.failed")}</div>
          <p class="sub">${reason}</p>
          <div class="actions" style="justify-content:flex-start">
            <button class="btn" id="btnAgain2">📸 ${t("photo.again")}</button>
          </div>`;
        resultEl.querySelector("#btnAgain2").onclick = () => {
          resultEl.innerHTML = ""; stepsEl.hidden = true; btn.disabled = false;
        };
        toast("✕ " + reason, "err");
      }
    } catch (err) {
      clearInterval(ticker);
      renderSteps(-1, 0, -1);
      resultEl.innerHTML = `<p class="sub">✕ ${err.message}</p>`;
      toast("✕ " + err.message, "err");
    } finally {
      btn.disabled = false;
    }
  };

  return () => {};
}
