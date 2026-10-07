# -*- coding: utf-8 -*-
"""主視窗 GUI 與錄影中的浮動控制列。

設定(工具列 > 設定)與上次使用狀態都存在設定檔(config.py):
語言、儲存位置、最後選取的錄影區域、影格率、隱藏主視窗、完成後開啟檔案位置。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import config
import i18n
import region_ui
from audio_sources import AudioSource, discover_sources
from i18n import LANG_NAMES, t, ui_font
from recorder import Recorder, RecorderError, Region, fmt_duration, fmt_size

FPS_OPTIONS = ["30 FPS", "60 FPS", "25 FPS", "24 FPS", "15 FPS"]

# 設定選單 entry 索引(建立後固定)
_IDX_LANG, _IDX_SEP, _IDX_HIDE, _IDX_OPEN = 0, 1, 2, 3


def _resource_path(rel: str) -> str:
    """打包後 (_MEIPASS) 與原始碼兩種執行方式的資源路徑。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def default_output_dir() -> str:
    home = os.path.expanduser("~")
    for name in ("Videos", "Documents", "Desktop"):
        base = os.path.join(home, name)
        if os.path.isdir(base):
            return base
    return home


def _load_saved_region() -> Region | None:
    """載入上次的錄影區域;格式錯誤或超出目前螢幕時捨棄。"""
    raw = config.get("last_region")
    if not isinstance(raw, dict):
        return None
    try:
        region = Region(int(raw["left"]), int(raw["top"]),
                        int(raw["width"]), int(raw["height"])).normalized()
    except (KeyError, TypeError, ValueError):
        return None
    if region.width < 32 or region.height < 32:
        return None
    vl, vt, vw, vh = region_ui.virtual_screen()
    if (region.left < vl or region.top < vt
            or region.right > vl + vw or region.bottom > vt + vh):
        return None          # 螢幕配置已變更 -> 捨棄舊區域
    return region


def open_in_explorer(path: str) -> None:
    """在檔案總管中選取該檔案。"""
    try:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    except Exception:
        pass


class FloatingBar:
    """錄影時的浮動控制列(主視窗隱藏時使用),可拖曳。"""

    def __init__(self, parent: tk.Tk, on_stop):
        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.withdraw()

        box = tk.Frame(self.win, bg="#202124", highlightbackground="#444",
                       highlightthickness=1, padx=10, pady=8)
        box.pack(fill="both", expand=True)

        self.time_var = tk.StringVar(value="00:00:00")
        left = tk.Frame(box, bg="#202124")
        left.pack(side="left", padx=(0, 12))
        self.lbl_rec = tk.Label(left, text=t("bar_rec"), fg="#ff5252",
                                bg="#202124", font=ui_font(10, True))
        self.lbl_rec.pack(anchor="w")
        tk.Label(left, textvariable=self.time_var, fg="#ffffff", bg="#202124",
                 font=("Consolas", 15, "bold")).pack(anchor="w")

        self.btn_stop = tk.Button(box, text=t("bar_stop"), command=on_stop,
                                  bg="#ff5252", fg="white",
                                  activebackground="#ff1744",
                                  activeforeground="white", relief="flat",
                                  font=ui_font(10, True),
                                  padx=14, pady=6, cursor="hand2")
        self.btn_stop.pack(side="right")

        # 拖曳移動
        self._off = (0, 0)
        for w in (box, left):
            w.bind("<ButtonPress-1>", self._press)
            w.bind("<B1-Motion>", self._motion)
        box.bind("<ButtonPress-1>", self._press)
        box.bind("<B1-Motion>", self._motion)

    def apply_lang(self) -> None:
        self.lbl_rec.config(text=t("bar_rec"), font=ui_font(10, True))
        self.btn_stop.config(text=t("bar_stop"), font=ui_font(10, True))

    def _press(self, e):
        self._off = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _motion(self, e):
        x = e.x_root - self._off[0]
        y = e.y_root - self._off[1]
        self.win.geometry(f"+{x}+{y}")

    def show(self) -> None:
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        self.win.geometry(f"+{sw - w - 40}+{sh - h - 70}")
        self.win.deiconify()

    def hide(self) -> None:
        self.win.withdraw()

    def set_time(self, text: str) -> None:
        self.time_var.set(text)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(t("app_title"))
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        # 從設定檔還原上次使用狀態
        self.region: Region | None = _load_saved_region()
        self.recorder: Recorder | None = None
        saved = config.get("save_dir")
        self.out_dir = saved if saved and os.path.isdir(saved) \
            else default_output_dir()
        self.sources: list[AudioSource] = discover_sources()
        self.bar = FloatingBar(root, self.stop_recording)
        self.outline = region_ui.RegionOutline(root)   # 錄影範圍虛線框

        fps_text = f"{config.get('fps')} FPS"
        if fps_text not in FPS_OPTIONS:
            fps_text = FPS_OPTIONS[0]

        self.region_var = tk.StringVar(value=t("region_none"))
        self.status_var = tk.StringVar(value=t("status_ready"))
        self.timer_var = tk.StringVar(value="00:00:00")
        self.fps_var = tk.StringVar(value=fps_text)
        self.dir_var = tk.StringVar(value=self.out_dir)
        self.hide_var = tk.IntVar(value=1 if config.get("hide_main") else 0)
        self.open_var = tk.IntVar(value=1 if config.get("open_after") else 0)
        self.audio_var = tk.StringVar()

        self._build()
        self._build_menu()
        root.after(200, self._poll)

        # 已有錄影區域 -> 立刻標示(狀態列 + 綠色虛線框)
        self._update_region_label()
        if self.region is not None:
            self._set_status(t("status_selected", w=self.region.width,
                               h=self.region.height))
            root.after(150, self._sync_outline)

    # ---------------- 版面
    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self.root, padding=14)
        frm.grid(row=0, column=0, sticky="nsew")

        # 區域
        self.lbl_region = ttk.Label(frm, text=t("region_label"))
        self.lbl_region.grid(row=0, column=0, sticky="w", **pad)
        ttk.Label(frm, textvariable=self.region_var,
                  width=30).grid(row=0, column=1, sticky="w", **pad)
        self.sel_btn = ttk.Button(frm, text=t("btn_select_region"),
                                  command=self.choose_region)
        self.sel_btn.grid(row=0, column=2, **pad)

        # 音訊
        self.lbl_audio = ttk.Label(frm, text=t("audio_label"))
        self.lbl_audio.grid(row=1, column=0, sticky="w", **pad)
        labels = [s.label for s in self.sources]
        self.audio_box = ttk.Combobox(frm, textvariable=self.audio_var,
                                      values=labels, state="readonly", width=32)
        self.audio_box.grid(row=1, column=1, sticky="w", **pad)
        system = next((s for s in self.sources if s.is_system), None)
        first = system or self.sources[0]
        self._audio_index = self.sources.index(first)
        self.audio_var.set(first.label)
        self.audio_box.bind("<<ComboboxSelected>>", self._on_audio_select)

        # 影格率(變更即存檔)
        self.lbl_fps = ttk.Label(frm, text=t("fps_label"))
        self.lbl_fps.grid(row=2, column=0, sticky="w", **pad)
        self.fps_box = ttk.Combobox(frm, textvariable=self.fps_var,
                                    values=FPS_OPTIONS, state="readonly",
                                    width=10)
        self.fps_box.grid(row=2, column=1, sticky="w", **pad)
        self.fps_box.bind("<<ComboboxSelected>>", lambda _e: self._persist())

        # 輸出資料夾(變更即存檔)
        self.lbl_dir = ttk.Label(frm, text=t("dir_label"))
        self.lbl_dir.grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.dir_var, width=32,
                  state="readonly").grid(row=3, column=1, sticky="w", **pad)
        self.btn_browse = ttk.Button(frm, text=t("btn_browse"),
                                     command=self.choose_dir)
        self.btn_browse.grid(row=3, column=2, **pad)

        # 開始/停止
        self.rec_btn = tk.Button(frm, text=t("btn_start"), command=self.toggle,
                                 bg="#00c853", fg="white", activebackground="#00e676",
                                 activeforeground="white", relief="flat",
                                 font=ui_font(14, True),
                                 padx=26, pady=10, cursor="hand2")
        self.rec_btn.grid(row=4, column=0, columnspan=2, sticky="we",
                          padx=10, pady=(14, 4))
        self.timer_lbl = ttk.Label(frm, textvariable=self.timer_var,
                                   font=("Consolas", 16, "bold"))
        self.timer_lbl.grid(row=4, column=2, padx=10, pady=(14, 4))

        # 狀態
        ttk.Separator(frm).grid(row=5, column=0, columnspan=3,
                                sticky="we", padx=10, pady=(8, 4))
        ttk.Label(frm, textvariable=self.status_var,
                  foreground="#555").grid(row=6, column=0, columnspan=3,
                                          sticky="w", padx=10, pady=(0, 2))

    def _build_menu(self) -> None:
        """上方工具列:設定 > 語言(單選)/ 錄影選項(點擊切換並存檔)。"""
        self.menubar = tk.Menu(self.root, tearoff=0)
        self.lang_var = tk.StringVar(value=i18n.get_lang())

        self.menu_settings = tk.Menu(self.menubar, tearoff=0)
        self.menu_lang = tk.Menu(self.menu_settings, tearoff=0)
        for code, name in LANG_NAMES.items():
            self.menu_lang.add_radiobutton(
                label=name, value=code, variable=self.lang_var,
                command=lambda c=code: self._choose_lang(c))
        self.menu_settings.add_cascade(label=t("menu_language"),
                                       menu=self.menu_lang)
        self.menu_settings.add_separator()
        self.menu_settings.add_checkbutton(label=t("chk_hide"),
                                           variable=self.hide_var,
                                           command=self._persist)
        self.menu_settings.add_checkbutton(label=t("chk_open"),
                                           variable=self.open_var,
                                           command=self._persist)
        self.menubar.add_cascade(label=t("menu_settings"),
                                 menu=self.menu_settings)
        self.root.config(menu=self.menubar)

    # ---------------- 語言 / 設定
    def _choose_lang(self, lang: str) -> None:
        if lang == i18n.get_lang():
            return
        i18n.set_lang(lang)
        self.apply_lang()

    def apply_lang(self) -> None:
        """語言切換後即時更新所有可見文字(含工具列選單)。"""
        self.root.title(t("app_title"))
        self.lbl_region.config(text=t("region_label"))
        self.sel_btn.config(text=t("btn_select_region"))
        self.lbl_audio.config(text=t("audio_label"))
        self.lbl_fps.config(text=t("fps_label"))
        self.lbl_dir.config(text=t("dir_label"))
        self.btn_browse.config(text=t("btn_browse"))
        self.bar.apply_lang()

        # 工具列選單(就地更新,避免選單重建)
        self.menubar.entryconfig(0, label=t("menu_settings"))
        self.menu_settings.entryconfig(_IDX_LANG, label=t("menu_language"))
        self.menu_settings.entryconfig(_IDX_HIDE, label=t("chk_hide"))
        self.menu_settings.entryconfig(_IDX_OPEN, label=t("chk_open"))
        self.lang_var.set(i18n.get_lang())

        # 音訊清單標籤會隨語言改變 -> 以索引保留目前選取的來源
        if 0 <= self._audio_index < len(self.sources):
            self.audio_var.set(self.sources[self._audio_index].label)
        labels = [s.label for s in self.sources]
        self.audio_box.config(values=labels)

        self._update_region_label()
        running = self.recorder is not None and self.recorder.is_running
        if running:
            self.rec_btn.config(text=t("btn_stop"), font=ui_font(14, True))
            src = self._rec_audio_label()
            self._set_status(t("status_recording", src=src))
        else:
            self.rec_btn.config(text=t("btn_start"), font=ui_font(14, True))
            if self.region is not None:
                self._set_status(t("status_selected", w=self.region.width,
                                   h=self.region.height))
            else:
                self._set_status(t("status_ready"))

    def _persist(self) -> None:
        """把 GUI 管理的設定寫入設定檔。"""
        r = self.region
        config.update(
            save_dir=self.out_dir,
            hide_main=bool(self.hide_var.get()),
            open_after=bool(self.open_var.get()),
            fps=int(self.fps_var.get().split()[0]),
            last_region=({"left": r.left, "top": r.top,
                          "width": r.width, "height": r.height}
                         if r is not None else None),
        )
        config.save()

    # ---------------- 狀態
    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _update_region_label(self) -> None:
        if self.region:
            r = self.region
            self.region_var.set(f"({r.left}, {r.top}) · {r.width} × {r.height}")
        else:
            self.region_var.set(t("region_none"))

    def _on_audio_select(self, _event=None) -> None:
        idx = self.audio_box.current()
        if idx >= 0:
            self._audio_index = idx

    def _selected_source(self) -> AudioSource | None:
        label = self.audio_var.get()
        for s in self.sources:
            if s.label == label:
                return s
        if 0 <= self._audio_index < len(self.sources):
            return self.sources[self._audio_index]
        return None

    def _rec_audio_label(self) -> str:
        rec = self.recorder
        audio = getattr(rec, "audio_source", None) if rec else None
        if audio is not None and audio.enabled:
            return audio.label
        return t("audio_none_disp")

    # ---------------- 動作
    def _sync_outline(self) -> None:
        """依目前 self.region 顯示/隱藏錄影範圍虛線框。"""
        if self.region is not None:
            self.outline.show(self.region)
        else:
            self.outline.hide()

    def choose_region(self) -> None:
        self.root.withdraw()
        self.outline.hide()          # 框選期間先隱藏舊的範圍框

        def done(region: Region) -> None:
            self.region = region
            self._update_region_label()
            self._sync_outline()     # 選完立刻顯示範圍虛線框
            self._persist()          # 記住這次的區域
            self.root.deiconify()
            self.root.focus_force()
            self._set_status(t("status_selected", w=region.width,
                               h=region.height))

        def cancel() -> None:
            self._sync_outline()     # 取消 -> 還原原本的範圍框
            self.root.deiconify()
            self.root.focus_force()
            if self.region is None:
                self._set_status(t("status_select_canceled"))
            else:
                self._set_status(t("status_keep_region", w=self.region.width,
                                   h=self.region.height))

        region_ui.select_region(done, cancel)

    def choose_dir(self) -> None:
        picked = filedialog.askdirectory(initialdir=self.out_dir,
                                         title=t("file_dialog_title"))
        if picked:
            self.out_dir = picked
            self.dir_var.set(picked)
            self._persist()

    def toggle(self) -> None:
        if self.recorder is not None and self.recorder.is_running:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if self.region is None:
            messagebox.showinfo(t("dlg_no_region_title"),
                                t("dlg_no_region_msg"))
            self.choose_region()
            return

        fps = int(self.fps_var.get().split()[0])
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(self.out_dir, f"{t('out_prefix')}{ts}.mp4")
        audio = self._selected_source()

        rec = Recorder(self.region, fps, out_path, audio)
        try:
            rec.start()
        except Exception as e:
            messagebox.showerror(t("dlg_cannot_start"), str(e))
            self._set_status(t("status_start_failed", err=e))
            return

        self.recorder = rec
        self.timer_var.set("00:00:00")
        self.rec_btn.config(text=t("btn_stop"), bg="#ff5252",
                            activebackground="#ff1744")
        src = audio.label if audio and audio.enabled else t("audio_none_disp")
        self._set_status(t("status_recording", src=src))
        if self.hide_var.get():
            self.root.withdraw()
            self.bar.show()

    def stop_recording(self) -> None:
        rec = self.recorder
        if rec is None:
            return
        self.recorder = None
        try:
            path = rec.stop()
            size = os.path.getsize(path) if os.path.exists(path) else 0
            self._set_status(t("status_saved", path=path,
                               size=fmt_size(size)))
            self.timer_var.set(fmt_duration(rec.elapsed))
            if self.open_var.get():
                open_in_explorer(path)
        except Exception as e:
            messagebox.showerror(t("dlg_failed"), str(e))
            self._set_status(t("status_failed", err=e))
        finally:
            self.bar.hide()
            self.root.deiconify()
            self.rec_btn.config(text=t("btn_start"), bg="#00c853",
                                activebackground="#00e676")

    # ---------------- 週期性更新
    def _poll(self) -> None:
        rec = self.recorder
        if rec is not None and rec.is_running:
            text = fmt_duration(rec.elapsed)
            self.timer_var.set(text)
            self.bar.set_time(text)
            if rec.error is not None:
                self.stop_recording()
        self.root.after(200, self._poll)

    def on_close(self) -> None:
        rec = self.recorder
        if rec is not None and rec.is_running:
            if not messagebox.askyesno(t("dlg_quit_title"),
                                       t("dlg_quit_msg")):
                return
            self.stop_recording()
        self._persist()              # 離開前確保設定已存檔
        self.outline.destroy()
        self.root.destroy()


def run_app() -> None:
    root = tk.Tk()
    # 程式圖示(打包後從 _MEIPASS/assets 讀取)
    try:
        icon = tk.PhotoImage(file=_resource_path(os.path.join("assets", "icon.png")))
        root.iconphoto(True, icon)
        root._app_icon = icon        # 保持參照,避免被垃圾回收
    except Exception:
        pass
    App(root)
    root.mainloop()
