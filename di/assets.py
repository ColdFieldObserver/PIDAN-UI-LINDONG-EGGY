# -*- coding: utf-8 -*-
"""图片素材加载：三张内嵌位图以真正的 PNG 放在 <资源目录>/assets/ 下。

    gear_sticker.png  齿轮贴纸，44×44 RGBA
    avatar.png        蛋仔头像，56×56 RGBA
    mini.png          收起态小图，PNG

齿轮/头像是带透明通道的 PNG，解码时保留 alpha（走 imaging._png_decode_rgba），
拿回原始 RGBA 字节交给主模块的 _alpha_rows 缩放；mini 是普通 PNG，主模块用
tk.PhotoImage(data=...) 加载，所以这里读文件后编码回 base64 字符串。

对外接口（sticker_raw / avatar_raw / mini_b64）与改成 PNG 之前完全一致，主模块无需改动。

资源目录解析与主模块保持一致：源码模式 = 脚本所在目录；PyInstaller 模式 = _MEIPASS。
解码结果做缓存，避免每次重画都读盘 + 解码。
"""
from __future__ import annotations

import base64
import os
import sys

from .imaging import _png_decode_rgba

_FROZEN = bool(getattr(sys, "frozen", False))
if _FROZEN:
    _RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
else:
    _RES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ASSET_DIR = os.path.join(_RES_DIR, "assets")

_CACHE = {}


def _read_png(name: str) -> bytes:
    """读取一份 PNG 素材的原始字节。"""
    if name in _CACHE:
        return _CACHE[name]
    with open(os.path.join(_ASSET_DIR, name + ".png"), "rb") as f:
        data = f.read()
    _CACHE[name] = data
    return data


def sticker_raw() -> bytes:
    """齿轮贴纸的 RGBA 原始字节。"""
    _w, _h, rgba = _png_decode_rgba(_read_png("gear_sticker"))
    return rgba


def avatar_raw() -> bytes:
    """蛋仔头像的 RGBA 原始字节。"""
    _w, _h, rgba = _png_decode_rgba(_read_png("avatar"))
    return rgba


def mini_b64() -> str:
    """收起态小图的 PNG base64，直接喂给 tk.PhotoImage(data=...)。"""
    return base64.b64encode(_read_png("mini")).decode("ascii")
