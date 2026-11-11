// Плеер живого видео: VideoRTC из go2rtc с более плавным MSE.
//
// Стандартный плеер go2rtc держит минимальную задержку: скорость воспроизведения он делает
// равной запасу буфера (запас 0,2 с — играет в 0,2 скорости). С камерами через P2P-туннель
// данные приходят неровно, и видео то замедляется, то встаёт с крутящимся кругом. Здесь
// плеер копит небольшой запас (BUFFER_TARGET) и играет с обычной скоростью, слегка ускоряясь,
// только если отстал. Пока ждём первый ключевой кадр, показывается последний снимок камеры (poster).
import { VideoRTC } from "/go2rtc/video-rtc.js";

const BUFFER_TARGET = 1.5;   // с: запас перед стартом и в работе (паузы P2P до ~1,5 с)
const BUFFER_MAX = 5.0;      // с: если отстали сильнее — прыжок к живому краю
const BUFFER_KEEP = 15.0;    // с: сколько уже показанного видео держать в памяти

export function playbackRate(gap) {
  if (gap > BUFFER_TARGET * 2.5) return 1.15;
  if (gap > BUFFER_TARGET * 1.5) return 1.05;
  if (gap < BUFFER_TARGET * 0.3) return 0.95;
  return 1;
}

class SmoothStream extends VideoRTC {
  oninit() {
    super.oninit();
    // свои кнопки есть на странице; родные элементы браузера показывали бы круг загрузки поверх картинки
    this.video.controls = false;
    const poster = this.getAttribute("poster");
    if (poster) this.video.poster = poster;
  }

  onmse() {
    let ms;
    if ("ManagedMediaSource" in window) {
      const MediaSource = window.ManagedMediaSource;
      ms = new MediaSource();
      ms.addEventListener("sourceopen", () => {
        this.send({ type: "mse", value: this.codecs(MediaSource.isTypeSupported) });
      }, { once: true });
      this.video.disableRemotePlayback = true;
      this.video.srcObject = ms;
    } else {
      ms = new MediaSource();
      ms.addEventListener("sourceopen", () => {
        URL.revokeObjectURL(this.video.src);
        this.send({ type: "mse", value: this.codecs(MediaSource.isTypeSupported) });
      }, { once: true });
      this.video.src = URL.createObjectURL(ms);
      this.video.srcObject = null;
    }

    this.mseCodecs = "";
    let started = false;

    this.onmessage["mse"] = (msg) => {
      if (msg.type !== "mse") return;
      this.mseCodecs = msg.value;

      const sb = ms.addSourceBuffer(msg.value);
      sb.mode = "segments";
      const buf = new Uint8Array(2 * 1024 * 1024);
      let bufLen = 0;

      sb.addEventListener("updateend", () => {
        if (!sb.updating && bufLen > 0) {
          try {
            sb.appendBuffer(buf.slice(0, bufLen));
            bufLen = 0;
          } catch (e) { /* буфер переполнен: следующий кусок догонит */ }
        }
        if (sb.updating || !sb.buffered || !sb.buffered.length) return;

        const v = this.video;
        const end = sb.buffered.end(sb.buffered.length - 1);
        const start0 = sb.buffered.start(0);
        if (end - BUFFER_KEEP > start0 && end - BUFFER_KEEP < v.currentTime) {
          sb.remove(start0, end - BUFFER_KEEP);
        }
        try { ms.setLiveSeekableRange(Math.max(start0, end - BUFFER_KEEP), end); } catch (e) { /* не везде есть */ }

        if (!started) {
          // копим запас, чтобы не встать сразу после старта
          if (end - start0 < BUFFER_TARGET) return;
          started = true;
          v.currentTime = Math.max(start0, end - BUFFER_TARGET);
          v.playbackRate = 1;
          this.play();
          return;
        }
        const gap = end - v.currentTime;
        if (gap > BUFFER_MAX || v.currentTime < start0) {
          v.currentTime = end - BUFFER_TARGET;
          v.playbackRate = 1;
        } else {
          v.playbackRate = playbackRate(gap);
        }
      });

      this.ondata = (data) => {
        if (sb.updating || bufLen > 0) {
          const b = new Uint8Array(data);
          if (bufLen + b.byteLength > buf.length) return; // не успеваем: кусок пропускаем, как и go2rtc
          buf.set(b, bufLen);
          bufLen += b.byteLength;
        } else {
          try { sb.appendBuffer(data); } catch (e) { /* см. выше */ }
        }
      };
    };
  }
}

if (!customElements.get("video-stream")) customElements.define("video-stream", SmoothStream);
