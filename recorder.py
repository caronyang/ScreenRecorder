# -*- coding: utf-8 -*-
"""螢幕錄影核心:擷取畫面 + 錄音 -> 編碼為 MP4 (H.264 + AAC)。

架構:
  - 影音容器 (av.open) 在 start() 開啟,由兩個執行緒分別編碼:
      * 影音執行緒: dxcam (60fps, DXGI) 或 mss (GDI) 擷取 -> H.264
      * 音訊執行緒: sounddevice callback 區塊 -> AAC
  - container.mux() 以鎖保護;時間軸以牆鐘為準 (VFR),音訊以樣本數為準。
"""
from __future__ import annotations

import gc
import os
import threading
import time
from dataclasses import dataclass

import numpy as np
import av

from audio_sources import AudioSource, AudioCapture
from i18n import t

# AAC 編碼器支援的取樣率;不在列表內時重採樣到 48000
_AAC_RATES = {8000, 11025, 12000, 16000, 22050, 24000, 32000,
              44100, 48000, 64000, 88200, 96000}
_H264_CODECS = ("libx264", "h264", "libopenh264")


class RecorderError(RuntimeError):
    pass


def _pick_h264_codec() -> str:
    available = getattr(av.codec, "codecs_available", set())
    for name in _H264_CODECS:
        if not available or name in available:
            return name
    return "mpeg4"


def _resample_linear(block: np.ndarray, in_rate: int, out_rate: int) -> np.ndarray:
    """線性內填重採樣:裝置取樣率非 AAC 標準值時轉到 48000。"""
    n_in = block.shape[0]
    n_out = max(1, int(round(n_in * out_rate / in_rate)))
    t_in = np.arange(n_in, dtype=np.float64) / in_rate
    t_out = np.arange(n_out, dtype=np.float64) / out_rate
    out = np.empty((n_out, block.shape[1]), dtype=np.float32)
    for c in range(block.shape[1]):
        out[:, c] = np.interp(t_out, t_in, block[:, c])
    return out


def _primary_display_size() -> tuple[int, int]:
    import ctypes
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def _even(v: int) -> int:
    return max(16, v - (v % 2))


@dataclass(frozen=True)
class Region:
    """螢幕區域 (左上角 + 寬高),單位為實像素。"""
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def ltrb(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)

    def normalized(self) -> "Region":
        w = _even(self.width)
        h = _even(self.height)
        return Region(self.left, self.top, w, h)


# ---------------------------------------------------------------- 擷取後端

class MssGrabber:
    """GDI 擷取:相容性最高 (含跨螢幕/負座標),約 30fps @1080p。"""

    name = "mss"

    def __init__(self, region: Region):
        from mss import MSS
        self._s = MSS()
        self._mon = {"left": region.left, "top": region.top,
                     "width": region.width, "height": region.height}
        self._size = (region.height, region.width)

    def read(self) -> np.ndarray | None:
        shot = self._s.grab(self._mon)
        return np.asarray(shot)  # BGRA

    @property
    def color_format(self) -> str:
        return "bgra"

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:
            pass


class DxcamGrabber:
    """DXGI Desktop Duplication:GPU 加速,1080p 可達 60fps。"""

    name = "dxcam"

    def __init__(self, region: Region, fps: int):
        import dxcam
        cam = dxcam.create(output_color="RGB", processor_backend="numpy")
        if cam is None:
            raise RecorderError(t("err_dxcam_init"))
        cam.start(region=region.ltrb, target_fps=min(60, max(30, fps)))
        self._cam = cam
        self._size = (region.height, region.width)

    def read(self) -> np.ndarray | None:
        # grab() 於背景擷取執行中時非阻塞,回傳最新畫面 (含靜止畫面)
        frame = self._cam.grab(copy=True)
        if frame is None:
            return None
        if frame.shape[:2] != self._size:
            raise RecorderError(t("err_dxcam_region",
                                  detail=f"{frame.shape} != {self._size}"))
        return frame

    @property
    def color_format(self) -> str:
        return "rgb24"

    def close(self) -> None:
        cam, self._cam = self._cam, None
        if cam is not None:
            try:
                cam.stop()
            except Exception:
                pass
            del cam
            gc.collect()


def open_grabber(region: Region, fps: int):
    """優先使用 dxcam;失敗或超出主螢幕範圍時退回 mss。"""
    pw, ph = _primary_display_size()
    in_primary = (region.left >= 0 and region.top >= 0
                  and region.right <= pw and region.bottom <= ph)
    if in_primary:
        try:
            return DxcamGrabber(region, fps)
        except Exception:
            gc.collect()
    return MssGrabber(region)


# ---------------------------------------------------------------- 錄影工作

class Recorder:
    """一次錄影工作:start() 開始、stop() 結束並輸出檔案。"""

    def __init__(self, region: Region, fps: int, out_path: str,
                 audio: AudioSource | None = None):
        self.region = region.normalized()
        self.fps = int(fps)
        self.out_path = out_path
        self.audio_source = audio

        self.error: Exception | None = None
        self._stop = threading.Event()
        self._mux_lock = threading.Lock()
        self._container = None
        self._vstream = None
        self._astream = None
        self._grabber = None
        self._cap: AudioCapture | None = None
        self._vthread: threading.Thread | None = None
        self._athread: threading.Thread | None = None
        self._t0: float | None = None
        self._t1: float | None = None
        self._last_pts = -1
        self._frames = 0
        self._audio_samples = 0
        self._in_rate = 0
        self._out_rate = 0

    # ---- 狀態
    @property
    def is_running(self) -> bool:
        return self._t0 is not None and self._t1 is None

    @property
    def elapsed(self) -> float:
        if self._t0 is None:
            return 0.0
        end = self._t1 if self._t1 is not None else time.monotonic()
        return end - self._t0

    # ---- 啟動
    def start(self) -> None:
        if self.is_running:
            raise RecorderError(t("err_already_recording"))
        if self.error is not None:
            raise RecorderError(str(self.error))

        out_dir = os.path.dirname(os.path.abspath(self.out_path))
        os.makedirs(out_dir, exist_ok=True)

        # 音訊先開啟,取得取樣率後建立串流
        cap = AudioCapture(self.audio_source) if self.audio_source else None
        if cap is not None:
            cap.open()
        self._cap = cap

        try:
            self._container = av.open(
                self.out_path, mode="w", options={"movflags": "+faststart"})
            self._vstream = self._container.add_stream(_pick_h264_codec(),
                                                       rate=self.fps)
            self._vstream.width = self.region.width
            self._vstream.height = self.region.height
            self._vstream.pix_fmt = "yuv420p"
            self._vstream.options = {
                "preset": "veryfast",
                "crf": "20",
                "tune": "zerolatency",
            }
            if cap is not None and cap.active:
                dev_rate = int(cap.samplerate)
                out_rate = dev_rate if dev_rate in _AAC_RATES else 48000
                self._in_rate, self._out_rate = dev_rate, out_rate
                self._astream = self._container.add_stream("aac",
                                                           rate=out_rate)
                self._astream.layout = "stereo"   # 恆為立體聲(見 _encode_audio)
        except Exception as e:
            self._cleanup_partial()
            raise RecorderError(t("err_create_output", err=e)) from e

        self._grabber = open_grabber(self.region, self.fps)
        self._stop.clear()
        self.error = None
        self._last_pts = -1
        self._frames = 0
        self._audio_samples = 0
        self._t0 = time.monotonic()
        self._t1 = None

        self._vthread = threading.Thread(target=self._video_loop,
                                         name="rec-video", daemon=True)
        self._vthread.start()
        if cap is not None and cap.active:
            self._athread = threading.Thread(target=self._audio_loop,
                                             name="rec-audio", daemon=True)
            self._athread.start()

    # ---- 影音編碼
    def _mux(self, packets) -> None:
        with self._mux_lock:
            for p in packets:
                self._container.mux(p)

    def _video_loop(self) -> None:
        try:
            fps = self.fps
            next_t = time.monotonic()
            while not self._stop.is_set():
                now = time.monotonic()
                if now < next_t:
                    time.sleep(min(next_t - now, 0.05))
                    continue
                frame = self._grabber.read()
                if frame is not None:
                    pts = int(round((now - self._t0) * fps))
                    if pts <= self._last_pts:
                        pts = self._last_pts + 1
                    self._last_pts = pts
                    vf = av.VideoFrame.from_ndarray(
                        frame, format=self._grabber.color_format)
                    vf = vf.reformat(format="yuv420p")
                    vf.pts = pts
                    try:
                        vf.color_range = "tv"
                    except Exception:
                        pass
                    self._mux(self._vstream.encode(vf))
                    self._frames += 1
                # 排程下一格;落後太多就重新對時
                next_t += 1.0 / fps
                if now - next_t > 1.0 / fps:
                    next_t = now
        except Exception as e:
            self._set_error(e)
            self._stop.set()

    def _audio_loop(self) -> None:
        cap, stream = self._cap, self._astream
        try:
            cap.start()
            while not self._stop.is_set():
                block = cap.get_block(timeout=0.2)
                if block is not None:
                    self._encode_audio(block)
            for block in cap.drain():
                self._encode_audio(block)
        except Exception as e:
            self._set_error(e)
            self._stop.set()
        finally:
            cap.close()

    def _encode_audio(self, block: np.ndarray) -> None:
        if block.size == 0:
            return
        if block.ndim == 1:
            block = block[:, None]
        if block.shape[1] == 1:
            # 單聲道 -> 立體聲(左右同音),避免播放時只有單邊出聲
            block = np.repeat(block, 2, axis=1)
        elif block.shape[1] > 2:
            block = block[:, :2]
        if self._in_rate and self._out_rate and self._in_rate != self._out_rate:
            block = _resample_linear(block, self._in_rate, self._out_rate)
        samples = np.ascontiguousarray(block.T, dtype=np.float32)
        af = av.AudioFrame.from_ndarray(samples, format="fltp",
                                        layout="stereo")
        af.sample_rate = self._out_rate
        af.pts = self._audio_samples
        self._audio_samples += block.shape[0]
        self._mux(self._astream.encode(af))

    # ---- 結束
    def stop(self) -> str:
        if self._t0 is None:
            raise RecorderError(t("err_not_started"))
        self._stop.set()
        for th in (self._vthread, self._athread):
            if th is not None and th.is_alive():
                th.join(timeout=10)
        self._t1 = time.monotonic()
        try:
            with self._mux_lock:
                if self._vstream is not None and self._frames > 0:
                    for p in self._vstream.encode(None):
                        self._container.mux(p)
                if self._astream is not None and self._audio_samples > 0:
                    for p in self._astream.encode(None):
                        self._container.mux(p)
                self._container.close()
        except Exception as e:
            self._set_error(e)
        finally:
            self._container = None
            self._vstream = None
            self._astream = None
            self._close_grabber()
            if self._cap is not None:
                self._cap.close()

        if self._frames == 0:
            raise RecorderError(t("err_no_frames"))
        if self.error is not None:
            raise RecorderError(t("err_recording", err=self.error))
        return self.out_path

    # ---- 雜項
    def _set_error(self, exc: Exception) -> None:
        if self.error is None:
            self.error = exc

    def _close_grabber(self) -> None:
        g, self._grabber = self._grabber, None
        if g is not None:
            try:
                g.close()
            except Exception:
                pass

    def _cleanup_partial(self) -> None:
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass
        self._container = None
        self._vstream = None
        self._astream = None
        if self._cap is not None:
            self._cap.close()
            self._cap = None
        self._close_grabber()


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def fmt_size(num_bytes: int) -> str:
    if num_bytes >= 1 << 30:
        return f"{num_bytes / (1 << 30):.2f} GB"
    if num_bytes >= 1 << 20:
        return f"{num_bytes / (1 << 20):.1f} MB"
    if num_bytes >= 1 << 10:
        return f"{num_bytes / (1 << 10):.0f} KB"
    return f"{num_bytes} B"
