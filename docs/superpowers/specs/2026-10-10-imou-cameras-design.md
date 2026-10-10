# streemcam — камеры Imou (облако, только просмотр)

Дата: 2026-10-10
Статус: дизайн утверждён, ожидает ревью спеки
Базовые спеки: `2026-09-25-streemcam-design.md`, `2026-09-25-tuya-cameras-design.md`

## 1. Цель

Показывать камеры Imou (первая — IPC-C32EP), доступные VPS только через облако Imou, в том же плеере и
тех же ботах, что и остальные камеры. Камеры подтягиваются из аккаунта автоматически.

### Критерии успеха
- После заполнения `IMOU_APP_ID` / `IMOU_APP_SECRET` и перезапуска все камеры аккаунта Imou видны в плеере
  и в `/cams` (≤ `refresh_minutes`) без правки списка `cameras`.
- Камера Imou открывается в плеере; задержка — как у HLS (несколько секунд), это ожидаемо.
- Совместимый режим (`~h264`) работает для камер Imou с тем же лимитом `max_transcodes`.
- Неверные ключи или недоступное облако не роняют сервис: остальные камеры работают, админ получает алерт.

### Вне рамок
Запись в архив (в том числе по событиям), PTZ и прочее управление, события/тревоги, несколько аккаунтов
Imou, локальный RTSP (камера не доступна VPS по сети), библиотека `imouapi`.

## 2. Решения

| Вопрос | Решение |
|---|---|
| Доступ к камере | Только облако: Imou Open API (`openapi.easy4ip.com`) |
| Учётные данные | `appId` / `appSecret` аккаунта разработчика open.imoulife.com (тот же аккаунт, что в Imou Life), из `.env` |
| Как камеры попадают в список | Автоматически все камеры аккаунта (`deviceBaseList`), опрос раз в `refresh_minutes` |
| Как go2rtc получает поток | `echo:curl` → внутренний эндпоинт streemcam отдаёт свежую HLS-ссылку при каждом подключении (как Tuya) |
| Клиент API | Свой на `httpx` (4 вызова); `imouapi` не берём — требует Python ≥ 3.11 и aiohttp |
| Запись | Нет: `rec_stream_name` для Imou → `None` |

### Риски
- Документация Imou недоступна из среды разработки; формат запросов и ответов взят из исходников
  `imouapi` 1.0.15 (MIT). Первый реальный запуск — у пользователя.
- Квоты бесплатного тарифа разработчика (запросы, число трансляций). Просмотр по запросу их почти не тратит.
- Если в приложении включено шифрование видео, облако может не отдать HLS — выключить его
  (вывод из документации, не проверено на камере).
- Аккаунт в другом регионе → другой адрес API; настраивается `imou.api_url`.

## 3. Архитектура

```
 Imou Life (телефон) ── аккаунт ──► облако Imou (openapi.easy4ip.com)
                                         ▲ accessToken, deviceBaseList, getLiveStreamInfo / bindDeviceLive
┌──────────── streemcam ─────────────────┼─────────────────────────┐
│ imou/: клиент API, сервис опроса списка камер                     │
│ catalog: камеры из config.yaml + Tuya + Imou                      │
│ :8080 публичный (туннель)   :8081 внутренний (только go2rtc)      │
└───────┬──────────────────────────────────▲────────────────────────┘
        │ go2rtc_sync (файл конфига)        │ curl …/imou/<device_id>?key=…
        ▼                                   │
     go2rtc: imou_<id>      = echo:curl -fsS http://streemcam:8081/imou/<device_id>?key=<internal_key>
                              (ответ: ffmpeg:https://…m3u8#video=copy#audio=aac)
             imou_<id>~h264 = ffmpeg:imou_<id>#video=h264#width=1280#audio=aac
```

## 4. Модули

### `imou/` (новый пакет)
- `imou/models.py` — `ImouCamera(device_id, name, online)`; `ImouError`, `ImouAuthError(ImouError)`.
- `imou/client.py` — `ImouClient(app_id, app_secret, api_url, http: httpx.AsyncClient)`:
  - Запрос: `POST <api_url>/<method>`, тело
    `{"system": {"ver": "1.0", "sign", "appId", "time", "nonce"}, "params": {..., "token"}, "id"}`,
    `sign = md5("time:<t>,nonce:<nonce>,appSecret:<secret>")`. Ответ: `result.code` (`"0"` — успех),
    `result.msg`, `result.data`.
  - `accessToken` получает лениво методом `accessToken` и хранит в памяти; `TK1002` → один повторный вход
    и повтор запроса; `OP1008` / `SN1001` / `OP1009` → `ImouAuthError`; прочие коды, не-200, транспорт,
    битый JSON → `ImouError`.
  - `list_cameras() -> list[ImouCamera]` — `deviceBaseList` (`bindId=-1`, `limit=50`, `type=bindAndShare`,
    `needApInfo=false`) даёт `deviceId`; имена — одним вызовом `deviceBaseDetailList` (поле `name`, канал
    `"0"`); `online` — `deviceOnline` по каждой камере (`onLine`: `"1"` онлайн, `"4"` спит — тоже онлайн,
    `"0"` и прочее — офлайн; ошибка этого вызова → офлайн для этой камеры). Больше 50 устройств — вне рамок
    (warning в лог).
  - `stream_url(device_id) -> str` — `getLiveStreamInfo(deviceId, channelId="0")`: берёт `https://`-ссылку
    `hls` основного потока; если трансляции нет — `bindDeviceLive(deviceId, channelId="0", streamId=0)`;
    нет ссылки → `ImouError`.
  - Логирование без секретов: `appSecret`, `token`, `sign`, ссылки на поток маскируются.
- `imou/service.py` — `ImouService(cfg, catalog, client, sync, on_alert)`:
  - `refresh()`: `list_cameras` → `catalog.set_imou` → `sync.sync()`; успех сбрасывает счётчик неудач.
  - Ошибка → `catalog.mark_imou_offline()`; после `FAILURE_ALERT_AFTER = 3` неудач подряд — один алерт
    админу до следующего успеха. `ImouAuthError` — алерт сразу (один раз), текст про проверку ключей.
  - `run()`: `refresh()` раз в `refresh_minutes`.
  - `stream_url(device_id)` — проброс в клиент.

### `catalog.py` (изменения)
- `kind` ∈ `config`, `tuya`, `imou`. ID камеры Imou: `imou_` + `device_id.lower()`.
- `set_imou(cameras)` (проверка `device_id` по `^[A-Za-z0-9]+$`, иначе пропуск с warning),
  `imou()`, `mark_imou_offline()`. `all()` — конфиг, затем Tuya, затем Imou (каждая группа по имени).

### `go2rtc_sync.py` (изменения)
- `imou_src(internal_url, device_id, key)` = `echo:curl -fsS <internal_url>/imou/<device_id>?key=<key>`.
- `desired_streams` добавляет для каждой камеры Imou `imou_<id>` и `imou_<id>~h264` = `compat_src(id)`.
- Управляемые потоки — имена с префиксом `tuya_` или `imou_`; остальные сохраняются как есть.

### `web/internal.py` (изменения)
- `create_internal_app(catalog, tuya, imou, internal_key)`.
- `GET /imou/{device_id}?key=` → `text/plain` `ffmpeg:<hls>#video=copy#audio=aac`. Неверный ключ → 403;
  Imou выключен или `device_id` не в каталоге → 404; `ImouError` → 502.

### `web/server.py`, `monitor.py`, боты
- WS-прокси: для `kind="imou"` upstream — сам поток `imou_<id>` (без `~live`), `compat=1` → `~h264`.
- Монитор: для Imou — `online` из облака, снимок кадром из go2rtc не чаще раза в `imou.snapshot_minutes`
  (общая логика с Tuya).
- `rec_stream_name`: Imou → `None`. Боты: камеры Imou приходят через каталог, новых команд нет.

### `config.py` (изменения)
- `ImouConfig{enabled: false, app_id, app_secret, api_url: "https://openapi.easy4ip.com/openapi",
  refresh_minutes: 10, snapshot_minutes: 15}`; пустые строки → `None`.
- Валидация: при `imou.enabled` обязательны `app_id`, `app_secret` и `internal_key`; ID камер из конфига
  не начинаются с `imou_`.

### `app.py`, развёртывание
- При `imou.enabled` создаётся `ImouService`, запускается под `supervise("imou", …)`; внутренний сервер
  поднимается, если включён Tuya или Imou; алерты — тем же путём, что у Tuya.
- `config.example.yaml`: секция `imou` с `${IMOU_APP_ID:-}` / `${IMOU_APP_SECRET:-}`; `.env.example` —
  эти переменные.
- README: раздел «Камеры Imou» — регистрация на open.imoulife.com тем же аккаунтом, где взять
  `appId`/`appSecret`, региональный `api_url`, шифрование видео, только просмотр.

## 5. Ошибки
- Облако недоступно → камеры Imou в каталоге, offline; после 3 неудач — алерт.
- Неверные ключи → алерт сразу, камеры Imou offline; остальные камеры работают.
- Нет ссылки на поток → эндпоинт 502 → go2rtc: ошибка потока → плеер «Камера офлайн».
- go2rtc недоступен при sync → лог, повтор при следующем цикле.

## 6. Безопасность
- `app_secret` только в `.env`; в лог не пишется. `accessToken` только в памяти.
- `device_id` в команде `echo:` проверяется по `^[A-Za-z0-9]+$` до регистрации.
- Внутренний эндпоинт — только docker-сеть, ключ сравнивается за постоянное время.

## 7. Тестирование (pytest, TDD, без сети)
- client (`httpx.MockTransport`): подпись и тело запроса, ленивый `accessToken`, повтор на `TK1002`,
  `ImouAuthError` на `OP1008`/`SN1001`/`OP1009`, `ImouError` на прочее; `list_cameras` (имена, online и спящий режим);
  `stream_url` (существующая трансляция, создание через `bindDeviceLive`, нет ссылки).
- service: обновление каталога и sync, offline при ошибке, алерт после 3 неудач и сразу при auth.
- catalog: `set_imou`, ID, небезопасный `device_id`, порядок `all()`.
- config: обязательные поля при `enabled`, запрет `imou_`.
- go2rtc_sync: потоки `imou_*`, совместное управление с `tuya_*`, сохранение прочих потоков.
- internal: 403 / 404 / 502 / 200 для `/imou/...`.
- web/monitor: WS для `imou` и `compat=1`; онлайн и снимки Imou.
- Ручная проверка у пользователя: камеры появились, поток открывается, совместимый режим.
