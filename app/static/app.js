"use strict";
/* Aplicación web de una sola página, sin dependencias.
   Todo el contenido dinámico se inserta con textContent (nunca innerHTML): los títulos
   provienen de nombres de archivo subidos por usuarios. */

const API = "api/v1";
const $ = id => document.getElementById(id);
const state = { user: null, view: "routes", videos: [], jobs: [], timer: null, registrationOpen: true, newKey: null,
  mapAttribution: "", maxMoments: 12 };

// ---------- Utilidades ----------
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && k !== "list") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c);
  return el;
}

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(path, { method = "GET", json, body } = {}) {
  let response;
  try {
    response = await fetch(`${API}${path}`, {
      method, credentials: "same-origin",
      headers: json ? { "Content-Type": "application/json" } : undefined,
      body: json ? JSON.stringify(json) : body,
    });
  } catch {
    throw new ApiError(0, "No se pudo conectar con el servidor.");
  }
  if (response.status === 204) return null;
  let data = null;
  try { data = await response.json(); } catch { /* respuesta sin JSON */ }
  if (response.ok) return data;
  let message = `Error ${response.status}`;
  if (data) {
    if (typeof data.message === "string") message = data.message;
    else if (typeof data.detail === "string") message = data.detail;
    else if (Array.isArray(data.detail) && data.detail[0]) message = String(data.detail[0].msg || message).replace(/^Value error, /, "");
  }
  if (response.status === 401 && state.user) { state.user = null; showAuth(); }
  throw new ApiError(response.status, message);
}

function toast(message, bad = false) {
  const el = h("div", { class: "toast" + (bad ? " bad" : ""), text: message });
  $("toasts").append(el);
  setTimeout(() => el.remove(), 5000);
}

function confirmDialog(title, text, okLabel = "Borrar") {
  const dialog = $("confirm");
  $("confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  $("confirm-ok").textContent = okLabel;
  return new Promise(resolve => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    dialog.returnValue = "cancel";
    dialog.showModal();
  });
}

const num = (v, d = 0) => v.toLocaleString("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d });
const km = m => num(m / 1000, 1);
function duration(s) {
  s = Math.round(s);
  const hrs = Math.floor(s / 3600), min = Math.floor((s % 3600) / 60);
  return hrs ? `${hrs} h ${String(min).padStart(2, "0")} min` : `${min} min`;
}
const dateTime = iso => new Date(iso).toLocaleString("es-ES", { dateStyle: "medium", timeStyle: "short" });
const dateOnly = iso => new Date(iso).toLocaleDateString("es-ES", { dateStyle: "medium" });

// ---------- Acceso ----------
let authMode = "login";
function setAuthMode(mode) {
  authMode = mode;
  const register = mode === "register";
  $("tab-login").setAttribute("aria-selected", String(!register));
  $("tab-register").setAttribute("aria-selected", String(register));
  $("auth-pass2-row").hidden = !register;
  $("auth-hint").hidden = !register;
  $("auth-pass").autocomplete = register ? "new-password" : "current-password";
  $("auth-submit").textContent = register ? "Crear cuenta" : "Entrar";
  $("auth-error").textContent = "";
}

function showAuth() {
  stopPolling();
  setDrawer(false);
  const mh=$("mobile-header"); if(mh) mh.hidden=true;
  $("app").hidden = true;
  $("auth").hidden = false;
  $("tab-register").hidden = !state.registrationOpen;
  setAuthMode("login");
  $("auth-user").focus();
}

$("tab-login").onclick = () => setAuthMode("login");
$("tab-register").onclick = () => setAuthMode("register");
$("auth-form").onsubmit = async event => {
  event.preventDefault();
  const username = $("auth-user").value.trim(), password = $("auth-pass").value;
  const error = $("auth-error");
  error.textContent = "";
  if (authMode === "register") {
    if (!/^[A-Za-z0-9_-]{3,32}$/.test(username)) return void (error.textContent = "El usuario debe tener entre 3 y 32 caracteres: letras, números, - y _.");
    if (password.length < 8) return void (error.textContent = "La contraseña debe tener al menos 8 caracteres.");
    if (password !== $("auth-pass2").value) return void (error.textContent = "Las contraseñas no coinciden.");
  } else if (!username || !password) {
    return void (error.textContent = "Escribe tu usuario y tu contraseña.");
  }
  const button = $("auth-submit");
  button.disabled = true;
  try {
    state.user = await api(authMode === "register" ? "/auth/register" : "/auth/login", { method: "POST", json: { username, password } });
    $("auth-pass").value = $("auth-pass2").value = "";
    showApp();
  } catch (e) {
    error.textContent = e.message;
  } finally {
    button.disabled = false;
  }
};

$("logout").onclick = async () => {
  try { await api("/auth/logout", { method: "POST" }); } catch { /* la sesión local se descarta igualmente */ }
  state.user = null; state.videos = []; state.jobs = []; state.newKey = null;
  showAuth();
};

// ---------- Estructura + Responsive drawer ----------
function setDrawer(open){
  const sb=$("sidebar"), ov=$("sidebar-overlay"), btn=$("menu-toggle");
  if(!sb) return;
  sb.classList.toggle("open", open);
  if(ov) ov.hidden=!open;
  if(btn) btn.setAttribute("aria-expanded", String(open));
  document.body.style.overflow = open && window.matchMedia("(max-width:1024px)").matches ? "hidden" : "";
}
$("menu-toggle")?.addEventListener("click", ()=> setDrawer(!$("sidebar")?.classList.contains("open")));
$("menu-close")?.addEventListener("click", ()=> setDrawer(false));
$("sidebar-overlay")?.addEventListener("click", ()=> setDrawer(false));
window.addEventListener("keydown", e=>{ if(e.key==="Escape") setDrawer(false); });
window.addEventListener("resize", ()=>{ if(window.innerWidth>1024) setDrawer(false); });

function showApp() {
  $("auth").hidden = true;
  $("app").hidden = false;
  const mh=$("mobile-header"); if(mh) mh.hidden=false;
  $("who-name").textContent = state.user.name;
  $("who-role").textContent = state.user.is_admin ? "Administrador" : "Usuario";
  $("avatar").textContent = state.user.name.slice(0, 1);
  $("logout").hidden = state.user.name === "anonymous";
  $("nav-users").hidden = !(state.user.is_admin && state.user.name !== "anonymous");
  navigate("routes");
}

const views = { home: renderHome, routes: renderRoutes, upload: renderUpload, keys: renderKeys, account: renderAccount, users: renderUsers };
function navigate(view) {
  state.view = view;
  for (const b of $("nav").querySelectorAll("button")) {
    if (b.dataset.view === view) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  setDrawer(false);
  stopPolling();
  if (activeMap) { activeMap.destroy(); activeMap = null; }
  views[view]();
  $("main").focus({ preventScroll: true });
  window.scrollTo(0, 0);
}
$("nav").addEventListener("click", e => { const b = e.target.closest("button[data-view]"); if (b) navigate(b.dataset.view); });

function pageHead(title, subtitle, action) {
  return h("div", { class: "page-head" }, h("div", {}, h("h1", { text: title }), subtitle && h("p", { text: subtitle })), action);
}

// ---------- Rutas ----------
const STATUS = { queued: "En cola", rendering: "Renderizando el vídeo 3D", compiling: "Finalizando el vídeo", failed: "Ha fallado" };
const isActive = j => j.status === "queued" || j.status === "rendering" || j.status === "compiling";

async function renderRoutes() {
  const main = $("main");
  main.replaceChildren(pageHead("Rutas", "Tus vídeos y los que están en proceso.",
    h("button", { class: "btn primary", text: "Nuevo vídeo", onclick: () => navigate("upload") })), h("p", { class: "muted", text: "Cargando…" }));
  try {
    const [videos, jobs] = await Promise.all([api("/videos?limit=200"), api("/jobs")]);
    state.videos = videos.items; state.jobs = jobs;
  } catch (e) {
    if (e.status !== 401) main.lastChild.textContent = e.message;
    return;
  }
  if (state.view !== "routes") return;
  drawRoutes();
  if (state.jobs.some(isActive)) startPolling();
}

function drawRoutes() {
  const main = $("main");
  const head = main.firstChild;
  const v = state.videos;
  const total = (f) => v.reduce((sum, x) => sum + f(x.metrics), 0);
  const stat = (label, value, unit) => h("div", { class: "card stat" }, h("small", { text: label }),
    h("strong", { text: value }, unit && h("span", { text: unit })));
  const nodes = [head, h("div", { class: "stats" },
    stat("Rutas", num(v.length)),
    stat("Distancia total", km(total(m => m.total_distance_m)), "km"),
    stat("Desnivel acumulado", num(total(m => m.elevation_gain_m)), "m"),
    stat("Tiempo en ruta", duration(total(m => m.duration_s))))];

  const pending = state.jobs.filter(j => j.status !== "completed");
  if (pending.length) {
    nodes.push(h("h2", { class: "section-title", text: "En proceso" }), h("div", { class: "jobs" }, pending.map(jobRow)));
  }
  nodes.push(h("h2", { class: "section-title", text: "Vídeos" }));
  if (v.length) nodes.push(h("div", { class: "grid" }, v.map(routeCard)));
  else nodes.push(h("div", { class: "card empty" }, h("h3", { text: "Aún no tienes vídeos" }),
    h("p", { text: "Sube el GPX de una salida y en unos minutos tendrás el recorrido animado en 3D." }),
    h("button", { class: "btn primary", text: "Subir un GPX", onclick: () => navigate("upload") })));
  main.replaceChildren(...nodes);
}

function jobRow(job) {
  const failed = job.status === "failed";
  const pct = Math.round(job.progress * 100);
  const bar = h("i");
  bar.style.width = `${failed ? 0 : pct}%`;
  return h("div", { class: "card job" + (failed ? " failed" : "") },
    h("div", {}, h("h3", { text: job.title }),
      h("div", { class: "state", text: failed ? `Ha fallado: ${job.error || "error desconocido"}` : STATUS[job.status] || job.status })),
    failed
      ? h("button", { class: "btn small", text: "Descartar", onclick: async () => {
          try { await api(`/jobs/${job.job_id}`, { method: "DELETE" }); } catch (e) { return toast(e.message, true); }
          state.jobs = state.jobs.filter(j => j.job_id !== job.job_id); drawRoutes();
        } })
      : h("span", { class: "pct", text: `${pct} %` }),
    !failed && h("div", { class: "bar", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 }, bar));
}

function routeCard(video) {
  const m = video.metrics;
  const metric = (label, value) => h("div", {}, h("small", { text: label }), h("b", { text: value }));
  const orient = video.options.orientation === "vertical" ? "Vertical 9:16" : "Horizontal 16:9";
  const resLabel = `${video.width}×${video.height} · ${orient}`;
  const info = `${resLabel} · ${num(video.size_bytes / 1e6, 1)} MB` + (video.moment_count ? ` · ${video.moment_count} ${video.moment_count === 1 ? "momento" : "momentos"}` : "") + (state.user.is_admin && video.owner && video.owner !== state.user.name ? ` · ${video.owner}` : "");
  const isVertical = video.options.orientation === "vertical";
  return h("article", { class: "card route" + (isVertical ? " vertical" : "") },
    h("video", { controls: true, preload: "metadata", playsInline: true, src: `${API}/videos/${video.video_id}/stream`, style: isVertical ? "aspect-ratio: 9 / 16; max-height: 560px; object-fit: contain; background:#000" : "" }),
    h("div", { class: "route-body" },
      h("div", {}, h("h3", { text: video.title }),
        h("time", { text: m.start_time ? `Ruta del ${dateOnly(m.start_time)}` : `Creado el ${dateOnly(video.created_at)}` })),
      h("div", { class: "metrics" },
        metric("Distancia", `${km(m.total_distance_m)} km`),
        metric("Desnivel", `+${num(m.elevation_gain_m)} m`),
        metric("Tiempo", duration(m.duration_s)),
        metric("Media", `${num(m.avg_speed_kmh, 1)} km/h`)),
      h("div", { class: "route-actions" },
        h("a", { class: "btn small", href: `${API}/videos/${video.video_id}/download`, text: "Descargar" }),
        h("span", { class: "spacer", text: info }),
        h("button", { class: "btn small danger", text: "Borrar", onclick: () => deleteVideo(video) }))));
}

async function deleteVideo(video) {
  if (!await confirmDialog("¿Borrar este vídeo?", `"${video.title}" se eliminará definitivamente.`)) return;
  try {
    await api(`/videos/${video.video_id}`, { method: "DELETE" });
    state.videos = state.videos.filter(v => v.video_id !== video.video_id);
    drawRoutes();
    toast("Vídeo borrado.");
  } catch (e) { toast(e.message, true); }
}

function startPolling() {
  stopPolling();
  state.timer = setInterval(async () => {
    if (state.view !== "routes") return stopPolling();
    let jobs;
    try { jobs = await api("/jobs"); } catch { return; }
    const finished = state.jobs.filter(isActive).some(old => jobs.find(j => j.job_id === old.job_id)?.status === "completed");
    // Detectar cambios de progreso/estado para decidir si hay que repintar
    const before = state.jobs.map(j => `${j.job_id}:${j.progress}:${j.status}`).join("|");
    const after = jobs.map(j => `${j.job_id}:${j.progress}:${j.status}`).join("|");
    const changed = before !== after;
    state.jobs = jobs;
    if (finished) {
      try { state.videos = (await api("/videos?limit=200")).items; toast("Tu vídeo está listo."); } catch { /* se reintenta al recargar */ }
    }
    const playing = [...document.querySelectorAll(".route video")].some(v => !v.paused && !v.ended);
    // Si hay vídeo reproduciéndose, no reconstruir todo el grid (cortaría el playback):
    // solo parchear la sección "En proceso" donde está la barra de progreso.
    if (playing && !finished) {
      if (!changed) { if (!jobs.some(isActive)) stopPolling(); return; }
      const main = $("main");
      const pending = state.jobs.filter(j => j.status !== "completed");
      const jobsBox = main && main.querySelector(".jobs");
      const jobsTitle = main && [...main.querySelectorAll(".section-title")].find(el => el.textContent.includes("En proceso"));
      if (pending.length) {
        const newBox = h("div", { class: "jobs" }, pending.map(jobRow));
        if (jobsBox) jobsBox.replaceWith(newBox);
        else if (jobsTitle) jobsTitle.after(newBox);
        else {
          const stats = main ? main.querySelector(".stats") : null;
          if (stats) { const t = h("h2", { class: "section-title", text: "En proceso" }); stats.after(t, newBox); }
          else drawRoutes();
        }
      } else {
        if (jobsBox) jobsBox.remove();
        if (jobsTitle) jobsTitle.remove();
      }
    } else if (changed || finished) {
      drawRoutes();
    }
    if (!jobs.some(isActive)) stopPolling();
  }, 2000);
}
function stopPolling() { clearInterval(state.timer); state.timer = null; }

// ---------- Mapa 2D de la ruta ----------
const TILE = 256;
const mercX = lon => (lon + 180) / 360;
const mercY = lat => { const s = Math.sin(lat * Math.PI / 180); return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI); };

function parseGpx(text) {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.getElementsByTagName("parsererror").length) throw new Error("El archivo no es un GPX válido.");
  let nodes = doc.getElementsByTagName("trkpt");
  if (!nodes.length) nodes = doc.getElementsByTagName("rtept");
  const raw = [];
  for (const n of nodes) {
    const lat = parseFloat(n.getAttribute("lat")), lon = parseFloat(n.getAttribute("lon"));
    if (Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 85 && Math.abs(lon) <= 180) raw.push({ lat, lon });
  }
  if (raw.length < 2) throw new Error("El GPX no contiene una ruta.");
  const step = Math.ceil(raw.length / 4000);   // suficiente para dibujar y elegir puntos
  const points = raw.filter((_, i) => i % step === 0 || i === raw.length - 1);
  let d = 0;
  points.forEach((p, i) => {
    if (i) {
      const q = points[i - 1], rad = Math.PI / 180;
      const a = Math.sin((p.lat - q.lat) * rad / 2) ** 2 + Math.cos(p.lat * rad) * Math.cos(q.lat * rad) * Math.sin((p.lon - q.lon) * rad / 2) ** 2;
      d += 2 * 6371008.8 * Math.asin(Math.min(1, Math.sqrt(a)));
    }
    p.d = d; p.x = mercX(p.lon); p.y = mercY(p.lat);
  });
  return { points, total: d };
}

/* Mapa satélite desplazable con la ruta dibujada. onPick(índice) al hacer clic sobre el recorrido. */
function createRouteMap(container, points, onPick) {
  const tiles = h("div", { class: "map-tiles" });
  const canvas = h("canvas", { class: "map-canvas" });
  const ctx = canvas.getContext("2d");
  const live = new Map();
  let zoom = 12, cx = 0.5, cy = 0.5, markers = [], pending = null, drag = null, wheel = 0;
  const size = () => [container.clientWidth, container.clientHeight];

  function fit() {
    const xs = points.map(p => p.x), ys = points.map(p => p.y);
    const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const [w, hgt] = size();
    for (zoom = 18; zoom > 2; zoom--) {
      const s = TILE * 2 ** zoom;
      if ((maxX - minX) * s <= w - 100 && (maxY - minY) * s <= hgt - 100) break;
    }
    cx = (minX + maxX) / 2; cy = (minY + maxY) / 2;
    render();
  }

  function screen(p) {
    const s = TILE * 2 ** zoom, [w, hgt] = size();
    return [(p.x - cx) * s + w / 2, (p.y - cy) * s + hgt / 2];
  }

  function render() {
    const [w, hgt] = size();
    if (!w || !hgt) return;
    const s = TILE * 2 ** zoom, n = 2 ** zoom;
    const left = cx * s - w / 2, top = cy * s - hgt / 2;
    const keep = new Set();
    for (let ty = Math.floor(top / TILE); ty <= Math.floor((top + hgt) / TILE); ty++) {
      for (let tx = Math.floor(left / TILE); tx <= Math.floor((left + w) / TILE); tx++) {
        if (tx < 0 || ty < 0 || tx >= n || ty >= n) continue;
        const key = `${zoom}/${tx}/${ty}`;
        keep.add(key);
        let img = live.get(key);
        if (!img) {
          img = h("img", { alt: "", draggable: false, src: `${API}/tiles/${key}` });
          live.set(key, img); tiles.append(img);
        }
        img.style.transform = `translate(${Math.round(tx * TILE - left)}px, ${Math.round(ty * TILE - top)}px)`;
      }
    }
    for (const [key, img] of live) if (!keep.has(key)) { img.remove(); live.delete(key); }

    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(hgt * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(hgt * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, hgt);
    ctx.lineJoin = ctx.lineCap = "round";
    ctx.beginPath();
    points.forEach((p, i) => { const [x, y] = screen(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
    ctx.strokeStyle = "rgba(20, 10, 4, .75)"; ctx.lineWidth = 7; ctx.stroke();
    ctx.strokeStyle = "#ff5c1e"; ctx.lineWidth = 3.5; ctx.stroke();
    const dot = (p, r, fill, stroke, label) => {
      const [x, y] = screen(p);
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = fill; ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = stroke; ctx.stroke();
      if (label) { ctx.fillStyle = "#1a1d22"; ctx.font = "700 12px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(label, x, y + 0.5); }
    };
    dot(points[0], 6, "#3fb97f", "#fff");
    dot(points[points.length - 1], 6, "#1a1d22", "#fff");
    markers.forEach((index, i) => dot(points[index], 11, "#fff", "#ff5c1e", String(i + 1)));
    if (pending != null) dot(points[pending], 9, "rgba(255, 92, 30, .35)", "#fff");
  }

  function zoomBy(delta, px, py) {
    const next = Math.max(3, Math.min(18, zoom + delta));
    if (next === zoom) return;
    const [w, hgt] = size();
    const ax = px ?? w / 2, ay = py ?? hgt / 2, s0 = TILE * 2 ** zoom, s1 = TILE * 2 ** next;
    const wx = cx + (ax - w / 2) / s0, wy = cy + (ay - hgt / 2) / s0;   // punto bajo el cursor: debe quedarse fijo
    zoom = next; cx = wx - (ax - w / 2) / s1; cy = wy - (ay - hgt / 2) / s1;
    render();
  }

  canvas.addEventListener("pointerdown", e => { drag = { x: e.clientX, y: e.clientY, cx, cy, moved: false }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", e => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.hypot(dx, dy) > 4) drag.moved = true;
    if (drag.moved) { const s = TILE * 2 ** zoom; cx = drag.cx - dx / s; cy = drag.cy - dy / s; render(); }
  });
  canvas.addEventListener("pointerup", e => {
    const wasDrag = drag && drag.moved;
    drag = null;
    if (wasDrag) return;
    const rect = canvas.getBoundingClientRect(), px = e.clientX - rect.left, py = e.clientY - rect.top;
    let best = -1, bestDist = 28;   // tolerancia del clic, en píxeles
    points.forEach((p, i) => { const [x, y] = screen(p), dist = Math.hypot(x - px, y - py); if (dist < bestDist) { bestDist = dist; best = i; } });
    if (best >= 0) onPick(best);
  });
  canvas.addEventListener("pointercancel", () => { drag = null; });
  canvas.addEventListener("wheel", e => {
    e.preventDefault();
    wheel += e.deltaY;
    if (Math.abs(wheel) < 60) return;
    const rect = canvas.getBoundingClientRect();
    zoomBy(wheel > 0 ? -1 : 1, e.clientX - rect.left, e.clientY - rect.top);
    wheel = 0;
  }, { passive: false });

  const controls = h("div", { class: "map-controls" },
    h("button", { type: "button", text: "+", title: "Acercar", "aria-label": "Acercar", onclick: () => zoomBy(1) }),
    h("button", { type: "button", text: "−", title: "Alejar", "aria-label": "Alejar", onclick: () => zoomBy(-1) }),
    h("button", { type: "button", text: "⤢", title: "Ver toda la ruta", "aria-label": "Ver toda la ruta", onclick: fit }));
  container.replaceChildren(tiles, canvas, controls, h("span", { class: "map-credit", text: state.mapAttribution }));
  const observer = new ResizeObserver(render);
  observer.observe(container);
  requestAnimationFrame(fit);
  return {
    setMarkers(list) { markers = list; render(); },
    setPending(index) { pending = index; render(); },
    destroy() { observer.disconnect(); },
  };
}

// ---------- Pantalla de inicio (Resumen) ----------
function renderHome() {
  const main = $("main");
  const head = pageHead("Resumen de actividades", "Tus rutas, vídeos y momentos recientes.");
  const error = h("p", { class: "error", role: "alert" });
  const stats = h("div", { class: "stats-grid" });
  const list = h("div", { class: "card panel" }, h("h2", { text: "Actividad reciente" }), h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, ["Título", "Estado", "Duración", "Momentos", "Acciones"].map(t => h("th", { text: t })))),
    h("tbody", { id: "home-jobs" }))));

  async function load() {
    stats.replaceChildren(h("p", { class: "muted", text: "Cargando resumen…" }));
    list.querySelector("tbody").replaceChildren(h("tr", {}, h("td", { colspan: 5, text: "Cargando…" })));
    try {
      const jobs = await api("/jobs");
      const vids = await api("/videos?limit=200");
      const items = Array.isArray(vids) ? vids : (vids.items || []);
      const completed = items.filter(v => !v.owner || v.owner === state.user.name);
      const totalDist = jobs.reduce((a, b) => a + (b.metrics?.total_distance_m || 0), 0) / 1000;
      const totalDur = jobs.reduce((a, b) => a + (b.video_duration_s || b.metrics?.duration_s || 0), 0);
      const moments = jobs.reduce((a, b) => a + (b.moment_count || 0), 0) + completed.reduce((a, b) => a + (b.moment_count || 0), 0);
      
      stats.replaceChildren(
        h("div", { class: "stat-card" }, h("strong", { text: num(totalDist, 1) }), h("small", { text: "km recorridos" })),
        h("div", { class: "stat-card" }, h("strong", { text: num(totalDur / 60, 1) }), h("small", { text: "minutos renderizados" })),
        h("div", { class: "stat-card" }, h("strong", { text: completed.length }), h("small", { text: "vídeos guardados" })),
        h("div", { class: "stat-card" }, h("strong", { text: moments }), h("small", { text: "momentos totales" }))
      );

      const tbody = list.querySelector("tbody");
      tbody.replaceChildren(...jobs.map(j => {
        const link = h("a", { href: `#/routes/${j.job_id}`, text: j.title || "Ruta sin título", class: "link" });
        return h("tr", {},
          h("td", {}, link),
          h("td", {}, h("span", { class: "badge" + (j.status === "completed" ? " success" : j.status === "failed" ? " danger" : " plain"), text: j.status.toUpperCase() })),
          h("td", {}, num(j.total_duration_s || 0, 1) + " s"),
          h("td", {}, j.moment_count || 0),
          h("td", {}, h("button", { class: "btn small", text: "Ver", onclick: () => navigate("routes", j.job_id) }))
        );
      }));
    } catch (e) {
      error.textContent = e.message;
      stats.replaceChildren();
      list.querySelector("tbody").replaceChildren(h("tr", {}, h("td", { colspan: 5, text: "Error al cargar." })));
    }
  }

  main.replaceChildren(head, error, h("div", { class: "card panel wide" }, h("h2", { text: "Resumen" }), stats), list);
  load();
}

// ---------- Nuevo vídeo ----------
// ---------- Nuevo vídeo ----------
const MEDIA_TYPES = "image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm";
let activeMap = null;

function renderUpload() {
  let file = null, route = null, picked = null;
  const moments = [];   // { index, file, caption, url }
  const error = h("p", { class: "error", role: "alert" });
  const input = h("input", { type: "file", accept: ".gpx,application/gpx+xml", hidden: true });
  const dropTitle = h("strong", { text: "Arrastra aquí tu archivo GPX" });
  const dropHint = h("span", { class: "muted", text: "o haz clic para elegirlo · máx. 25 MB" });
  const drop = h("div", { class: "drop", role: "button", tabIndex: 0, "aria-label": "Elegir archivo GPX" },
    h("span", { class: "ico ico-upload", "aria-hidden": "true" }), dropTitle, dropHint);

  // --- Momentos ---
  const titleInput = h("input", { type: "text", name: "title", maxLength: 120, placeholder: "Ej: Gorbea invernal · 12 km", autocomplete: "off" });
  const orientationHelp = h("p", { class: "hint", text: "Vertical 9:16 ocupa toda la pantalla en móviles (Reels, TikTok). El relieve y la ruta se adaptan al formato." });
  const mapBox = h("div", { class: "map" });
  const editor = h("div", { class: "moment-editor" });
  const list = h("div", { class: "moment-list" });
  const momentsSection = h("fieldset", { hidden: true },
    h("legend", { text: "Momentos (opcional)" }),
    h("p", { class: "hint", text: "Haz clic sobre la ruta en el punto donde ocurrió y adjunta una foto o un clip. Aparecerá en el vídeo al pasar por ahí." }),
    mapBox, editor, list);

  const kmAt = index => num(route.points[index].d / 1000, 1);
  function drawMoments() {
    moments.sort((a, b) => a.index - b.index);
    activeMap.setMarkers(moments.map(m => m.index));
    list.replaceChildren(...moments.map((m, i) => h("div", { class: "moment" },
      h("span", { class: "moment-n", text: String(i + 1) }),
      m.file.type.startsWith("image/") ? h("img", { src: m.url, alt: "" }) : h("span", { class: "moment-clip", text: "Clip" }),
      h("div", {}, h("strong", { text: m.caption || m.file.name }), h("small", { text: `km ${kmAt(m.index)} · ${m.file.type.startsWith("image/") ? "foto" : "vídeo"} · ${num(m.file.size / 1e6, 1)} MB` })),
      h("button", { class: "btn small danger", type: "button", text: "Quitar", onclick: () => {
        URL.revokeObjectURL(m.url); moments.splice(moments.indexOf(m), 1); drawMoments();
      } }))));
  }
  function closeEditor() { picked = null; editor.replaceChildren(); if (activeMap) activeMap.setPending(null); }
  function openEditor(index) {
    if (moments.length >= state.maxMoments) return toast(`Puedes añadir hasta ${state.maxMoments} momentos por vídeo.`, true);
    picked = index;
    activeMap.setPending(index);
    const media = h("input", { type: "file", accept: MEDIA_TYPES });
    const caption = h("input", { maxLength: 80, placeholder: "p. ej. Cima del Gorbea" });
    const problem = h("p", { class: "error", role: "alert" });
    editor.replaceChildren(h("div", { class: "card moment-form" },
      h("strong", { text: `Nuevo momento en el km ${kmAt(index)}` }),
      h("label", { text: "Foto o clip (JPG, PNG, WebP, MP4, MOV, WebM)" }, media),
      h("label", { text: "Pie de foto (opcional)" }, caption),
      problem,
      h("div", { class: "row" },
        h("button", { class: "btn primary small", type: "button", text: "Añadir momento", onclick: () => {
          const f = media.files[0];
          if (!f) return void (problem.textContent = "Elige una foto o un clip.");
          if (!/^(image\/(jpeg|png|webp)|video\/(mp4|quicktime|webm))$/.test(f.type)) return void (problem.textContent = "Formato no admitido. Usa JPG, PNG, WebP, MP4, MOV o WebM.");
          if (f.size > 100 * 1024 * 1024) return void (problem.textContent = "El archivo supera los 100 MB.");
          moments.push({ index, file: f, caption: caption.value.trim(), url: URL.createObjectURL(f) });
          closeEditor(); drawMoments();
        } }),
        h("button", { class: "btn small", type: "button", text: "Cancelar", onclick: closeEditor }))));
    media.focus();
  }

  const setFile = async f => {
    if (!f) return;
    if (!/\.gpx$/i.test(f.name)) return void (error.textContent = "El archivo debe tener extensión .gpx.");
    file = f; error.textContent = "";
    drop.classList.add("has-file");
    dropTitle.textContent = f.name;
    dropHint.textContent = `${num(f.size / 1024)} kB · haz clic para cambiarlo`;
    // sugerir título a partir del nombre del archivo si está vacío
    if (!titleInput.value.trim()) {
      titleInput.value = f.name.replace(/\.gpx$/i, "").replace(/[_-]+/g, " ").trim().slice(0, 120);
    }
    moments.splice(0).forEach(m => URL.revokeObjectURL(m.url));
    list.replaceChildren(); closeEditor();
    if (activeMap) { activeMap.destroy(); activeMap = null; }
    try {
      route = parseGpx(await f.text());
      momentsSection.hidden = false;
      activeMap = createRouteMap(mapBox, route.points, openEditor);
    } catch {
      route = null; momentsSection.hidden = true;   // el servidor validará el archivo al enviarlo
    }
  };
  drop.onclick = () => input.click();
  drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } };
  input.onchange = () => setFile(input.files[0]);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); setFile(e.dataTransfer.files[0]); };

  const option = (name, value, title, text, checked) => h("label", { class: "option" },
    h("input", { type: "radio", name, value, checked }), h("span", {}, h("b", { text: title }), h("small", { text })));
  const speedOut = h("output", { text: "1×" });
  const speed = h("input", { type: "range", min: 0.5, max: 4, step: 0.25, value: 1, "aria-label": "Velocidad de la animación",
    oninput: () => { speedOut.textContent = `${num(Number(speed.value), 2).replace(/,?0+$/, "")}×`; } });
  const submit = h("button", { class: "btn primary", type: "submit", text: "Crear vídeo" });

  const form = h("form", { class: "form", novalidate: true, onsubmit: async e => {
    e.preventDefault();
    if (!file) return void (error.textContent = "Elige primero un archivo GPX.");
    const data = new FormData();
    data.set("file", file);
    const t = titleInput.value.trim();
    if (t) data.set("title", t);
    data.set("camera_style", form.elements.camera_style.value);
    data.set("resolution", form.elements.resolution.value);
    data.set("orientation", form.elements.orientation.value);
    data.set("animation_speed", speed.value);
    if (moments.length) {
      data.set("moments", JSON.stringify(moments.map((m, i) => {
        const p = route.points[m.index];
        return { lat: p.lat, lon: p.lon, position: route.total ? p.d / route.total : 0, caption: m.caption, file_index: i };
      })));
      moments.forEach(m => data.append("moment_files", m.file, m.file.name));
    }
    submit.disabled = true; submit.textContent = moments.length ? "Subiendo ruta y momentos…" : "Subiendo…"; error.textContent = "";
    try {
      await api("/render-gpx", { method: "POST", body: data });
      moments.forEach(m => URL.revokeObjectURL(m.url));
      toast("Ruta recibida. Estamos creando el vídeo.");
      navigate("routes");
    } catch (err) {
      error.textContent = err.message;
      submit.disabled = false; submit.textContent = "Crear vídeo";
    }
  } },
    h("fieldset", {}, h("legend", { text: "Nombre del vídeo" }),
      h("label", { text: "Título (opcional) — se usa como nombre del vídeo en tu biblioteca" }, titleInput),
      h("p", { class: "hint", text: "Si lo dejas vacío se usa el nombre del archivo GPX. Máx. 120 caracteres." })),
    drop, input, momentsSection,
    h("fieldset", {}, h("legend", { text: "Cámara" }), h("div", { class: "options" },
      option("camera_style", "follow", "Seguimiento", "Vuela detrás del recorrido", true),
      option("camera_style", "orbit", "Órbita", "Gira alrededor de tu posición"),
      option("camera_style", "overhead", "Cenital", "Desde arriba, norte al frente"))),
    h("fieldset", {}, h("legend", { text: "Formato" }), h("div", { class: "options" },
      option("orientation", "horizontal", "Horizontal 16:9", "YouTube, TV — panorámico", true),
      option("orientation", "vertical", "Vertical 9:16", "Reels / TikTok / Shorts — pantalla completa"))),
    h("fieldset", {}, h("legend", { text: "Resolución" }), h("div", { class: "options" },
      option("resolution", "1080p", "1080p", "Máxima calidad, tarda más", true),
      option("resolution", "720p", "720p", "Más rápido y ligero"))),
    orientationHelp,
    h("fieldset", {}, h("legend", { text: "Velocidad de la animación" }),
      h("div", { class: "range-row" }, speed, speedOut),
      h("p", { class: "hint", text: "A 1× el vídeo dura entre 20 y 90 s según la distancia; a 2× dura la mitad. Los momentos se suman a esa duración." })),
    error,
    h("div", {}, submit));
  $("main").replaceChildren(pageHead("Nuevo vídeo", "Sube un GPX, marca tus momentos y elige cómo quieres verlo."), form);
}

// ---------- Usuarios (administración) ----------
function promptDialog(title, text, { type = "password", okLabel = "Guardar", minLength = 0 } = {}) {
  const dialog = $("prompt"), field = $("prompt-input");
  $("prompt-title").textContent = title;
  $("prompt-text").textContent = text;
  $("prompt-ok").textContent = okLabel;
  field.type = type; field.value = ""; field.minLength = minLength; field.required = minLength > 0;
  return new Promise(resolve => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok" ? field.value : null), { once: true });
    dialog.returnValue = "cancel";
    dialog.showModal();
    field.focus();
  });
}

async function renderUsers() {
  const main = $("main");
  const head = pageHead("Usuarios", "Cuentas con acceso al servicio.");
  const error = h("p", { class: "error", role: "alert" });
  let users = [];
  try { users = await api("/admin/users"); } catch (e) { if (e.status === 401) return; error.textContent = e.message; }
  if (state.view !== "users") return;

  const act = async (fn, okMessage) => {
    try { await fn(); if (okMessage) toast(okMessage); renderUsers(); } catch (e) { toast(e.message, true); }
  };
  const rows = users.map(u => {
    const self = u.username.toLowerCase() === state.user.name.toLowerCase();
    return h("tr", {},
      h("td", {}, h("strong", { text: u.username }), self && h("span", { class: "muted", text: "  (tú)" })),
      h("td", {}, h("span", { class: "badge" + (u.is_admin ? "" : " plain"), text: u.is_admin ? "Administrador" : "Usuario" })),
      h("td", { text: dateOnly(u.created_at) }),
      h("td", { text: `${num(u.video_count)} · ${num(u.storage_bytes / 1e6, 1)} MB` }),
      h("td", { text: num(u.api_key_count) }),
      h("td", {}, h("div", { class: "row-actions" },
        !self && h("button", { class: "btn small", text: u.is_admin ? "Quitar admin" : "Hacer admin",
          onclick: () => act(() => api(`/admin/users/${u.id}`, { method: "PATCH", json: { is_admin: !u.is_admin } }), "Rol actualizado.") }),
        h("button", { class: "btn small", text: "Contraseña", onclick: async () => {
          const password = await promptDialog("Restablecer contraseña", `Nueva contraseña para ${u.username} (mínimo 8 caracteres). Se cerrarán sus sesiones.`, { minLength: 8 });
          if (password) act(() => api(`/admin/users/${u.id}`, { method: "PATCH", json: { new_password: password } }), "Contraseña restablecida.");
        } }),
        !self && h("button", { class: "btn small danger", text: "Eliminar", onclick: async () => {
          const text = `Se eliminará la cuenta de ${u.username} junto con sus ${u.video_count} vídeos y sus claves de API. No se puede deshacer.`;
          if (await confirmDialog("¿Eliminar este usuario?", text, "Eliminar")) act(() => api(`/admin/users/${u.id}`, { method: "DELETE" }), "Usuario eliminado.");
        } }))));
  });

  const name = h("input", { maxLength: 32, autocomplete: "off", required: true });
  const password = h("input", { type: "password", autocomplete: "new-password", required: true, minLength: 8 });
  const admin = h("input", { type: "checkbox" });
  const createError = h("p", { class: "error", role: "alert" });
  const create = h("form", { class: "inline-form", novalidate: true, onsubmit: async e => {
    e.preventDefault();
    createError.textContent = "";
    if (!/^[A-Za-z0-9_-]{3,32}$/.test(name.value.trim())) return void (createError.textContent = "El usuario debe tener entre 3 y 32 caracteres: letras, números, - y _.");
    if (password.value.length < 8) return void (createError.textContent = "La contraseña debe tener al menos 8 caracteres.");
    try {
      await api("/admin/users", { method: "POST", json: { username: name.value.trim(), password: password.value, is_admin: admin.checked } });
      toast("Usuario creado."); renderUsers();
    } catch (err) { createError.textContent = err.message; }
  } },
    h("label", { text: "Usuario" }, name), h("label", { text: "Contraseña inicial" }, password),
    h("label", { class: "check" }, admin, "Administrador"),
    h("button", { class: "btn primary", type: "submit", text: "Crear usuario" }));

  main.replaceChildren(head,
    h("div", { class: "card panel wide" }, h("h2", { text: `Cuentas (${users.length})` }), error,
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Usuario", "Rol", "Alta", "Vídeos", "Claves", ""].map(t => h("th", { text: t })))),
        h("tbody", {}, rows)))),
    h("div", { class: "card panel wide" }, h("h2", { text: "Crear un usuario" }), create, createError));
}

// ---------- Claves de API ----------
async function renderKeys() {
  const main = $("main");
  const head = pageHead("Claves de API", "Para usar el servicio desde scripts u otras aplicaciones.");
  if (!state.user.has_account) {
    main.replaceChildren(head, h("div", { class: "card panel" },
      h("p", { class: "muted", text: "Las claves de API se gestionan desde una cuenta de usuario registrada." })));
    return;
  }
  let keys = [];
  const error = h("p", { class: "error", role: "alert" });
  try { keys = await api("/api-keys"); } catch (e) { if (e.status === 401) return; error.textContent = e.message; }
  if (state.view !== "keys") return;

  const name = h("input", { maxLength: 60, placeholder: "p. ej. Portátil de casa", required: true });
  const create = h("form", { class: "inline-form", onsubmit: async e => {
    e.preventDefault();
    if (!name.value.trim()) return void (error.textContent = "Ponle un nombre a la clave para reconocerla.");
    try { state.newKey = await api("/api-keys", { method: "POST", json: { name: name.value.trim() } }); renderKeys(); }
    catch (err) { error.textContent = err.message; }
  } }, h("label", { text: "Nombre de la clave" }, name), h("button", { class: "btn primary", type: "submit", text: "Crear clave" }));

  let reveal = null;
  if (state.newKey) {
    const key = state.newKey.key;
    const copy = h("button", { class: "btn small", type: "button", text: "Copiar", onclick: async () => {
      try { await navigator.clipboard.writeText(key); copy.textContent = "Copiada"; }
      catch { toast("No se pudo copiar. Selecciona la clave y cópiala a mano.", true); }
    } });
    reveal = h("div", { class: "reveal" },
      h("strong", { text: `Clave «${state.newKey.name}» creada. Cópiala ahora: no se volverá a mostrar.` }),
      h("div", { class: "row" }, h("code", { text: key }), copy));
  }

  const rows = keys.map(k => h("tr", {},
    h("td", { text: k.name }),
    h("td", {}, h("code", { text: `${k.prefix}…` })),
    h("td", { text: dateOnly(k.created_at) }),
    h("td", { text: k.last_used_at ? dateTime(k.last_used_at) : "Nunca" }),
    h("td", {}, h("button", { class: "btn small danger", text: "Revocar", onclick: async () => {
      if (!await confirmDialog("¿Revocar esta clave?", `«${k.name}» dejará de funcionar de inmediato.`, "Revocar")) return;
      try {
        await api(`/api-keys/${k.id}`, { method: "DELETE" });
        if (state.newKey && state.newKey.id === k.id) state.newKey = null;
        renderKeys();
      } catch (err) { toast(err.message, true); }
    } }))));
  const list = keys.length
    ? h("table", {}, h("thead", {}, h("tr", {}, ["Nombre", "Clave", "Creada", "Último uso", ""].map(t => h("th", { text: t })))), h("tbody", {}, rows))
    : h("p", { class: "muted", text: "Todavía no has creado ninguna clave." });

  const origin = location.origin;
  const sample = `curl -H "X-API-Key: TU_CLAVE" \\\n     -F "file=@ruta.gpx" -F resolution=1080p -F camera_style=follow \\\n     ${origin}/api/v1/render-gpx\n\ncurl -H "X-API-Key: TU_CLAVE" ${origin}/api/v1/videos`;
  main.replaceChildren(head,
    h("div", { class: "card panel" }, h("h2", { text: "Crear una clave" }), create, error, reveal),
    h("div", { class: "card panel" }, h("h2", { text: "Tus claves" }), list),
    h("div", { class: "card panel" }, h("h2", { text: "Cómo usarla" }),
      h("p", { class: "muted", text: "Envía la clave en la cabecera X-API-Key (o Authorization: Bearer) de cada petición." }),
      h("pre", {}, h("code", { text: sample })),
      h("p", { class: "muted" }, "Referencia completa de la API en ", h("a", { href: "docs", target: "_blank", rel: "noopener", text: "/docs", class: "badge" }), ".")));
}

// ---------- Cuenta ----------
function renderAccount() {
  const head = pageHead("Cuenta", "Tus datos de acceso.");
  const profile = h("div", { class: "card panel" }, h("h2", { text: "Perfil" }),
    h("p", {}, h("strong", { text: state.user.name }), " ", h("span", { class: "badge", text: state.user.is_admin ? "Administrador" : "Usuario" })),
    state.user.is_admin && h("p", { class: "muted", text: "Como administrador ves y puedes borrar los vídeos de todos los usuarios." }));
  if (!state.user.has_account) return void $("main").replaceChildren(head, profile);

  const error = h("p", { class: "error", role: "alert" });
  const current = h("input", { type: "password", autocomplete: "current-password", required: true });
  const next = h("input", { type: "password", autocomplete: "new-password", required: true, minLength: 8 });
  const repeat = h("input", { type: "password", autocomplete: "new-password", required: true, minLength: 8 });
  const form = h("form", { class: "stack", novalidate: true, onsubmit: async e => {
    e.preventDefault();
    error.textContent = "";
    if (next.value.length < 8) return void (error.textContent = "La nueva contraseña debe tener al menos 8 caracteres.");
    if (next.value !== repeat.value) return void (error.textContent = "Las contraseñas nuevas no coinciden.");
    try {
      await api("/auth/password", { method: "PUT", json: { current_password: current.value, new_password: next.value } });
      form.reset();
      toast("Contraseña cambiada.");
    } catch (err) { error.textContent = err.message; }
  } },
    h("label", { text: "Contraseña actual" }, current),
    h("label", { text: "Nueva contraseña" }, next),
    h("label", { text: "Repite la nueva contraseña" }, repeat),
    error,
    h("div", {}, h("button", { class: "btn primary", type: "submit", text: "Cambiar contraseña" })));
  $("main").replaceChildren(head, profile, h("div", { class: "card panel" }, h("h2", { text: "Contraseña" }), form));
}

// ---------- Arranque ----------
(async function init() {
  try {
    const config = await api("/auth/config");
    state.registrationOpen = config.registration_open;
    state.mapAttribution = config.map_attribution || "";
    state.maxMoments = config.max_moments || 12;
  } catch { /* se asume la configuración por defecto */ }
  try {
    state.user = await api("/auth/me");
    showApp();
  } catch {
    showAuth();
  }
})();
