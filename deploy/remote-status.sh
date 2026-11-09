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

echo "== сайт: /web $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/web), бот для входа: $(curl -s http://127.0.0.1:8080/api/web/config)"
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
print("часовой пояс записи: %s" % cfg.recording.timezone)
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
# P2P-туннели к камерам Imou (dh-p2p): лог уже без адресов, плюс проверка, что RTSP камеры отвечает
if docker compose ps --status running --services | grep -qx dhp2p; then
  echo "== p2p: последние строки"
  docker compose logs --since "${hours}h" --no-color dhp2p 2>&1 | grep -v " DBG" | tail -n 80 | redact
  # RTSP-запрос сюда не шлём: каждое новое подключение перезапускает P2P-сессию и рвёт запись.
  # Вместо этого — кадр go2rtc по камерам, чей источник идёт через dhp2p (имена потоков, без адресов)
  echo "== p2p: кадры камер через dhp2p (go2rtc /api/frame.jpeg)"
  for src in $(docker compose exec -T streemcam python -c '
import json, urllib.request
streams = json.load(urllib.request.urlopen("http://go2rtc:1984/api/streams", timeout=10))
for name, st in streams.items():
    if any("@dhp2p:" in (p.get("url") or "") for p in (st.get("producers") or [])):
        print(name)' </dev/null 2>/dev/null | tr -d '\r'); do
    echo "$src: $(curl -sS -m 25 -o /dev/null -w "%{http_code} %{size_download}" "http://127.0.0.1:1984/api/frame.jpeg?src=$src" </dev/null 2>&1)"
  done
  echo "== p2p: запись (последний файл: размер, сколько секунд назад менялся)"
  now=$(date +%s)
  for d in recordings/p2p_*/; do
    [ -d "$d" ] || continue
    f=$(find "$d" -type f -name '*.mp4' -printf '%T@ %s\n' 2>/dev/null | sort -n | tail -n 1)
    if [ -z "$f" ]; then echo "$(basename "$d"): файлов нет"; continue; fi
    echo "$(basename "$d"): $(echo "$f" | awk -v now="$now" '{ printf "%d КБ, %d с назад", $2/1024, now-$1 }')"
  done
fi
# WireGuard: сколько секунд назад каждый дом выходил на связь (без ключей и адресов — лог публичный)
if docker compose ps --status running --services | grep -qx wireguard; then
  echo "== wireguard: последнее рукопожатие по домам"
  now=$(date +%s)
  docker compose exec -T wireguard wg show wg0 latest-handshakes </dev/null 2>&1 \
    | awk -v now="$now" '{ if ($2 == 0) print "дом " NR ": ещё не подключался"; else print "дом " NR ": " now - $2 " с назад" }'
  docker compose logs --since "${hours}h" --no-color wireguard 2>&1 | grep -iE "error|fail|warn" | tail -n 10 | redact
  wgx() { docker compose exec -T wireguard "$@" </dev/null 2>/dev/null; }
  ifaces=$(wgx wg show interfaces | tr -d '\r')
  echo "== wireguard: туннели: $ifaces"
  for ifc in $ifaces; do
    [ "$ifc" = wg0 ] && continue
    now=$(date +%s)
    wgx wg show "$ifc" latest-handshakes | awk -v now="$now" -v i="$ifc" '{ if ($2 == 0) print i ": рукопожатия ещё не было"; else print i ": рукопожатие " now - $2 " с назад" }'
  done
  # Доступность домашних сетей через туннели: печатаем только последний октет найденных устройств
  echo "== wireguard: устройства в домашних сетях (порты 554 RTSP / 37777 Dahua / 80)"
  for ifc in $ifaces; do
    for net in $(wgx wg show "$ifc" allowed-ips | tr ' \t' '\n\n' | grep -E '^[0-9.]+/[0-9]+$' | grep -v '^10\.13\.13\.' | grep -v '^0\.0\.0\.0'); do
      base=${net%.*}
      dev=$(ip route get "${base}.1" 2>/dev/null | grep -o 'dev [^ ]*' | cut -d' ' -f2)
      if [ "$dev" != "$ifc" ]; then
        echo "$ifc сеть: маршрут идёт через ${dev:-нет маршрута}"; continue
      fi
      hits=$(
        for i in $(seq 1 254); do
          for port in 554 37777 80; do
            ( timeout 0.5 bash -c "</dev/tcp/${base}.${i}/${port}" 2>/dev/null && echo ".${i}:${port}" ) </dev/null &
          done
          if (( i % 32 == 0 )); then wait; fi
        done
        wait
      )
      echo "$ifc сеть: ${hits:-ничего не отвечает}" | tr '\n' ' '; echo
    done
  done
  docker compose logs --tail 60 --no-color wireguard 2>&1 | grep -iE "wg_confs|tunnel|dom1|error|fail|cannot|RTNETLINK" | tail -n 15 | redact
fi
# Клиентские туннели к домам: только безопасные строки (без ключей и адреса Endpoint)
for f in wireguard/wg_confs/*.conf; do
  [ -f "$f" ] || continue
  [ "$(basename "$f")" = wg0.conf ] && continue
  echo "== файл $(basename "$f")"
  grep -iE '^\s*(Address|AllowedIPs|DNS|Table|PostUp|PostDown|PersistentKeepalive|MTU)\s*=' "$f" | sed 's/\r$//'
  grep -iqE '^\s*Endpoint\s*=' "$f" && echo "Endpoint задан"
done
echo "== маршрут по умолчанию идёт через: $(ip route show default | grep -o 'dev [^ ]*' | head -1)"
echo "== маршруты сервера (публичные адреса скрыты)"
maskpub() { sed -E 's/\b([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\b/&@/g' | perl -pe 's/(\d+)\.(\d+)\.\d+\.\d+@/($1==10||$1==127||($1==192&&$2==168)||($1==172&&$2>=16&&$2<=31)||($1==100&&$2>=64&&$2<=127)||$1==0||$1==255)?$&:"PUB"/ge; s/@//g'; }
ip -4 route | maskpub
ip -4 -o addr | awk '{print $2, $4}' | maskpub
echo "== DNS сервера"
grep -E '^nameserver' /etc/resolv.conf | maskpub
command -v resolvectl >/dev/null && resolvectl dns 2>/dev/null | maskpub
echo "== sshd слушает"
ss -tln | awk '$4 ~ /:22$/ {print $4}' | maskpub
echo "== imou: строки лога за ${hours} ч"
docker compose logs --since "${hours}h" --no-color streemcam 2>&1 | grep -i "imou" | grep -v "HTTP Request" | tail -n 40 | redact
echo "== go2rtc: последние строки"
docker compose logs --tail 40 --no-color go2rtc | redact

echo "== go2rtc-config (генерация go2rtc.yaml из config.yaml)"
docker compose logs --tail 10 --no-color go2rtc-config

echo "== последние строки streemcam"
docker compose logs --tail 30 --no-color streemcam

exit "$failed"
