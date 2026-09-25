# streemcam — запись по расписанию и архив

Дата: 2026-09-25
Статус: дизайн утверждён, ожидает ревью спеки
Зависит от: `2026-09-25-tuya-cameras-design.md` (каталог камер `Catalog`, `Go2rtcSync`), базовая спека `2026-09-25-streemcam-design.md`

## 1. Цель

Записывать камеры с 08:00 до 19:00 (по часовому поясу из конфига) из дополнительного потока, в H.264,
часовыми файлами; хранить с автоочисткой; давать смотреть и скачивать записи всем пользователям белого списка
в плеере (вкладка «Архив»).

### Критерии успеха
- В 08:00 начинается запись всех записываемых камер, в 19:00 — останавливается; файлы режутся ровно по часам.
- Любой час открывается в плеере (в т.ч. в Telegram Mini App) с перемоткой, и скачивается кнопкой.
- Текущий час можно смотреть, пока он пишется.
- Диск не переполняется: записи старше `retention_days` удаляются, при свободном месте < `min_free_gb` удаляются самые старые часы.
- Запись не мешает живому просмотру; нагрузка на CPU от трёх записей — меньше одного ядра.

### Вне рамок
Запись по движению; шкала времени через границы часов; экспорт фрагментов; облачное хранилище;
отдельные права на архив (доступ = белый список).

## 2. Решения

| Вопрос | Решение |
|---|---|
| Расписание | 08:00–19:00 (`start` включительно, `end` не включительно), `timezone` из конфига (по умолчанию `Europe/Moscow`) |
| Источник | Дополнительный поток: Xiaomi — `record_subtype` (по умолчанию 1), Dahua — `subtype=1`; RTSP и Tuya — основной поток |
| Формат | H.264 (libx264 veryfast, CRF 28) + AAC, фрагментированный MP4, 1 файл = 1 час |
| Хранение | `retention_days` (14) + `min_free_gb` (15) |
| Доступ | Все из белого списка (как живое видео) |
| Просмотр | Плеер: камера → дата → часы; `<video controls>` + «Скачать» |
| Движок | ffmpeg в контейнере streemcam, вход — RTSP-сервер go2rtc (`:8554`, только docker-сеть) |

### Оценка места
Доп. поток C701 ≈ 0,3–0,5 Мбит/с после перекодирования ≈ 0,15–0,2 ГБ/ч на камеру → 3 камеры × 11 ч ≈ 5–7 ГБ/сутки →
при ~130 ГБ свободных ≈ 2–3 недели. Камеры Tuya пишутся из облачного потока (расход входящего трафика VPS).

## 3. Архитектура

```
 go2rtc :8554 (RTSP, только docker-сеть; rtsp.listen задаёт render-go2rtc)
   <id>~rec  = доп. поток (xiaomi …&subtype=<record_subtype>, dahua …&subtype=1)
   <id>, tuya_<id> — основной поток (rtsp, tuya)
        │
 streemcam
   recording/schedule.py  — чистые функции расписания
   recording/recorder.py  — ffmpeg на камеру, старт/стоп по расписанию, перезапуск, алерты
   recording/archive.py   — список дней/часов, безопасный путь к файлу, очистка
   web: /api/archive/…    — под сессией
   плеер: вкладка «Архив»
```

## 4. Модули

### Конфиг
- `recording:` — `enabled: false`, `timezone: "Europe/Moscow"`, `start: "08:00"`, `end: "19:00"`,
  `retention_days: 14`, `min_free_gb: 15`, `path: "recordings"` (docker: `/recordings`),
  `rtsp_url: "rtsp://127.0.0.1:8554"` (docker: `rtsp://go2rtc:8554`), `tuya: true`, `alert_minutes: 10`.
- Камеры из конфига: `record: bool = true`. `XiaomiCamera.record_subtype: int = 1`.
- Валидация: `start`/`end` — `HH:MM`, `start < end`; `timezone` — существующая зона (`zoneinfo`).

### go2rtc_config (изменения)
- `render()` дополнительно: `rtsp: {listen: ":8554"}` (сохраняя чужие ключи); для камер `type: xiaomi`
  с `record: true` — поток `<id>~rec` = `stream_url` с `subtype=record_subtype`; для `type: dahua` — с `subtype=1`.
- `rec_stream(cam: CameraInfo, cfg) -> str` — имя потока для записи: `<id>~rec` для xiaomi/dahua, иначе `<id>`.
  (Каталогу для этого нужен тип камеры из конфига: `CameraInfo.source_type`.)

### `recording/schedule.py`
- `parse_hhmm(s) -> time`; `is_recording_time(now: datetime, rec_cfg) -> bool` (now приводится к `timezone`);
  `seconds_until_change(now, rec_cfg) -> float`.

### `recording/recorder.py`
- `ffmpeg_args(input_url, out_dir) -> list[str]`:
  `ffmpeg -hide_banner -loglevel warning -rtsp_transport tcp -i <input> -map 0:v:0 -map 0:a:0? -c:v libx264 -preset veryfast -crf 28 -g 50 -c:a aac -b:a 64k -f segment -segment_time 3600 -segment_atclocktime 1 -reset_timestamps 1 -strftime 1 -segment_format mp4 -segment_format_options movflags=+frag_keyframe+empty_moov+default_base_moof <out_dir>/%Y-%m-%d/%H-%M-%S.mp4`. Имя файла — время начала сегмента: первый сегмент после старта в 10:25:13 — `10-25-13.mp4`, дальше `11-00-00.mp4` и т.д.; перезапуск в том же часе никогда не затирает начало часа.
  Папку текущего и следующего дня создаёт recorder заранее (ffmpeg не создаёт каталоги).
- `Recorder(cfg, catalog, spawn=asyncio.create_subprocess_exec, clock=..., on_alert=None)`:
  цикл каждые 30 с — в окне расписания держит по процессу на камеру (`record: true`, Tuya — если `recording.tuya`),
  вне окна — останавливает все. Упавший процесс — перезапуск с паузой 5→10→…→60 с. Камера не пишется
  > `alert_minutes` → одно уведомление админам; восстановилась → ещё одно.
- Остановка: `q\n` в stdin, ожидание 10 с, затем `terminate`, затем `kill`.
- Нет ffmpeg в PATH → запись выключается с ошибкой в логе, остальной сервис работает.

### `recording/archive.py`
- Имя файла: `HH-MM-SS.mp4` (время начала сегмента); день: `YYYY-MM-DD`. Час = первые две цифры; в одном часе может быть несколько файлов (после перезапуска).
- `Archive(root: Path, catalog)`: `days(cam_id) -> list[str]` (новые сначала), `hours(cam_id, day) -> list[HourEntry(name, hour, size, recording)]`,
  `file(cam_id, day, name) -> Path | None` (строгий regex + `resolve()` внутри `root`), `cleanup(now, retention_days, min_free_gb, active: set[Path], disk_free=shutil.disk_usage)`.
- `recording` = файл изменялся за последние 60 с.

### Веб (под сессией, как `/api/cameras`)
- `GET /api/archive/{cam}/days` → `{"days": [...]}`
- `GET /api/archive/{cam}/{day}` → `{"hours": [{"name": "08-00-00", "hour": 8, "size": 123, "recording": false}, ...]}`
- `GET /api/archive/{cam}/{day}/{name}.mp4[?download=1]` → `FileResponse` (Range → 206); `download=1` →
  `Content-Disposition: attachment; filename="<cam>_<day>_<name>.mp4"`.
- 401/403 — как у сессий; 404 — неизвестная камера, неверный формат, нет файла.

### Плеер
- Переключатель «Живое / Архив» в шапке.
- Архив: список камер → даты (новые сначала) → сетка часов → `<video controls playsinline>` + «Скачать».
- URL файла с `?s=<сессия>`.

### Развёртывание
- Dockerfile: `apt-get install -y --no-install-recommends ffmpeg tzdata`.
- compose: том `./recordings:/recordings` у streemcam; `recordings/` в `.gitignore` и `.dockerignore`.
- README: раздел «Запись и архив» (расписание, оценка места, где файлы, как выключить камеру `record: false`).

## 5. Ошибки
- ffmpeg падает → перезапуск с паузой; > `alert_minutes` без записи → уведомление.
- Диск заполняется → cleanup удаляет старые часы (не активные файлы).
- go2rtc недоступен → ffmpeg падает и перезапускается; алерт по той же логике.

## 6. Безопасность
- Путь к файлу строится только из проверенных частей: `cam` — из каталога, `day` — `^\d{4}-\d{2}-\d{2}$`,
  `name` — `^\d{2}-\d{2}-\d{2}$`; итоговый путь проверяется `resolve()` на вхождение в `root`.
- Доступ — сессия + белый список (как у живого видео).
- RTSP go2rtc (:8554) не публикуется наружу.

## 7. Тестирование (pytest, TDD, без реального ffmpeg)
- schedule: 07:59/08:00/18:59/19:00; другой часовой пояс; `seconds_until_change`.
- go2rtc_config: `rtsp.listen`, потоки `~rec` для xiaomi/dahua, отсутствие `~rec` при `record: false`.
- recorder: фейковый `spawn` — аргументы ffmpeg, старт в окне, стоп вне окна, перезапуск после падения,
  мягкая остановка (`q`), алерт после `alert_minutes`, создание папок текущего и следующего дня.
- archive: days/hours, отказ `..`/неверный формат/чужая камера, cleanup по сроку и месту, активные файлы не трогаются.
- web: 200/206 Range, 401/403/404, `download=1` → `Content-Disposition`.
- Плеер: статическая проверка вкладки «Архив».
- Ручная проверка на проде: час записи, просмотр, скачивание.
