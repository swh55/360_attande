// Recordings: management table with playback + download.
import { t, fmt } from "../i18n.js";
import { api } from "../api.js";
import { el, toast } from "../app.js";

export async function renderRecordings(container) {
  const data = await api.get("/api/recordings?limit=300");
  const recs = data.recordings || [];

  container.innerHTML = `
    <div class="card">
      <h3>⏺ ${t("recs.title")} <span class="grow"></span>
        <span class="badge muted">${fmt.num(recs.length)}</span></h3>
      <div style="max-height:70vh; overflow-y:auto">
        <table class="table"><thead><tr>
          <th></th><th>${t("gal.type")}</th><th>${t("gal.duration")}</th>
          <th>${t("gal.size")}</th><th>Date</th><th style="width:150px"></th>
        </tr></thead><tbody id="recRows"></tbody></table>
      </div>
    </div>`;

  const rows = container.querySelector("#recRows");
  if (!recs.length) {
    rows.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--text-2)">${t("gal.empty")}</td></tr>`;
    return () => {};
  }
  for (const r of recs) {
    const tr = el(`<tr>
      <td><img src="${api.thumbUrl("recording", r.id)}" style="width:86px; height:50px; object-fit:cover; border-radius:7px; background:#101820" loading="lazy"></td>
      <td>${r.filename || ""}</td>
      <td>${fmt.dur(r.duration_sec)}</td>
      <td>${fmt.bytes(r.size_bytes)}</td>
      <td>${fmt.ts(r.created_at)}</td>
      <td style="display:flex; gap:6px">
        <a class="btn sm primary" href="#/live?recording=${r.id}">▶ 360°</a>
        <a class="btn sm" href="${api.mediaFileUrl("recording", r.id)}&download=true">⬇</a>
        <button class="btn sm danger">🗑</button>
      </td></tr>`);
    tr.querySelector(".danger").onclick = async () => {
      if (!confirm(t("common.confirm_delete"))) return;
      await api.del(`/api/gallery/recording/${r.id}`);
      tr.remove();
      toast("✓", "ok");
    };
    rows.appendChild(tr);
  }
  return () => {};
}
