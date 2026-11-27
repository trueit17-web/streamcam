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

while read -r _ cam_id port <&3; do
  serial=""
  for item in $p2p_cams; do
    [ "${item##*:}" = "$port" ] && serial="${item%%:*}"
  done
  if [ -z "$serial" ]; then echo "$cam_id: порт $port не найден в DHP2P_CAMERAS"; continue; fi
  pp=$((port + 300))
  for variant in main sub; do
    echo "== sd: $cam_id, вариант $variant (временный туннель на порту $pp)"
    docker compose exec -d dhp2p bash -c \
      "timeout 200 dh-p2p ${relay[*]} -p 0.0.0.0:${pp}:554 ${serial} > /tmp/sdprobe.${pp}.log 2>&1"
    docker compose exec -T -e CAM_ID="$cam_id" -e PP="$pp" -e VARIANT="$variant" streemcam python - <<'PY'
import os, subprocess, time, json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from streemcam.config import load_config
cfg = load_config("/app/config.yaml")
cam = cfg.camera(os.environ["CAM_ID"])
pw = cam.password
tz = ZoneInfo(cfg.recording.timezone)
start = datetime.now(tz) - timedelta(minutes=10)
end = start + timedelta(seconds=40)
fmt = "%Y_%m_%d_%H_%M_%S"
q = f"channel={cam.channel}&starttime={start.strftime(fmt)}&endtime={end.strftime(fmt)}"
if os.environ["VARIANT"] == "sub":
    q += "&subtype=1"
url = f"rtsp://{cam.user}:{pw}@dhp2p:{os.environ['PP']}/cam/playback?{q}"
out = f"/tmp/sdprobe-{os.environ['PP']}.mp4"

def clean(s: str) -> str:
    s = s.replace(pw, "***").replace(url, "URL")
    return "\n".join(l for l in s.splitlines() if "dhp2p:" not in l)[-1500:]

print(f"интервал (время камеры): {start.strftime('%H:%M:%S')}–{end.strftime('%H:%M:%S')}")
deadline = time.monotonic() + 150
attempt = 0
while True:
    attempt += 1
    t0 = time.monotonic()
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
                        "-rtsp_transport", "tcp", "-timeout", "20000000", "-i", url,
                        "-t", "30", "-map", "0", "-c", "copy", "-f", "mp4", out],
                       capture_output=True, text=True, timeout=120)
    took = time.monotonic() - t0
    refused = "Connection refused" in r.stderr
    if refused and time.monotonic() < deadline:
        time.sleep(3)
        continue
    break
print(f"попыток подключения: {attempt}, ffmpeg код {r.returncode}, длилось {took:.0f} с")
if r.stderr.strip():
    print("ffmpeg:\n" + clean(r.stderr))
if os.path.exists(out):
    size = os.path.getsize(out)
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,codec_name,width,height",
                        "-of", "json", out], capture_output=True, text=True)
    try:
        info = json.loads(p.stdout)
        streams = [f"{s.get('codec_type')}:{s.get('codec_name')} {s.get('width','')}x{s.get('height','')}"
                   for s in info.get("streams", [])]
        dur = info.get("format", {}).get("duration")
    except ValueError:
        streams, dur = [], None
    print(f"файл: {size // 1024} КБ, длительность {dur} с, потоки: {', '.join(streams) or 'нет'}")
    os.remove(out)
else:
    print("файл не создан")
PY
    echo "туннель:"
    docker compose exec -T dhp2p bash -c "cat /tmp/sdprobe.${pp}.log 2>/dev/null" \
      | grep -E "established|Ready to connect|Not Found|Error response|Timeout|panicked|closed|authentication" \
      | sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}(:[0-9]+)?/IP/g' | tail -n 8
    docker compose exec -T dhp2p bash -c 'for p in /proc/[0-9]*; do grep -aq "0.0.0.0:'"${pp}"':554" "$p/cmdline" 2>/dev/null && [ "${p#/proc/}" != "$$" ] && kill "${p#/proc/}"; done; rm -f /tmp/sdprobe.'"${pp}"'.log' >/dev/null 2>&1
    sleep 2
  done
done 3< <(echo "$found" | grep "^CAM ")
