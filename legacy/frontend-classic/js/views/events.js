// Events: timeline + stats + type filters.
import { t, fmt } from "../i18n.js";
import { api, feed } from "../api.js";
import { el, modal } from "../app.js";

const TYPES = ["motion", "person", "face", "recognition_known", "recognition_unknown",
  "person_left", "attendance_in", "attendance_out"];

export async function renderEvents(container) {
  const [stats, today] = await Promise.all([api.eventsStats(24), api.eventsToday()]);

  container.innerHTML = `
    <div class="grid cols-4" style="margin-bottom:14px">
      <div class="card"><div class="kpi"><span class="num">${fmt.num(stats.total)}</span><span class="lbl">${t("ev.stats")}</span></div></div>
      ${TYPES.slice(0, 3).map((ty) => `
        <div class="card"><div class="kpi"><span class="num">${fmt.num(stats.by_type?.[ty] || 0)}</span>
        <span class="lbl">${t("ev.type." + ty)}</span></div></div>`).join("")}
    </div>
    <div class="card">
      <h3>≡ ${t("ev.timeline")}</h3>
      <div class="chips" id="evChips">
        <button class="chip active" data-f="">${t("gal.all")}</button>
        ${TYPES.map((ty) => `<button class="chip" data-f="${ty}">${t("ev.type." + ty)}</button>`).join("")}
      </div>
      <div id="evTimeline" style="max-height:66vh; overflow-y:auto">${t("common.loading")}</div>
    </div>`;

  let filter = "";
  const box = container.querySelector("#evTimeline");

  async function load() {
    const qs = new URLSearchParams({ days: "2" });
    if (filter) qs.set("type", filter);
    const data = await api.get("/api/events/timeline?" + qs);
    const groups = data.timeline || [];
    box.innerHTML = "";
    if (!groups.length) {
      box.innerHTML = `<div style="color:var(--text-2); padding:16px; text-align:center">${t("ev.empty")}</div>`;
      return;
    }
    for (const g of groups.slice().reverse()) {
      const group = el(`<div class="tl-group"><h4>${g.group}</h4></div>`);
      for (const ev of g.events.slice().reverse()) {
        const sevClass = ev.severity === "warning" ? "warn" : ev.severity === "success" ? "ok" : "muted";
        const row = el(`<div class="tl-event">
          <span class="time">${fmt.time(ev.ts)}</span>
          ${ev.snapshot_path ? `<img class="snap" src="/api/media/file?kind=photo&path_mode=event&id=0" data-snap="${ev.id}">` : ""}
          <div class="body">
            <div class="m">${ev.message || t("ev.type." + ev.type, ev.type)}
              <span class="badge ${sevClass}" style="margin-inline-start:8px">${t("ev.type." + ev.type, ev.type)}</span></div>
            <div class="x">${ev.confidence != null ? t("ev.confidence") + ": " + Math.round(ev.confidence * 100) + "%" : ""}
              ${ev.track_id != null ? " · #" + ev.track_id : ""} ${ev.lon != null ? " · " + ev.lon.toFixed(1) + "°, " + (ev.lat ?? 0).toFixed(1) + "°" : ""}</div>
          </div></div>`);
        const snap = row.querySelector(".snap");
        if (snap) {
          // snapshot images are served via events snapshot endpoint
          snap.src = `/api/events/${ev.id}/snapshot`;
          snap.onerror = () => snap.remove();
          snap.onclick = () => {
            const body = el(`<div><img style="width:100%; border-radius:10px" src="${snap.src}"></div>`);
            modal(t("ev.type." + ev.type, ev.type), body, true);
          };
        }
        group.appendChild(row);
      }
      box.appendChild(group);
    }
  }

  container.querySelectorAll("#evChips .chip").forEach((chip) => {
    chip.onclick = () => {
      container.querySelectorAll("#evChips .chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      filter = chip.dataset.f;
      load();
    };
  });

  const unSub = feed.on("event", () => load());
  await load();
  return () => unSub();
}
