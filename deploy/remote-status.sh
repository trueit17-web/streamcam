#!/usr/bin/env bash
# Состояние прода. Запускается на сервере: ssh ... 'bash -s -- <путь к клону> [часы логов]' < deploy/remote-status.sh
set -uo pipefail

cd "${1:?usage: remote-status.sh <path to streamcam checkout> [log hours]}" || exit 1

# клон может принадлежать другому пользователю (например, root): без этого git отказывается работать
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$PWD"

hours="${2:-1}"
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
  | grep -Ei "warn|error|exception|traceback|fail" | tail -n 100 || echo "(нет)"

# логи Actions публичны: от ссылок оставляем только схему и хост
redact() { sed -E 's#((https?|rtsps?)://[^/ "]+)[^ "]*#\1/***#g; s/(key|token|liveToken)=[^&" ]*/\1=***/g'; }

echo "== кадры облачных камер (go2rtc /api/frame.jpeg)"
for src in $(curl -fsS http://127.0.0.1:1984/api/streams </dev/null 2>/dev/null \
             | grep -oE '"(imou|tuya)_[a-z0-9]+"' | tr -d '"' | sort -u); do
  res=$(curl -sS -m 25 -o /tmp/streemcam-frame.$$ -w "%{http_code} %{size_download}" \
        "http://127.0.0.1:1984/api/frame.jpeg?src=$src" </dev/null 2>&1)
  echo "$src: $res"
  case "$res" in 200*) continue ;; esac
  head -c 400 /tmp/streemcam-frame.$$ | redact; echo
  case "$src" in imou_*) ;; *) continue ;; esac
  # что именно не нравится ffmpeg во входном потоке
  cmd=$(curl -fsS "http://127.0.0.1:1984/api/streams?src=$src" </dev/null 2>/dev/null \
        | grep -oE 'echo:curl [^"]*' | head -1 | sed 's/^echo://; s/\\u0026/\&/g')
  [ -n "$cmd" ] || continue
  docker compose exec -T go2rtc sh -c "u=\$($cmd); u=\${u#ffmpeg:}; u=\${u%%#*}; \
    echo \"источник: \${u%%://*}, \$(echo \"\$u\" | sed -E 's#^[a-z]+://[^/]+##; s#\\?.*##; s#.*\\.##')\"; \
    echo '-- ffmpeg -i (как есть):'; timeout 20 ffmpeg -hide_banner -v info -i \"\$u\" -frames:v 1 -f null - >/tmp/ff.log 2>&1; \
    c=\$?; grep -vE '^ +(Metadata|[a-z_]+ +:)' /tmp/ff.log | tail -n 20; echo \"код \$c\"; \
    echo '-- как go2rtc (nobuffer, copy + aac):'; timeout 20 ffmpeg -hide_banner -v warning -fflags nobuffer -flags low_delay -i \"\$u\" -c:v copy -c:a aac -t 3 -f null - >/tmp/ff.log 2>&1; \
    c=\$?; tail -n 15 /tmp/ff.log; echo \"код \$c\"; rm -f /tmp/ff.log" </dev/null 2>&1 | redact
done
rm -f /tmp/streemcam-frame.$$
echo "== go2rtc: последние строки"
docker compose logs --tail 40 --no-color go2rtc | redact

echo "== go2rtc-config (генерация go2rtc.yaml из config.yaml)"
docker compose logs --tail 10 --no-color go2rtc-config

echo "== последние строки streemcam"
docker compose logs --tail 30 --no-color streemcam

exit "$failed"
