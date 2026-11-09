#!/usr/bin/env bash
# Один процесс dh-p2p на камеру: DHP2P_CAMERAS="СЕРИЙНИК:ПОРТ СЕРИЙНИК2:ПОРТ2".
# RTSP камеры будет доступен в docker-сети как dhp2p:ПОРТ. Упавший туннель перезапускается.
# dh-p2p печатает адреса и служебные запросы — в лог пропускаем только состояние (логи статуса публичные).
set -u
[ -n "${DHP2P_CAMERAS:-}" ] || { echo "DHP2P_CAMERAS не задан — нечего запускать"; exec sleep infinity; }
relay=()
[ "${DHP2P_RELAY:-0}" = 1 ] && relay=(--relay)

tunnel() {
  local sn=$1 port=$2 tag="камера …${1: -5} (порт $2)" pid line pause
  while true; do
    pause=15
    echo "$tag: подключение"
    # После закрытия RTSP-подключения P2P-сессия dh-p2p перестаёт отвечать новым подключениям,
    # поэтому туннель перезапускается сразу: go2rtc переподключится к свежей сессии.
    coproc P2P { exec dh-p2p "${relay[@]}" -p "0.0.0.0:${port}:554" "$sn" 2>&1; }
    pid=$P2P_PID
    while IFS= read -r line <&"${P2P[0]}"; do
      case "$line" in
        *"PTCP session established"*|*"Ready to connect"*|*Timeout*|*"requires authentication"*|*panicked*|*"Error response"*|*"called \`"*)
          echo "$tag: ${line}" | sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}(:[0-9]+)?/IP/g' ;;
        *"closed by peer"*|*"Connection reset"*)
          echo "$tag: подключение закрыто, перезапуск туннеля"
          pause=1; kill "$pid" 2>/dev/null; break ;;
      esac
    done
    wait "$pid" 2>/dev/null
    echo "$tag: туннель завершился, повтор через $pause с"
    sleep "$pause"
  done
}

for item in ${DHP2P_CAMERAS//,/ }; do
  tunnel "${item%%:*}" "${item##*:}" &
done
wait
