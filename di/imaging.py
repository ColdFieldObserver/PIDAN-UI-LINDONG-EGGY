# -*- coding: utf-8 -*-
"""纯像素 / PNG 工具：颜色换算、渐变行取色、PNG 编解码、RGBA 缩放。

这些函数只做算法，输入决定输出：不引用任何主题色变量，也不读配置文件，
因此从主模块拆出后可独立做单元测试。依赖 tkinter 的地方仅限
`tk.PhotoImage` 的构造（Tk 8.6 原生支持 data=），属纯展示，无状态耦合。
"""
from __future__ import annotations

import base64
import math
import os
import struct
import tempfile
import zlib

import tkinter as tk

GRAD_STEPS = 96          # 渐变分级（同时让 Tk 的取色缓存命中，铺图更快）
_PPM_SEQ = [0]

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


GRAD_STEPS = 96          # 渐变分级（同时让 Tk 的取色缓存命中，铺图更快）


def panel_row_color(y: int, h: int, top_rgb, bottom_rgb) -> str:
    t = max(0.0, min(1.0, float(y) / max(1, h - 1)))
    t = round(t * GRAD_STEPS) / GRAD_STEPS
    col = _lerp(top_rgb, bottom_rgb, t)
    gloss = 1.0 - t / 0.45          # 顶部一点淡淡的聚光
    if gloss > 0.0:
        col = _lerp(col, (255, 255, 255), 0.12 * min(1.0, gloss))
    return _rgb_hex(col)


_PPM_SEQ = [0]


def _photo_from_ppm(master, w: int, h: int, body: bytes):
    """把 P6 原始像素写进临时 ppm 再让 Tk 读回来：比逐点 put 颜色名快得多"""
    _PPM_SEQ[0] += 1
    path = os.path.join(tempfile.gettempdir(),
                        "di_panel_%d_%d.ppm" % (os.getpid(), _PPM_SEQ[0]))
    try:
        with open(path, "wb") as fp:
            fp.write(b"P6\n%d %d\n255\n" % (w, h) + body)
        return tk.PhotoImage(master=master, file=path, format="ppm")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass



def _png_decode(data: bytes):
    """解 8 位 RGB / RGBA、非隔行的 PNG，返回 (宽, 高, RGB 字节)。图片轮换的素材走这条路径。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a png")
    pos, w, h, depth, ctype, inter = 8, 0, 0, 0, 0, 0
    idat = []
    while pos + 8 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h, depth, ctype, _comp, _filt, inter = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
        pos += 12 + ln
    if depth != 8 or ctype not in (2, 6) or inter != 0:
        raise ValueError("unsupported png %d/%d/%d" % (depth, ctype, inter))

    raw = zlib.decompress(b"".join(idat))
    ch = 4 if ctype == 6 else 3
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ft == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line

    if ch == 4:
        rgb = bytearray(w * h * 3)
        for i in range(w * h):
            j, k = i * 4, i * 3
            rgb[k:k + 3] = out[j:j + 3]
        return w, h, rgb
    return w, h, out


def _png_bytes_rgba(w: int, h: int, rows) -> bytes:
    """把逐行 RGBA 字节编成 PNG（图标要压在照片上，必须带透明通道）"""
    return _png_pack(w, h, 6, b"".join(b"\x00" + bytes(r) for r in rows))


def _rgba_photo(master, w: int, h: int, rows, into=None):
    """into 不为空时原地换内容：画布上已经绑着这张图，重建对象会让它消失"""
    data = base64.b64encode(_png_bytes_rgba(w, h, rows)).decode("ascii")
    if into is not None:
        into.configure(data=data)
        return into
    return tk.PhotoImage(master=master, data=data)



def _png_bytes_rgb(w: int, h: int, rows) -> bytes:
    """逐行 RGB 字节编成 PNG（淡入淡出的过渡帧用）"""
    body = b"".join(b"\x00" + bytes(r) for r in rows)
    return _png_pack(w, h, 2, body)


def _png_pack(w: int, h: int, color_type: int, body: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(body, 6)) +
            chunk(b"IEND", b""))


def _blend_rgb(a: bytes, b: bytes, t: float) -> bytes:
    """两张整条底图的线性混合（整数运算，避免每字节浮点乘）"""
    n = len(a)
    ta = max(0, int((1.0 - t) * 1000))
    tb = max(0, int(t * 1000))
    out = bytearray(n)
    for i in range(n):
        out[i] = (a[i] * ta + b[i] * tb) // 1000
    return bytes(out)


def _rgb_photo(master, w: int, h: int, body: bytes):
    """一整帧 RGB 像素 -> PhotoImage（淡入淡出的过渡帧）"""
    rows = [body[i * w * 3:(i + 1) * w * 3] for i in range(h)]
    return tk.PhotoImage(master=master,
                         data=base64.b64encode(_png_bytes_rgb(w, h, rows)).decode("ascii"))



def _alpha_rows(raw, side: int, size: int, tint=(1.0, 1.0, 1.0)):
    """把原图缩/放到 size×size 的 RGBA 行：缩小用 2×2 盒式平均，放大用双线性，
    两条路径都能把边缘磨平（源图很小，直接最近邻会出硬台阶）"""
    tr, tg, tb = tint
    rows = []
    if size <= side:
        for y in range(size):
            sy0 = int(y * side / size)
            sy1 = max(sy0 + 1, int((y + 1) * side / size))
            if sy1 >= side:
                sy1 = side - 1
            span_y = sy1 - sy0
            row = bytearray()
            for x in range(size):
                sx0 = int(x * side / size)
                sx1 = max(sx0 + 1, int((x + 1) * side / size))
                if sx1 >= side:
                    sx1 = side - 1
                span_x = sx1 - sx0
                r = g = b = a = 0
                for oy in (0.25, 0.75):
                    qy = min(side - 1, sy0 + int(span_y * oy))
                    base = qy * side
                    for ox in (0.25, 0.75):
                        i = (base + min(side - 1, sx0 + int(span_x * ox))) * 4
                        r += raw[i]
                        g += raw[i + 1]
                        b += raw[i + 2]
                        a += raw[i + 3]
                row += bytes((min(255, int(r * 0.25 * tr)),
                              min(255, int(g * 0.25 * tg)),
                              min(255, int(b * 0.25 * tb)),
                              int(a * 0.25 + 0.5)))
            rows.append(row)
        return rows

    k = side / float(size)
    for y in range(size):
        fy = (y + 0.5) * k - 0.5
        iy = int(fy)
        ty = fy - iy
        if iy < 0:
            iy, ty = 0, 0.0
        if ty < 0.0:
            ty = 0.0
        elif ty > 1.0:
            ty = 1.0
        iy2 = min(side - 1, iy + 1)
        row = bytearray()
        for x in range(size):
            fx = (x + 0.5) * k - 0.5
            ix = int(fx)
            tx = fx - ix
            if ix < 0:
                ix, tx = 0, 0.0
            if tx < 0.0:
                tx = 0.0
            elif tx > 1.0:
                tx = 1.0
            ix2 = min(side - 1, ix + 1)
            i00 = (iy * side + ix) * 4
            i10 = (iy * side + ix2) * 4
            i11 = (iy2 * side + ix) * 4
            i12 = (iy2 * side + ix2) * 4
            w00 = (1.0 - tx) * (1.0 - ty)
            w10 = tx * (1.0 - ty)
            w01 = (1.0 - tx) * ty
            w11 = tx * ty
            row += bytes((min(255, int((raw[i00] * w00 + raw[i10] * w10
                                       + raw[i11] * w01 + raw[i12] * w11) * tr)),
                          min(255, int((raw[i00 + 1] * w00 + raw[i10 + 1] * w10
                                       + raw[i11 + 1] * w01 + raw[i12 + 1] * w11) * tg)),
                          min(255, int((raw[i00 + 2] * w00 + raw[i10 + 2] * w10
                                       + raw[i11 + 2] * w01 + raw[i12 + 2] * w11) * tb)),
                          min(255, int(raw[i00 + 3] * w00 + raw[i10 + 3] * w10
                                       + raw[i11 + 3] * w01 + raw[i12 + 3] * w11 + 0.5))))
        rows.append(row)
    return rows

