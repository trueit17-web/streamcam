#!/usr/bin/env bash
# Один процесс dh-p2p на камеру: DHP2P_CAMERAS="СЕРИЙНИК:ПОРТ СЕРИЙНИК2:ПОРТ2".
# RTSP камеры будет доступен в docker-сети как dhp2p:ПОРТ. Упавший туннель перезапускается.
# dh-p2p печатает адреса и служебные запросы — в лог пропускаем только состояние (логи статуса публичные).
set -u
[ -n "${DHP2P_CAMERAS:-}" ] || { echo "DHP2P_CAMERAS не задан — нечего запускать"; exec sleep infinity; }
relay=()
[ "${DHP2P_RELAY:-0}" = 1 ] && relay=(--relay)

tunnel() {
  local sn=$1 port=$2 tag="камера …${1: -5} (порт $2)" pid line pause fd wait_s rc
  while true; do
    pause=15
    wait_s=90   # рукопожатие должно уложиться в 90 с, иначе процесс завис — перезапускаем
    echo "$tag: подключение"
    exec {fd}< <(exec dh-p2p "${relay[@]}" -p "0.0.0.0:${port}:554" "$sn" 2>&1)
    pid=$!
    while true; do
      IFS= read -r -t "$wait_s" line <&"$fd"; rc=$?
      if (( rc > 128 )); then echo "$tag: нет ответа ${wait_s} с, перезапуск"; break; fi
      (( rc == 0 )) || break
      case "$line" in
        *"Ready to connect"*) wait_s=86400 ;;&
        *"PTCP session established"*|*"Ready to connect"*|*Timeout*|*"requires authentication"*|*panicked*|*"Error response"*|*"called \`"*)
          echo "$tag: ${line}" | sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}(:[0-9]+)?/IP/g' ;;
        # P2P-сессия dh-p2p обслуживает одно RTSP-подключение: после его закрытия новые не получают
        # ответа, поэтому туннель перезапускается, и go2rtc переподключается к свежей сессии
        *"closed by peer"*|*"Connection reset"*)
          echo "$tag: подключение закрыто, перезапуск туннеля"
          pause=1; kill "$pid" 2>/dev/null; break ;;
      esac
    done
    kill "$pid" 2>/dev/null
    exec {fd}<&-
    echo "$tag: туннель завершился, повтор через $pause с"
    sleep "$pause"
  done
}

for item in ${DHP2P_CAMERAS//,/ }; do
  tunnel "${item%%:*}" "${item##*:}" &
done
wait
