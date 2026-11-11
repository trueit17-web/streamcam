// Сайт для браузера: дома → камеры → живое видео и записи. Вход: кнопка Telegram или ссылка от бота (/web).
import "/static/player.js";

const $ = (sel) => document.querySelector(sel);
const TOKEN_KEY = "scw_token";
const NO_HOME = "Другие камеры";
const ALL = "Все";
const SNAPSHOT_REFRESH_MS = 30000;

let token = null;
let info = null;
let state = { home: null, cam: null, tab: "live", day: null, hour: null };
let wall = false;
let snapshotTimer = null;

function save(value) {
  try { value ? localStorage.setItem(TOKEN_KEY, value) : localStorage.removeItem(TOKEN_KEY); } catch {}
}
function load() {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(path, { ...options, headers });
}

const q = (params) => new URLSearchParams(params).toString();
const snapshotUrl = (id) => `/api/snapshot/${encodeURIComponent(id)}?${q({ s: token, _: Date.now() })}`;
const streamUrl = (id) => `${location.origin}/api/ws?${q({ src: id, s: token })}`;
const fileUrl = (id, day, name, download) =>
  `/api/archive/${encodeURIComponent(id)}/${day}/${name}.mp4?${q({ s: token, ...(download ? { download: 1 } : {}) })}`;

function show(id) {
  for (const s of ["#login", "#grid-view", "#cam-view", "#message"]) $(s).hidden = s !== id;
}

function message(text) {
  stopPlayers(document);
  $("#message").textContent = text;
  show("#message");
}

// ---------- вход ----------

async function finishLogin(r) {
  const data = await r.json().catch(() => ({}));
  if (r.ok) {
    token = data.token;
    save(token);
    return true;
  }
  if (r.status === 403) {
    showLogin(`Нет доступа. Ваш Telegram ID: ${data.user_id}. Передайте его администратору.`);
  } else {
    showLogin("Ссылка или код входа устарели. Войдите ещё раз.");
  }
  return false;
}

let loginPoll = null;

async function telegramLogin() {
  const btn = $("#tg-login");
  btn.disabled = true;
  clearInterval(loginPoll);
  const r = await fetch("/api/web/login", { method: "POST" }).catch(() => null);
  btn.disabled = false;
  if (!r || !r.ok) {
    showLogin("Бот сейчас недоступен. Напишите ему /web или попробуйте позже.");
    return;
  }
  const { code, bot, ttl } = await r.json();
  const start = `login_${code}`;
  // tg:// открывает установленное приложение Telegram сразу, без входа по номеру телефона в браузере
  $("#tg-link").href = `https://t.me/${bot}?start=${start}`;
  $("#tg-wait").hidden = false;
  $("#login-error").hidden = true;
  location.href = `tg://resolve?domain=${bot}&start=${start}`;
  const until = Date.now() + ttl * 1000;
  loginPoll = setInterval(async () => {
    if (Date.now() > until) {
      clearInterval(loginPoll);
      $("#tg-wait").hidden = true;
      showLogin("Время ожидания вышло. Нажмите «Войти через Telegram» ещё раз.");
      return;
    }
    const p = await fetch(`/api/web/login/${encodeURIComponent(code)}`).catch(() => null);
    if (!p || p.status === 200 && (await p.clone().json()).status === "pending") return;
    clearInterval(loginPoll);
    $("#tg-wait").hidden = true;
    if (await finishLogin(p)) start_();
  }, 2000);
}

$("#tg-login").onclick = telegramLogin;

function showLogin(error) {
  stopPlayers(document);
  $("#homes").replaceChildren();
  $("#logout").hidden = true;
  show("#login");
  $("#login-error").hidden = !error;
  if (error) $("#login-error").textContent = error;
}

async function login() {
  const params = new URLSearchParams(location.search);
  const t = params.get("t");
  if (t) {
    history.replaceState(null, "", location.pathname + location.hash);
    const r = await api("/api/link/redeem?web=1", { method: "POST", body: JSON.stringify({ t }) });
    return finishLogin(r);
  }
  token = load();
  if (!token) {
    showLogin();
    return false;
  }
  return true;
}

$("#logout").onclick = () => {
  token = null;
  save(null);
  location.hash = "";
  showLogin();
};

// ---------- данные ----------

async function loadCameras() {
  const r = await api("/api/cameras");
  if (r.status === 401 || r.status === 403) {
    token = null;
    save(null);
    showLogin(r.status === 403 ? "Доступ отозван." : "Сессия закончилась, войдите снова.");
    return false;
  }
  if (!r.ok) {
    message("Сервер не отвечает. Обновите страницу чуть позже.");
    return false;
  }
  info = await r.json();
  return true;
}

function homes() {
  const map = new Map();
  for (const cam of info.cameras) {
    const home = cam.home || NO_HOME;
    if (!map.has(home)) map.set(home, []);
    map.get(home).push(cam);
  }
  return new Map([...map].sort(([a], [b]) => (a === NO_HOME) - (b === NO_HOME) || a.localeCompare(b, "ru")));
}

function camsOf(home) {
  if (!home || home === ALL) return info.cameras;
  return homes().get(home) || [];
}

// ---------- маршруты: #home=Дача, #cam=id&tab=archive&day=2026-11-09&hour=13 ----------

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  state = {
    home: p.get("home"),
    cam: p.get("cam"),
    tab: p.get("tab") === "archive" ? "archive" : "live",
    day: p.get("day"),
    hour: p.has("hour") ? Number(p.get("hour")) : null,
  };
}

function go(next, replace = false) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(next)) if (v !== null && v !== undefined && v !== "") p.set(k, v);
  const hash = "#" + p.toString();
  if (replace) history.replaceState(null, "", hash);
  else history.pushState(null, "", hash);
  route();
}

window.addEventListener("popstate", route);

function route() {
  if (!info) return; // ещё не вошли — на экране вход
  readHash();
  stopPlayers(document);
  clearInterval(snapshotTimer);
  renderHomes();
  const cam = state.cam && info.cameras.find((c) => c.id === state.cam);
  if (cam) renderCam(cam);
  else renderGrid();
}

// ---------- плееры ----------

function stopPlayers(root) {
  for (const el of root.querySelectorAll("video-stream")) el.ondisconnect?.();
  for (const el of root.querySelectorAll("video")) { el.pause(); el.removeAttribute("src"); el.load?.(); }
}

function livePlayer(cam) {
  const v = document.createElement("video-stream");
  v.setAttribute("poster", snapshotUrl(cam.id)); // пока ждём видео — последний снимок
  v.mode = info.player_mode;
  v.src = streamUrl(cam.id);
  const mute = () => { if (v.video) v.video.muted = true; };
  mute();
  requestAnimationFrame(mute);
  return v;
}

const ICONS = {
  muted: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 9l5 6M21 9l-5 6" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round"/></svg>',
  sound: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round"/></svg>',
  full: '<svg viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round"/></svg>',
  exit: '<svg viewBox="0 0 24 24"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round"/></svg>',
};

function isFullscreen() {
  return Boolean(document.fullscreenElement || document.webkitFullscreenElement);
}

// Во весь экран: сам блок с видео; iPhone умеет только сам <video>.
function toggleFullscreen(box, player) {
  if (isFullscreen()) {
    (document.exitFullscreen || document.webkitExitFullscreen)?.call(document);
  } else if (box.requestFullscreen || box.webkitRequestFullscreen) {
    (box.requestFullscreen || box.webkitRequestFullscreen).call(box);
  } else {
    player.video?.webkitEnterFullscreen?.();
  }
}

// Панель живого видео: звук (вкл/выкл и громкость) и «во весь экран».
function liveControls(box, player) {
  const bar = document.createElement("div");
  bar.className = "controls";
  const mute = document.createElement("button");
  mute.title = "Звук";
  const volume = document.createElement("input");
  volume.type = "range";
  volume.min = "0";
  volume.max = "1";
  volume.step = "0.05";
  volume.value = "1";
  volume.title = "Громкость";
  const spacer = document.createElement("span");
  spacer.className = "spacer";
  const full = document.createElement("button");
  full.title = "Во весь экран";
  const sync = () => {
    const v = player.video;
    const muted = !v || v.muted || v.volume === 0;
    mute.innerHTML = muted ? ICONS.muted : ICONS.sound;
    full.innerHTML = isFullscreen() ? ICONS.exit : ICONS.full;
    if (v && !v.muted) volume.value = String(v.volume);
    if (v && v.muted) volume.value = "0";
  };
  mute.onclick = (e) => {
    e.stopPropagation();
    const v = player.video;
    if (!v) return;
    v.muted = !v.muted;
    if (!v.muted && v.volume === 0) v.volume = 1;
    sync();
  };
  volume.oninput = (e) => {
    e.stopPropagation();
    const v = player.video;
    if (!v) return;
    v.volume = Number(volume.value);
    v.muted = v.volume === 0;
    sync();
  };
  full.onclick = (e) => { e.stopPropagation(); toggleFullscreen(box, player); };
  if (box.classList.contains("stage")) box.ondblclick = () => toggleFullscreen(box, player);
  document.addEventListener("fullscreenchange", sync);
  document.addEventListener("webkitfullscreenchange", sync);
  bar.append(mute, volume, spacer, full);
  sync();
  setTimeout(sync, 300); // <video> внутри плеера появляется чуть позже

  return bar;
}

function placeholder(text) {
  const d = document.createElement("div");
  d.className = "placeholder";
  d.textContent = text;
  return d;
}

// ---------- шапка: дома ----------

function renderHomes() {
  const nav = $("#homes");
  const map = homes();
  const items = map.size > 1 ? [[ALL, info.cameras], ...map] : [...map];
  const current = state.home || (map.size > 1 ? ALL : [...map.keys()][0]);
  nav.replaceChildren(...items.map(([home, cams]) => {
    const b = document.createElement("button");
    b.innerHTML = `<span></span> <span class="count"></span>`;
    b.firstChild.textContent = home;
    const off = cams.filter((c) => c.online === false).length;
    b.querySelector(".count").textContent = off ? `${cams.length - off}/${cams.length}` : String(cams.length);
    b.setAttribute("aria-current", String(!state.cam && home === current));
    b.onclick = () => { wall = false; go({ home: home === ALL ? null : home }); };
    return b;
  }));
  $("#logout").hidden = false;
}

// ---------- сетка камер ----------

function tile(cam, live) {
  const li = document.createElement("li");
  li.className = "tile" + (cam.online === false ? " offline" : "");
  const media = document.createElement("div");
  media.className = "media";
  if (live && cam.online !== false) {
    const v = livePlayer(cam);
    media.append(v, liveControls(media, v));
  } else {
    const img = document.createElement("img");
    img.alt = "";
    img.loading = "lazy";
    img.src = snapshotUrl(cam.id);
    img.onerror = () => { img.remove(); media.append(placeholder(cam.online === false ? "офлайн" : "нет снимка")); };
    media.append(img);
  }
  const cap = document.createElement("div");
  cap.className = "caption";
  cap.innerHTML = `<b></b><span class="dot"></span>`;
  cap.querySelector("b").textContent = cam.name;
  cap.querySelector(".dot").classList.add(cam.online === true ? "on" : cam.online === false ? "off" : "unk");
  cap.querySelector(".dot").title = cam.online === true ? "онлайн" : cam.online === false ? "офлайн" : "проверяется";
  li.append(media, cap);
  li.onclick = () => go({ home: state.home, cam: cam.id });
  return li;
}

function renderGrid() {
  const map = homes();
  const home = state.home && map.has(state.home) ? state.home : null;
  const cams = camsOf(home);
  $("#grid-title").textContent = home || (map.size > 1 ? "Все камеры" : [...map.keys()][0] || "Камеры");
  const off = cams.filter((c) => c.online === false).length;
  $("#grid-count").textContent = off ? `${cams.length} · ${off} офлайн` : `${cams.length}`;
  const online = cams.filter((c) => c.online !== false);
  const liveShown = wall ? online.slice(0, info.max_streams).map((c) => c.id) : [];
  $("#tiles").replaceChildren(...cams.map((c) => tile(c, liveShown.includes(c.id))));
  $("#wall-toggle").textContent = wall ? "Остановить видео" : "Смотреть всё вживую";
  $("#wall-toggle").hidden = !online.length;
  $("#wall-note").hidden = !(wall && online.length > liveShown.length);
  $("#wall-note").textContent = `Вживую показаны первые ${liveShown.length}: больше одновременно нельзя. Остальные — снимки.`;
  show("#grid-view");
  document.title = `${$("#grid-title").textContent} · Камеры`;
  if (!wall) snapshotTimer = setInterval(refreshSnapshots, SNAPSHOT_REFRESH_MS);
}

function refreshSnapshots() {
  for (const img of document.querySelectorAll("#tiles img")) {
    const id = new URL(img.src, location.href).pathname.split("/").pop();
    const next = new Image();
    next.onload = () => { img.src = next.src; };
    next.src = snapshotUrl(decodeURIComponent(id));
  }
}

$("#wall-toggle").onclick = () => {
  wall = !wall;
  stopPlayers($("#tiles"));
  clearInterval(snapshotTimer);
  renderGrid();
};

// ---------- камера ----------

function renderCam(cam) {
  $("#cam-title").textContent = cam.name;
  const dot = $("#cam-dot");
  dot.className = "dot " + (cam.online === true ? "on" : cam.online === false ? "off" : "");
  $("#tab-archive").hidden = !info.recording;
  const archive = state.tab === "archive" && info.recording;
  $("#tab-live").setAttribute("aria-selected", String(!archive));
  $("#tab-archive").setAttribute("aria-selected", String(archive));
  $("#tab-live").onclick = () => go({ home: state.home, cam: cam.id }, true);
  $("#tab-archive").onclick = () => go({ home: state.home, cam: cam.id, tab: "archive" }, true);
  $("#back").onclick = () => go({ home: state.home });
  $("#archive").hidden = !archive;
  show("#cam-view");
  document.title = `${cam.name} · Камеры`;
  const stage = $("#stage");
  if (!archive) {
    if (cam.online === false) {
      stage.replaceChildren(placeholder("Камера офлайн"));
    } else {
      const v = livePlayer(cam);
      stage.replaceChildren(v, liveControls(stage, v));
    }
    return;
  }
  stage.replaceChildren(placeholder("Выберите час на шкале ниже"));
  renderArchive(cam);
}

function dayLabel(day) {
  const d = new Date(day + "T12:00:00");
  const today = new Date();
  const diff = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (diff === 0) return "Сегодня";
  if (diff === 1) return "Вчера";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short", weekday: "short" });
}

async function renderArchive(cam) {
  const r = await api(`/api/archive/${encodeURIComponent(cam.id)}/days`);
  const days = r.ok ? (await r.json()).days : [];
  if (state.cam !== cam.id) return;
  $("#archive-empty").hidden = days.length > 0;
  $("#archive-empty").textContent = "Записей пока нет.";
  $("#timeline").hidden = $("#days").hidden = !days.length;
  $(".archive-actions").hidden = !days.length;
  if (!days.length) {
    $("#stage").replaceChildren(placeholder("Записей пока нет"));
    $("#archive-empty").hidden = true;
    return;
  }
  const day = days.includes(state.day) ? state.day : days[0];
  $("#days").replaceChildren(...days.map((d) => {
    const b = document.createElement("button");
    b.textContent = dayLabel(d);
    b.title = d;
    b.setAttribute("aria-pressed", String(d === day));
    b.onclick = () => go({ home: state.home, cam: cam.id, tab: "archive", day: d }, true);
    return b;
  }));
  const hr = await api(`/api/archive/${encodeURIComponent(cam.id)}/${day}`);
  const hours = hr.ok ? (await hr.json()).hours : [];
  if (state.cam !== cam.id) return;
  const byHour = new Map(hours.map((h) => [h.hour, h]));
  let hour = byHour.has(state.hour) ? state.hour : null;
  $("#timeline").replaceChildren(...Array.from({ length: 24 }, (_, h) => {
    const b = document.createElement("button");
    b.textContent = String(h).padStart(2, "0");
    const e = byHour.get(h);
    if (e) {
      b.className = "has" + (e.recording ? " live" : "");
      b.title = `${String(h).padStart(2, "0")}:00 · ${(e.size / 1048576).toFixed(0)} МБ${e.recording ? " · идёт запись" : ""}`;
      b.onclick = () => go({ home: state.home, cam: cam.id, tab: "archive", day, hour: h }, true);
    } else {
      b.disabled = true;
    }
    b.setAttribute("aria-pressed", String(h === hour));
    return b;
  }));
  const recorded = hours.map((h) => h.hour);
  const idx = recorded.indexOf(hour);
  const step = (d) => go({ home: state.home, cam: cam.id, tab: "archive", day, hour: recorded[idx + d] }, true);
  $("#prev-hour").disabled = idx <= 0;
  $("#next-hour").disabled = idx < 0 || idx >= recorded.length - 1;
  $("#prev-hour").onclick = () => step(-1);
  $("#next-hour").onclick = () => step(1);
  const dl = $("#download");
  if (hour === null) {
    $("#archive-label").textContent = "Выберите час";
    dl.hidden = true;
    return;
  }
  const e = byHour.get(hour);
  $("#archive-label").textContent = `${dayLabel(day)}, ${String(hour).padStart(2, "0")}:00–${String(hour + 1).padStart(2, "0")}:00`;
  dl.hidden = false;
  dl.href = fileUrl(cam.id, day, e.name, true);
  const video = document.createElement("video");
  video.controls = true;
  video.playsInline = true;
  video.muted = true;
  video.autoplay = true;
  video.preload = "metadata";
  video.src = fileUrl(cam.id, day, e.name, false);
  $("#stage").replaceChildren(video);
}

// ---------- старт ----------

async function start_() {
  if (!(await loadCameras())) return;
  if (!info.cameras.length) {
    message("Камер пока нет.");
    return;
  }
  route();
}

if (await login()) start_();
