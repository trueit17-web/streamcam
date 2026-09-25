# streemcam

Просмотр камер (RTSP, Xiaomi, Tuya/Smart Life, Dahua) из Telegram и Discord для пользователей из белого списка.

## Быстрый старт

1. `copy config.example.yaml config.yaml`, `copy .env.example .env`, заполнить.
   Секрет: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
   Сгенерировать и вписать ещё STREEMCAM_INTERNAL_KEY (той же командой).
2. Публикация наружу — выбрать одно (`COMPOSE_PROFILES` в `.env`):
   - **VPS с белым IP** — `COMPOSE_PROFILES=caddy`, `STREEMCAM_DOMAIN=cams.example.com`;
     A-запись домена → IP сервера, порты 80/443 открыты. Caddy сам получит сертификат Let's Encrypt.
   - **Домашний сервер без белого IP** — `COMPOSE_PROFILES=cloudflared` (DNS домена должен быть в Cloudflare):
     Cloudflare Zero Trust → Networks → Tunnels → Create tunnel → токен в `CLOUDFLARE_TUNNEL_TOKEN`,
     Public hostname: `cams.example.com` → `http://streemcam:8080`.
3. `docker compose up -d --build`

## Telegram
1. @BotFather → `/newbot` → токен в `TELEGRAM_BOT_TOKEN`.
2. @BotFather → `/newapp` → выбрать бота → URL = `public_url`, short name = `mini_app_short_name`.
3. Добавить бота админом в канал, в личке боту: `/post_channel`, закрепить пост.
4. Команды: `/cams`, админам — `/allow <id|dc:id>`, `/deny`, `/users` (можно ответом на сообщение).

## Discord
1. https://discord.com/developers → New Application → Bot → токен в `DISCORD_BOT_TOKEN`.
2. OAuth2 → URL Generator: scopes `bot`, `applications.commands` → пригласить на сервер.
3. ID сервера в `discord.guild_ids` (команды появятся сразу).
4. Команды: `/cams`, админам — `/allow`, `/deny`, `/users`.

## Доступ

`/deny` отзывает доступ сразу, но пользователи из списка `allow` в config.yaml снова получат доступ после перезапуска — чтобы отозвать навсегда, удалите их и из config.yaml.

## Камеры Xiaomi
Открыть http://127.0.0.1:1984 (WebUI go2rtc, только с сервера) → Add → Xiaomi → войти в аккаунт
Mi Home. go2rtc сохранит ключ в `go2rtc/go2rtc.yaml` (streemcam его не перезаписывает).
Там же видны `did` и `model` камер. Для получения ключей go2rtc нужен интернет.
Список поддерживаемых моделей — в документации go2rtc (`internal/xiaomi`).
Xiaomi Smart Camera C701 — model chuangmi.camera.079ae2 (поддерживается go2rtc ≥ 1.9.13).

## VPS и домашние камеры (WireGuard на роутере)
Камеры Xiaomi отдают видео только в локальной сети, поэтому VPS должен видеть домашнюю подсеть:
1. На VPS поднять WireGuard-сервер (например, `apt install wireguard`, интерфейс `wg0`, адрес `10.8.0.1/24`,
   порт UDP 51820 открыт), в `AllowedIPs` пира-роутера указать `10.8.0.2/32, 192.168.1.0/24` (ваша домашняя подсеть).
2. На роутере создать WireGuard-подключение к VPS (клиент), `AllowedIPs = 10.8.0.0/24`,
   разрешить маршрутизацию/NAT из туннеля в домашнюю сеть.
3. Проверить с VPS: `ping 192.168.1.31` (IP камеры). Контейнеры Docker ходят в эту подсеть через хост.
4. Закрепить за камерами постоянные IP (DHCP-резервирование на роутере).

## Камеры Tuya / Smart Life
1. В `config.yaml`: `tuya.enabled: true`, в `.env` — `STREEMCAM_INTERNAL_KEY`.
2. В приложении Smart Life / Tuya Smart: «Я» → ⚙ → «Аккаунт и безопасность» → «Код пользователя».
3. В личке с ботом (админ): `/tuya_login <код>` → бот пришлёт QR → Smart Life → сканер → подтвердить.
4. Камеры аккаунта появятся в плеере автоматически (`/tuya_status` — список, `/tuya_logout` — выход).
Вход использует client_id интеграции Tuya из Home Assistant; если Tuya его заблокирует, вход перестанет работать.

Потоки Tuya прописываются прямо в конфиг go2rtc (`go2rtc.yaml`) и применяются через `POST /api/restart`
(go2rtc API не позволяет добавить источник `echo:`/`exec:` через `/api/streams`, только через файл конфига)
— поэтому после `/tuya_login`, `/tuya_logout` или изменения списка камер аккаунта go2rtc ненадолго
перезапускается и текущие зрители переподключаются. Поскольку `go2rtc.yaml` в результате содержит
`STREEMCAM_INTERNAL_KEY`, файл должен оставаться локальным (не публиковать), а API go2rtc — слушать
только `127.0.0.1`/сеть docker (как и задано в `docker-compose.yml`).

## Совместимый режим (H.265)
C701 отдают H.265 — его показывают Safari/iOS и Chrome/Edge с аппаратным декодированием. Если видео чёрное,
включите в плеере «Совместимый режим»: сервер перекодирует поток в H.264 720p. Одновременно — не больше
`max_transcodes` таких просмотров.

## Запись и архив
- Запись идёт по расписанию (`recording.start`–`recording.end`, часовой пояс `recording.timezone`)
  из дополнительного потока камеры (Xiaomi — `record_subtype`, по умолчанию 1), перекодируется в H.264
  и режется на часовые файлы `recordings/<камера>/<ГГГГ-ММ-ДД>/<ЧЧ-ММ-СС>.mp4`.
- Архив доступен всем из белого списка: в плеере кнопка «Архив» → камера → дата → час, есть «Скачать».
- Очистка: записи старше `retention_days` удаляются; если свободно меньше `min_free_gb`, удаляются самые старые часы.
- Оценка места: доп. поток ≈ 0,15–0,2 ГБ/ч на камеру → 3 камеры × 11 ч ≈ 5–7 ГБ в сутки.
- Не писать камеру: `record: false` у камеры; не писать Tuya: `recording.tuya: false`.
- Если запись не идёт больше `alert_minutes`, админам придёт уведомление в Telegram.

## Изменение конфигурации

`go2rtc.yaml` генерируется только одноразовым сервисом `go2rtc-config` — go2rtc и streemcam
его не перечитывают сами. После правки `config.yaml`:

- Docker: `docker compose up -d --force-recreate go2rtc-config go2rtc streemcam`
- Без Docker: заново выполнить `python -m streemcam render-go2rtc`, затем перезапустить go2rtc
  и streemcam.

## Без Docker
`pip install .`, поменять адреса в конфиге (см. комментарии), затем
`python -m streemcam render-go2rtc`, запустить go2rtc с `go2rtc/go2rtc.yaml`, `python -m streemcam run`.

## Выдать ссылку вручную
`docker compose exec streemcam python -m streemcam link tg:123456 --base http://127.0.0.1:8080`
