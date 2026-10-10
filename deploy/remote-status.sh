#!/usr/bin/env bash
# Состояние прода. Запускается на сервере: ssh ... 'bash -s -- <путь к клону> [часы логов]' < deploy/remote-status.sh
set -uo pipefail

cd "${1:?usage: remote-status.sh <path to streamcam checkout> [log hours]}" || exit 1
hours="${2:-1}"
failed=0

echo "== версия: $(git rev-parse --short HEAD) ($(git log -1 --format=%cd --date=iso))"
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

echo "== последние строки streemcam"
docker compose logs --tail 30 --no-color streemcam

exit "$failed"
