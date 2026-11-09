# Туннель к камерам Imou/Dahua через P2P-облако Easy4IP (как приложение Imou Life): по серийному номеру
# открывает на сервере обычный RTSP камеры. Проект: https://github.com/khoanguyen-3fc/dh-p2p (MIT),
# версия закреплена коммитом — обновлять осознанно.
FROM rust:1-slim-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG DHP2P_COMMIT=9158ed08b74cdd153ae5dfe40bf13b247b573ffc
# наши исправления dh-p2p (dh-p2p-fixes.patch):
# - порт RTSP открывается только после P2P-рукопожатия (иначе подключения копятся в очереди);
# - PTCP принимает данные строго по порядку: повторы и пакеты после пропуска отбрасываются,
#   подтверждение говорит камере, что переслать (раньше дубли портили поток RTSP);
# - чужие датаграммы relay и неизвестные realm не роняют процесс; DISC от камеры закрывает подключение;
# - DHP2P_DEBUG=1 — строки «DBG» со счётчиками пакетов (без адресов и данных).
COPY dh-p2p-fixes.patch /tmp/
RUN git clone https://github.com/khoanguyen-3fc/dh-p2p.git /src \
    && cd /src && git checkout "$DHP2P_COMMIT" \
    && git apply /tmp/dh-p2p-fixes.patch \
    && cargo build --release --locked

FROM debian:bookworm-slim
COPY --from=build /src/target/release/dh-p2p /usr/local/bin/dh-p2p
COPY run.sh /run.sh
ENTRYPOINT ["/bin/bash", "/run.sh"]
