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
  | grep -Ei "warn|error|exception|traceback|fail" | tail -n 100 | redact

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
# Imou: что отдаёт облако (только теги HLS, без ссылок) и насколько ровно go2rtc отдаёт поток
echo "== imou: HLS и равномерность потока"
docker compose exec -T streemcam python -c '
import re, time, httpx
from streemcam.config import load_config
cfg = load_config("/app/config.yaml")
streams = httpx.get(cfg.go2rtc_url + "/api/streams", timeout=10).json()
ids = sorted(n for n in streams if re.fullmatch(r"imou_[a-z0-9]+", n))
tested = 0
for name in ids:
    if tested >= 2:
        break
    r = httpx.get("%s/imou/%s" % (cfg.internal_url, name[5:].upper()), params={"key": cfg.internal_key}, timeout=30)
    if r.status_code != 200:
        print(name, "internal:", r.status_code); continue
    url = r.text.split(":", 1)[1].split("#", 1)[0]
    tested += 1
    print("--", name)
    try:
        pl = httpx.get(url, timeout=15, follow_redirects=True).text
        tags = [l for l in pl.splitlines() if l.startswith("#EXT")]
        if any(l.startswith("#EXT-X-STREAM-INF") for l in tags):
            print("  master:", [l for l in tags if l.startswith("#EXT-X-STREAM-INF")][:3])
            sub = next(l for l in pl.splitlines() if l and not l.startswith("#"))
            pl = httpx.get(httpx.URL(url).join(sub), timeout=15, follow_redirects=True).text
            tags = [l for l in pl.splitlines() if l.startswith("#EXT")]
        print("  media:", [l for l in tags if not l.startswith("#EXTINF")][:6])
        print("  EXTINF:", [l[8:].rstrip(",") for l in tags if l.startswith("#EXTINF")])
    except Exception as e:
        print("  playlist error:", type(e).__name__)
    per_sec = [0] * 40
    t0 = time.monotonic(); first = None
    try:
        with httpx.stream("GET", cfg.go2rtc_url + "/api/stream.mp4", params={"src": name}, timeout=40) as s:
            for chunk in s.iter_bytes():
                t = time.monotonic() - t0
                if t >= 40: break
                if first is None: first = t
                per_sec[int(t)] += len(chunk)
    except Exception as e:
        print("  stream error:", type(e).__name__)
    print("  первый байт через %.1f с" % (first or -1))
    print("  КБ по секундам:", [round(b / 1024) for b in per_sec])
' </dev/null 2>&1 | tail -n 40 | redact
echo "== go2rtc: последние строки"
docker compose logs --tail 40 --no-color go2rtc | redact

echo "== go2rtc-config (генерация go2rtc.yaml из config.yaml)"
docker compose logs --tail 10 --no-color go2rtc-config

echo "== последние строки streemcam"
docker compose logs --tail 30 --no-color streemcam

exit "$failed"
