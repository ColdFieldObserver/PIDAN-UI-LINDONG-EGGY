# -*- coding: utf-8 -*-
"""图片素材加载：三张内嵌位图从源码里的 base64 字面量迁到独立素材文件。

素材放在 <资源目录>/assets/ 下，纯文本 base64（单块，无换行）：
    gear_sticker.b64  齿轮贴纸，RGBA 原始字节
    avatar.b64        蛋仔头像，RGBA 原始字节
    mini.b64          收起态小图，PNG

资源目录解析与主模块保持一致：源码模式 = 脚本所在目录；PyInstaller 模式 = _MEIPASS。
解码结果做缓存，避免每次重画都读盘 + base64 解码。
"""
from __future__ import annotations

import base64
import os
import sys

_FROZEN = bool(getattr(sys, "frozen", False))
if _FROZEN:
    _RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
else:
    _RES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ASSET_DIR = os.path.join(_RES_DIR, "assets")

_CACHE = {}


def _read_b64(name: str) -> str:
    """读取一份 base64 素材；去掉空白字符后返回。"""
    if name in _CACHE:
        return _CACHE[name]
    path = os.path.join(_ASSET_DIR, name + ".b64")
    with open(path, "r", encoding="ascii") as f:
        text = "".join(f.read().split())
    _CACHE[name] = text
    return text


def sticker_raw() -> bytes:
    """齿轮贴纸的 RGBA 原始字节。"""
    return base64.b64decode(_read_b64("gear_sticker"))


def avatar_raw() -> bytes:
    """蛋仔头像的 RGBA 原始字节。"""
    return base64.b64decode(_read_b64("avatar"))


def mini_b64() -> str:
    """收起态小图的 PNG base64，直接喂给 tk.PhotoImage(data=...)。"""
    return _read_b64("mini")
