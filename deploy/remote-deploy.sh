#!/usr/bin/env bash
# Обновление прода. Запускается на сервере: ssh ... 'bash -s -- <путь к клону>' < deploy/remote-deploy.sh
set -euo pipefail

cd "${1:?usage: remote-deploy.sh <path to streamcam checkout>}"

# клон может принадлежать другому пользователю (например, root): без этого git отказывается работать
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$PWD"

echo "== было: $(git rev-parse --short HEAD)"
git fetch --quiet origin main
git merge --ff-only origin/main
echo "== стало: $(git rev-parse --short HEAD)"

docker compose build go2rtc-config streemcam
# сначала проверяем config.yaml и .env, не трогая работающие контейнеры
if ! docker compose run --rm --no-deps go2rtc-config \
    python -m streemcam render-go2rtc --out /tmp/go2rtc-check.yaml; then
  echo "!! config.yaml или .env с ошибкой (см. выше), работающие контейнеры не тронуты" >&2
  exit 1
fi

# go2rtc-config перерисовывает go2rtc.yaml из config.yaml, go2rtc сам его не перечитывает
if ! docker compose up -d --force-recreate go2rtc-config go2rtc streemcam; then
  echo "!! сервисы не поднялись" >&2
  docker compose ps -a
  docker compose logs --tail 50 --no-color go2rtc-config go2rtc streemcam
  exit 1
fi
docker compose up -d --remove-orphans

echo "== проверка http://127.0.0.1:8080/"
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null http://127.0.0.1:8080/; then
    echo "streemcam отвечает"
    docker compose ps
    exit 0
  fi
  sleep 2
done

echo "streemcam не ответил за 60 с" >&2
docker compose ps
docker compose logs --tail 100 streemcam go2rtc
exit 1
