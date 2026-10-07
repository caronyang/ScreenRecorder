# -*- coding: utf-8 -*-
"""音訊來源探測與擷取(系統聲音 / 麥克風)。

系統聲音的擷取方式(依序):
  1. WASAPI 迴圈錄音 — 錄「預設輸出裝置」播放中的數位副本,任何電腦皆可用
  2. 「立體聲混音 (Stereo Mix)」類裝置 -> 以 sounddevice 錄音(次選/備援)
  3. 都沒有時只提供麥克風,README 說明如何啟用立體聲混音
"""
from __future__ import annotations

import queue
from dataclasses import dataclass

import sounddevice as sd

import wasapi_loopback
from i18n import t

# 「系統聲音」來源的裝置名稱關鍵字(各家音效卡命名不同)
_SYSTEM_KEYWORDS = (
    "立體聲混音", "立体声混音", "stereo mix", "what u hear",
    "what you hear", "wave out mix", "loopback", "混音",
)


@dataclass(frozen=True)
class AudioSource:
    key: str               # "none" | "system" | "mic:<index>"
    device_index: int | None = None
    is_system: bool = False
    backend: str = "sd"    # "sd" (sounddevice) | "loopback" (WASAPI 迴圈)
    device_name: str | None = None   # Windows 裝置名稱(mic / 混音裝置才有)

    @property
    def label(self) -> str:
        """顯示名稱(依目前語言動態產生)。"""
        if self.key == "none":
            return t("src_none")
        if self.is_system and self.device_name:
            return t("src_system_dev", name=self.device_name)
        if self.is_system:
            return t("src_system")
        return self.device_name or self.key

    @property
    def enabled(self) -> bool:
        return self.device_index is not None or self.backend == "loopback"


def discover_sources() -> list[AudioSource]:
    """列出可用的音訊來源:不錄音、系統聲音(迴圈/混音)、各個麥克風。"""
    sources: list[AudioSource] = [AudioSource("none")]

    # 1) WASAPI 迴圈錄音:不需要任何輸入裝置,錄系統正在播放的聲音
    try:
        out_name = wasapi_loopback.probe()
    except Exception:
        out_name = None
    if out_name:
        sources.append(AudioSource("system", None, True,
                                   backend="loopback"))

    try:
        devices = sd.query_devices()
    except Exception:
        devices = []

    # 2) 立體聲混音類輸入裝置(硬體混音,備援)
    system: AudioSource | None = None
    mics: list[AudioSource] = []
    for i, d in enumerate(devices):
        if int(d.get("max_input_channels", 0)) <= 0:
            continue
        name = str(d["name"]).strip()
        low = name.lower()
        is_system = any(k in low for k in _SYSTEM_KEYWORDS)
        if is_system:
            # 優先取名稱最明確者(「立體聲混音」排最前面)
            cand = AudioSource("system", i, True, device_name=name)
            if system is None or "立體聲混音" in name:
                system = cand
        else:
            mics.append(AudioSource(f"mic:{i}", i, device_name=name))

    if system is not None:
        sources.append(system)
    sources.extend(mics)
    return sources


def find_system_source() -> AudioSource | None:
    for s in discover_sources():
        if s.is_system:
            return s
    return None


class AudioCapture:
    """開啟一個輸入串流,以 callback 收集音訊區塊。

    backend="loopback" 時改用 WASAPI 迴圈錄音(系統聲音),介面一致。
    """

    def __init__(self, source: AudioSource):
        self.source = source
        self.samplerate: int = 0
        self.channels: int = 0
        self._stream: sd.InputStream | None = None
        self._lb = None                     # wasapi_loopback.LoopbackStream
        self._queue: queue.Queue = queue.Queue()
        self._closed = False

    @property
    def active(self) -> bool:
        return self.source.enabled

    def open(self) -> None:
        """開啟(但不啟動)音訊串流,先確認裝置可用並取得取樣率。"""
        if not self.source.enabled:
            return
        if self.source.backend == "loopback":
            from wasapi_loopback import LoopbackStream
            lb = LoopbackStream()
            rate, ch = lb.open()
            self._lb = lb
            self.samplerate = rate
            self.channels = ch
            return
        info = sd.query_devices(self.source.device_index)
        rate = int(round(float(info["default_samplerate"])))
        max_ch = int(info["max_input_channels"])
        want = [2, 1] if max_ch >= 2 else [1]
        last_err: Exception | None = None
        for ch in want:
            try:
                # sounddevice 串流建立即開始擷取;callback 只進 queue,不影響時序
                self._stream = sd.InputStream(
                    device=self.source.device_index,
                    samplerate=rate,
                    channels=ch,
                    dtype="float32",
                    callback=self._callback,
                )
                self.samplerate = rate
                self.channels = ch
                return
            except Exception as e:  # 裝置不支援該取樣率/聲道時重試
                last_err = e
                self._stream = None
        raise RuntimeError(t("err_open_device", label=self.source.label,
                             err=last_err))

    def _callback(self, indata, frames, time_info, status) -> None:
        try:
            self._queue.put_nowait(indata.copy())
        except queue.Full:
            pass

    def _enqueue(self, block) -> None:
        try:
            self._queue.put_nowait(block)
        except queue.Full:
            pass

    def start(self) -> None:
        if self._lb is not None:
            self._lb.start(self._enqueue)
            return
        if self._stream is not None and not self._stream.active:
            self._stream.start()

    def get_block(self, timeout: float = 0.2):
        """取得一個音訊區塊;逾時回傳 None;串流結束回傳 sentinel。"""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> list:
        blocks = []
        while True:
            try:
                blocks.append(self._queue.get_nowait())
            except queue.Empty:
                return blocks

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        lb, self._lb = self._lb, None
        if lb is not None:
            try:
                lb.close()
            except Exception:
                pass
        st, self._stream = self._stream, None
        if st is not None:
            try:
                if st.active:
                    st.stop()
            except Exception:
                pass
            try:
                st.close()
            except Exception:
                pass
