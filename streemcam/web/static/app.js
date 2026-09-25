import "/go2rtc/video-stream.js";

const $ = (sel) => document.querySelector(sel);
const tg = window.Telegram?.WebApp;
let token = null;
let info = null;

function store(key, value) {
  try { sessionStorage.setItem(key, value); } catch {}
}
function load(key) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}

function showMessage(text) {
  $("#message").textContent = text;
  $("#message").hidden = false;
  $("#cams").hidden = true;
  $("#viewer").hidden = true;
  $("#viewer").replaceChildren();
  $("#back").hidden = true;
  $("#grid").hidden = true;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(path, { ...options, headers });
}

async function login() {
  const params = new URLSearchParams(location.search);
  let r;
  if (tg && tg.initData) {
    tg.ready();
    tg.expand();
    r = await api("/api/tg/session", { method: "POST", body: JSON.stringify({ init_data: tg.initData }) });
  } else if (params.get("t")) {
    r = await api("/api/link/redeem", { method: "POST", body: JSON.stringify({ t: params.get("t") }) });
    history.replaceState(null, "", location.pathname + location.hash);
  } else {
    token = load("sc_token");
    if (!token) showMessage("Откройте камеры через бота: /cams в Telegram или Discord.");
    return Boolean(token);
  }
  const data = await r.json().catch(() => ({}));
  if (r.ok) {
    token = data.token;
    store("sc_token", token);
    return true;
  }
  if (r.status === 403) {
    showMessage(`Нет доступа. Ваш ID: ${data.user_id}. Передайте его администратору.`);
  } else {
    showMessage("Ссылка недействительна или устарела. Запросите новую командой /cams.");
  }
  return false;
}

async function refresh() {
  const r = await api("/api/cameras");
  if (r.status === 401 || r.status === 403) {
    token = null;
    store("sc_token", "");
    showMessage(r.status === 403 ? "Доступ отозван." : "Сессия истекла. Запросите новую ссылку командой /cams.");
    return false;
  }
  if (!r.ok) return true;
  try {
    info = await r.json();
  } catch {
    return true;
  }
  return true;
}

function snapshotUrl(id) {
  return `/api/snapshot/${encodeURIComponent(id)}?s=${encodeURIComponent(token)}&_=${Date.now()}`;
}

function streamUrl(id) {
  return `${location.origin}/api/ws?src=${encodeURIComponent(id)}&s=${encodeURIComponent(token)}`;
}

function statusText(online) {
  return online === true ? "онлайн" : online === false ? "офлайн" : "проверяется";
}

function renderList() {
  const list = $("#cams");
  list.replaceChildren(...info.cameras.map((cam) => {
    const li = document.createElement("li");
    li.className = "cam";
    if (cam.online === false) li.classList.add("offline");
    const img = document.createElement("img");
    img.alt = cam.name;
    img.src = snapshotUrl(cam.id);
    img.onerror = () => { img.removeAttribute("src"); };
    const caption = document.createElement("div");
    caption.innerHTML = `<b></b><span class="status"></span>`;
    caption.querySelector("b").textContent = cam.name;
    caption.querySelector(".status").textContent = statusText(cam.online);
    li.append(img, caption);
    li.onclick = () => openCameras([cam]);
    return li;
  }));
  $("#message").hidden = true;
  $("#viewer").hidden = true;
  $("#viewer").replaceChildren();
  list.hidden = false;
  $("#back").hidden = true;
  $("#grid").hidden = info.cameras.length < 2;
  $("#title").textContent = "Камеры";
}

function player(cam) {
  const box = document.createElement("div");
  box.className = "player";
  const video = document.createElement("video-stream");
  video.mode = info.player_mode;
  video.src = streamUrl(cam.id);
  const bar = document.createElement("div");
  bar.className = "bar";
  const name = document.createElement("span");
  name.textContent = cam.online === false ? `${cam.name} — камера офлайн` : cam.name;
  const retry = document.createElement("button");
  retry.textContent = "↻";
  retry.title = "Повторить";
  // VideoRTC игнорирует новый src, пока открыт старый WebSocket — сначала рвём соединение
  retry.onclick = () => { video.ondisconnect(); video.src = streamUrl(cam.id); };
  const full = document.createElement("button");
  full.textContent = "⛶";
  full.title = "Во весь экран";
  full.onclick = () => (box.requestFullscreen?.() ?? box.webkitRequestFullscreen?.());
  bar.append(name, retry, full);
  box.append(video, bar);
  return box;
}

function openCameras(cams) {
  const viewer = $("#viewer");
  viewer.className = cams.length > 1 ? "grid" : "single";
  viewer.replaceChildren(...cams.map(player));
  viewer.hidden = false;
  $("#cams").hidden = true;
  $("#back").hidden = false;
  $("#grid").hidden = true;
  $("#title").textContent = cams.length > 1 ? "Сетка" : cams[0].name;
  if (cams.length === 1) history.replaceState(null, "", `#cam=${cams[0].id}`);
}

$("#back").onclick = () => { history.replaceState(null, "", location.pathname); renderList(); };
$("#grid").onclick = () => openCameras(info.cameras.slice(0, info.max_streams));

async function main() {
  try {
    if (!(await login())) return;
    if (!(await refresh())) return;
    const wanted = new URLSearchParams(location.hash.slice(1)).get("cam");
    const cam = info.cameras.find((c) => c.id === wanted);
    cam ? openCameras([cam]) : renderList();
  } catch (e) {
    showMessage("Не удалось загрузить камеры. Проверьте соединение и обновите страницу.");
    return;
  }
  setInterval(async () => {
    if (!token) return;
    try {
      const ok = await refresh();
      if (ok && !$("#cams").hidden) renderList();
    } catch {
      // Ignore network errors during periodic refresh
    }
  }, 60000);
}

main();
