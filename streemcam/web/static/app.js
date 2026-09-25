import "/go2rtc/video-stream.js";

const $ = (sel) => document.querySelector(sel);
const tg = window.Telegram?.WebApp;
let token = null;
let info = null;
let currentCams = [];
function loadCompat() {
  try { return localStorage.getItem("sc_compat") === "1"; } catch { return false; }
}
let compat = loadCompat();

function store(key, value) {
  try { sessionStorage.setItem(key, value); } catch {}
}
function load(key) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}

// go2rtc закрывает WebSocket video-stream'а только через 5с после отсоединения
// от DOM (DISCONNECT_TIMEOUT), а сам video-rtc переподключается через 15с.
// Если сразу открыть новую камеру, старое соединение ещё живо и может упереться
// в лимит max_streams_per_user. Поэтому перед заменой #viewer явно отключаем
// все текущие плееры.
function stopPlayers() {
  for (const el of document.querySelectorAll("#viewer video-stream")) {
    el.ondisconnect?.();
  }
}

function showMessage(text) {
  stopPlayers();
  hideArchive();
  $("#message").textContent = text;
  $("#message").hidden = false;
  $("#cams").hidden = true;
  $("#viewer").hidden = true;
  $("#viewer").replaceChildren();
  $("#back").hidden = true;
  $("#grid").hidden = true;
  $("#mode-archive").hidden = true;
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
  const extra = compat ? "&compat=1" : "";
  return `${location.origin}/api/ws?src=${encodeURIComponent(id)}&s=${encodeURIComponent(token)}${extra}`;
}

function statusText(online) {
  return online === true ? "онлайн" : online === false ? "офлайн" : "проверяется";
}

function renderList() {
  stopPlayers();
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
    li.onclick = () => (archiveMode ? openArchive(cam) : openCameras([cam]));
    return li;
  }));
  $("#message").hidden = true;
  $("#viewer").hidden = true;
  $("#viewer").replaceChildren();
  list.hidden = false;
  $("#back").hidden = true;
  $("#grid").hidden = info.cameras.length < 2;
  $("#title").textContent = "Камеры";
  $("#mode-archive").hidden = !info.recording;
  if (archiveMode) $("#grid").hidden = true;
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
  stopPlayers();
  currentCams = cams;
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

$("#back").onclick = () => { hideArchive(); stopPlayers(); history.replaceState(null, "", location.pathname); renderList(); };
$("#grid").onclick = () => openCameras(info.cameras.slice(0, info.max_streams));
function applyCompatUi() {
  $("#compat").checked = compat;
  $("#compat-hint").hidden = !compat;
}
$("#compat").onchange = () => {
  compat = $("#compat").checked;
  try { localStorage.setItem("sc_compat", compat ? "1" : "0"); } catch {}
  applyCompatUi();
  if (!$("#viewer").hidden && currentCams.length) openCameras(currentCams);
};
applyCompatUi();

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

let archiveMode = false;

function archiveUrl(path) {
  return `/api/archive/${path}`;
}

function fileUrl(cam, day, name, download) {
  const q = `s=${encodeURIComponent(token)}${download ? "&download=1" : ""}`;
  return archiveUrl(`${encodeURIComponent(cam)}/${encodeURIComponent(day)}/${encodeURIComponent(name)}.mp4?${q}`);
}

function hideArchive() {
  const video = $("#archive-video");
  video.pause();
  video.removeAttribute("src");
  video.load();
  $("#archive").hidden = true;
}

function setArchiveMode(on) {
  archiveMode = on;
  $("#mode-archive").textContent = on ? "Живое" : "Архив";
  hideArchive();
  renderList();
  $("#title").textContent = on ? "Архив" : "Камеры";
}

function chip(text, onClick, active) {
  const b = document.createElement("button");
  b.textContent = text;
  if (active) b.classList.add("active");
  b.onclick = onClick;
  return b;
}

function archiveEmpty(text) {
  $("#archive-empty").textContent = text;
  $("#archive-empty").hidden = false;
}

async function loadArchiveJson(path, key) {
  try {
    const r = await api(archiveUrl(path));
    if (r.status === 401 || r.status === 403) {
      token = null;
      store("sc_token", "");
      hideArchive();
      showMessage(r.status === 403 ? "Доступ отозван." : "Сессия истекла. Запросите новую ссылку командой /cams.");
      return null;
    }
    if (!r.ok) {
      archiveEmpty("Не удалось загрузить архив.");
      return null;
    }
    return (await r.json())[key];
  } catch {
    archiveEmpty("Не удалось загрузить архив. Проверьте соединение.");
    return null;
  }
}

async function openArchive(cam) {
  stopPlayers();
  $("#cams").hidden = true;
  $("#grid").hidden = true;
  $("#back").hidden = false;
  $("#title").textContent = `Архив: ${cam.name}`;
  $("#archive").hidden = false;
  $("#archive-player").hidden = true;
  $("#archive-hours").replaceChildren();
  $("#archive-empty").hidden = true;
  const days = await loadArchiveJson(`${encodeURIComponent(cam.id)}/days`, "days");
  if (days === null) return;
  $("#archive-empty").hidden = days.length > 0;
  $("#archive-empty").textContent = "Записей пока нет.";
  $("#archive-days").replaceChildren(...days.map((d) => chip(d, () => openDay(cam, d))));
  if (days.length) openDay(cam, days[0]);
}

async function openDay(cam, day) {
  for (const b of $("#archive-days").children) b.classList.toggle("active", b.textContent === day);
  const hours = await loadArchiveJson(`${encodeURIComponent(cam.id)}/${encodeURIComponent(day)}`, "hours");
  if (hours === null) return;
  $("#archive-hours").replaceChildren(...hours.map((h) => {
    const label = `${h.name.slice(0, 2)}:${h.name.slice(3, 5)}${h.recording ? " •" : ""} · ${(h.size / 1e6).toFixed(0)} МБ`;
    return chip(label, (ev) => playHour(cam, day, h, ev.currentTarget));
  }));
}

function playHour(cam, day, h, button) {
  for (const b of $("#archive-hours").children) b.classList.toggle("active", b === button);
  const video = $("#archive-video");
  video.src = fileUrl(cam.id, day, h.name, false);
  video.play().catch(() => {});
  $("#archive-download").href = fileUrl(cam.id, day, h.name, true);
  $("#archive-player").hidden = false;
}

$("#mode-archive").onclick = () => setArchiveMode(!archiveMode);

main();
