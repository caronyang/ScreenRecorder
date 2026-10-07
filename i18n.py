# -*- coding: utf-8 -*-
"""i18n — UI language strings (English default / 繁體中文).

The selected language is persisted in the settings file
(%APPDATA%\\ScreenRecorder\\config.json, key "language"); missing/invalid = English.
"""
from __future__ import annotations

import config

DEFAULT_LANG = "en"

# Native names shown in the language picker (never translated)
LANG_NAMES = {"en": "English", "zh": "繁體中文"}

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "app_title": "Screen Recorder",
        # menu bar
        "menu_settings": "Settings",
        "menu_language": "Language",
        # main window
        "region_label": "Recording region",
        "btn_select_region": "Select region",
        "audio_label": "Audio source",
        "fps_label": "Frame rate",
        "dir_label": "Save to",
        "btn_browse": "Browse",
        "chk_hide": "Hide main window while recording",
        "chk_open": "Open file location when finished",
        "lang_label": "Language",
        "btn_start": "● Start recording",
        "btn_stop": "■ Stop recording",
        "region_none": "Not selected",
        # floating bar (during recording)
        "bar_rec": "● REC",
        "bar_stop": "■ Stop",
        # status lines
        "status_ready": "Ready — select a region to record",
        "status_selected": "Region {w} × {h} selected — press “Start recording”",
        "status_select_canceled": "Selection canceled",
        "status_keep_region": "Keeping previous region ({w} × {h})",
        "status_start_failed": "Failed to start: {err}",
        "status_recording": "Recording · Audio: {src}",
        "status_saved": "Saved: {path} ({size})",
        "status_failed": "Recording failed: {err}",
        "audio_none_disp": "None",
        # dialogs
        "dlg_no_region_title": "Select a region",
        "dlg_no_region_msg": "Please select the region you want to record first.",
        "dlg_cannot_start": "Cannot start recording",
        "dlg_failed": "Recording failed",
        "dlg_quit_title": "Recording in progress",
        "dlg_quit_msg": "A recording is in progress. Stop recording and quit?",
        "file_dialog_title": "Choose a save folder",
        "out_prefix": "ScreenRecording_",
        # region selection overlay
        "sel_hint_main": "Drag to select a recording region",
        "sel_hint_sub": "Hold the left button and drag · ESC to cancel",
        "sel_too_small": "Selection too small — please try again",
        # audio sources
        "src_none": "None",
        "src_system": "System audio",
        "src_system_dev": "System audio ({name})",
        # errors (shown in dialogs / status)
        "err_open_device": 'Cannot open audio device "{label}": {err}',
        "err_dxcam_init": "Cannot initialize screen capture "
                          "(GPU does not support Desktop Duplication)",
        "err_dxcam_region": "Capture size mismatch: {detail}",
        "err_already_recording": "Already recording",
        "err_create_output": "Failed to create output file: {err}",
        "err_not_started": "Recording was not started",
        "err_no_frames": "No frames were captured — recording failed",
        "err_recording": "Recording error: {err}",
    },
    "zh": {
        "app_title": "螢幕錄影工具",
        "menu_settings": "設定",
        "menu_language": "語言",
        "region_label": "錄影區域",
        "btn_select_region": "框選區域",
        "audio_label": "音訊來源",
        "fps_label": "影格率",
        "dir_label": "儲存位置",
        "btn_browse": "瀏覽",
        "chk_hide": "錄影時隱藏主視窗",
        "chk_open": "完成後開啟檔案位置",
        "lang_label": "語言",
        "btn_start": "● 開始錄影",
        "btn_stop": "■ 停止錄影",
        "region_none": "尚未選擇",
        "bar_rec": "● 錄影中",
        "bar_stop": "■ 停止",
        "status_ready": "就緒 — 請先框選要錄影的區域",
        "status_selected": "已框選 {w} × {h},按「開始錄影」",
        "status_select_canceled": "已取消框選",
        "status_keep_region": "沿用原本區域 ({w} × {h})",
        "status_start_failed": "開始失敗:{err}",
        "status_recording": "錄影中 · 音訊:{src}",
        "status_saved": "已儲存:{path}({size})",
        "status_failed": "錄影失敗:{err}",
        "audio_none_disp": "無",
        "dlg_no_region_title": "請先框選",
        "dlg_no_region_msg": "請先框選要錄影的區域範圍。",
        "dlg_cannot_start": "無法開始錄影",
        "dlg_failed": "錄影失敗",
        "dlg_quit_title": "錄影中",
        "dlg_quit_msg": "目前正在錄影,停止錄影並結束程式?",
        "file_dialog_title": "選擇儲存資料夾",
        "out_prefix": "螢幕錄影_",
        "sel_hint_main": "拖曳滑鼠框選錄影區域",
        "sel_hint_sub": "按住左鍵拖曳 · ESC 取消",
        "sel_too_small": "區塊太小,請重新框選",
        "src_none": "不錄音",
        "src_system": "系統聲音",
        "src_system_dev": "系統聲音 ({name})",
        "err_open_device": "無法開啟音訊裝置「{label}」:{err}",
        "err_dxcam_init": "dxcam 無法建立 (顯示卡不支援 Desktop Duplication)",
        "err_dxcam_region": "dxcam 區域不符: {detail}",
        "err_already_recording": "已在錄影中",
        "err_create_output": "建立輸出檔失敗: {err}",
        "err_not_started": "尚未開始錄影",
        "err_no_frames": "沒有擷取到任何畫面,錄影失敗",
        "err_recording": "錄影過程發生錯誤: {err}",
    },
}


def _load_lang() -> str:
    lang = config.get("language")
    return lang if lang in _STRINGS else DEFAULT_LANG


_lang = _load_lang()


def get_lang() -> str:
    return _lang


def set_lang(lang: str) -> None:
    """Switch UI language and persist it to the settings file."""
    global _lang
    _lang = lang if lang in _STRINGS else DEFAULT_LANG
    config.update(language=_lang)
    config.save()


def t(key: str, **kwargs) -> str:
    """Translate a key in the current language (falls back to English)."""
    table = _STRINGS.get(_lang) or _STRINGS[DEFAULT_LANG]
    text = table.get(key) or _STRINGS[DEFAULT_LANG].get(key, key)
    try:
        return text.format(**kwargs)
    except Exception:
        return text


def ui_font(size: int, bold: bool = False) -> tuple:
    """UI font family follows the language (Segoe UI / 微軟正黑體)."""
    family = "Segoe UI" if _lang == "en" else "Microsoft JhengHei UI"
    return (family, size, "bold") if bold else (family, size)
