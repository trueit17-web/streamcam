#!/usr/bin/env bash
# Разовая проверка: отдаёт ли камера Imou запись с карты памяти по RTSP (Dahua cam/playback).
# Запускается из remote-status.sh в папке клона: sd-probe.sh "Сч1,Сч2" (имена камер из config.yaml).
# Для каждой камеры поднимается ВТОРОЙ, временный P2P-туннель (живая запись идёт по своему),
# запрашивается 30 с записи за интервал 10 минут назад, файл проверяется ffprobe и удаляется.
# Логи Actions публичны: не печатаем ни пароли, ни адреса, ни серийные номера.
set -uo pipefail

names="${1:?usage: sd-probe.sh <имена камер через запятую>}"

if ! docker compose ps --status running --services | grep -qx dhp2p; then
  echo "!! dhp2p не запущен — проверить нечем"
  exit 0
fi
p2p_cams=$(docker compose exec -T dhp2p printenv DHP2P_CAMERAS 2>/dev/null | tr ',' ' ')
relay=()
[ "$(docker compose exec -T dhp2p printenv DHP2P_RELAY 2>/dev/null | tr -d '\r')" = 1 ] && relay=(--relay)

# id и порт туннеля камер с указанными именами
found=$(docker compose exec -T -e SD_NAMES="$names" streemcam python - <<'PY'
import os, re
from streemcam.config import load_config
cfg = load_config("/app/config.yaml")
LAT = str.maketrans("acekmhoptxyb", "асекмнортхуь")
def norm(s):
    return re.sub(r"[^0-9a-zа-яё]", "", (s or "").casefold().translate(LAT))
want = [n for n in os.environ["SD_NAMES"].split(",") if n.strip()]
for n in want:
    q = norm(n)
    hits = [c for c in cfg.cameras if norm(c.name) == q] or \
           [c for c in cfg.cameras if q and (q in norm(c.name) or q in norm(c.id))]
    if not hits:
        print(f"MISS {n.strip()}")
    for c in hits:
        if getattr(c, "host", None) != "dhp2p":
            print(f"NOTP2P {c.id}")
        else:
            print(f"CAM {c.id} {c.port}")
PY
)

echo "$found" | grep -E "^(MISS|NOTP2P)" | sed 's/^MISS/нет камеры с именем:/; s/^NOTP2P/не через dhp2p:/'
if echo "$found" | grep -q "^MISS"; then
  echo "камеры через dhp2p (id — название — дом):"
  docker compose exec -T streemcam python -c '
from streemcam.config import load_config
for c in load_config("/app/config.yaml").cameras:
    if getattr(c, "host", None) == "dhp2p":
        print(c.id, "—", c.name, "—", c.home or "-")
'
fi
echo "$found" | grep "^CAM " | sed 's/^CAM /проверяю: /'

# Временный туннель к порту камеры REMOTE на локальном порту PP; ждём «Ready to connect» (до 2 попыток).
tunnel_up() {
  local pp=$1 remote=$2 serial=$3 try i
  for try in 1 2; do
    tunnel_down "$pp"
    docker compose exec -d dhp2p bash -c \
      "timeout 300 dh-p2p ${relay[*]} -p 0.0.0.0:${pp}:${remote} ${serial} > /tmp/sdprobe.${pp}.log 2>&1"
    for i in $(seq 1 35); do
      sleep 2
      if docker compose exec -T dhp2p grep -q "Ready to connect" "/tmp/sdprobe.${pp}.log" 2>/dev/null; then
        echo "туннель к порту $remote готов (попытка $try, ~$((i * 2)) с)"
        return 0
      fi
    done
    echo "туннель к порту $remote не поднялся за 70 с (попытка $try)"
  done
  return 1
}
tunnel_log() {
  docker compose exec -T dhp2p bash -c "cat /tmp/sdprobe.$1.log 2>/dev/null" \
    | grep -E "established|Not Found|Error response|Timeout|panicked|closed|authentication" \
    | grep -v "PTCP session established" \
    | sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}(:[0-9]+)?/IP/g' | tail -n 6 | sed 's/^/  туннель: /'
}
tunnel_down() {
  docker compose exec -T dhp2p bash -c 'for p in /proc/[0-9]*; do grep -aq "0.0.0.0:'"$1"':" "$p/cmdline" 2>/dev/null && [ "${p#/proc/}" != "$$" ] && kill "${p#/proc/}"; done; rm -f /tmp/sdprobe.'"$1"'.log' >/dev/null 2>&1
  sleep 1
}
probe() {  # probe <режим> <cam_id> <pp>
  docker compose exec -T -e MODE="$1" -e CAM_ID="$2" -e PP="$3" streemcam python - <<'PY'
import base64, hashlib, json, os, re, socket, subprocess, time, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from streemcam.config import load_config
cfg = load_config("/app/config.yaml")
cam = cfg.camera(os.environ["CAM_ID"])
user, pw, pp, mode = cam.user, cam.password, int(os.environ["PP"]), os.environ["MODE"]
tz = ZoneInfo(cfg.recording.timezone)
start = datetime.now(tz) - timedelta(minutes=15)
end = start + timedelta(seconds=40)
fmt = "%Y_%m_%d_%H_%M_%S"
play_path = f"/cam/playback?channel={cam.channel}&starttime={start.strftime(fmt)}&endtime={end.strftime(fmt)}"
live_path = f"/cam/realmonitor?channel={cam.channel}&subtype=1"

def clean(s):
    return s.replace(pw, "***")

def digest(method, uri, hdr):
    realm = re.search(r'realm="([^"]*)"', hdr).group(1)
    nonce = re.search(r'nonce="([^"]*)"', hdr).group(1)
    h = lambda x: hashlib.md5(x.encode()).hexdigest()
    resp = h(f"{h(f'{user}:{realm}:{pw}')}:{nonce}:{h(f'{method}:{uri}')}")
    return f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{resp}"'

def rtsp_describe_all():
    s = socket.create_connection(("dhp2p", pp), timeout=20)
    f = s.makefile("rb")
    cseq = 0
    auth_hdr = None
    def req(path, auth=None):
        nonlocal cseq
        cseq += 1
        uri = f"rtsp://dhp2p:{pp}{path}"
        lines = [f"DESCRIBE {uri} RTSP/1.0", f"CSeq: {cseq}", "Accept: application/sdp", "User-Agent: sdprobe"]
        if auth:
            lines.append("Authorization: " + digest("DESCRIBE", uri, auth))
        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        status = f.readline().decode(errors="replace").strip()
        if not status:
            return "соединение закрыто камерой", {}, ""
        hdrs = {}
        while True:
            l = f.readline().decode(errors="replace").rstrip("\r\n")
            if not l:
                break
            k, _, v = l.partition(":")
            hdrs[k.strip().lower()] = v.strip()
        body = f.read(int(hdrs.get("content-length", 0) or 0)).decode(errors="replace")
        return status, hdrs, body
    for name, path in (("живой поток", live_path), ("запись с карты", play_path)):
        try:
            st, h, body = req(path, auth_hdr)
            if " 401 " in st + " " and "www-authenticate" in h:
                auth_hdr = h["www-authenticate"]
                st, h, body = req(path, auth_hdr)
            media = [l for l in body.splitlines() if l.startswith(("m=", "a=rtpmap", "a=range", "a=control:"))][:6]
            print(f"  RTSP DESCRIBE {name}: {st}" + (f" | {'; '.join(media)}" if media else ""))
        except Exception as e:
            print(f"  RTSP DESCRIBE {name}: ошибка {type(e).__name__}: {clean(str(e))}")
            return
    s.close()

def ffmpeg_play():
    url = f"rtsp://{user}:{pw}@dhp2p:{pp}{play_path}"
    out = f"/tmp/sdprobe-{pp}.mp4"
    t0 = time.monotonic()
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-rtsp_transport", "tcp",
                        "-timeout", "20000000", "-i", url, "-t", "30", "-map", "0", "-c", "copy", "-f", "mp4", out],
                       capture_output=True, text=True, timeout=150)
    print(f"  ffmpeg: код {r.returncode}, {time.monotonic() - t0:.0f} с")
    for l in clean(r.stderr).replace(url, "URL").splitlines()[-6:]:
        print("   ", l)
    if os.path.exists(out):
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", out],
                           capture_output=True, text=True)
        try:
            info = json.loads(p.stdout)
            streams = [f"{x.get('codec_type')}:{x.get('codec_name')} {x.get('width','')}x{x.get('height','')}"
                       for x in info.get("streams", [])]
            print(f"  файл: {os.path.getsize(out) // 1024} КБ, {info.get('format', {}).get('duration')} с, {', '.join(streams)}")
        except ValueError:
            print("  файл не читается")
        os.remove(out)

def http_probe():
    base = f"http://dhp2p:{pp}"
    pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pm.add_password(None, base, user, pw)
    op = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(pm), urllib.request.HTTPBasicAuthHandler(pm))
    for path in ("/cgi-bin/magicBox.cgi?action=getDeviceType",
                 "/cgi-bin/mediaFileFind.cgi?action=factory.create"):
        try:
            r = op.open(base + path, timeout=20)
            print(f"  HTTP {path.split('?')[0]}: {r.status} {clean(r.read(200).decode(errors='replace')).strip()!r}")
        except urllib.error.HTTPError as e:
            print(f"  HTTP {path.split('?')[0]}: {e.code}")
        except Exception as e:
            print(f"  HTTP {path.split('?')[0]}: ошибка {type(e).__name__}: {clean(str(e))[:150]}")
            return

print(f"  интервал записи (время камеры): {start.strftime('%H:%M:%S')}–{end.strftime('%H:%M:%S')}")
{"rtsp": rtsp_describe_all, "play": ffmpeg_play, "http": http_probe}[mode]()
PY
}

while read -r _ cam_id port <&3; do
  serial=""
  for item in $p2p_cams; do
    [ "${item##*:}" = "$port" ] && serial="${item%%:*}"
  done
  if [ -z "$serial" ]; then echo "$cam_id: порт $port не найден в DHP2P_CAMERAS"; continue; fi
  pp=$((port + 300))
  echo "== sd: $cam_id — RTSP-запросы (живой поток и запись с карты)"
  if tunnel_up "$pp" 554 "$serial"; then probe rtsp "$cam_id" "$pp"; fi
  tunnel_log "$pp"
  echo "== sd: $cam_id — скачивание 30 с записи с карты (ffmpeg)"
  if tunnel_up "$pp" 554 "$serial"; then probe play "$cam_id" "$pp"; fi
  tunnel_log "$pp"
  echo "== sd: $cam_id — HTTP API камеры (порт 80)"
  if tunnel_up "$pp" 80 "$serial"; then probe http "$cam_id" "$pp"; fi
  tunnel_log "$pp"
  tunnel_down "$pp"
done 3< <(echo "$found" | grep "^CAM ")
