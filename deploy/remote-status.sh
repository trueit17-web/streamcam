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

echo "== go2rtc-config (генерация go2rtc.yaml из config.yaml)"
docker compose logs --tail 10 --no-color go2rtc-config

echo "== последние строки streemcam"
docker compose logs --tail 30 --no-color streemcam

exit "$failed"
