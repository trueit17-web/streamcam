import hashlib
import logging
import random
import secrets
import time

import httpx

from .models import ImouAuthError, ImouCamera, ImouError

log = logging.getLogger(__name__)

AUTH_CODES = ("OP1008", "SN1001", "OP1009")
TOKEN_EXPIRED = "TK1002"
LIVE_MISSING = "LV1002"
LIVE_EXISTS = "LV1001"
ONLINE_STATES = ("1", "4")  # 4 — камера спит, но просыпается по запросу потока
PAGE_LIMIT = 50
SHARE_PAGE = 100   # queryRange shareDeviceList: не больше 100 записей за раз
SHARE_MAX_PAGES = 5


class _ApiError(ImouError):
    def __init__(self, method: str, code: str, msg: str):
        super().__init__(f"{method}: {code} {msg}")
        self.code = code


class ImouClient:
    """Клиент Imou Open API (https://open.imoulife.com). Токен доступа хранится только в памяти."""

    def __init__(self, app_id: str, app_secret: str, api_url: str, http: httpx.AsyncClient,
                 clock=time.time, nonce=lambda: secrets.token_hex(16)):
        self.api_url = api_url.rstrip("/")
        self._app_id = app_id
        self._app_secret = app_secret
        self._http = http
        self._clock = clock
        self._nonce = nonce
        self._token: str | None = None

    def _redact(self, text: str) -> str:
        for secret in (self._app_secret, self._token):
            if secret:
                text = text.replace(secret, "***")
        return text

    async def _request(self, method: str, params: dict) -> dict:
        ts = round(self._clock())
        nonce = self._nonce()
        sign = hashlib.md5(f"time:{ts},nonce:{nonce},appSecret:{self._app_secret}".encode()).hexdigest()
        body = {
            "system": {"ver": "1.0", "sign": sign, "appId": self._app_id, "time": ts, "nonce": nonce},
            "params": params,
            "id": str(random.randint(1, 10000)),
        }
        try:
            r = await self._http.post(f"{self.api_url}/{method}", json=body)
        except httpx.HTTPError as e:
            raise ImouError(f"{method}: {type(e).__name__}") from None
        if r.status_code != 200:
            raise ImouError(f"{method}: HTTP {r.status_code}")
        try:
            result = r.json()["result"]
            code = str(result["code"])
        except (ValueError, KeyError, TypeError):
            raise ImouError(f"{method}: malformed response") from None
        if code != "0":
            msg = self._redact(str(result.get("msg", "")))
            if code in AUTH_CODES:
                raise ImouAuthError(f"{method}: {code} {msg}")
            raise _ApiError(method, code, msg)
        data = result.get("data")
        return data if isinstance(data, dict) else {}

    async def _login(self) -> None:
        data = await self._request("accessToken", {})
        token = data.get("accessToken")
        if not token:
            raise ImouError("accessToken: no token in response")
        self._token = token

    async def _call(self, method: str, params: dict) -> dict:
        if self._token is None:
            await self._login()
        try:
            return await self._request(method, {**params, "token": self._token})
        except _ApiError as e:
            if e.code != TOKEN_EXPIRED:
                raise
        log.info("imou access token expired, logging in again")
        self._token = None
        await self._login()
        try:
            return await self._request(method, {**params, "token": self._token})
        except _ApiError as e:
            if e.code == TOKEN_EXPIRED:
                raise ImouError(f"{method}: access token rejected after re-login") from None
            raise

    async def list_cameras(self) -> list[ImouCamera]:
        data = await self._call("deviceBaseList", {"bindId": -1, "limit": PAGE_LIMIT,
                                                    "type": "bindAndShare", "needApInfo": False})
        ids = [d["deviceId"] for d in data.get("deviceList") or [] if d.get("deviceId")]
        if len(ids) >= PAGE_LIMIT:
            log.warning("imou: %d devices returned, only the first %d are supported", len(ids), PAGE_LIMIT)
        cams = []
        if ids:
            detail = await self._call("deviceBaseDetailList",
                                      {"deviceList": [{"deviceId": d, "channelList": "0"} for d in ids]})
            names = {d.get("deviceId"): d.get("name") for d in detail.get("deviceList") or []}
            for device_id in ids:
                cams.append(ImouCamera(device_id, names.get(device_id) or device_id, await self._online(device_id)))
        # камеры, которыми поделились в приложении Imou Life, deviceBaseList не отдаёт
        shared = [c for c in await self._shared_cameras() if c.device_id not in ids]
        log.info("imou: %d own cameras, %d shared from the app", len(cams), len(shared))
        return cams + shared

    async def _shared_cameras(self) -> list[ImouCamera]:
        cams: list[ImouCamera] = []
        for page in range(SHARE_MAX_PAGES):
            start = page * SHARE_PAGE + 1
            try:
                data = await self._call("shareDeviceList", {"queryRange": f"{start}-{start + SHARE_PAGE - 1}"})
            except ImouAuthError:
                raise
            except ImouError as e:
                log.warning("imou shared devices failed: %s", e)
                break
            devices = data.get("deviceList") or []
            for d in devices:
                if d.get("deviceId"):
                    cams.append(ImouCamera(d["deviceId"], d.get("name") or d["deviceId"],
                                           str(d.get("status")) == "1"))
            if len(devices) < SHARE_PAGE:
                break
        return cams

    async def _online(self, device_id: str) -> bool:
        try:
            data = await self._call("deviceOnline", {"deviceId": device_id})
        except ImouAuthError:
            raise
        except ImouError as e:
            log.warning("imou online status for %s failed: %s", device_id, e)
            return False
        return str(data.get("onLine")) in ONLINE_STATES

    async def _live_hls(self, device_id: str) -> str | None:
        try:
            data = await self._call("getLiveStreamInfo", {"deviceId": device_id, "channelId": "0"})
        except _ApiError as e:
            if e.code == LIVE_MISSING:
                return None
            raise
        for s in data.get("streams") or []:
            hls = s.get("hls") or ""
            if s.get("streamId") == 0 and str(s.get("status")) == "1" and hls.startswith("https://"):
                return hls
        return None

    async def stream_url(self, device_id: str) -> str:
        url = await self._live_hls(device_id)
        if url:
            return url
        try:
            await self._call("bindDeviceLive", {"deviceId": device_id, "channelId": "0", "streamId": 0})
        except _ApiError as e:
            if e.code != LIVE_EXISTS:
                raise
        url = await self._live_hls(device_id)
        if not url:
            raise ImouError(f"no live stream for {device_id}")
        return url
