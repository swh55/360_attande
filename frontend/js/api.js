// Gear360 Hub API client + live WebSocket with auto-reconnect.
const base = "";

async function request(method, url, body, isForm) {
  const opts = { method, headers: {} };
  if (body !== undefined && body !== null) {
    if (isForm) {
      opts.body = body; // FormData
    } else if (typeof body === "string" && method === "PUT") {
      // settings endpoint expects a raw JSON object as the body
      opts.headers["Content-Type"] = "application/json";
      opts.body = body;
    } else {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
  }
  const res = await fetch(base + url, opts);
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  if (!res.ok) {
    let msg = `${res.status}`;
    let structured = null;
    if (data && typeof data === "object") {
      if (data.error && data.message) { msg = data.message; structured = data; }
      else if (data.detail) {
        msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      }
    }
    const err = new Error(msg);
    if (structured) err.payload = structured;
    throw err;
  }
  return data;
}

export const api = {
  get: (url) => request("GET", url),
  post: (url, body) => request("POST", url, body),
  postForm: (url, form) => request("POST", url, form, true),
  putRaw: (url, jsonStr) => request("PUT", url, jsonStr),
  put: (url, body) => request("PUT", url, body),
  del: (url) => request("DELETE", url),

  // domain helpers
  status: () => api.get("/api/stream/status"),
  camStatus: () => api.get("/api/camera/status"),
  cameraProfiles: () => api.get("/api/camera/profiles"),
  connect: () => api.post("/api/camera/connect"),
  disconnect: () => api.post("/api/camera/disconnect"),
  restartCam: () => api.post("/api/camera/restart"),
  rearmCam: (apply = false) => api.post(`/api/camera/rearm?apply=${apply ? "true" : "false"}`),
  takePhoto: () => api.post("/api/camera/photo"),
  controlStatus: () => api.get("/api/camera/control/status"),
  counters: () => api.get("/api/stream/counters"),
  testCam: () => api.post("/api/camera/test"),
  health: () => api.get("/api/system/health"),
  info: () => api.get("/api/system/info"),
  aiStatus: () => api.get("/api/ai/status"),
  settings: () => api.get("/api/settings"),
  saveSettings: (values) => api.putRaw("/api/settings", JSON.stringify(values)),
  galleryStats: () => api.get("/api/gallery/stats"),
  events: (qs = "") => api.get("/api/events" + qs),
  eventsStats: (h = 24) => api.get(`/api/events/stats?hours=${h}`),
  eventsToday: () => api.get("/api/events/today"),
  people: () => api.get("/api/people"),
  vcams: () => api.get("/api/vcameras"),
  mediaFileUrl: (kind, id) => `/api/media/file?kind=${kind}&id=${id}`,
  thumbUrl: (kind, id) => `/api/media/thumb?kind=${kind}&id=${id}`,
};

// ---- WebSocket live feed ----
export class LiveFeed {
  constructor() {
    this.subs = new Map();   // topic -> Set(fn)
    this.ws = null;
    this.retry = 0;
    this.alive = false;
  }
  on(topic, fn) {
    if (!this.subs.has(topic)) this.subs.set(topic, new Set());
    this.subs.get(topic).add(fn);
    return () => this.subs.get(topic)?.delete(fn);
  }
  _emit(topic, data) { this.subs.get(topic)?.forEach((fn) => { try { fn(data); } catch {} }); }
  connect() {
    if (this.alive) return;
    this.alive = true;
    const open = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      this.ws = new WebSocket(`${proto}://${location.host}/api/ws`);
      this.ws.onopen = () => { this.retry = 0; this._emit("open", {}); };
      this.ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          this._emit(msg.type || "message", msg);
        } catch {}
      };
      this.ws.onclose = () => {
        this._emit("close", {});
        if (this.alive) {
          this.retry += 1;
          setTimeout(open, Math.min(5000, 500 * this.retry));
        }
      };
      this.ws.onerror = () => this.ws?.close();
      setInterval(() => { if (this.ws?.readyState === 1) this.ws.send("ping"); }, 20000);
    };
    open();
  }
  disconnect() { this.alive = false; this.ws?.close(); }
}

export const feed = new LiveFeed();
