// Gallery: photos + videos with filters, favorites, 360 playback, delete.
import { t, fmt } from "../i18n.js";
import { api } from "../api.js";
import { el, modal, toast } from "../app.js";

const FILTERS = [
  ["all", "gal.all"], ["photos", "gal.photos"], ["videos", "gal.videos"],
  ["equirect", "proj.equirect"], ["favorites", "gal.favorites"],
];

export async function renderGallery(container) {
  container.innerHTML = `
    <div class="chips" id="galChips">
      ${FILTERS.map(([id, key], i) => `<button class="chip ${i === 0 ? "active" : ""}" data-f="${id}">${t(key)}</button>`).join("")}
    </div>
    <div id="galStats" class="grid cols-4" style="margin-bottom:14px"></div>
    <div class="gal-grid" id="galGrid">${t("common.loading")}</div>`;

  let filter = "all";
  const grid = container.querySelector("#galGrid");

  async function loadStats() {
    const s = await api.galleryStats();
    const box = container.querySelector("#galStats");
    box.innerHTML = `
      <div class="card"><div class="kpi"><span class="num">${fmt.num(s.total.videos)}</span><span class="lbl">${t("gal.videos")}</span></div></div>
      <div class="card"><div class="kpi"><span class="num">${fmt.num(s.total.photos)}</span><span class="lbl">${t("gal.photos")}</span></div></div>
      <div class="card"><div class="kpi"><span class="num">${fmt.bytes(s.total.storage_bytes)}</span><span class="lbl">${t("gal.size")}</span></div></div>
      <div class="card"><div class="kpi"><span class="num">${fmt.bytes(s.disk.free_bytes)}</span><span class="lbl">Disk free</span></div></div>`;
  }

  async function load() {
    grid.innerHTML = t("common.loading");
    let items = [];
    const fav = filter === "favorites";
    if (filter === "all" || filter === "videos" || filter === "favorites") {
      const recs = await api.get(`/api/recordings?limit=200${fav ? "&favorite=true" : ""}`);
      items.push(...recs.recordings.map((r) => ({ kind: "recording", ...r })));
    }
    if (filter === "all" || filter === "photos" || filter === "equirect" || filter === "favorites") {
      const qs = new URLSearchParams({ limit: "200" });
      if (fav) qs.set("favorite", "true");
      if (filter === "equirect") qs.set("projection", "equirect");
      const ph = await api.get("/api/photos?" + qs);
      items.push(...ph.photos.map((p) => ({ kind: "photo", ...p })));
    }
    items.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));

    grid.innerHTML = "";
    if (!items.length) {
      grid.innerHTML = `<div class="card" style="grid-column:1/-1; text-align:center; color:var(--text-2)">${t("gal.empty")}</div>`;
      return;
    }
    for (const item of items) {
      grid.appendChild(card(item));
    }
  }

  function card(item) {
    const isVideo = item.kind === "recording";
    const badge = isVideo ? "VIDEO" : String(item.projection || "photo").toUpperCase();
    const node = el(`<div class="gal-item">
      <img class="thumb" loading="lazy" src="${api.thumbUrl(item.kind, item.id)}" alt="">
      <span class="type-tag">${badge}</span>
      <button class="fav" title="favorite">${item.favorite ? "★" : "☆"}</button>
      <div class="meta">
        <div class="t">${item.filename || ""}</div>
        <div class="s">
          <span>${fmt.ts(item.created_at)}</span>
          ${isVideo ? `<span>${fmt.dur(item.duration_sec)}</span>` : ""}
          <span>${fmt.bytes(item.size_bytes)}</span>
        </div>
      </div>
      <div class="gal-actions">
        ${isVideo ? `<button class="btn sm primary" data-play>▶ 360°</button>` : `<button class="btn sm primary" data-play>🔍</button>`}
        <a class="btn sm" data-dl href="${api.mediaFileUrl(item.kind, item.id)}&download=true">⬇</a>
        <button class="btn sm danger" data-del>🗑</button>
      </div>
    </div>`);
    const thumb = node.querySelector(".thumb");
    thumb.onerror = () => { thumb.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='100' height='60'%3E%3Crect width='100' height='60' fill='%23101820'/%3E%3C/svg%3E"; };
    node.querySelector(".fav").onclick = async (e) => {
      e.stopPropagation();
      const r = await api.post(`/api/gallery/${item.kind}/${item.id}/favorite`);
      e.target.textContent = r.favorite ? "★" : "☆";
    };
    node.querySelector("[data-del]").onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(t("common.confirm_delete"))) return;
      await api.del(`/api/gallery/${item.kind}/${item.id}`);
      node.remove();
      loadStats();
      toast("✓", "ok");
    };
    const open = () => {
      if (isVideo) {
        const body = el(`<div><div class="viewer-wrap" style="min-height:420px">
          <canvas id="pvCanvas"></canvas></div></div>`);
        const m = modal(item.filename, body, true);
        import("../gl360.js").then(({ GL360, attachInteraction }) => {
          const video = document.createElement("video");
          video.src = api.mediaFileUrl(item.kind, item.id);
          video.loop = true; video.muted = false; video.playsInline = true;
          video.play().catch(() => {});
          const gl = new GL360(body.querySelector("#pvCanvas"));
          gl.setSource(video);
          gl.setMode("perspective");
          attachInteraction(gl, body.querySelector("#pvCanvas"));
          const loop = () => { gl.render(); requestAnimationFrame(loop); };
          loop();
          m.root.closest(".modal-back").addEventListener("DOMNodeRemoved", () => {}, { once: true });
          new MutationObserver((_, obs) => {
            if (!document.body.contains(m.root)) { obs.disconnect(); video.pause(); gl.destroy(); }
          }).observe(document.getElementById("modalRoot"), { childList: true, subtree: false });
        });
      } else {
        const body = el(`<div><img style="width:100%; border-radius:10px" src="${api.mediaFileUrl(item.kind, item.id)}"></div>`);
        modal(item.filename, body, true);
      }
    };
    node.querySelector("[data-play]").onclick = (e) => { e.stopPropagation(); open(); };
    node.addEventListener("click", open);
    return node;
  }

  container.querySelectorAll("#galChips .chip").forEach((chip) => {
    chip.onclick = () => {
      container.querySelectorAll("#galChips .chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      filter = chip.dataset.f;
      load();
    };
  });

  await Promise.all([loadStats(), load()]);
  return () => {};
}
