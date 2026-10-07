# -*- coding: utf-8 -*-
"""產生程式圖示:深色圓角磁磚 + 白色螢幕框選圖形 + 紅色錄影點。

素材:Google Material Symbols「screenshot_monitor」SVG (Apache License 2.0)
      https://github.com/google/material-design-icons
輸出:assets/icon.ico (多尺寸)、assets/icon.png (256px,給 Tk 窗口圖示)

用法: python tools/make_icon.py
"""
import io
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "assets")
SVG = os.path.join(ASSETS, "screenshot_monitor.svg")

S = 1024          # 繪製尺寸(4x 超取樣,最後縮到 256)
FINAL = 256


def rounded_tile(size: int = S, radius: int = 210) -> Image.Image:
    """深藍灰垂直漸層圓角磁磚。"""
    top = np.array([64, 71, 86], dtype=float)    # 上緣較亮的藍灰
    bot = np.array([23, 26, 33], dtype=float)    # 下緣近黑
    t = np.linspace(0.0, 1.0, size, dtype=float)[:, None, None]
    rgb = (top[None, None, :] * (1 - t) + bot[None, None, :] * t)
    rgb = np.broadcast_to(rgb, (size, size, 3)).astype(np.uint8).copy()

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=radius, fill=255)
    tile = np.dstack([rgb, np.array(mask)])

    # 細內框線(淡淡的高光邊)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    inset = 7
    d.rounded_rectangle([inset, inset, size - 1 - inset, size - 1 - inset],
                        radius=radius - inset, outline=(255, 255, 255, 30),
                        width=7)
    base = Image.fromarray(tile, "RGBA")
    return Image.alpha_composite(base, layer)


def load_glyph(size: int) -> Image.Image:
    """載入 Material screenshot_monitor SVG,渲染後上色為白色(保留邊緣)。"""
    from reportlab.graphics import renderPM
    from svglib.svglib import svg2rlg

    drawing = svg2rlg(SVG)
    if drawing is None:
        raise RuntimeError(f"SVG 解析失敗: {SVG}")
    k = size / max(drawing.width, drawing.height)
    drawing.scale(k, k)
    drawing.width *= k
    drawing.height *= k
    # renderPM 輸出不透明 RGB(黑圖形/白底) -> 用亮度轉成 alpha
    png = renderPM.drawToString(drawing, fmt="PNG", bg=0xFFFFFF)
    img = Image.open(io.BytesIO(png)).convert("L")
    lum = np.array(img)
    alpha = (255 - lum).astype(np.uint8)      # 黑色圖形 -> 不透明
    rgba = np.zeros((*lum.shape, 4), dtype=np.uint8)
    rgba[..., 0] = 255
    rgba[..., 1] = 255
    rgba[..., 2] = 255
    rgba[..., 3] = alpha
    return Image.fromarray(rgba, "RGBA")


def record_dot(center: tuple[int, int], radius: int) -> Image.Image:
    """紅色徑向漸層錄影點 + 深色分隔環 + 高光。"""
    pad = radius + 40
    size = pad * 2
    yy, xx = np.mgrid[0:size, 0:size]
    cx = cy = pad
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)

    # 分隔環(深色,讓紅點從白色圖形中跳出來)
    ring = np.zeros((size, size, 4), dtype=np.uint8)
    ring_mask = dist <= radius + 26
    ring[..., 0][ring_mask] = 24
    ring[..., 1][ring_mask] = 27
    ring[..., 2][ring_mask] = 34
    ring[..., 3][ring_mask] = 255

    # 紅點:中心亮 -> 邊緣深
    dot = np.zeros((size, size, 4), dtype=np.uint8)
    k = np.clip(dist / radius, 0, 1)
    c_hi = np.array([255, 106, 96], dtype=float)
    c_lo = np.array([211, 40, 40], dtype=float)
    grad = c_hi[None, None, :] * (1 - k[..., None]) + c_lo[None, None, :] * k[..., None]
    dot[..., :3] = grad.astype(np.uint8)
    dot_mask = dist <= radius
    dot[..., 3][dot_mask] = 255

    out = Image.fromarray(ring, "RGBA")
    out = Image.alpha_composite(out, Image.fromarray(dot, "RGBA"))

    # 高光(左上方的小橢圓,柔化)
    gloss = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gloss)
    gx, gy = cx - radius * 0.38, cy - radius * 0.45
    gd.ellipse([gx - radius * 0.42, gy - radius * 0.26,
                gx + radius * 0.42, gy + radius * 0.26],
               fill=(255, 255, 255, 90))
    gloss = gloss.filter(ImageFilter.GaussianBlur(radius * 0.10))
    out = Image.alpha_composite(out, gloss)

    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(out, (center[0] - pad, center[1] - pad), out)
    return canvas


def main() -> int:
    if not os.path.exists(SVG):
        print(f"找不到 {SVG}")
        return 1

    tile = rounded_tile()

    # 白色螢幕框選圖形:置中偏左上,讓右下角留給錄影點
    g_size = int(S * 0.60)
    glyph = load_glyph(g_size)
    gx = int(S * 0.17)
    gy = int(S * 0.13)
    tile.alpha_composite(glyph, (gx, gy))

    # 紅色錄影點:疊在磁磚右下角(不遮住框選角標記)
    tile.alpha_composite(record_dot((int(S * 0.765), int(S * 0.765)),
                                    int(S * 0.12)))

    # 縮小輸出(4x 超取樣 -> 平滑邊緣)
    master = tile.resize((FINAL, FINAL), Image.LANCZOS)

    png_path = os.path.join(ASSETS, "icon.png")
    master.save(png_path)
    print("written:", png_path)

    ico_path = os.path.join(ASSETS, "icon.ico")
    master.save(ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                 (64, 64), (128, 128), (256, 256)])
    print("written:", ico_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
