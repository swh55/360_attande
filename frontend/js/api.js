// Stable Edition REST client — no WebSocket (§17).
async function request(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined && body !== null) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  if (!res.ok) {
    const msg = (data && data.detail) ? String(data.detail)
      : (data && data.message) ? String(data.message) : `${res.status}`;
    const err = new Error(msg);
    err.payload = data;
    throw err;
  }
  return data;
}

export const api = {
  get: (url) => request("GET", url),
  post: (url, body) => request("POST", url, body ?? {}),
  put: (url, body) => request("PUT", url, body),

  status: () => api.get("/api/status"),
  services: () => api.get("/api/camera/services"),
  test: () => api.post("/api/camera/test"),
  cameraWeb: () => api.get("/api/camera/web"),
  takePhoto: () => api.post("/api/camera/photo"),
  media: () => api.get("/api/media"),
  mediaDownload: (path, size) => api.post("/api/media/download", { path, size }),
  mediaPreviewUrl: (path) => `/api/media/preview?path=${encodeURIComponent(path)}`,
  gallery: () => api.get("/api/gallery"),
  galleryFileUrl: (id) => `/api/gallery/file?id=${encodeURIComponent(id)}`,
  settings: () => api.get("/api/settings"),
  saveSettings: (flat) => api.put("/api/settings", flat),
  about: () => api.get("/api/about"),
};
