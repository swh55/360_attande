// People: face database management with photo upload + embeddings.
import { t, fmt } from "../i18n.js";
import { api } from "../api.js";
import { el, modal, toast } from "../app.js";

export async function renderPeople(container) {
  container.innerHTML = `
    <div class="card">
      <h3>☺ ${t("ppl.title")} <span class="grow"></span>
      <button class="btn primary" id="btnAddPerson">+ ${t("ppl.add")}</button></h3>
      <div class="people-grid" id="pplGrid">${t("common.loading")}</div>
    </div>`;

  const grid = container.querySelector("#pplGrid");

  async function load() {
    const data = await api.people();
    const people = data.people || [];
    grid.innerHTML = "";
    if (!people.length) {
      grid.innerHTML = `<div style="grid-column:1/-1; text-align:center; color:var(--text-2); padding:20px">${t("ppl.empty")}</div>`;
      return;
    }
    for (const p of people) {
      const card = el(`<div class="person-card">
        ${p.photo_path ? `<img class="ph" src="/api/people/${p.id}/photo">` : `<div class="ph-empty">☺</div>`}
        <div class="pm">
          <div class="n">${p.name}</div>
          <div class="d">
            ${p.person_code ? `<span>🆔 ${p.person_code}</span>` : ""}
            ${p.department ? `<span>🏢 ${p.department}</span>` : ""}
            <span><span class="badge ${p.embeddings ? "ok" : "muted"}">${t("ppl.embeddings")}: ${p.embeddings}</span></span>
            <span><span class="badge accent">${t("ppl.recognitions")}: ${p.recognitions}</span></span>
          </div>
        </div>
        <div class="pa">
          <button class="btn sm" data-photo>📷</button>
          <button class="btn sm danger" data-del>🗑</button>
        </div>
      </div>`);
      card.querySelector("[data-photo]").onclick = () => uploadPhoto(p);
      card.querySelector("[data-del]").onclick = async () => {
        if (!confirm(t("common.confirm_delete"))) return;
        await api.del(`/api/people/${p.id}`);
        load();
        toast("✓", "ok");
      };
      card.querySelector(".ph, .ph-empty").onclick = () => uploadPhoto(p);
      grid.appendChild(card);
    }
  }

  function uploadPhoto(p) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.onchange = async () => {
      const file = input.files[0];
      if (!file) return;
      const form = new FormData();
      form.append("photo", file);
      try {
        const r = await api.postForm(`/api/people/${p.id}/photo`, form);
        toast(r.embedding_created ? "✓ embedding ✓" : "✓ (no face found)", r.embedding_created ? "ok" : "");
        load();
      } catch (err) { toast(err.message, "err"); }
    };
    input.click();
  }

  container.querySelector("#btnAddPerson").onclick = () => {
    const body = el(`<div>
      <div class="field"><label>${t("ppl.name")} *</label><input type="text" id="fName"></div>
      <div class="field"><label>${t("ppl.code")}</label><input type="text" id="fCode"></div>
      <div class="field"><label>${t("ppl.dept")}</label><input type="text" id="fDept"></div>
      <div class="field"><label>${t("ppl.notes")}</label><textarea id="fNotes" rows="2"></textarea></div>
      <div class="field"><label>${t("ppl.photo")}</label><input type="file" id="fPhoto" accept="image/*"></div>
    </div>`);
    const m = modal(t("ppl.add"), body);
    const actions = m.root.querySelector(".actions");
    const saveBtn = el(`<button class="btn primary">${t("common.save")}</button>`);
    saveBtn.onclick = async () => {
      const name = body.querySelector("#fName").value.trim();
      if (!name) return toast(t("ppl.name") + " *", "err");
      const form = new FormData();
      form.append("name", name);
      form.append("person_code", body.querySelector("#fCode").value);
      form.append("department", body.querySelector("#fDept").value);
      form.append("notes", body.querySelector("#fNotes").value);
      const photo = body.querySelector("#fPhoto").files[0];
      if (photo) form.append("photo", photo);
      try {
        await api.postForm("/api/people?", form);
        m.close();
        load();
        toast("✓", "ok");
      } catch (err) { toast(err.message, "err"); }
    };
    actions.prepend(saveBtn);
  };

  await load();
  return () => {};
}
