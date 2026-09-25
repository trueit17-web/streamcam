# streemcam — камеры Tuya (вход по QR) и Xiaomi C701 на VPS

Дата: 2026-09-25
Статус: дизайн утверждён, ожидает ревью спеки
Базовая спека: `2026-09-25-streemcam-design.md`

## 1. Цель

1. Показывать камеры из аккаунта Tuya / Smart Life, у которых нет RTSP, через тот же плеер и тех же ботов.
   Вход в аккаунт — по QR-коду из Telegram-бота администратора.
2. Поддержать реальную конфигурацию: сервис на VPS (4 vCPU, 8 ГБ), 3 камеры Xiaomi Smart Camera C701
   (`chuangmi.camera.079ae2`, cs2, H.265 + Opus) в домашней сети, связанной с VPS через WireGuard на роутере.
3. Дать зрителям на устройствах без H.265 «совместимый режим» (перекодирование в H.264 по запросу).

### Критерии успеха
- Админ выполняет `/tuya_login <код>`, сканирует QR в Smart Life, через ≤ 2 мин бот сообщает список камер;
  камеры Tuya сразу видны в плеере и в `/cams` без правки конфига и перезапуска.
- Камера Tuya открывается в плеере за ≤ 5 с; после обрыва/переподключения поток восстанавливается (свежая ссылка).
- C701 играют в Safari/Telegram iOS/Chrome с HW HEVC без перекодирования; в совместимом режиме — в любом браузере.
- Одновременно не больше `max_transcodes` перекодировок.

### Вне рамок
Несколько аккаунтов Tuya; управление камерами Tuya (PTZ, запись); вход по email/паролю; `tuya://` go2rtc;
Dahua остаётся в коде, но убирается из примеров.

## 2. Решения

| Вопрос | Решение |
|---|---|
| Где вход по QR | Telegram-бот, команды админа |
| Как камеры Tuya попадают в список | Автоматически все камеры аккаунта (категория `sp`) |
| Как go2rtc получает поток Tuya | Источник `echo:curl` → внутренний эндпоинт streemcam выдаёт свежую RTSP-ссылку при каждом подключении go2rtc |
| H.265 | Как есть; совместимый режим `<id>~h264` через ffmpeg по запросу; лимит `max_transcodes` (2) |
| Доступ VPS к C701 | WireGuard на домашнем роутере (вне кода; инструкция в README) |
| Публикация | Cloudflare Tunnel остаётся |
| Библиотека | `tuya-device-sharing-sdk` (как интеграция Tuya в Home Assistant) |

### Риски
- SDK использует client_id интеграции Home Assistant (`HA_3y9q4ak7g4ephrvke`, schema `haauthorize`);
  Tuya может изменить правила. Запасной путь (вне рамок): `tuya://` go2rtc c email/паролем Tuya Smart.
- Источник `echo:` исполняет команду в контейнере go2rtc. API go2rtc не публикуется наружу (только
  `127.0.0.1` и docker-сеть); streemcam регистрирует только фиксированный шаблон команды с `device_id`,
  проверенным по `^[A-Za-z0-9]+$`.

## 3. Архитектура

```
 Smart Life (телефон) ──QR──► облако Tuya
                                  │ токены / временная RTSP-ссылка
┌──────────── streemcam ──────────┼──────────────────────────────┐
│ tg-бот: /tuya_login <код> → QR  │                              │
│ tuya/: вход, хранение токенов, список камер, выдача ссылки     │
│ catalog: камеры из config.yaml + камеры Tuya                   │
│ :8080 публичный (туннель)   :8081 внутренний (только go2rtc)   │
└───────┬─────────────────────────────────▲──────────────────────┘
        │ регистрирует потоки через API go2rtc     │ curl …/tuya/<device_id>?key=…
        ▼                                          │
     go2rtc: tuya_<id>  = echo:curl -fsS http://streemcam:8081/tuya/<device_id>?key=<internal_key>
             <cam>~h264 = ffmpeg:<cam>#video=h264#width=1280#audio=opus
        ▲ WireGuard (роутер)
   3× Xiaomi C701 (type: xiaomi, model chuangmi.camera.079ae2)
```

## 4. Модули

### `tuya/` (новый пакет)
- `tuya/login.py` — `start_qr(user_code) -> QrSession(token, qr_payload)`;
  `poll(session) -> TuyaCredentials | None`. Обёртка над `LoginControl.qr_code(client_id, schema, user_code)`
  и `LoginControl.login_result(token, client_id, user_code)`. QR-содержимое: `tuyaSmart--qrLogin?token=<token>`.
- `tuya/store.py` — SQLite-таблица `tuya_account` (одна строка): `user_code`, `terminal_id`, `endpoint`,
  `token_json`, `uid`, `updated_at`. Методы `load() / save(creds) / update_token(token_json) / clear()`.
- `tuya/client.py` — `TuyaClient`: создаёт `Manager(client_id, user_code, terminal_id, end_point,
  token_response, listener)` из сохранённых данных; слушатель токенов пишет обновления в store.
  Методы (синхронные внутри, наружу — `async` через `asyncio.to_thread`):
  `list_cameras() -> list[TuyaCamera(device_id, name, online)]` (устройства категории `sp`),
  `allocate_rtsp(device_id) -> str` (`get_device_stream_allocate(device_id, "rtsp")`, `None` → ошибка).
  Ошибки авторизации → `TuyaAuthError`, прочие → `TuyaError`.
- Константы: `CLIENT_ID = "HA_3y9q4ak7g4ephrvke"`, `SCHEMA = "haauthorize"`.

### `catalog.py` (новый)
- `CameraInfo(id, name, kind)`; `kind` ∈ `config`, `tuya`.
- `Catalog(cfg)`: `all() -> list[CameraInfo]` (сначала из конфига, затем Tuya по имени),
  `get(cam_id) -> CameraInfo | None`, `set_tuya(cameras)`; `tuya_device_id(cam_id) -> str | None`.
- ID камеры Tuya: `tuya_` + `device_id.lower()`; конфликт с ID из конфига невозможен (конфиг-ID не
  начинаются с `tuya_` — валидация в `config.py`).
- Все потребители `cfg.cameras` / `cfg.camera()` (web, monitor, tg, dc, app) переходят на `Catalog`.

### Синхронизация с go2rtc (`go2rtc_sync.py`, новый)
go2rtc валидирует любой источник, добавленный через HTTP API `/api/streams` (`PUT`/`PATCH`), и отклоняет
(400) источники со схемой `echo:`/`exec:` или с пробелами внутри — т.е. `echo:curl -fsS …` через этот API
зарегистрировать нельзя. Потоки, загруженные из YAML-файла конфига go2rtc, этой проверке не подвергаются.
Поэтому синхронизация управляет только потоками `tuya_*` через файл конфига:
- `Go2rtcSync.sync()` (под `asyncio.Lock`, вызывается при старте, после входа/выхода Tuya и после каждого
  обновления списка камер Tuya): считает желаемое состояние — для каждой камеры Tuya в каталоге (только
  если задан `internal_key`) `tuya_<id>` = `echo:curl -fsS <internal_url>/tuya/<device_id>?key=<key>` и
  `tuya_<id>~h264` = `ffmpeg:tuya_<id>#video=h264#width=1280#audio=aac`.
- `GET /api/config` (текст YAML); при ошибке транспорта/не-200/невалидном YAML — лог и выход без записи.
  Сравнивает текущие записи `streams`, чьё имя начинается с `tuya_`, с желаемыми (значение может быть
  строкой или списком из одного элемента — нормализуется); при совпадении ничего не пишет.
- Иначе собирает новый конфиг: все остальные ключи и потоки (включая `xiaomi:` и т. п.) сохраняются как
  есть, записи `tuya_*` заменяются на желаемые; `POST /api/config` с телом — `yaml.safe_dump(...)`; при
  не-2xx — лог и выход; иначе `POST /api/restart` (go2rtc перечитывает конфиг и перезапускается — текущие
  зрители переподключаются).
- Потоки `<id>~h264` для камер из `config.yaml` рендерит одноразовый сервис `render-go2rtc`
  (`go2rtc_config.py`), а не `Go2rtcSync` — они не требуют динамики.
- Ошибка go2rtc на любом шаге → лог, повтор при следующей синхронизации.

### Внутренний сервер (`web/internal.py`, новый)
- Отдельное FastAPI-приложение на `internal_listen` (`0.0.0.0:8081` в docker; порт не публикуется).
- `GET /tuya/{device_id}?key=` → `text/plain` RTSP-ссылка. Неверный/нет `key` → 403 (сравнение
  `hmac.compare_digest`); нет входа → 503; `TuyaError`/`None` → 502; неизвестный `device_id` (нет в каталоге) → 404.

### Веб (`web/server.py`, изменения)
- `/api/cameras` строится из каталога; элемент дополняется `"kind"`.
- `WS /api/ws?src=<cam>&s=<session>&compat=1` → upstream-поток `<cam>~h264`; проверка лимита
  `max_transcodes` (глобальный счётчик); превышение → закрытие `4430`.
- Плеер: переключатель «Совместимый режим» (хранится в `localStorage`), при 4430 — сообщение
  «Сервер занят перекодированием, выключите совместимый режим или попробуйте позже».

### Монитор (изменения)
- Камеры из конфига — как раньше.
- Камеры Tuya — `online` из списка облака; снимок `frame.jpeg` не чаще раза в `tuya_snapshot_minutes` (15).

### Telegram-бот (изменения)
- `/tuya_login <код>` (админ): QR PNG (библиотека `segno`) с подписью-инструкцией; опрос `poll` каждые 2 с,
  до 120 с; успех → store.save, обновление каталога, sync, ответ со списком камер; тайм-аут → сообщение.
  Повторный `/tuya_login` во время ожидания отменяет предыдущую попытку.
- `/tuya_status` (админ): вошёл ли, аккаунт (uid), камеры и их online.
- `/tuya_logout` (админ): store.clear, каталог без Tuya, sync.
- `TuyaAuthError` при обновлении/выдаче ссылки → одно уведомление админам до следующего успешного входа.

### Конфиг (изменения)
- `tuya: {enabled: true, refresh_minutes: 10, snapshot_minutes: 15}`.
- `max_transcodes: 2`, `internal_listen: "127.0.0.1:8081"` (docker: `0.0.0.0:8081`),
  `internal_url: "http://127.0.0.1:8081"` (docker: `http://streemcam:8081`), `internal_key` (из env, ≥ 16).
- Валидация: ID камер из конфига не начинаются с `tuya_` и не содержат `~`.

### Развёртывание
- `config.example.yaml`: убрать Dahua, добавить 3× C701 (`type: xiaomi`, `model: chuangmi.camera.079ae2`),
  секцию `tuya`, новые поля. `.env.example`: `STREEMCAM_INTERNAL_KEY=`.
- `docker-compose.yml`: streemcam слушает 8081 только в docker-сети (без `ports`).
- README: раздел «VPS + WireGuard на роутере» (общая схема: роутер — клиент WireGuard, VPS — сервер,
  маршрут в домашнюю подсеть; go2rtc в контейнере достигает `192.168.x.x` через хост), раздел «Камеры Tuya»
  (где взять код пользователя в Smart Life, `/tuya_login`), раздел «Совместимый режим».

## 5. Ошибки
- Внутренний эндпоинт: 403 / 404 / 502 / 503 как выше; go2rtc помечает поток недоступным → «Камера офлайн».
- Протухший refresh-токен (`TuyaAuthError`) → одно уведомление админам; камеры Tuya остаются в каталоге, offline.
- Облако недоступно при обновлении списка → сохраняется последний список, warning в лог.
- go2rtc недоступен при sync → лог, повтор при следующем цикле.

## 6. Безопасность
- `internal_key` ≥ 16 символов, в `.env`; эндпоинт только в docker-сети; сравнение за постоянное время.
- `device_id` в команде `echo:` проверяется по `^[A-Za-z0-9]+$` до регистрации.
- Токены Tuya — в SQLite в `data/` (не в git); в лог не пишутся.
- Команды Tuya — только админам.

## 7. Тестирование (pytest, TDD, без сети)
- login: мок `LoginControl` — успех, ожидание, тайм-аут; содержимое QR.
- store: save/load/update_token/clear.
- client: мок `Manager` — фильтр `sp`, `allocate_rtsp` (успех, `None` → `TuyaError`), слушатель токенов → store.
- catalog: слияние, ID, `get`, `tuya_device_id`, запрет `tuya_`/`~` в конфиге.
- go2rtc_sync: MockTransport — GET/POST `/api/config` + `/api/restart`, отсутствие записи при совпадении,
  сохранение чужих ключей/потоков, ошибки транспорта на каждом шаге, отказ при плохом `device_id`.
- internal: 403/404/502/503/200.
- web: `/api/cameras` с Tuya; WS `compat=1` → `~h264`; лимит → 4430.
- tg: `/tuya_login` (мок login + мок bot), `/tuya_status`, `/tuya_logout`, права.
- Ручная проверка на проде: QR-вход, просмотр Tuya и C701, совместимый режим.
