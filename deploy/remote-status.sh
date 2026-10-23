#!/usr/bin/env bash
# Состояние прода. Запускается на сервере: ssh ... 'bash -s -- <путь к клону> [часы логов]' < deploy/remote-status.sh
set -uo pipefail

cd "${1:?usage: remote-status.sh <path to streamcam checkout> [log hours]}" || exit 1

# клон может принадлежать другому пользователю (например, root): без этого git отказывается работать
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$PWD"

hours="${2:-1}"

# логи Actions публичны: от ссылок оставляем только схему и хост
redact() { sed -E 's#((https?|rtsps?)://[^/ "]+)[^ "]*#\1/***#g; s/(key|token|liveToken)=[^&" ]*/\1=***/g'; }
failed=0

echo "== версия: $(git rev-parse --short HEAD) ($(git log -1 --format=%cd --date=iso))"
echo "== владелец клона: $(stat -c %U .git), ssh-пользователь: $(id -un)"
echo "== контейнеры"
docker compose ps
for svc in go2rtc streemcam; do
  if ! docker compose ps --status running --services | grep -qx "$svc"; then
    echo "!! $svc не запущен" >&2
    failed=1
  fi
done

echo "== http://127.0.0.1:8080/"
if curl -fsS -o /dev/null -w "%{http_code} за %{time_total} с\n" http://127.0.0.1:8080/; then :; else
  echo "!! streemcam не отвечает" >&2
  failed=1
fi

echo "== диск и память"
df -h . recordings 2>/dev/null | uniq
free -m

echo "== предупреждения и ошибки за ${hours} ч"
docker compose logs --since "${hours}h" --no-color streemcam go2rtc 2>&1 \
  | grep -Ei "warn|error|exception|traceback|fail" \
  | grep -Ev "decode_slice_header|Error submitting packet to decoder|producer.go:170 > error=EOF" | tail -n 100 | redact

# Imou отдаёт H.265, кадр go2rtc режет только из H.264-копии (~h264): её и проверяем
echo "== кадры облачных камер (go2rtc /api/frame.jpeg)"
for src in $(curl -fsS http://127.0.0.1:1984/api/streams </dev/null 2>/dev/null \
             | grep -oE '"(tuya_[a-z0-9]+|imou_[a-z0-9]+~h264)"' | tr -d '"' | sort -u); do
  res=$(curl -sS -m 25 -o /tmp/streemcam-frame.$$ -w "%{http_code} %{size_download}" \
        "http://127.0.0.1:1984/api/frame.jpeg?src=$src" </dev/null 2>&1)
  echo "$src: $res"
  case "$res" in 200*) continue ;; esac
  head -c 400 /tmp/streemcam-frame.$$ | redact; echo
done
rm -f /tmp/streemcam-frame.$$
# названия домов и камер в публичный лог не пишем — только количества
echo "== группы «дом» в списке камер"
docker compose exec -T streemcam python -c '
import collections, json, time, urllib.request
from streemcam.config import load_config
from streemcam.tokens import sign
cfg = load_config("/app/config.yaml")
admin = cfg.admins.telegram[0] if cfg.admins.telegram else None
if admin is None:
    raise SystemExit("нет admins.telegram — пропускаю")
token = sign({"typ": "session", "p": "tg", "u": admin, "exp": time.time() + 60}, cfg.secret)
req = urllib.request.Request("http://127.0.0.1:%d/api/cameras" % cfg.listen_port,
                             headers={"Authorization": "Bearer " + token})
cams = json.load(urllib.request.urlopen(req, timeout=10))["cameras"]
groups = collections.Counter(c["home"] for c in cams)
print("камер: %d, групп с домом: %d, без дома: %d" % (len(cams), len([h for h in groups if h]), groups.get(None, 0)))
for i, (home, n) in enumerate(sorted(((h, n) for h, n in groups.items() if h), key=lambda x: x[0]), 1):
    print("  дом %d: %d камер" % (i, n))
ids = {c["id"] for c in cams}
names = {c["name"] for c in cams}
unmatched = [k for k in cfg.homes if k not in ids and k not in names]
print("homes: записей %d, не совпали ни с id, ни с названием камеры: %d" % (len(cfg.homes), len(unmatched)))
print("imou.home задан: %s, аккаунтов imou.accounts с home: %d" % (bool(cfg.imou.home), sum(1 for a in cfg.imou.accounts if a.home)))
' </dev/null 2>&1 | tail -n 20 | redact
# Imou: что внутренний API отвечает go2rtc по каждой камере (без ссылок)
echo "== imou: ответ внутреннего API по камерам"
docker compose exec -T streemcam python -c '
import re, httpx
from streemcam.config import load_config
cfg = load_config("/app/config.yaml")
streams = httpx.get(cfg.go2rtc_url + "/api/streams", timeout=10).json()
for name in sorted(n for n in streams if re.fullmatch(r"imou_[a-z0-9]+", n)):
    r = httpx.get("%s/imou/%s" % (cfg.internal_url, name[5:].upper()), params={"key": cfg.internal_key}, timeout=40)
    body = "ok" if r.status_code == 200 else r.text[:80]
    print(name, r.status_code, body)
' </dev/null 2>&1 | tail -n 40 | redact
# Imou: статус камер, привязки live и содержимое HLS-плейлистов (без ссылок)
echo "== imou: облако по камерам"
docker compose exec -T streemcam python - 2>&1 <<'PY' | tail -n 120 | redact
# Диагностика Imou для status.yml: что облако отдаёт по каждой камере. Ссылки и токены не печатаются.
import asyncio
import re
from urllib.parse import urljoin, urlsplit

import httpx

from streemcam.config import load_config
from streemcam.imou.client import ImouClient


def host(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.hostname}:{u.port or ''}"


async def playlist(http: httpx.AsyncClient, url: str, depth: int = 0) -> str:
    try:
        r = await http.get(url, timeout=15, follow_redirects=True)
    except httpx.HTTPError as e:
        return f"ошибка {type(e).__name__}"
    url = str(r.url)
    if r.status_code != 200:
        return f"HTTP {r.status_code} {host(url)}"
    lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]
    uris = [ln for ln in lines if not ln.startswith("#")]
    tags = sorted({ln.split(":", 1)[0] for ln in lines if ln.startswith("#")})
    info = [f"{k}={v}" for ln in lines for k, v in [ln[1:].split(":", 1) if ":" in ln else (ln[1:], "")]
            if k in ("EXT-X-TARGETDURATION", "EXT-X-MEDIA-SEQUENCE", "EXT-X-PLAYLIST-TYPE")]
    durs = [ln.split(":", 1)[1].rstrip(",") for ln in lines if ln.startswith("#EXTINF")]
    out = f"{host(url)} строк {len(lines)}, ссылок {len(uris)}, {' '.join(info)}, EXTINF {durs[:6]}, ENDLIST {'#EXT-X-ENDLIST' in tags}"
    if "#EXT-X-STREAM-INF" in tags and uris and depth == 0:
        return out + " | вложенный: " + await playlist(http, urljoin(url, uris[0]), 1)
    if uris:
        seg = urljoin(url, uris[-1])
        try:
            s = await http.get(seg, timeout=15, follow_redirects=True)
            out += f" | последний сегмент: HTTP {s.status_code}, {len(s.content)} байт, .{seg.rsplit('.', 1)[-1][:5]}"
        except httpx.HTTPError as e:
            out += f" | сегмент: {type(e).__name__}"
    return out


async def main() -> None:
    cfg = load_config("/app/config.yaml")
    async with httpx.AsyncClient(timeout=20) as http:
        accounts = [("основной", cfg.imou.app_id, cfg.imou.app_secret, cfg.imou.api_url)]
        accounts += [(f"доп.{i}", a.app_id, a.app_secret, a.api_url)
                     for i, a in enumerate(cfg.imou.accounts, 1) if a.app_id and a.app_secret]
        for label, app_id, secret, api in accounts:
            client = ImouClient(app_id, secret, api, http)
            cams = await client.list_cameras()
            print(f"## аккаунт {label}: камер {len(cams)}")
            for cam in cams[:2] if label == "основной" else cams:
                dev = cam.device_id
                try:
                    online = (await client._call("deviceOnline", {"deviceId": dev})).get("onLine")
                except Exception as e:  # noqa: BLE001
                    online = f"ошибка {e}"
                print(f"-- {dev} online={online}")
                try:
                    data = await client._call("getLiveStreamInfo", {"deviceId": dev, "channelId": "0"})
                except Exception as e:  # noqa: BLE001
                    print(f"   getLiveStreamInfo: {client._redact(str(e))}")
                    continue
                for s in data.get("streams") or []:
                    hls = s.get("hls") or ""
                    if not hls.startswith("https://"):
                        continue
                    print(f"   streamId={s.get('streamId')} status={s.get('status')} {host(hls) if hls else '-'}")
                    if hls.startswith("https://"):
                        print("     " + await playlist(http, hls))


asyncio.run(main())
PY
echo "== imou: строки лога за ${hours} ч"
docker compose logs --since "${hours}h" --no-color streemcam 2>&1 | grep -i "imou" | grep -v "HTTP Request" | tail -n 40 | redact
echo "== go2rtc: последние строки"
docker compose logs --tail 40 --no-color go2rtc | redact

echo "== go2rtc-config (генерация go2rtc.yaml из config.yaml)"
docker compose logs --tail 10 --no-color go2rtc-config

echo "== последние строки streemcam"
docker compose logs --tail 30 --no-color streemcam

exit "$failed"
