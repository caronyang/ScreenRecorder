# -*- coding: utf-8 -*-
"""螢幕錄影工具 — 入口。

用法:
  python main.py                     # 開啟圖形介面
  python main.py --selftest          # 背景自我測試 (供打包後驗證)
  python main.py --record L T W H 秒 輸出路徑 [--fps 30] [--audio system|none|mic:N]
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time


def _set_dpi_awareness() -> None:
    """讓 Tk / mss / dxcam 座標一致(在 Tk 初始化前呼叫)。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _log(msg: str, log_path: str | None = None) -> None:
    line = msg if msg.endswith("\n") else msg + "\n"
    try:
        sys.stdout.write(line)
        sys.stdout.flush()
    except Exception:
        pass
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def run_selftest() -> int:
    """背景錄 2.5 秒並驗證輸出;回傳 0=通過。結果寫入 temp log。"""
    log_path = os.path.join(os.environ.get("TEMP", "."), "screenrecord_selftest.log")
    if os.path.exists(log_path):
        try:
            os.remove(log_path)
        except Exception:
            pass

    def log(m: str) -> None:
        _log(m, log_path)

    log(f"selftest start {time.strftime('%Y-%m-%d %H:%M:%S')} py={sys.version.split()[0]}")
    try:
        import av
        from audio_sources import find_system_source
        from recorder import Recorder, Region

        # 主螢幕中央 640x360
        import ctypes
        u = ctypes.windll.user32
        sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        w, h = 640, 360
        region = Region((sw - w) // 2, (sh - h) // 2, w, h)
        audio = find_system_source()
        log(f"region={region} audio={audio.label if audio else None}")

        out = os.path.join(os.path.dirname(log_path), "screenrecord_selftest.mp4")
        rec = Recorder(region, fps=30, out_path=out, audio=audio)
        rec.start()
        dur = 2.5
        t0 = time.time()
        while time.time() - t0 < dur:
            time.sleep(0.5)
            if rec.error:
                break
        path = rec.stop()
        size = os.path.getsize(path)
        log(f"recorded: {path} size={size} frames={rec._frames} "
            f"audio_samples={rec._audio_samples} err={rec.error}")

        v_frames = a_samples = 0
        a_rate = 0
        with av.open(path) as c:
            if len(c.streams.audio):
                a_rate = c.streams.audio[0].sample_rate
            for fr in c.decode():
                if isinstance(fr, av.AudioFrame):
                    a_samples += fr.samples
                else:
                    v_frames += 1

        ok = True
        if v_frames < 20:
            log(f"FAIL: video frames={v_frames} < 20"); ok = False
        if size < 20000:
            log(f"FAIL: size={size} < 20000"); ok = False
        if audio and a_rate == 0:
            log("FAIL: audio stream missing"); ok = False
        if audio and a_samples / max(a_rate, 1) < 1.5:
            log(f"FAIL: audio too short {a_samples}/{a_rate}"); ok = False
        log(f"video_frames={v_frames} audio_samples={a_samples} "
            f"({a_samples/max(a_rate,1):.2f}s)")
        log("RESULT: " + ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    except Exception as e:
        import traceback
        log("FAIL: exception " + repr(e))
        log(traceback.format_exc())
        return 1


def run_cli_record(args) -> int:
    from audio_sources import discover_sources
    from recorder import Recorder, RecorderError, Region

    region = Region(args.left, args.top, args.width, args.height)
    sources = discover_sources()
    audio = None
    if args.audio == "system":
        audio = next((s for s in sources if s.is_system), None)
        if audio is None:
            print("No system audio source found (stereo mix / loopback)")
            return 2
    elif args.audio.startswith("mic:"):
        want = args.audio
        audio = next((s for s in sources if s.key == want), None)
        if audio is None:
            print(f"Microphone not found: {want}")
            return 2
    elif args.audio != "none":
        print(f"Invalid --audio value: {args.audio}")
        return 2

    rec = Recorder(region, args.fps, args.output, audio)
    try:
        rec.start()
    except RecorderError as e:
        print(f"Failed to start: {e}")
        return 1
    print(f"Recording {args.seconds}s -> {args.output}")
    try:
        t0 = time.time()
        while time.time() - t0 < args.seconds:
            time.sleep(0.5)
            print(f"  {rec.elapsed:.1f}s frames={rec._frames} "
                  f"audio={rec._audio_samples} err={rec.error}", flush=True)
            if rec.error:
                break
        path = rec.stop()
        print(f"Done: {path} ({os.path.getsize(path)} bytes)")
        return 0
    except RecorderError as e:
        print(f"Recording failed: {e}")
        return 1


def _fix_stdio() -> None:
    """視窗版 exe 沒有 console 時 sys.stdout/stderr 是 None,
    直接 print/reconfigure 會崩潰 -> 補上無輸出的替代流。"""
    class _NullStream:
        encoding = "utf-8"
        errors = "replace"

        def write(self, *args, **kwargs):
            return 0

        def flush(self):
            pass

        def reconfigure(self, **kwargs):
            pass

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, _NullStream())
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                import io
                buf = getattr(stream, "buffer", None)
                if buf is not None:
                    setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8",
                                                        errors="replace"))
            except Exception:
                pass


def main() -> int:
    _set_dpi_awareness()
    _fix_stdio()

    p = argparse.ArgumentParser(
        description="Screen recorder with region selection and audio")
    p.add_argument("--selftest", action="store_true",
                   help="headless self test (used to verify packaged builds)")
    p.add_argument("--record", action="store_true",
                   help="CLI recording: --record L T W H seconds output.mp4")
    p.add_argument("--left", type=int, default=0)
    p.add_argument("--top", type=int, default=0)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=360)
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--output", default="record_out.mp4")
    p.add_argument("--fps", type=int, default=30, choices=[15, 24, 25, 30, 60])
    p.add_argument("--audio", default="system",
                   help="system | none | mic:<index>")
    p.add_argument("--lang", choices=["en", "zh"],
                   help="UI language (en = English, zh = 繁體中文); "
                        "default: en, choice is remembered")
    args = p.parse_args()

    if args.lang:
        import i18n
        i18n.set_lang(args.lang)

    if args.selftest:
        return run_selftest()
    if args.record:
        return run_cli_record(args)

    from gui import run_app
    run_app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
