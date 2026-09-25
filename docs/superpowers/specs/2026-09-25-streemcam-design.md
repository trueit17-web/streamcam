# streemcam — просмотр камер из Telegram и Discord

Дата: 2026-09-25
Статус: утверждён дизайн, ожидает ревью спеки

## 1. Цель

Дать пользователям из белого списка возможность в один-два клика открыть живое видео с камер
из Telegram (канал, группа, личка) и Discord (сервер). Источники: RTSP-камеры, камеры Dahua,
камеры Xiaomi (Mi Home, стоковая прошивка).

### Критерии успеха
- Пользователь из белого списка нажимает кнопку в Telegram-канале → внутри Telegram открывается
  Mini App со списком камер → живое видео стартует за ≤ 3 с, задержка ≤ 1.5 с (через Cloudflare Tunnel).
- Пользователь в Discord вызывает `/cams` → получает личную (эфемерную) ссылку → видео в браузере.
- Пользователь не из белого списка видео не получает ни одним путём; пересланная ссылка не работает.
- Добавление камеры — одна запись в `config.yaml` и перезапуск.

### Вне рамок v1
Запись/архив, детекция движения, Discord Activity, PTZ, обратный звук, доступ по ролям/по камерам.

## 2. Решения

| Вопрос | Решение |
|---|---|
| Формат доступа | Живой плеер (веб-страница / Telegram Mini App), не снимки |
| Размещение | Локальный сервер в сети камер + Cloudflare Tunnel; переносимо на VPS (Docker) |
| Права | Белый список ID (Telegram + Discord), правится админами командами бота |
| Xiaomi | Стоковая прошивка, источник `xiaomi://` в go2rtc (≥ 1.9.13) |
| Медиаядро | go2rtc |
| Язык | Python 3.12: FastAPI, aiogram 3, discord.py 2, pydantic 2, SQLite |

## 3. Архитектура

```
 Камеры (RTSP / Dahua / Xiaomi)
        │  локальная сеть
   ┌────▼─────┐   127.0.0.1:1984 (не опубликован)
   │  go2rtc  │◄───────────────┐
   └──────────┘                │ proxy WS/снапшоты (только разрешённые камеры)
                        ┌──────┴───────────────────────────┐
                        │  streemcam (Python, один процесс) │
                        │  ├─ web:  FastAPI — плеер, /api   │
                        │  ├─ tg:   aiogram бот             │
                        │  ├─ dc:   discord.py бот          │
                        │  ├─ auth: белый список + токены   │
                        │  └─ config: config.yaml → go2rtc  │
                        └──────┬───────────────────────────┘
                               │ :8080
                        ┌──────▼─────┐
                        │ cloudflared│ → https://<домен>
                        └────────────┘
```

Развёртывание: `docker-compose.yml` с сервисами `go2rtc`, `streemcam`, `cloudflared`.
На VPS `cloudflared` убирается, открывается порт 8555 (WebRTC) и HTTPS через reverse-proxy.

### Транспорт видео
Cloudflare Tunnel пропускает только HTTP/WebSocket, поэтому основной транспорт — MSE поверх
WebSocket (`/api/ws` go2rtc). Плеер — `video-rtc.js` из go2rtc, режим `mse` (с `webrtc`, если
доступен). Никакой транскодировки в v1: поток H.264 отдаётся как есть; для H.265-камер
используется substream в H.264 (для Dahua — `subtype=1`).

## 4. Модули

Пакет `streemcam/`:

### `config`
- `config.yaml` (не в git; в репо — `config.example.yaml`) + секреты в `.env`.
- Модель (pydantic):
  - `public_url`, `session_ttl_hours` (12), `token_ttl_minutes` (10), `max_streams_per_user` (4)
  - `telegram`: `bot_token` (env), `mini_app_short_name`, `channel_id` (опц.)
  - `discord`: `bot_token` (env), `guild_ids` (список)
  - `admins`: `telegram: [int]`, `discord: [int]`
  - `allow` (начальный белый список): `telegram: [int]`, `discord: [int]`
  - `cameras`: список, каждая `{id, name, type, ...}`:
    - `type: rtsp` — `url`
    - `type: dahua` — `host`, `port` (554), `user`, `password`, `channel` (1), `subtype` (0|1)
      → `rtsp://user:pass@host:port/cam/realmonitor?channel=N&subtype=M`
    - `type: xiaomi` — `account`, `region`, `host`, `did`, `model`, `subtype` (опц.)
      → `xiaomi://account:region@host?did=…&model=…`
- `id` камеры: `[a-z0-9_-]+`, уникален.
- Функция `render_go2rtc(config) -> dict` генерирует `go2rtc.yaml` (streams + `api.listen: 127.0.0.1:1984`
  или имя сервиса в docker-сети, `webrtc.listen: :8555`). Генерация при старте, файл пишется в общий volume.
- Невалидный конфиг — отказ старта с сообщением вида `cameras[2].host: field required`.

### `auth`
- SQLite `data/streemcam.db`:
  - `allowed(platform TEXT, user_id INTEGER, added_by INTEGER, added_at TEXT, PK(platform,user_id))`
  - `link_tokens(jti TEXT PK, platform, user_id, expires_at, used INTEGER)`
- При старте `allow` из конфига добавляется в таблицу (idempotent). Админы всегда разрешены.
- `issue_link_token(platform, user_id) -> str` — HMAC-SHA256 (секрет `STREEMCAM_SECRET`), одноразовый, TTL 10 мин.
- `redeem_link_token(token) -> Identity | error` — проверка подписи, срока, `used=0`, помечает used.
- `verify_tg_init_data(init_data, bot_token, max_age=3600) -> Identity | error` — официальный алгоритм
  (`secret = HMAC("WebAppData", bot_token)`, сравнение `hash`).
- Сессия: подписанная cookie `sc_session` (`platform`, `user_id`, `exp`), `HttpOnly; Secure; SameSite=Lax`.
- `is_allowed(identity)` — проверка по таблице при каждом новом WS-подключении и каждом API-запросе.

### `web` (FastAPI)
- `GET /` — страница плеера (статический HTML/JS): список камер с превью, открытие камеры, сетка, fullscreen.
  Если есть `?t=<token>` — редирект на `/auth/link?t=…`.
- `GET /auth/link?t=` — погашение токена → cookie → редирект на `/`.
- `POST /api/tg/session` — тело `{init_data}` → проверка → cookie. Ответ 403 содержит ID пользователя.
- `GET /api/cameras` — список `{id, name, online}` (online — из кэша опроса go2rtc `/api/streams` раз в 60 с).
- `GET /api/snapshot/{cam}` — прокси `go2rtc /api/frame.jpeg?src=cam`.
- `WS /api/ws?src={cam}` — прокси в `go2rtc /api/ws?src=cam`. Проверки: сессия, `is_allowed`, камера
  есть в конфиге, лимит одновременных потоков пользователя.
- Активные WS регистрируются по пользователю; при `/deny` — закрываются.
- Все остальные пути go2rtc недоступны снаружи.

### `tg` (aiogram 3)
- `/start`, `/cams` — сообщение с кнопкой `web_app` (в личке) / URL-кнопкой `https://t.me/<bot>/<short_name>`
  (в группах).
- `/post_channel` (админ) — публикует в `channel_id` пост с URL-кнопкой «📹 Камеры» (для закрепа).
- `/allow <id>`, `/deny <id>`, `/users` (админ). `/allow` / `/deny` также ответом на сообщение пользователя.
  Платформа по умолчанию — telegram; `/allow dc:<id>` — для Discord.
- Уведомление админам при ошибках камер Xiaomi (переход online→offline держится > 5 мин).

### `dc` (discord.py 2)
- Slash `/cams` — проверка белого списка, эфемерный ответ: кнопка «Все камеры» + по кнопке на камеру
  (≤ 24 камер, иначе только «Все камеры»), ссылки `https://<домен>/?t=<токен>` (+`#cam=<id>`).
- Не из белого списка — эфемерно «Нет доступа, ваш ID: …».
- Slash `/allow user:<@user|id>`, `/deny`, `/users` (админ). `tg:<id>` — для Telegram.

### `app`
- Точка входа `python -m streemcam`: загрузка конфига → генерация go2rtc.yaml → init БД →
  uvicorn + оба бота в одном asyncio-цикле. Каждый бот — отдельная задача-супервизор:
  при падении лог + перезапуск с backoff (до 5 мин); отсутствие токена бота = бот отключён.

## 5. Обработка ошибок
- Камера офлайн → в плеере «Камера офлайн» + «Повторить»; в списке пометка.
- Обрыв WS → автопереподключение с backoff 1→2→4…30 с.
- Xiaomi: ошибка ключей/авторизации → лог + уведомление админам; перелогин в WebUI go2rtc
  (только локально, `http://127.0.0.1:1984`), инструкция в README.
- Падение одного бота не затрагивает остальное.
- Секреты не логируются (маскирование URL камер в логах).

## 6. Безопасность
- go2rtc слушает только loopback/docker-сеть; наружу — только `streemcam`.
- Одноразовые короткоживущие токены ссылок; сессия в подписанной cookie; проверка белого списка на каждом подключении.
- `initData` проверяется по подписи и возрасту.
- `config.yaml`, `.env`, `data/` — в `.gitignore`.
- Лимит одновременных потоков на пользователя.

## 7. Тестирование (pytest, TDD)
- Unit: `render_go2rtc` для rtsp/dahua/xiaomi; валидация конфига; токены (валидный, просроченный,
  повторный, чужая подпись); `verify_tg_init_data` на эталонных данных; белый список.
- Web (FastAPI TestClient + фейковый go2rtc): вход через TG и через link-токен; 403 чужим;
  403 для неизвестной камеры; лимит потоков; закрытие WS после `/deny`.
- Боты: обработчики команд с моками API (без сети).
- Ручная проверка: `docker compose up` с тестовым источником go2rtc (`ffmpeg:` из файла), открыть плеер.

## 8. Структура репозитория

```
streemcam/
  __main__.py  app.py  config.py  go2rtc.py  auth.py  db.py
  web/ (server.py, static/index.html, static/player.js)
  tg/bot.py
  dc/bot.py
tests/
config.example.yaml  .env.example  docker-compose.yml  Dockerfile  pyproject.toml  README.md
```
