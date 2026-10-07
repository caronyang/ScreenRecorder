# -*- coding: utf-8 -*-
"""Persistent settings stored in %APPDATA%\\ScreenRecorder\\config.json.

Behavior:
  - First run (file missing)  -> create the file with defaults
  - Later runs                -> load and apply the stored values
  - Corrupt / invalid values  -> fall back to defaults and rebuild the file
  The file is plain JSON and safe to edit by hand.
"""
from __future__ import annotations

import json
import os

APP_NAME = "ScreenRecorder"

DEFAULTS: dict = {
    "language": "en",       # "en" | "zh"
    "save_dir": None,       # output folder (null = auto-detect)
    "last_region": None,    # {"left","top","width","height"} or null
    "hide_main": True,      # hide main window while recording
    "open_after": True,     # open file location when finished
    "fps": 30,              # frame rate
}


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def _clean(data: dict) -> dict:
    """套用預設值並做基本型別驗證(設定檔允許手工編輯)。"""
    out = dict(DEFAULTS)
    for key, value in data.items():
        if key in DEFAULTS:
            out[key] = value

    if out["language"] not in ("en", "zh"):
        out["language"] = "en"
    if out["save_dir"] is not None and not isinstance(out["save_dir"], str):
        out["save_dir"] = None

    region = out["last_region"]
    if not isinstance(region, dict) or not all(
            isinstance(region.get(k), int) for k in
            ("left", "top", "width", "height")):
        out["last_region"] = None

    out["hide_main"] = bool(out["hide_main"])
    out["open_after"] = bool(out["open_after"])
    try:
        out["fps"] = int(out["fps"])
    except (TypeError, ValueError):
        out["fps"] = DEFAULTS["fps"]
    return out


def _decode(raw: bytes) -> str:
    """容錯解碼:支援 UTF-8(含 BOM)、UTF-16(含 BOM)與 ANSI(cp950)。

    手工編輯者常用記事本另存為各種編碼,不能因此把使用者設定重建掉。
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp950")   # 舊版記事本「ANSI」;仍失敗會被 ValueError 接住


def _load() -> tuple[dict, bool]:
    """回傳 (設定內容, 檔案是否有效)。檔案不存在或損毀 -> (預設值, False)。"""
    try:
        with open(config_path(), "rb") as f:
            data = json.loads(_decode(f.read()))
        if not isinstance(data, dict):
            raise ValueError("config root must be an object")
        return _clean(data), True
    except (OSError, ValueError):
        return dict(DEFAULTS), False


_data: dict
_valid: bool
_data, _valid = _load()


def get(key: str):
    """讀取一項設定(未知鍵回傳 None)。"""
    return _data.get(key, DEFAULTS.get(key))


def update(**values) -> None:
    """更新多項設定(只接受已知鍵,暫存於記憶體)。"""
    for key, value in values.items():
        if key in DEFAULTS:
            _data[key] = value


def save() -> bool:
    """寫入設定檔(建立資料夾、暫存檔原子取代);失敗回傳 False 不中斷程式。"""
    try:
        os.makedirs(config_dir(), exist_ok=True)
        path = config_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def reload() -> tuple[dict, bool]:
    """重新讀取設定檔(測試/除錯用)。"""
    global _data, _valid
    _data, _valid = _load()
    return _data, _valid


# 首次執行:沒有設定檔(或損毀)就建立一份
if not _valid:
    save()
