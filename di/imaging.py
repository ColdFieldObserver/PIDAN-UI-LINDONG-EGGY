# -*- coding: utf-8 -*-
"""像素 / 图像工具：颜色换算、渐变取色、PNG 编解码、RGBA 缩放。

图像编解码、缩放、混合、PhotoImage 转换统一交给 Pillow，删掉了原先手写的
PNG 分块解析 / 每行反滤波 / 双线性插值（约两百行）。对外函数签名与返回类型
保持不变，主模块无需改动。

仍不引用任何主题色变量、不读配置文件——纯输入决定输出，可独立测试。
"""
from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image, ImageTk

GRAD_STEPS = 96          # 渐变分级（同时让 Tk 的取色缓存命中，铺图更快）
_PPM_SEQ = [0]           # 兼容旧接口（主模块 import），Pillow 方案已不再需要临时 ppm


def _hex_rgb(color: str):
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def _rgb_hex(rgb) -> str:
    return "#%02X%02X%02X" % (rgb[0], rgb[1], rgb[2])


def _lerp(a, b, t: float):
    return (int(round(a[0] * (1.0 - t) + b[0] * t)),
            int(round(a[1] * (1.0 - t) + b[1] * t)),
            int(round(a[2] * (1.0 - t) + b[2] * t)))


def _mix_hex(bg, fg, cov: float) -> str:
    if cov <= 0.0:
        return _rgb_hex(bg)
    if cov >= 1.0:
        return _rgb_hex(fg)
    return _rgb_hex(_lerp(bg, fg, cov))


def panel_row_color(y: int, h: int, top_rgb, bottom_rgb) -> str:
    t = max(0.0, min(1.0, float(y) / max(1, h - 1)))
    t = round(t * GRAD_STEPS) / GRAD_STEPS
    col = _lerp(top_rgb, bottom_rgb, t)
    gloss = 1.0 - t / 0.45          # 顶部一点淡淡的聚光
    if gloss > 0.0:
        col = _lerp(col, (255, 255, 255), 0.12 * min(1.0, gloss))
    return _rgb_hex(col)


def _photo_from_ppm(master, w: int, h: int, body: bytes):
    """一整帧 RGB 像素 -> PhotoImage（名字沿用旧接口）"""
    return ImageTk.PhotoImage(Image.frombytes("RGB", (w, h), body), master=master)


def _png_decode(data: bytes):
    """解 8 位 RGB / RGBA PNG，返回 (宽, 高, RGB 字节)，供图片轮换素材使用。

    RGBA 图按原有语义处理：直接丢掉 alpha 通道取 RGB，不做背景合成
    （与拆分前手写解码器的行为一致，保证轮换图显示不变）。
    """
    im = Image.open(BytesIO(data))
    if im.mode == "RGBA":
        # 丢弃 alpha，只取 RGB 三通道
        r, g, b, _a = im.split()
        im = Image.merge("RGB", (r, g, b))
    elif im.mode != "RGB":
        im = im.convert("RGB")
    return im.width, im.height, im.tobytes()


def _png_decode_rgba(data: bytes):
    """解 8 位 RGBA PNG，返回 (宽, 高, RGBA 字节)，保留 alpha。"""
    im = Image.open(BytesIO(data)).convert("RGBA")
    return im.width, im.height, im.tobytes()


def _png_bytes_rgba(w: int, h: int, rows) -> bytes:
    """把逐行 RGBA 字节编成 PNG。"""
    im = Image.frombytes("RGBA", (w, h), b"".join(bytes(r) for r in rows))
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _rgba_photo(master, w: int, h: int, rows, into=None):
    """into 不为空时原地换内容：画布上已经绑着这张图，重建对象会让它消失。"""
    im = Image.frombytes("RGBA", (w, h), b"".join(bytes(r) for r in rows))
    buf = BytesIO()
    im.save(buf, format="PNG")
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    if into is not None:
        # ImageTk.PhotoImage 沿用 Tk 的 configure(data=...) 原地换内容，保持对象身份
        into.configure(data=data)
        return into
    return ImageTk.PhotoImage(im, master=master)


def _png_bytes_rgb(w: int, h: int, rows) -> bytes:
    """逐行 RGB 字节编成 PNG。"""
    im = Image.frombytes("RGB", (w, h), b"".join(bytes(r) for r in rows))
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _png_pack(w: int, h: int, color_type: int, body: bytes) -> bytes:
    """把原始行像素（每行首字节是 filter）编成 PNG。

    color_type: 2=RGB, 6=RGBA。Pillow 的 frombytes 接收的是「无 filter 字节」
    的紧凑像素，因此这里先剥掉每行开头的 filter 字节再交给 Pillow。
    """
    ch = 4 if color_type == 6 else 3
    stride = w * ch
    raw = b"".join(body[y * (stride + 1) + 1:y * (stride + 1) + 1 + stride]
                   for y in range(h))
    mode = "RGBA" if color_type == 6 else "RGB"
    im = Image.frombytes(mode, (w, h), raw)
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _blend_rgb(a: bytes, b: bytes, t: float) -> bytes:
    """两张整条底图的线性混合（整数运算，避免每字节浮点乘）。

    输入是无宽高信息的紧凑 RGB 字节，保持逐字节实现最稳妥；Pillow 的
    Image.blend 需要尺寸，反而要额外推断，这里不引入。
    """
    n = len(a)
    ta = max(0, int((1.0 - t) * 1000))
    tb = max(0, int(t * 1000))
    out = bytearray(n)
    for i in range(n):
        out[i] = (a[i] * ta + b[i] * tb) // 1000
    return bytes(out)


def _rgb_photo(master, w: int, h: int, body: bytes):
    """一整帧 RGB 像素 -> PhotoImage。"""
    return ImageTk.PhotoImage(Image.frombytes("RGB", (w, h), body), master=master)


def _alpha_rows(raw, side: int, size: int, tint=(1.0, 1.0, 1.0)):
    """把原图缩放到 size×size 的 RGBA 行，并按 tint 调色。

    缩小用 LANCZOS、放大用 BILINEAR（Pillow 成熟实现，替代原先手写的
    盒式平均 / 双线性）。返回 list[bytes]，每行是一行的 RGBA 字节，
    与旧接口一致，供 _rgba_photo 使用。
    """
    im = Image.frombytes("RGBA", (side, side), raw)
    resample = Image.LANCZOS if size <= side else Image.BILINEAR
    im = im.resize((size, size), resample)

    tr, tg, tb = tint
    if (tr, tg, tb) != (1.0, 1.0, 1.0):
        r, g, b, a = im.split()
        lut = lambda c, k: c.point(lambda p: min(255, int(p * k)))
        im = Image.merge("RGBA", (lut(r, tr), lut(g, tg), lut(b, tb), a))

    px = im.tobytes()
    return [px[y * size * 4:(y + 1) * size * 4] for y in range(size)]
