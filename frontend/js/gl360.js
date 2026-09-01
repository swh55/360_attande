// Gear360 Hub — WebGL dual-fisheye 360 renderer.
// Renders the camera's dual-fisheye source into: raw / equirect / perspective /
// fisheye (single lens) / tiny planet, with yaw/pitch/zoom interaction.
// Math mirrors app/media/projections.py (see conventions there).

const VERT = `
attribute vec2 aPos;
varying vec2 vUv;
void main() {
  vUv = aPos * 0.5 + 0.5;
  vUv.y = 1.0 - vUv.y;           // image row 0 = top
  gl_Position = vec4(aPos, 0.0, 1.0);
}`;

const FRAG = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uTex;
uniform float uTexW, uTexH;
uniform int uMode;               // 0 raw, 1 equirect, 2 perspective, 3 fisheye, 4 tiny planet
uniform float uYaw, uPitch, uFov;      // radians
uniform vec4 uCal;               // fovFisheye(rad), radiusFactor, centerXOffset, roll(rad)
uniform vec2 uPan;               // normalized pan for flat modes
uniform float uZoom;
uniform float uAspect;
uniform int uLensSel;            // for fisheye mode
uniform float uYawOff;           // lens0 heading offset (rad), includes lens swap

const float PI = 3.14159265358979;

// maps a world direction (lon, lat) to source texture uv (dual fisheye).
// returns v<0 when outside both lens circles.
vec3 rayToSource(float lon, float lat, out bool ok) {
  float fovHalf = uCal.x * 0.5;
  float r = uTexH * 0.5 * uCal.y;
  float cx0 = uTexW * 0.25 + uCal.z * uTexW;
  float cx1 = uTexW * 0.75 + uCal.z * uTexW;
  float cy = uTexH * 0.5;
  float roll = uCal.w;

  // lens0 heading = 0 rad, lens1 heading = PI (swap handled on CPU via uCal.z flip? -> handled by yawOff on CPU)
  for (int k = 0; k < 2; k++) {
    float az = (k == 0) ? 0.0 : PI;
    float dlon = lon - az;
    if (cos(dlon) >= 0.0) {
      float cl = cos(lat);
      float xf = cl * cos(dlon);
      float yf = sin(lat);
      float zf = cl * sin(dlon);
      float theta = acos(clamp(xf, -1.0, 1.0));
      float rho = theta / fovHalf;
      float psi = atan(-yf, zf) + roll;
      float px = ((k == 0) ? cx0 : cx1) + rho * r * cos(psi);
      float py = cy + rho * r * sin(psi);
      ok = rho <= 1.0;
      return vec3(px / uTexW, py / uTexH, 1.0);
    }
  }
  ok = false;
  return vec3(0.0);
}

vec4 sampleRay(float lon, float lat) {
  bool ok;
  vec3 s = rayToSource(lon + uYawOff, lat, ok);
  if (!ok) return vec4(0.02, 0.03, 0.04, 1.0);
  return texture2D(uTex, s.xy);
}

// perspective ray from yaw/pitch with fov
vec4 viewPerspective() {
  // vUv.y is flipped for texture sampling; screen-up is vUv.y = 0
  vec2 p = vec2((vUv.x - 0.5) * 2.0, (0.5 - vUv.y) * 2.0);
  float tanF = tan(uFov * 0.5);
  vec3 dir = normalize(vec3(p.x * tanF * uAspect, p.y * tanF, -1.0));
  float cy = cos(uPitch), sy = sin(uPitch);
  float cp = cos(uYaw), sp = sin(uYaw);
  // pitch about X
  float y1 = dir.y * cy - dir.z * sy;
  float z1 = dir.y * sy + dir.z * cy;
  // yaw about Y
  float x2 = dir.x * cp + z1 * sp;
  float z2 = -dir.x * sp + z1 * cp;
  float lon = atan(x2, -z2);
  float lat = asin(clamp(y1, -1.0, 1.0));
  return sampleRay(lon, lat);
}

vec4 viewEquirect() {
  float lon = (vUv.x - 0.5) * 2.0 * PI / uZoom + uPan.x;
  float lat = (0.5 - vUv.y) * PI / uZoom + uPan.y;
  return sampleRay(lon, lat);
}

vec4 viewFisheye() {
  float r = uTexH * 0.5 * uCal.y;
  float cx = (uLensSel == 0) ? uTexW * 0.25 + uCal.z * uTexW : uTexW * 0.75 + uCal.z * uTexW;
  float cy = uTexH * 0.5;
  vec2 c = vec2(cx / uTexW, cy / uTexH);
  float rr = r / uTexW;
  vec2 q = (vUv - c) / rr;                 // in circle radii
  q /= uZoom;
  q += uPan * uAspect;
  if (length(q) > 1.0) return vec4(0.02, 0.03, 0.04, 1.0);
  return texture2D(uTex, c + q * rr);
}

vec4 viewTinyPlanet() {
  vec2 p = vec2((vUv.x - 0.5) * 2.0, (0.5 - vUv.y) * 2.0);
  p /= uZoom;
  p += uPan;
  float rho = length(p);
  float lambda = atan(p.y, p.x) + uYaw;
  float theta = 2.0 * atan(rho / 2.0);
  float lat = PI * 0.5 - theta;            // stereographic from zenith
  float lon = lambda;
  return sampleRay(lon, lat);
}

void main() {
  vec4 col;
  if (uMode == 0) {
    // raw fit (contain)
    float srcAspect = uTexW / uTexH;
    vec2 p = (vUv - 0.5) * 2.0;
    if (uAspect > srcAspect) { p.x *= uAspect / srcAspect; } else { p.y *= srcAspect / uAspect; }
    p /= uZoom;
    p += uPan;
    if (abs(p.x) > 1.0 || abs(p.y) > 1.0) col = vec4(0.02, 0.03, 0.04, 1.0);
    else col = texture2D(uTex, vec2(p.x * 0.5 + 0.5, 0.5 - p.y * 0.5));
  } else if (uMode == 1) col = viewEquirect();
  else if (uMode == 2) col = viewPerspective();
  else if (uMode == 3) col = viewFisheye();
  else col = viewTinyPlanet();
  gl_FragColor = col;
}`;

export class GL360 {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = canvas.getContext("webgl", { antialias: false, preserveDrawingBuffer: false });
    if (!gl) throw new Error("WebGL not supported");
    this.gl = gl;
    this.mode = 2;            // perspective default
    this.yaw = 0; this.pitch = 0; this.fov = 90 * Math.PI / 180;
    this.zoom = 1.0; this.pan = [0, 0];
    this.lensSel = 0;
    this.cal = { fovDeg: 195, radiusFactor: 1.0, centerXOffset: 0.0, rollDeg: 0 };
    this.source = null;       // <img> or <video>
    this._prog = this._buildProgram(VERT, FRAG);
    this._initQuad();
    this._initTexture();
    this._lastUpload = 0;
  }

  _buildProgram(vs, fs) {
    const gl = this.gl;
    const compile = (type, src) => {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        throw new Error("shader: " + gl.getShaderInfoLog(sh));
      }
      return sh;
    };
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, vs));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error("link: " + gl.getProgramInfoLog(prog));
    }
    return prog;
  }

  _initQuad() {
    const gl = this.gl;
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    gl.useProgram(this._prog);
    const loc = gl.getAttribLocation(this._prog, "aPos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  }

  _initTexture() {
    const gl = this.gl;
    this._tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this._tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
      new Uint8Array([16, 24, 32, 255]));
  }

  setSource(el) { this.source = el; }

  setMode(modeStr) {
    const map = { raw: 0, equirect: 1, perspective: 2, fisheye: 3, tiny: 4 };
    this.mode = map[modeStr] ?? 2;
  }
  setViewDeg(yawDeg, pitchDeg, fovDeg) {
    if (yawDeg !== undefined) this.yaw = yawDeg * Math.PI / 180;
    if (pitchDeg !== undefined) this.pitch = pitchDeg * Math.PI / 180;
    if (fovDeg !== undefined) this.fov = fovDeg * Math.PI / 180;
  }
  setCalibration(cal) {
    Object.assign(this.cal, cal);
    this._yawOff = ((cal.lens0AzimuthDeg || 0) + (cal.swapLenses ? 180 : 0)) * Math.PI / 180;
  }
  resetView() { this.yaw = 0; this.pitch = 0; this.fov = 90 * Math.PI / 180; this.zoom = 1; this.pan = [0, 0]; }

  render() {
    const gl = this.gl;
    const src = this.source;
    if (src) {
      const w = src.naturalWidth || src.videoWidth || 0;
      const h = src.naturalHeight || src.videoHeight || 0;
      if (w && h) {
        const isVideo = !!src.videoWidth;
        if (w !== this._tw || h !== this._th || isVideo || this._stale) {
          this._tw = w; this._th = h;
          gl.bindTexture(gl.TEXTURE_2D, this._tex);
          gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
          try {
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src);
          } catch (e) { /* frame not ready yet */ }
          this._stale = false;
        }
      }
    }

    // size
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = this.canvas.getBoundingClientRect();
    const cw = Math.max(2, Math.round(rect.width * dpr));
    const ch = Math.max(2, Math.round(rect.height * dpr));
    if (this.canvas.width !== cw || this.canvas.height !== ch) {
      this.canvas.width = cw; this.canvas.height = ch;
    }
    gl.viewport(0, 0, cw, ch);
    gl.useProgram(this._prog);
    gl.bindTexture(gl.TEXTURE_2D, this._tex);
    gl.uniform1i(gl.getUniformLocation(this._prog, "uTex"), 0);
    gl.uniform1f(gl.getUniformLocation(this._prog, "uTexW"), this._tw || 1280);
    gl.uniform1f(gl.getUniformLocation(this._prog, "uTexH"), this._th || 720);
    gl.uniform1i(gl.getUniformLocation(this._prog, "uMode"), this.mode);
    gl.uniform1f(gl.getUniformLocation(this._prog, "uYaw"), this.yaw);
    gl.uniform1f(gl.getUniformLocation(this._prog, "uPitch"), this.pitch);
    gl.uniform1f(gl.getUniformLocation(this._prog, "uFov"), this.fov);
    gl.uniform4f(gl.getUniformLocation(this._prog, "uCal"),
      this.cal.fovDeg * Math.PI / 180, this.cal.radiusFactor, this.cal.centerXOffset,
      this.cal.rollDeg * Math.PI / 180);
    gl.uniform2f(gl.getUniformLocation(this._prog, "uPan"), this.pan[0], this.pan[1]);
    gl.uniform1f(gl.getUniformLocation(this._prog, "uZoom"), this.zoom);
    gl.uniform1f(gl.getUniformLocation(this._prog, "uAspect"), cw / ch);
    gl.uniform1i(gl.getUniformLocation(this._prog, "uLensSel"), this.lensSel);
    gl.uniform1f(gl.getUniformLocation(this._prog, "uYawOff"), this._yawOff || 0);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  markStale() { this._stale = true; }

  destroy() {
    const gl = this.gl;
    gl.deleteTexture(this._tex);
    gl.deleteProgram(this._prog);
  }
}

// Interaction helper: drag -> yaw/pitch, wheel -> fov (perspective) or zoom.
export function attachInteraction(gl360, canvas, opts = {}) {
  let dragging = false, lastX = 0, lastY = 0;
  const flat = () => gl360.mode === 1 || gl360.mode === 4 || gl360.mode === 0;
  canvas.addEventListener("pointerdown", (e) => {
    dragging = true; lastX = e.clientX; lastY = e.clientY;
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
    const rect = canvas.getBoundingClientRect();
    if (flat()) {
      gl360.pan[0] -= (dx / rect.width) * 2 / gl360.zoom * (document.dir === "rtl" ? 1 : 1);
      gl360.pan[1] += (dy / rect.height) * 2 / gl360.zoom;
    } else {
      const dir = document.documentElement.dir === "rtl" ? -1 : 1;
      gl360.yaw += (dx / rect.height) * gl360.fov * dir * 1.2;
      gl360.pitch += (dy / rect.height) * gl360.fov * 1.2;
      gl360.pitch = Math.max(-89 * Math.PI / 180, Math.min(89 * Math.PI / 180, gl360.pitch));
    }
  });
  canvas.addEventListener("pointerup", () => { dragging = false; });
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 0.9 : 1.1;
    if (gl360.mode === 2) {
      gl360.fov = Math.max(20 * Math.PI / 180, Math.min(120 * Math.PI / 180, gl360.fov * factor));
    } else {
      gl360.zoom = Math.max(0.4, Math.min(12, gl360.zoom / factor));
    }
  }, { passive: false });
  canvas.addEventListener("dblclick", () => { if (opts.onDoubleClick) opts.onDoubleClick(); });
}
