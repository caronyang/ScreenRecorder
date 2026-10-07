# -*- coding: utf-8 -*-
"""WASAPI 迴圈錄音(系統聲音) — 以 ctypes 直接呼叫 Windows Core Audio COM。

錄製「預設輸出裝置正在播放的聲音」的數位副本:
  * 任何電腦皆可用,不依賴硬體的「立體聲混音 (Stereo Mix)」裝置
  * 原始數位音量(不會過小)、立體聲輸出
  * 播放停止、引擎靜默時自動補靜音區塊,維持與影片對時

不使用任何外部相依(僅 ctypes + numpy)。
"""
from __future__ import annotations

import ctypes
import threading
import time
import uuid
from ctypes import wintypes

import numpy as np

# ---------------------------------------------------------------- 常數
CLSCTX_INPROC_SERVER = 0x1
CLSCTX_ALL = 0x17
AUDCLNT_DATAFLOW_RENDER = 0
AUDCLNT_ROLE_CONSOLE = 0
AUDCLNT_ROLE_MULTIMEDIA = 1
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
COINIT_MULTITHREADED = 0
STGM_READ = 0
VT_LPWSTR = 31
WAVE_FORMAT_IEEE_FLOAT = 3
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
IID_IAudioClient = "1CB9AD4C-DBFA-4C32-B178-C2F568A703B2"
IID_IAudioCaptureClient = "C8ADBD64-E71E-48A0-A4DE-185C395CD317"
PKEY_Device_FriendlyName = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)

_ole32 = ctypes.OleDLL("ole32.dll")
_ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_ole32.CoInitializeEx.restype = ctypes.c_long
_ole32.CoUninitialize.argtypes = []
_ole32.CoUninitialize.restype = None
_ole32.CoCreateInstance.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_ole32.CoCreateInstance.restype = ctypes.c_long
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_ole32.CoTaskMemFree.restype = None
_ole32.PropVariantClear.argtypes = [ctypes.c_void_p]
_ole32.PropVariantClear.restype = ctypes.c_long

# ---------------------------------------------------------------- COM 基礎


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> GUID:
    g = GUID()
    ctypes.memmove(ctypes.byref(g), uuid.UUID(text).bytes_le, 16)
    return g


class WAVEFORMATEXTENSIBLE(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
        ("wValidBitsPerSample", wintypes.WORD),
        ("dwChannelMask", wintypes.DWORD),
        ("SubFormat", GUID),
    ]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", wintypes.WORD), ("wReserved1", wintypes.WORD),
        ("wReserved2", wintypes.WORD), ("wReserved3", wintypes.WORD),
        ("pwszVal", wintypes.LPWSTR),
    ]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} 失敗 (HRESULT=0x{hr & 0xFFFFFFFF:08X})")


def _vt(ptr: int, idx: int, restype, *argtypes):
    """取得介面指標 vtable 第 idx 個函式(已含 this 參數)。"""
    vtbl = ctypes.c_void_p.from_address(ptr).value
    arr = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(arr[idx])


def _release(ptr: int) -> None:
    if ptr:
        try:
            _vt(ptr, 2, ctypes.c_ulong, )(ptr)  # IUnknown::Release
        except Exception:
            pass


def _co_init() -> bool:
    """目前執行緒 COM 初始化(MTA);回傳是否由本呼叫建立(需配對 uninit)。

    OleDLL 對失敗 HRESULT 會直接拋例外:若執行緒已被其他函式庫以 STA
    初始化(RPC_E_CHANGED_MODE),改以現有模式使用 COM(介面皆為 agile)。
    """
    try:
        hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        return hr >= 0   # S_FALSE=1 表示相容的已初始化
    except OSError:
        return False


def _co_uninit(owned: bool) -> None:
    if owned:
        _ole32.CoUninitialize()


def _create_enumerator() -> int:
    clsid = _guid(CLSID_MMDeviceEnumerator)
    iid = _guid(IID_IMMDeviceEnumerator)
    ptr = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(ctypes.byref(clsid), None,
                                 CLSCTX_INPROC_SERVER, ctypes.byref(iid),
                                 ctypes.byref(ptr))
    _check(hr, "建立 MMDeviceEnumerator")
    return ptr.value


def _endpoint(enum: int, flow: int) -> int:
    """取指定流程(render=0 / capture=1)的預設端點;
    沒有預設時退回第一個「使用中 (ACTIVE)」端點。"""
    fn = _vt(enum, 4, ctypes.c_long, ctypes.c_long, ctypes.c_long,
             ctypes.POINTER(ctypes.c_void_p))
    for role in (AUDCLNT_ROLE_MULTIMEDIA, AUDCLNT_ROLE_CONSOLE, 2):
        ptr = ctypes.c_void_p()
        hr = fn(enum, flow, role, ctypes.byref(ptr))
        if hr >= 0 and ptr.value:
            return ptr.value
    # 沒有預設端點(少數未設定預設裝置的系統) -> 取第一個「使用中」輸出端點。
    # 回圈錄音仍可錄到在該端點上開啟的串流。
    coll = ctypes.c_void_p()
    hr = _vt(enum, 3, ctypes.c_long, ctypes.c_long, ctypes.c_ulong,
             ctypes.POINTER(ctypes.c_void_p))(
                 enum, flow, 0x1,  # DEVICE_STATE_ACTIVE
                 ctypes.byref(coll))
    if hr < 0 or not coll.value:
        raise OSError("找不到可用的音訊輸出裝置")
    try:
        count = ctypes.c_ulong(0)
        _check(_vt(coll.value, 3, ctypes.c_long,
                   ctypes.POINTER(ctypes.c_ulong))(
                       coll.value, ctypes.byref(count)),
               "IMMDeviceCollection.GetCount")
        if count.value == 0:
            raise OSError("沒有使用中的音訊輸出裝置")
        dev = ctypes.c_void_p()
        _check(_vt(coll.value, 4, ctypes.c_long, ctypes.c_ulong,
                   ctypes.POINTER(ctypes.c_void_p))(
                       coll.value, 0, ctypes.byref(dev)),
               "IMMDeviceCollection.Item")
        if not dev.value:
            raise OSError("沒有使用中的音訊輸出裝置")
        return dev.value
    finally:
        _release(coll.value)


def _get_device(enum: int, device_id: str) -> int:
    """以裝置 ID 取得 IMMDevice。"""
    ptr = ctypes.c_void_p()
    _check(_vt(enum, 5, ctypes.c_long, ctypes.c_wchar_p,
               ctypes.POINTER(ctypes.c_void_p))(
                   enum, device_id, ctypes.byref(ptr)),
           "IMMDeviceEnumerator.GetDevice")
    if not ptr.value:
        raise OSError(f"找不到指定端點: {device_id}")
    return ptr.value


def _device_id(dev: int) -> str:
    """IMMDevice -> 裝置 ID 字串(供指定端點開啟用)。"""
    p = ctypes.c_void_p()
    _check(_vt(dev, 5, ctypes.c_long,
               ctypes.POINTER(ctypes.c_void_p))(dev, ctypes.byref(p)),
           "IMMDevice.GetId")
    try:
        return ctypes.wstring_at(p.value) if p.value else ""
    finally:
        if p.value:
            _ole32.CoTaskMemFree(p)


def _device_friendly_name(dev: int) -> str:
    """IMMDevice -> 裝置名稱(失敗回傳空字串)。"""
    try:
        ps = ctypes.c_void_p()
        hr = _vt(dev, 4, ctypes.c_long, ctypes.c_ulong,
                 ctypes.POINTER(ctypes.c_void_p))(dev, STGM_READ,
                                                  ctypes.byref(ps))
        _check(hr, "OpenPropertyStore")
        key = PROPERTYKEY()
        ctypes.memmove(ctypes.byref(key), uuid.UUID(
            PKEY_Device_FriendlyName[0]).bytes_le, 16)
        key.pid = PKEY_Device_FriendlyName[1]
        pv = PROPVARIANT()
        hr = _vt(ps.value, 5, ctypes.c_long, ctypes.c_void_p,
                 ctypes.c_void_p)(ps.value, ctypes.byref(key),
                                  ctypes.byref(pv))
        name = ""
        if hr >= 0:
            if pv.vt == VT_LPWSTR and pv.pwszVal:
                name = pv.pwszVal
            _ole32.PropVariantClear(ctypes.byref(pv))
        _release(ps.value)
        return name
    except Exception:
        return ""


def _parse_mix_format(p: int) -> tuple[int, int, bool]:
    """WAVEFORMATEX* -> (取樣率, 聲道數, 是否 float32)。"""
    fmt = ctypes.cast(p, ctypes.POINTER(WAVEFORMATEXTENSIBLE)).contents
    rate = int(fmt.nSamplesPerSec)
    ch = int(fmt.nChannels)
    if fmt.wFormatTag == WAVE_FORMAT_IEEE_FLOAT:
        is_float = True
    elif fmt.wFormatTag == WAVE_FORMAT_EXTENSIBLE and fmt.cbSize >= 22:
        is_float = fmt.SubFormat.Data1 == WAVE_FORMAT_IEEE_FLOAT
    else:
        is_float = False
    return rate, ch, is_float


# ---------------------------------------------------------------- 迴圈串流

class LoopbackStream:
    """擷取預設輸出裝置播放中的聲音(迴圈錄音)。

    open() -> (取樣率, 聲道數);start(on_block) 啟動背景執行緒,
    以 np.float32 (frames, channels) 區塊推播給 on_block。
    """

    SILENCE_THRESHOLD = 0.15   # 落後牆鐘超過此秒數就補靜音
    SILENCE_MAX_FILL = 0.25    # 每次最多補多少秒

    def __init__(self) -> None:
        self.samplerate = 0
        self.channels = 0
        self.device_name = ""
        self.last_error: Exception | None = None
        self._client: int = 0
        self._capture: int = 0
        self._mix_ch = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_block = None
        self._started = False

    # ---- 生命週期
    def open(self, device_id: str | None = None, *,
             loopback: bool = True) -> tuple[int, int]:
        """開啟端點。

        loopback=True (生產路徑):錄「輸出端點」播放中的聲音(需 LOOPBACK 旗標)。
        loopback=False (測試路徑):對「輸入端點」以一般擷取模式開啟,
          用來在沒有可用輸出裝置的機器上驗證同一套擷取/推播機制。
        """
        owned = _co_init()
        try:
            enum = _create_enumerator()
            try:
                if device_id is not None:
                    dev = _get_device(enum, device_id)
                else:
                    # AUDCLNT_DATAFLOW_CAPTURE = 1
                    flow = (AUDCLNT_DATAFLOW_RENDER if loopback else 1)
                    dev = _endpoint(enum, flow)
            finally:
                _release(enum)
            try:
                self.device_name = _device_friendly_name(dev)
                # IAudioClient
                client = ctypes.c_void_p()
                iid = _guid(IID_IAudioClient)
                _check(_vt(dev, 3, ctypes.c_long, ctypes.c_void_p,
                           ctypes.c_ulong, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_void_p))(
                               dev, ctypes.byref(iid), CLSCTX_ALL,
                               None, ctypes.byref(client)),
                       "Activate(IAudioClient)")
                self._client = client.value
            finally:
                _release(dev)

            # 混音格式:直接以系統回傳的原始格式指標初始化(迴圈要求格式一致)
            mixp = ctypes.c_void_p()
            _check(_vt(self._client, 8, ctypes.c_long,
                       ctypes.POINTER(ctypes.c_void_p))(
                           self._client, ctypes.byref(mixp)),
                   "GetMixFormat")
            try:
                rate, ch, is_float = _parse_mix_format(mixp.value)
                if not is_float:
                    raise OSError("混音格式非 float32,不支援")
                if ch < 1 or rate < 8000:
                    raise OSError(f"混音格式異常: rate={rate} ch={ch}")
                _check(_vt(self._client, 3, ctypes.c_long, ctypes.c_long,
                           ctypes.c_ulong, ctypes.c_longlong, ctypes.c_longlong,
                           ctypes.c_void_p, ctypes.c_void_p)(
                               self._client, AUDCLNT_SHAREMODE_SHARED,
                               (AUDCLNT_STREAMFLAGS_LOOPBACK if loopback
                                else 0),
                               20_000_000, 0,
                               mixp, None),
                       "IAudioClient.Initialize")
            finally:
                _ole32.CoTaskMemFree(mixp)

            cap = ctypes.c_void_p()
            iid2 = _guid(IID_IAudioCaptureClient)
            _check(_vt(self._client, 14, ctypes.c_long, ctypes.c_void_p,
                       ctypes.POINTER(ctypes.c_void_p))(
                           self._client, ctypes.byref(iid2),
                           ctypes.byref(cap)),
                   "GetService(IAudioCaptureClient)")
            self._capture = cap.value

            self.samplerate = rate
            self._mix_ch = ch
            self.channels = 2 if ch > 2 else ch   # >2 聲道錄製時降混為立體聲
            return rate, self.channels
        finally:
            _co_uninit(owned)

    def start(self, on_block) -> None:
        if self._client == 0:
            raise OSError("尚未 open()")
        if self._started:
            return
        self._started = True
        self._on_block = on_block
        self._stop.clear()
        self._thread = threading.Thread(target=self._run,
                                        name="wasapi-loopback", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        th, self._thread = self._thread, None
        if th is not None and th.is_alive():
            th.join(timeout=2.0)
        if self._client:
            try:   # 未啟動時 Stop 回傳錯誤可忽略
                _vt(self._client, 11, ctypes.c_long)(self._client)
            except Exception:
                pass
        cap, self._capture = self._capture, 0
        client, self._client = self._client, 0
        _release(cap)
        _release(client)
        self._started = False

    # ---- 擷取執行緒
    def _run(self) -> None:
        owned = _co_init()
        try:
            _check(_vt(self._client, 10, ctypes.c_long)(
                self._client), "IAudioClient.Start")
            rate, ch = self.samplerate, self._mix_ch
            out_ch = self.channels
            t0 = time.monotonic()
            delivered = 0
            g_next = _vt(self._capture, 5, ctypes.c_long,
                         ctypes.POINTER(ctypes.c_ulong))
            g_buf = _vt(self._capture, 3, ctypes.c_long, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_ulong),
                        ctypes.POINTER(ctypes.c_ulong),
                        ctypes.POINTER(ctypes.c_ulonglong),
                        ctypes.POINTER(ctypes.c_ulonglong))
            r_buf = _vt(self._capture, 4, ctypes.c_long, ctypes.c_ulong)
            while not self._stop.is_set():
                alive = True
                try:
                    while alive:
                        npk = ctypes.c_ulong(0)
                        if g_next(self._capture,
                                  ctypes.byref(npk)) < 0:
                            alive = False   # 裝置失效等
                            break
                        if npk.value == 0:
                            break
                        p_data = ctypes.c_void_p()
                        n = ctypes.c_ulong(0)
                        flags = ctypes.c_ulong(0)
                        devpos = ctypes.c_ulonglong(0)
                        qpc = ctypes.c_ulonglong(0)
                        hr = g_buf(self._capture, ctypes.byref(p_data),
                                   ctypes.byref(n), ctypes.byref(flags),
                                   ctypes.byref(devpos), ctypes.byref(qpc))
                        if hr < 0:
                            alive = False
                            break
                        frames = int(n.value)
                        try:
                            if frames > 0 and p_data.value:
                                if flags.value & AUDCLNT_BUFFERFLAGS_SILENT:
                                    block = np.zeros((frames, ch),
                                                     dtype=np.float32)
                                else:
                                    buf = (ctypes.c_float * (frames * ch)
                                           ).from_address(p_data.value)
                                    block = np.ctypeslib.as_array(buf)
                                    block = block.reshape(frames, ch)
                                delivered += self._deliver(block, out_ch)
                        finally:
                            r_buf(self._capture, frames)
                except OSError:
                    alive = False

                # 播放引擎靜默(無封包)時補靜音,維持音畫對時
                delivered += self._fill_if_lagged(rate, out_ch, t0, delivered)
                if not alive:
                    break
                self._stop.wait(0.008)
            try:
                _vt(self._client, 11, ctypes.c_long)(self._client)
            except Exception:
                pass
        except Exception as e:
            self.last_error = e   # 裝置中止:停止推送(錄影端以現有資料收尾)
        finally:
            _co_uninit(owned)

    def _fill_if_lagged(self, rate: int, out_ch: int, t0: float,
                        delivered: int) -> int:
        """牆鐘明顯落後已交付樣本數時補靜音;回傳補的樣本數。"""
        expected = int((time.monotonic() - t0) * rate)
        deficit = expected - delivered
        if deficit >= int(self.SILENCE_THRESHOLD * rate):
            fill = min(deficit, int(self.SILENCE_MAX_FILL * rate))
            if self._on_block is not None:
                self._on_block(np.zeros((fill, out_ch), dtype=np.float32))
            return fill
        return 0

    def _deliver(self, block: np.ndarray, out_ch: int) -> int:
        if block.ndim == 1:
            block = block[:, None]
        # 必須複製:釋放 WASAPI 緩衝區後原始記憶體會被引擎回收
        block = np.array(block, dtype=np.float32, copy=True, order="C")
        if block.shape[1] > out_ch:
            block = _downmix_stereo(block)
        np.clip(block, -1.0, 1.0, out=block)
        if self._on_block is not None:
            self._on_block(block)
        return block.shape[0]


def _downmix_stereo(block: np.ndarray) -> np.ndarray:
    """WASAPI 聲道順序 (FL FR FC LFE SL SR...) -> 立體聲。"""
    n = block.shape[1]
    left = block[:, 0].astype(np.float32, copy=True)
    right = block[:, 1].astype(np.float32, copy=True)
    if n >= 3:
        c = 0.7071 * block[:, 2]
        left += c
        right += c
    if n >= 4:
        lf = 0.354 * block[:, 3]
        left += lf
        right += lf
    if n >= 5:
        left += 0.7071 * block[:, 4]
    if n >= 6:
        right += 0.7071 * block[:, 5]
    for i in range(6, n):   # 其餘聲道平均分配
        if i % 2 == 0:
            left += 0.5 * block[:, i]
        else:
            right += 0.5 * block[:, i]
    out = np.stack([left, right], axis=1)
    np.clip(out, -1.0, 1.0, out=out)
    return out


def probe() -> str | None:
    """快速確認迴圈錄音可用,回傳預設輸出裝置名稱;不可用回 None。"""
    s = LoopbackStream()
    try:
        s.open()
        return s.device_name or "(預設輸出)"
    except Exception:
        return None
    finally:
        s.close()
