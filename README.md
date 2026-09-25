# streemcam

Просмотр камер (RTSP, Dahua, Xiaomi) из Telegram и Discord для пользователей из белого списка.

## Быстрый старт

1. `copy config.example.yaml config.yaml`, `copy .env.example .env`, заполнить.
   Секрет: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Cloudflare Zero Trust → Networks → Tunnels → Create tunnel → скопировать токен в
   `CLOUDFLARE_TUNNEL_TOKEN`. Public hostname: `cams.example.com` → `http://streemcam:8080`.
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

## Камеры Dahua
`type: dahua`, `subtype: 1` — дополнительный поток (обычно H.264, работает во всех браузерах).

## Без Docker
`pip install .`, поменять адреса в конфиге (см. комментарии), затем
`python -m streemcam render-go2rtc`, запустить go2rtc с `go2rtc/go2rtc.yaml`, `python -m streemcam run`.

## Выдать ссылку вручную
`docker compose exec streemcam python -m streemcam link tg:123456 --base http://127.0.0.1:8080`
