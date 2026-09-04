// Photos & Files — SD browser (DCIM, view/download/open only) + local gallery.
import { t, fmtSize } from "../i18n.js";
import { api } from "../api.js";
import { el, toast, lightbox } from "../app.js";

const TYPE_ICONS = { JPG: "🖼", MP4: "🎬", Other: "📄" };

export async function renderFiles(container) {
  container.innerHTML = `
  <div class="card">
    <h2>🗂 ${t("files.title")}</h2>
    <p class="sub">${t("files.sub")}</p>
    <div class="row-btns" style="margin-bottom:12px">
      <button class="mini-btn" id="tabSd">💳 ${t("files.sd")}</button>
      <button class="mini-btn" id="tabGal">🖼 ${t("files.gallery")}</button>
      <button class="mini-btn" id="btnRefresh">🔄 ${t("files.refresh")}</button>
    </div>
    <div id="paneSd"></div>
    <div id="paneGal" hidden></div>
  </div>`;

  const paneSd = container.querySelector("#paneSd");
  const paneGal = container.querySelector("#paneGal");

  async function loadSd() {
    paneSd.innerHTML = `<div class="empty">${t("common.loading")}</div>`;
    try {
      const r = await api.media();
      if (!r.files.length) {
        paneSd.innerHTML = `<div class="empty">${t("files.empty")}</div>`;
        return;
      }
      paneSd.innerHTML = `
      <div style="overflow-x:auto">
      <table class="tbl">
        <thead><tr><th>${t("files.name")}</th><th>${t("files.date")}</th>
          <th>${t("files.size")}</th><th>${t("files.type")}</th><th></th></tr></thead>
        <tbody>${r.files.map((f) => `
          <tr>
            <td><b>${TYPE_ICONS[f.type] || "📄"} ${f.name}</b></td>
            <td class="num">${f.mtime || "—"}</td>
            <td class="num">${fmtSize(f.size)}</td>
            <td><span class="badge ${f.type === "JPG" ? "ok" : f.type === "MP4" ? "warn" : "muted"}">${f.type}</span></td>
            <td><div class="row-btns">
              ${f.type === "JPG" ? `<button class="mini-btn" data-view="${f.path}">${t("files.view")}</button>` : ""}
              <button class="mini-btn" data-dl="${f.path}" data-size="${f.size}">${t("files.download")}</button>
            </div></td>
          </tr>`).join("")}
        </tbody>
      </table></div>`;
      paneSd.querySelectorAll("[data-view]").forEach((b) => {
        b.onclick = () => lightbox(api.mediaPreviewUrl(b.dataset.view));
      });
      paneSd.querySelectorAll("[data-dl]").forEach((b) => {
        b.onclick = async () => {
          b.disabled = true;
          toast(t("files.downloading"), "ok");
          try {
            const r2 = await api.mediaDownload(b.dataset.dl, Number(b.dataset.size));
            toast(`✓ ${t("files.downloaded")}: ${r2.name}`, "ok");
            loadGallery();
          } catch (err) { toast("✕ " + err.message, "err"); }
          finally { b.disabled = false; }
        };
      });
    } catch (err) {
      paneSd.innerHTML = `<div class="empty">✕ ${t("files.load_err")}<br><small>${err.message}</small></div>`;
    }
  }

  async function loadGallery() {
    paneGal.innerHTML = `<div class="empty">${t("common.loading")}</div>`;
    try {
      const r = await api.gallery();
      if (!r.items.length) {
        paneGal.innerHTML = `<div class="empty">${t("gallery.empty")}</div>`;
        return;
      }
      paneGal.innerHTML = `<div class="grid-media">${r.items.map((it) => `
        <div class="mcard">
          <div class="thumb">${it.type === "JPG"
            ? `<img loading="lazy" src="${api.galleryFileUrl(it.id)}" alt="${it.name}">`
            : "🎬"}</div>
          <div class="meta"><b title="${it.name}">${it.name}</b>
            <span class="num">${it.mtime} · ${fmtSize(it.size)}</span></div>
          <div class="mact">
            ${it.type === "JPG"
              ? `<button data-view="${it.id}">${t("files.view")}</button>`
              : `<button data-open="${it.id}">${t("files.open")}</button>`}
            <button data-save="${it.id}" data-name="${it.name}">${t("gallery.dl")}</button>
          </div>
        </div>`).join("")}</div>`;
      paneGal.querySelectorAll("[data-view]").forEach((b) => {
        b.onclick = () => lightbox(api.galleryFileUrl(b.dataset.view));
      });
      paneGal.querySelectorAll("[data-open]").forEach((b) => {
        b.onclick = () => window.open(api.galleryFileUrl(b.dataset.open), "_blank", "noopener");
      });
      paneGal.querySelectorAll("[data-save]").forEach((b) => {
        b.onclick = () => {
          const a = el(`<a href="${api.galleryFileUrl(b.dataset.save)}"
            download="${b.dataset.name}"></a>`);
          document.body.appendChild(a); a.click(); a.remove();
        };
      });
    } catch (err) {
      paneGal.innerHTML = `<div class="empty">✕ ${err.message}</div>`;
    }
  }

  container.querySelector("#tabSd").onclick = () => {
    paneSd.hidden = false; paneGal.hidden = true; loadSd();
  };
  container.querySelector("#tabGal").onclick = () => {
    paneSd.hidden = true; paneGal.hidden = false; loadGallery();
  };
  container.querySelector("#btnRefresh").onclick = () => { loadSd(); loadGallery(); };

  await loadSd();
  loadGallery();
  return () => {};
}
