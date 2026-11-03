#!/usr/bin/env bash
# Один процесс dh-p2p на камеру: DHP2P_CAMERAS="СЕРИЙНИК:ПОРТ СЕРИЙНИК2:ПОРТ2".
# RTSP камеры будет доступен в docker-сети как dhp2p:ПОРТ. Упавший туннель перезапускается.
# dh-p2p печатает адреса и служебные запросы — в лог пропускаем только состояние (логи статуса публичные).
set -u
[ -n "${DHP2P_CAMERAS:-}" ] || { echo "DHP2P_CAMERAS не задан — нечего запускать"; exec sleep infinity; }
relay=()
[ "${DHP2P_RELAY:-0}" = 1 ] && relay=(--relay)

tunnel() {
  local sn=$1 port=$2 tag="камера …${1: -5} (порт $2)"
  while true; do
    echo "$tag: подключение"
    dh-p2p "${relay[@]}" -p "0.0.0.0:${port}:554" "$sn" 2>&1 \
      | grep --line-buffered -E 'PTCP session established|Ready to connect|Timeout|requires authentication|panicked|Error response|closed|called `' \
      | sed -u -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}(:[0-9]+)?/IP/g; s/^/'"$tag"': /'
    echo "$tag: туннель завершился, повтор через 15 с"
    sleep 15
  done
}

for item in ${DHP2P_CAMERAS//,/ }; do
  tunnel "${item%%:*}" "${item##*:}" &
done
wait
