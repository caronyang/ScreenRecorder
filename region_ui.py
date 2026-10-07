# -*- coding: utf-8 -*-
"""全螢幕框選錄影區域 + 錄影範圍虛線提示框。

框選介面(兩層視窗):
  - 底層 dim : 半透明黑色遮罩 (-alpha + -transparentcolor 同時生效),
               整個螢幕都「看得到」,拖曳出的選取區完全穿透顯示原畫面。
  - 上層 chrome: 不套 alpha 的亮色層,只畫提示文字、十字線、選取框線,
               保持清晰銳利。
錄影範圍框(RegionOutline):
  - 選完區域後顯示在錄影區外側的綠色虛線框,錄影期間持續保留。
  - WS_EX_TRANSPARENT 完全滑鼠穿透,不影響任何操作。
"""
from __future__ import annotations

import ctypes

from i18n import t, ui_font
from recorder import Region

MASK = "#ff00ff"       # 穿透色 (Windows transparentcolor)
DIM = "#000000"        # 遮罩底色
ALPHA = 0.45           # 遮罩強度:0.45 = 底下畫面保持清晰可見
ACCENT = "#00e676"     # 選取框 / 範圍框顏色
MIN_SIZE = 32

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000


def virtual_screen() -> tuple[int, int, int, int]:
    """回傳 (left, top, width, height),涵蓋所有螢幕。"""
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
            u.GetSystemMetrics(78), u.GetSystemMetrics(79))


def _set_click_through(win) -> None:
    """設定視窗樣式:滑鼠完全穿透、不搶焦點、不出現在工作列。"""
    user32 = ctypes.windll.user32
    for hwnd in (win.winfo_id(), user32.GetParent(win.winfo_id())):
        if not hwnd:
            continue
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, _GWL_EXSTYLE,
            style | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE)


# ---------------------------------------------------------------- 框選介面

def select_region(on_done, on_cancel=None) -> None:
    """開啟框選介面;完成時 on_done(Region),取消時 on_cancel()。"""
    import tkinter as tk

    vs_left, vs_top, vs_w, vs_h = virtual_screen()
    geo = f"{vs_w}x{vs_h}{vs_left:+d}{vs_top:+d}"
    state = {"start": None}

    # ---- 底層:半透明遮罩(可看見整個螢幕),選取區穿透
    dim = tk.Toplevel()
    dim.overrideredirect(True)
    dim.attributes("-topmost", True)
    dim.attributes("-transparentcolor", MASK)   # 順序:先 colorkey
    dim.attributes("-alpha", ALPHA)             # 再 alpha -> 兩者同時生效
    dim.geometry(geo)
    dcanvas = tk.Canvas(dim, bg=DIM, highlightthickness=0, cursor="crosshair")
    dcanvas.pack(fill="both", expand=True)

    # ---- 上層:亮色 chrome(提示文字 / 十字線 / 選取框),不套 alpha
    chrome = tk.Toplevel()
    chrome.overrideredirect(True)
    chrome.attributes("-topmost", True)
    chrome.attributes("-transparentcolor", MASK)
    chrome.geometry(geo)
    ccanvas = tk.Canvas(chrome, bg=MASK, highlightthickness=0,
                        cursor="crosshair")
    ccanvas.pack(fill="both", expand=True)

    # ---- 繪製
    def draw_idle() -> None:
        dcanvas.delete("all")              # 純遮罩(無洞)
        ccanvas.delete("all")
        cx, cy = vs_w // 2, vs_h // 2
        ccanvas.create_text(cx, cy - 20, text=t("sel_hint_main"),
                            fill="#ffffff", font=ui_font(17, True))
        ccanvas.create_text(cx, cy + 24, text=t("sel_hint_sub"),
                            fill="#d0d0d0", font=ui_font(12))

    def _rect(cur: tuple[int, int]):
        x0, y0 = state["start"]
        l, r = sorted((x0, cur[0]))
        t, b = sorted((y0, cur[1]))
        return l, t, r, b

    def draw_drag(cur: tuple[int, int]) -> None:
        l, t, r, b = _rect(cur)
        # 遮罩層:選取區挖洞 -> 完全顯示原畫面
        dcanvas.delete("all")
        dcanvas.create_rectangle(l, t, r, b, fill=MASK, outline="")
        # chrome 層:銳利的框線、十字線、尺寸
        ccanvas.delete("all")
        ccanvas.create_rectangle(l, t, r, b, outline=ACCENT, width=3)
        ccanvas.create_line(0, t, vs_w, t, fill=ACCENT, dash=(6, 6))
        ccanvas.create_line(l, 0, l, vs_h, fill=ACCENT, dash=(6, 6))
        w, h = r - l, b - t
        ty = t - 14 if t > 34 else b + 22
        ccanvas.create_text(max(44, l), ty, text=f"{w} x {h}", fill=ACCENT,
                            font=("Consolas", 15, "bold"), anchor="w")

    # ---- 事件(兩層都綁,任一層收到都能操作)
    def on_press(e) -> None:
        state["start"] = (e.x, e.y)
        draw_drag((e.x, e.y))

    def on_drag(e) -> None:
        if state["start"] is not None:
            draw_drag((e.x, e.y))

    def on_release(e) -> None:
        if state["start"] is None:
            return
        l, t, r, b = _rect((e.x, e.y))
        w, h = r - l, b - t
        if w < MIN_SIZE or h < MIN_SIZE:
            state["start"] = None
            draw_idle()
            ccanvas.create_text(vs_w // 2, vs_h // 2 - 20,
                                text=t("sel_too_small"),
                                fill="#ffcc80",
                                font=ui_font(17, True))
            return
        w -= w % 2
        h -= h % 2
        region = Region(vs_left + l, vs_top + t, w, h)
        _close()
        on_done(region)

    def on_key(e) -> None:
        if getattr(e, "keysym", "") == "Escape":
            _close()
            if on_cancel:
                on_cancel()

    def _close() -> None:
        for w in (dim, chrome):
            try:
                w.grab_release()
            except Exception:
                pass
            try:
                w.destroy()
            except Exception:
                pass

    for cv in (dcanvas, ccanvas):
        cv.bind("<ButtonPress-1>", on_press)
        cv.bind("<B1-Motion>", on_drag)
        cv.bind("<ButtonRelease-1>", on_release)
    dim.bind("<KeyPress>", on_key)
    chrome.bind("<KeyPress>", on_key)

    draw_idle()
    chrome.lift()          # chrome 必須蓋在 dim 之上
    dim.lift()
    chrome.lift()
    dim.focus_force()
    dim.grab_set()         # 攔截輸入直到完成或取消
    dim.protocol("WM_DELETE_WINDOW", lambda: (_close(), on_cancel and on_cancel()))


# ---------------------------------------------------------------- 範圍提示框

class RegionOutline:
    """錄影範圍的綠色虛線框:顯示在錄影區外側,滑鼠完全穿透。"""

    def __init__(self, parent, margin: int = 8, inset: int = 3):
        self._parent = parent
        self._margin = margin    # 視窗比錄影區外擴的像素
        self._inset = inset      # 虛線距視窗邊界(即錄影區外側 margin-inset 像素)
        self._win = None

    @property
    def visible(self) -> bool:
        return self._win is not None

    def show(self, region: Region) -> None:
        import tkinter as tk
        self.hide()
        m = self._margin
        w = region.width + 2 * m
        h = region.height + 2 * m
        win = tk.Toplevel(self._parent)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-transparentcolor", MASK)  # 底皆穿透,只留虛線
        win.geometry(f"{w}x{h}{region.left - m:+d}{region.top - m:+d}")
        canvas = tk.Canvas(win, bg=MASK, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        i = self._inset
        canvas.create_rectangle(i, i, w - i, h - i,
                                outline=ACCENT, width=2, dash=(7, 5))
        # 小小的尺寸標籤(錄影區左上角外側)
        win.deiconify()
        _set_click_through(win)   # 滑鼠完全穿透,不影響任何操作
        self._win = win

    def hide(self) -> None:
        win, self._win = self._win, None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def destroy(self) -> None:
        self.hide()
