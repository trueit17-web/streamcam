# Камеры Imou (облако, только просмотр) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** камеры аккаунта Imou автоматически появляются в плеере и ботах; go2rtc берёт свежую HLS-ссылку через внутренний эндпоинт.

**Architecture:** новый пакет `streemcam/imou/` (модели, async-клиент Imou Open API на `httpx`, сервис опроса) встраивается в уже существующие механизмы Tuya: `Catalog` (новый `kind="imou"`), `Go2rtcSync` (управляемые потоки `imou_*`), внутреннее приложение `web/internal.py` (`/imou/{device_id}`), `CameraMonitor`, WS-прокси.

**Tech Stack:** Python 3.12, FastAPI, httpx, pydantic v2, pytest + pytest-asyncio (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-10-10-imou-cameras-design.md`

## Global Constraints

- Новых зависимостей нет; `imouapi` не используется.
- API по умолчанию: `https://openapi.easy4ip.com/openapi`; запрос `POST <api_url>/<method>`.
- `sign = md5("time:<t>,nonce:<nonce>,appSecret:<secret>")` (hex), тело `{"system": {"ver": "1.0", "sign", "appId", "time", "nonce"}, "params": {...}, "id": "<str>"}`; `token` кладётся в `params` для всех методов, кроме `accessToken`.
- Коды: `"0"` успех; `TK1002` → один повторный вход и повтор; `OP1008`, `SN1001`, `OP1009` → `ImouAuthError`; `LV1002` у `getLiveStreamInfo` — «трансляции нет»; `LV1001` у `bindDeviceLive` — «уже есть», не ошибка.
- ID камеры Imou: `"imou_" + device_id.lower()`; `device_id` должен соответствовать `^[A-Za-z0-9]+$`.
- Ответ внутреннего эндпоинта: `ffmpeg:<hls>#video=copy#audio=aac`.
- `FAILURE_ALERT_AFTER = 3`; `refresh_minutes` 10, `snapshot_minutes` 15 по умолчанию.
- Секреты (`app_secret`, `accessToken`, `sign`, HLS-ссылки) не попадают ни в логи, ни в текст исключений.
- Записи для Imou нет. Все тексты для пользователя — на русском.

## Review Focus

1. Пустой аккаунт или выключенный Imou: потоки `imou_*` удаляются из go2rtc, потоки `tuya_*` и прочие не трогаются — тест в Task 4.
2. `device_id` с заглавными буквами: ID камеры в нижнем регистре, но в облако и в URL эндпоинта уходит исходный `device_id` — тесты в Tasks 3 и 5.
3. `TK1002` дважды подряд: ровно один повторный вход, затем `ImouError`, без бесконечного цикла — тест в Task 2.
4. В ответе `getLiveStreamInfo` есть только `http://`-ссылка, неактивная (`status != "1"`) или `streamId=1`: она не выбирается, создаётся трансляция — тест в Task 2.
5. Текст `ImouError` при ошибке API не содержит `app_secret` и токен (он доходит до логов и алертов) — тест в Task 2.

---

### Task 1: Конфиг `imou`

**Files:**
- Modify: `streemcam/config.py`
- Modify: `config.example.yaml`, `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `ImouConfig(enabled: bool = False, app_id: str | None = None, app_secret: str | None = None, api_url: str = "https://openapi.easy4ip.com/openapi", refresh_minutes: int = 10, snapshot_minutes: int = 15)`; `Config.imou: ImouConfig = ImouConfig()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_imou_defaults(cfg):
    assert cfg.imou.enabled is False
    assert cfg.imou.api_url == "https://openapi.easy4ip.com/openapi"
    assert (cfg.imou.refresh_minutes, cfg.imou.snapshot_minutes) == (10, 15)

def test_imou_empty_strings_are_none(make_cfg):
    c = make_cfg(imou={"app_id": "", "app_secret": ""})
    assert c.imou.app_id is None and c.imou.app_secret is None

def test_imou_api_url_trailing_slash_stripped(make_cfg):
    assert make_cfg(imou={"api_url": "https://x/openapi/"}).imou.api_url == "https://x/openapi"

@pytest.mark.parametrize("imou,msg", [
    ({"enabled": True, "app_secret": "s"}, "app_id"),
    ({"enabled": True, "app_id": "a"}, "app_secret"),
])
def test_imou_enabled_requires_keys(make_cfg, imou, msg):
    with pytest.raises(ConfigError, match=msg):
        make_cfg(imou=imou, internal_key="k" * 20)

def test_imou_enabled_requires_internal_key(make_cfg):
    with pytest.raises(ConfigError, match="internal_key"):
        make_cfg(imou={"enabled": True, "app_id": "a", "app_secret": "s"})

def test_camera_id_must_not_start_with_imou(make_cfg, base_data):
    cams = base_data["cameras"] + [{"id": "imou_x", "name": "X", "type": "rtsp", "url": "rtsp://x"}]
    with pytest.raises(ConfigError, match="imou_"):
        make_cfg(cameras=cams)
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_config.py -q` — Expected: новые тесты FAIL (`imou` нет).
- [ ] **Step 3: Implement** `ImouConfig` в `config.py` (пустые `app_id`/`app_secret` → `None` через `mode="before"` валидатор, `api_url.rstrip("/")`); в `_check_cameras_and_tuya` — запрет префикса `imou_` и при `imou.enabled` проверки: нет `app_id` → `"imou.app_id is required when imou.enabled is true"`, аналогично `app_secret`, нет `internal_key` → `"internal_key is required when imou.enabled is true"`.
- [ ] **Step 4: Update examples:** в `config.example.yaml` после `tuya:` секция `imou:` (`enabled: false`, `app_id: ${IMOU_APP_ID:-}`, `app_secret: ${IMOU_APP_SECRET:-}`, закомментированный `api_url` с пояснением про регион, `refresh_minutes: 10`, `snapshot_minutes: 15`); в `.env.example` — `IMOU_APP_ID=` и `IMOU_APP_SECRET=` с комментарием «ключи с open.imoulife.com».
- [ ] **Step 5: Run** `python3 -m pytest -q` — Expected: всё PASS (в т. ч. тест, что пример конфига парсится, если он есть).
- [ ] **Step 6: Commit** `feat: imou config section`

### Task 2: Модели и клиент Imou Open API

**Files:**
- Create: `streemcam/imou/__init__.py` (пустой), `streemcam/imou/models.py`, `streemcam/imou/client.py`
- Test: `tests/test_imou_client.py`

**Interfaces:**
- Produces (`models.py`): `class ImouError(Exception)`, `class ImouAuthError(ImouError)`, `@dataclass(frozen=True) class ImouCamera(device_id: str, name: str, online: bool)`.
- Produces (`client.py`): `ImouClient(app_id: str, app_secret: str, api_url: str, http: httpx.AsyncClient, clock=time.time, nonce=lambda: secrets.token_hex(16))`; `async list_cameras() -> list[ImouCamera]`; `async stream_url(device_id: str) -> str` (возвращает голую `https://…m3u8`).

Поведение, которое тесты фиксируют:
- `_call(method, params)`: при отсутствии токена сначала `accessToken` (берёт `data.accessToken`); не-200, ошибка транспорта, не-JSON, нет `result.code` → `ImouError(f"{method}: <краткая причина>")`; код ≠ `"0"` → `ImouError(f"{method}: {code} {msg}")` (или `ImouAuthError` для auth-кодов); `TK1002` → сброс токена, один повторный `accessToken`, повтор; второй `TK1002` → `ImouError`.
- `list_cameras`: `deviceBaseList` (`{"bindId": -1, "limit": 50, "type": "bindAndShare", "needApInfo": False}`) → `deviceList[].deviceId`; пусто → `[]` без других вызовов; `deviceBaseDetailList` (`{"deviceList": [{"deviceId": id, "channelList": "0"}, ...]}`) → `name` по `deviceId` (нет имени → `deviceId`); для каждой камеры `deviceOnline({"deviceId": id})` → `onLine in ("1", "4")`; `ImouError` из `deviceOnline` → `online=False` (auth-ошибка пробрасывается); `len(deviceList) >= 50` → `log.warning`.
- `stream_url`: `getLiveStreamInfo({"deviceId", "channelId": "0"})`, выбор потока `streamId == 0 and status == "1" and hls.startswith("https://")`; `LV1002` или нет подходящего → `bindDeviceLive({"deviceId", "channelId": "0", "streamId": 0})` (`LV1001` игнорируется) → повторный `getLiveStreamInfo`; нет подходящего → `ImouError("no live stream for <device_id>")`.

- [ ] **Step 1: Write the failing tests** (фейковый сервер на `httpx.MockTransport`, словарь `method -> list[response dict]`, записывает тела запросов):

```python
def test_sign_and_body():          # первый запрос — accessToken без token; sign == md5("time:1700000000,nonce:n1,appSecret:SECRET")
                                   # system.appId == "APP", system.ver == "1.0"; следующий запрос несёт params.token == "T1"
async def test_token_reused():     # два list-вызова → accessToken запрошен один раз
async def test_tk1002_relogin_once():   # TK1002, затем успех → accessToken дважды, результат возвращён
async def test_tk1002_twice_raises():   # TK1002 дважды → ImouError, accessToken ровно дважды
@pytest.mark.parametrize("code", ["OP1008", "SN1001", "OP1009"])
async def test_auth_codes(code):        # → ImouAuthError
async def test_http_500_and_bad_json(): # → ImouError
async def test_error_text_has_no_secrets():  # str(exc) не содержит "SECRET" и "T1"
async def test_list_cameras():     # 2 устройства "AB12CD", "ef34": имена из detail, onLine "1" → True, "4" → True, "0" → False
async def test_list_cameras_online_error_is_offline():
async def test_list_cameras_empty(): # deviceList [] → [] и вызывался только accessToken + deviceBaseList
async def test_stream_url_existing(): # streams: [{streamId:1,...https}, {streamId:0,status:"1",hls:"https://h/0.m3u8"}] → "https://h/0.m3u8", bindDeviceLive не вызывался
async def test_stream_url_binds_when_missing():  # LV1002 → bindDeviceLive(streamId=0) → getLiveStreamInfo → url
async def test_stream_url_ignores_http_and_inactive(): # только http:// и status "0" → bind → url
async def test_stream_url_bind_already_exists(): # bind → LV1001, второй getLiveStreamInfo даёт url → url
async def test_stream_url_none_raises():          # после bind подходящего нет → ImouError
```

Каждая заглушка выше — полноценный тест с ассертами, указанными в комментарии.

- [ ] **Step 2: Run** `python3 -m pytest tests/test_imou_client.py -q` — Expected: FAIL (`ModuleNotFoundError: streemcam.imou`).
- [ ] **Step 3: Implement** `models.py` и `client.py` по поведению выше; логгер `logging.getLogger(__name__)` пишет только метод и код.
- [ ] **Step 4: Run** `python3 -m pytest tests/test_imou_client.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: Imou Open API client`

### Task 3: Камеры Imou в каталоге

**Files:**
- Modify: `streemcam/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `ImouCamera` (Task 2).
- Produces: `imou_cam_id(device_id: str) -> str`; `CameraInfo.kind: Literal["config", "tuya", "imou"]`; `Catalog.imou() -> list[CameraInfo]`, `Catalog.set_imou(cameras: list[ImouCamera]) -> None`, `Catalog.mark_imou_offline() -> None`. `all()` = конфиг + Tuya + Imou.

- [ ] **Step 1: Write the failing tests**

```python
def test_set_imou(cfg):
    c = Catalog(cfg)
    c.set_tuya([TuyaCamera("bf1", "Я", True)])
    c.set_imou([ImouCamera("AB12CD", "Улица", True), ImouCamera("bad id", "Х", True)])
    imou = c.get("imou_ab12cd")
    assert (imou.kind, imou.device_id, imou.name, imou.online) == ("imou", "AB12CD", "Улица", True)
    assert [x.kind for x in c.all()][-2:] == ["tuya", "imou"]
    assert len(c.imou()) == 1

def test_mark_imou_offline(cfg):
    c = Catalog(cfg)
    c.set_imou([ImouCamera("AB12CD", "Улица", True)])
    c.mark_imou_offline()
    assert c.get("imou_ab12cd").online is False
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_catalog.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** по образцу `set_tuya` / `mark_tuya_offline` (та же регулярка `^[A-Za-z0-9]+$`, сортировка по имени).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat: Imou cameras in catalog`

### Task 4: Потоки go2rtc для Imou, без записи

**Files:**
- Modify: `streemcam/go2rtc_sync.py`, `streemcam/go2rtc_config.py` (`rec_stream_name`)
- Test: `tests/test_go2rtc_sync.py`, `tests/test_go2rtc_config.py`

**Interfaces:**
- Consumes: `Catalog.imou()` (Task 3).
- Produces: `imou_src(internal_url: str, device_id: str, key: str) -> str` = `f"echo:curl -fsS {internal_url}/imou/{device_id}?key={key}"`; `MANAGED_PREFIXES = ("tuya_", "imou_")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_desired_streams_imou(cfg):
    catalog = Catalog(cfg)
    catalog.set_imou([ImouCamera("AB12CD", "Улица", True)])
    d = desired_streams(catalog, URL, KEY)
    assert d["imou_ab12cd"] == f"echo:curl -fsS {URL}/imou/AB12CD?key={KEY}"
    assert d["imou_ab12cd~h264"] == compat_src("imou_ab12cd")
    assert "imou_ab12cd~live" not in d

async def test_sync_replaces_imou_keeps_tuya_and_others(cfg):
    # текущий конфиг go2rtc: room (xiaomi), tuya_bf1, imou_old; каталог: tuya bf1 + imou AB12CD
    # после sync: room как был, tuya_* как desired, imou_old удалён, imou_ab12cd добавлен

async def test_sync_empty_imou_removes_streams(cfg):
    # каталог без Imou, в go2rtc есть imou_old и imou_old~h264 → оба удалены, room сохранён

def test_rec_stream_name_imou_is_none(cfg):
    assert rec_stream_name(cfg, CameraInfo("imou_ab12cd", "Улица", "imou", device_id="AB12CD")) is None
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_go2rtc_sync.py tests/test_go2rtc_config.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement:** `desired_streams` добавляет Imou-потоки; фильтры `name.startswith("tuya_")` в `_sync_locked` заменить на `name.startswith(MANAGED_PREFIXES)`; в `rec_stream_name` ранний `if cam.kind == "imou": return None`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat: go2rtc streams for Imou cameras`

### Task 5: Внутренний эндпоинт `/imou/{device_id}`

**Files:**
- Modify: `streemcam/web/internal.py`
- Test: `tests/test_web_internal.py`

**Interfaces:**
- Consumes: `Catalog.imou()`; объект `imou` с `async stream_url(device_id) -> str` (Task 6 даёт `ImouService.stream_url`); `ImouError`.
- Produces: `create_internal_app(catalog: Catalog, tuya, internal_key: str | None, imou=None) -> FastAPI` (`imou` — keyword с дефолтом, существующие вызовы не ломаются).

- [ ] **Step 1: Write the failing tests** (`FakeImou.stream_url` возвращает `"https://h/AB12CD.m3u8"` или бросает `ImouError`):

```python
def test_imou_ok(cfg):         # GET /imou/AB12CD?key=KEY → 200, text == "ffmpeg:https://h/AB12CD.m3u8#video=copy#audio=aac"
def test_imou_bad_key(cfg):    # → 403
def test_imou_unknown(cfg):    # /imou/ZZZ → 404
def test_imou_disabled(cfg):   # create_internal_app(catalog, None, KEY) → /imou/AB12CD → 404
def test_imou_cloud_error(cfg):# ImouError → 502
def test_tuya_disabled_imou_enabled(cfg):  # tuya=None: /tuya/bf1 → 404, а не 500
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_web_internal.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** маршрут по образцу `tuya_stream` (`hmac.compare_digest`, тексты `"forbidden"`, `"unknown device"`, `"imou cloud error"`); в `tuya_stream` при `tuya is None` → 404 `"unknown device"`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat: internal Imou stream endpoint`

### Task 6: `ImouService`

**Files:**
- Create: `streemcam/imou/service.py`
- Test: `tests/test_imou_service.py`

**Interfaces:**
- Consumes: `ImouClient` (Task 2; в тестах — фейк с теми же async-методами), `Catalog.set_imou/mark_imou_offline` (Task 3), `sync` с `async sync()`.
- Produces: `FAILURE_ALERT_AFTER = 3`; `ImouService(cfg: Config, catalog: Catalog, client, sync=None, on_alert: Callable[[str], Awaitable[None]] | None = None)`; публичный атрибут `client`; `async refresh() -> bool`; `async stream_url(device_id: str) -> str`; `async run() -> None`; тексты `AUTH_TEXT = "⚠️ Imou: облако отклонило appId/appSecret. Проверьте IMOU_APP_ID и IMOU_APP_SECRET в .env."` и `FAIL_TEXT = "⚠️ Imou: не удаётся получить список камер несколько раз подряд. Проверьте доступ к облаку Imou и imou.api_url."`

- [ ] **Step 1: Write the failing tests**

```python
async def test_refresh_ok():            # каталог заполнен, sync вызван, True
async def test_refresh_error_marks_offline_and_alerts_on_third():
    # успех, затем 3 ImouError: после каждой камеры offline; алерт FAIL_TEXT ровно один раз (на 3-й);
    # 4-я ошибка — алерта нет; затем успех и снова 3 ошибки — алерт ещё раз
async def test_auth_error_alerts_once_until_success():
    # ImouAuthError дважды → один алерт AUTH_TEXT; успех; ImouAuthError → снова алерт
async def test_stream_url_delegates():
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_imou_service.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** по образцу `TuyaService.refresh` и `run` (`imou.refresh_minutes * 60`); отличие от Tuya — `mark_imou_offline()` при каждой ошибке, флаг «auth-алерт уже отправлен», сбрасываемый успехом.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat: Imou camera list service`

### Task 7: Монитор и WS-прокси

**Files:**
- Modify: `streemcam/monitor.py`, `streemcam/web/server.py`
- Test: `tests/test_monitor.py`, `tests/test_web_ws.py`

**Interfaces:**
- Consumes: `CameraInfo.kind == "imou"`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_imou_online_from_cloud_and_snapshot_throttle(make_cfg, alerts):
    # imou.snapshot_minutes=15; online из каталога; кадр запрошен на 1-м проходе, не запрошен через 10 мин,
    # запрошен через 16 мин; офлайн-камера кадр не запрашивает; алертов нет
def test_ws_imou_uses_base_stream(...):   # src=imou_ab12cd → upstream "imou_ab12cd" (не "~live")
def test_ws_imou_compat(...):             # compat=1 → "imou_ab12cd~h264"
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_monitor.py tests/test_web_ws.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement:** `_check_tuya` → `_check_cloud(cam, snapshot_minutes)`; `_check` выбирает `cfg.tuya.snapshot_minutes` или `cfg.imou.snapshot_minutes` по `kind`. В `ws_proxy` ветка `kind == "tuya"` остаётся, Imou идёт в общий `else` — проверить тестом, код менять только если тест падает.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat: monitor and player support for Imou cameras`

### Task 8: Подключение в `app.py` и README

**Files:**
- Modify: `streemcam/app.py`, `README.md`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `ImouClient`, `ImouService` (Tasks 2, 6), `create_internal_app(..., imou=...)` (Task 5).

- [ ] **Step 1: Write the failing tests**

```python
def test_build_imou_disabled(cfg):
    assert build_imou(cfg, Catalog(cfg), sync=None, on_alert=None) is None

def test_build_imou_enabled(make_cfg):
    c = make_cfg(imou={"enabled": True, "app_id": "APP", "app_secret": "SECRET",
                       "api_url": "https://openapi-fk.easy4ip.com/openapi"}, internal_key="k" * 20)
    svc = build_imou(c, Catalog(c), sync=None, on_alert=None)
    assert isinstance(svc, ImouService)
    assert svc.client.api_url == "https://openapi-fk.easy4ip.com/openapi"

def test_needs_internal_api(make_cfg):
    key = {"internal_key": "k" * 20}
    assert needs_internal_api(make_cfg()) is False
    assert needs_internal_api(make_cfg(tuya={"enabled": True}, **key)) is True
    assert needs_internal_api(make_cfg(imou={"enabled": True, "app_id": "a", "app_secret": "s"}, **key)) is True
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_app.py -q` — Expected: FAIL (`ImportError: build_imou`).
- [ ] **Step 3: Implement** в `app.py`: `build_imou(cfg, catalog, sync, on_alert) -> ImouService | None` (клиент `ImouClient(cfg.imou.app_id, cfg.imou.app_secret, cfg.imou.api_url, httpx.AsyncClient(timeout=15))`; `ImouService.client` и `ImouClient.api_url` — публичные атрибуты); `needs_internal_api(cfg) -> bool` = `cfg.tuya.enabled or cfg.imou.enabled`. В `run`: `on_tuya_alert` → `on_admin_alert`; внутренний сервер (`create_internal_app(catalog, tuya, cfg.internal_key, imou=imou)`) при `needs_internal_api`; `supervise("imou", imou.run)` при наличии; `sync_forever` — только если нет ни Tuya, ни Imou.
- [ ] **Step 4: README:** раздел «Камеры Imou» после «Камеры Tuya / Smart Life»: регистрация на open.imoulife.com тем же аккаунтом, что в Imou Life; где взять `appId`/`appSecret`; `.env`; `imou.enabled: true`; региональный `api_url`; выключить шифрование видео, если поток не открывается; только просмотр, без записи; задержка HLS несколько секунд.
- [ ] **Step 5: Run** `python3 -m pytest -q` — Expected: всё PASS.
- [ ] **Step 6: Commit** `feat: wire Imou service, docs`
