# -*- coding: utf-8 -*-
"""灵动岛 · Windows 硬件状态悬浮条

纯标准库实现（tkinter + ctypes 直调 Win32 API），无需 pip 安装任何第三方包。
代码按职责拆成 di/ 包；三张内嵌位图以 PNG 素材放在 assets/ 下。
Python 3.9+ / Windows 10 及以上。

一条细长胶囊常驻屏幕顶部中央，CPU / 内存 / GPU / 磁盘 / 电池 五项指标
全部直接显示在条内，每项左侧一个小圆环显示占用比例，不展开、不收起。

两种底色：默认跟随主题渐变，也可以改成轮换播放
程序旁独立目录 `carousel/` 里的图片（原图居中裁剪铺满，默认每 25 秒一张，
间隔可在设置面板选 10 / 25 / 60 秒，换图时带一段淡入淡出）。
轮播图不打包进程序，只从外部目录动态读取；目录缺失或为空时退化为经典黑主题，
此时尝试开启「图片轮换」会弹出错误提示。

用法：
    python dynamic_island.py

交互：
    位置固定，不能拖动
    右键        -> 设置菜单（主题、刷新频率、图片轮换、开机自启、全屏自动隐藏、
                   下一张图片、问题反馈 / 生成报告、收起为图标、退出）
    出错时自动弹出错误报告窗口（含环境与堆栈），可一键复制提交到反馈站；
    也可随时用右键菜单主动生成报告。
    右侧齿轮    -> 单击同样打开设置菜单（快捷入口）
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import struct
import tempfile
import sys
import threading
import time
import winreg
import zlib

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

# 拆分出的内部模块
from dyanmic_island_modules import assets, error_report
from dyanmic_island_modules.monitor import Sampler, _battery_info, _cpu_info, fmt_uptime, user32
from dyanmic_island_modules.imaging import GRAD_STEPS, _PPM_SEQ, _alpha_rows, _blend_rgb, _hex_rgb, _lerp, _photo_from_ppm, _png_bytes_rgb, _png_bytes_rgba, _png_decode, _png_pack, _rgb_hex, _rgb_photo, _rgba_photo, panel_row_color

APP_NAME = "灵动岛 · 硬件状态"
APP_VERSION = "3.0"
SCRIPT_PATH = os.path.abspath(__file__)

# 打包成 exe（PyInstaller）后：__file__ 指向解包临时目录，sys.executable 才是 exe 本体。
# APP_DIR = 程序/脚本所在目录（配置、用户自备素材放这儿）
# RES_DIR = 只读资源目录（源码模式就是同一个目录；exe 模式是解包目录）
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = os.path.dirname(SCRIPT_PATH)
    RES_DIR = APP_DIR

# ---------------------------------------------------------------- 主题配色
# 每个主题就是一组颜色名 -> 颜色值。所有自绘函数都在运行时读这些模块级名字，
# 所以 apply_theme() 改写它们之后，重画一遍界面就换主题了。
# 浅色系按《蛋仔派对》的思路做：白色外描边 + 粉彩渐变 + 糖果色圆环。
KEY_COLOR = "#000000"     # 窗口抠色（该颜色的像素完全透明）

THEMES = {
    "dark": {
        "label": "深色",
        "BG_TOP": "#181F2A", "BG_BOTTOM": "#0C1117",
        "BORDER": "#252D3A", "BORDER_DEEP": "#323C4C",
        "TRACK": "#2B3547", "TEXT": "#EDF1F7", "MUTED": "#78849A",
        "DIVIDER": "#222B38", "MENU_BG": "#161B24", "MENU_ACTIVE": "#2A3342",
        "ACC": {"cpu": "#4E9BFF", "mem": "#3FD6C4", "gpu": "#6FE39A",
                "disk": "#FFB25C", "net": "#B98BFF", "bat": "#FF7E9E"},
        "COL_DOT_OK": "#4E9BFF", "COL_DOT_WARN": "#FFB25C", "COL_DOT_HOT": "#FF5D5D",
        "CARD_BG": "#141A24", "CARD_STROKE": "#2A3445",
        "ITEM_BG": "#242D3C", "ITEM_STROKE": "#303B4E",
        "ACTIVE_BG": "#4E9BFF", "PILL_ON_TEXT": "#FFFFFF", "PILL_OFF_TEXT": "#94A2B8",
        "DANGER_BG": "#3A2027", "DANGER_STROKE": "#5A2A33", "DANGER_TEXT": "#FF8A8A",
        "SW_TRACK": "#2B3547", "SW_KNOB": "#8C97AC", "EDGE_W": 1.2,
    },
    "pink": {
        "label": "粉白",
        "BG_TOP": "#FFF7FA", "BG_BOTTOM": "#FBDFE9",
        "BORDER": "#EDB6CC", "BORDER_DEEP": "#FFFFFF",
        "TRACK": "#F3D2DE", "TEXT": "#5B4551", "MUTED": "#A98C9B",
        "DIVIDER": "#F4DCE5", "MENU_BG": "#FFFFFF", "MENU_ACTIVE": "#FCE4EE",
        "ACC": {"cpu": "#F06A93", "mem": "#28B7AC", "gpu": "#5FBF63",
                "disk": "#E39B2A", "net": "#8C6FE3", "bat": "#3F8AE6"},
        "COL_DOT_OK": "#FF8FB1", "COL_DOT_WARN": "#FFC44D", "COL_DOT_HOT": "#F2607A",
        "CARD_BG": "#FFFFFF", "CARD_STROKE": "#F0C8D8",
        "ITEM_BG": "#FDF0F5", "ITEM_STROKE": "#F2D2DF",
        "ACTIVE_BG": "#FF7FA8", "PILL_ON_TEXT": "#FFFFFF", "PILL_OFF_TEXT": "#B98FA0",
        "DANGER_BG": "#FCE7EC", "DANGER_STROKE": "#F2C6D0", "DANGER_TEXT": "#D45C6E",
        "SW_TRACK": "#F1D4DF", "SW_KNOB": "#DB9FB3", "EDGE_W": 1.8,
    },
    "snow": {
        "label": "冷白",
        "BG_TOP": "#FFFFFF", "BG_BOTTOM": "#E8EEF6",
        "BORDER": "#C7D6E6", "BORDER_DEEP": "#FFFFFF",
        "TRACK": "#DDE6F1", "TEXT": "#2E3A4B", "MUTED": "#8A96A8",
        "DIVIDER": "#E6EDF5", "MENU_BG": "#FFFFFF", "MENU_ACTIVE": "#EEF4FB",
        "ACC": {"cpu": "#4E9BFF", "mem": "#2FBFB4", "gpu": "#5EC47A",
                "disk": "#E8A13C", "net": "#8E7BEA", "bat": "#E86A87"},
        "COL_DOT_OK": "#4E9BFF", "COL_DOT_WARN": "#E8A13C", "COL_DOT_HOT": "#E8556B",
        "CARD_BG": "#FFFFFF", "CARD_STROKE": "#D5E1EF",
        "ITEM_BG": "#F3F8FE", "ITEM_STROKE": "#DCE6F2",
        "ACTIVE_BG": "#4E9BFF", "PILL_ON_TEXT": "#FFFFFF", "PILL_OFF_TEXT": "#8090A6",
        "DANGER_BG": "#FCEEF0", "DANGER_STROKE": "#F3CBD2", "DANGER_TEXT": "#C9505F",
        "SW_TRACK": "#DDE7F2", "SW_KNOB": "#9FB0C6", "EDGE_W": 1.8,
    },
    "mint": {
        "label": "薄荷",
        "BG_TOP": "#F3FCF8", "BG_BOTTOM": "#D8F2E7",
        "BORDER": "#A9DCC5", "BORDER_DEEP": "#FFFFFF",
        "TRACK": "#D2EBE0", "TEXT": "#2C4A3E", "MUTED": "#7FA396",
        "DIVIDER": "#E0F1E9", "MENU_BG": "#FFFFFF", "MENU_ACTIVE": "#E6F7EF",
        "ACC": {"cpu": "#DC6280", "mem": "#17AE97", "gpu": "#4FB264",
                "disk": "#DB9526", "net": "#7A6BD6", "bat": "#3285C8"},
        "COL_DOT_OK": "#3FD6C4", "COL_DOT_WARN": "#F2B24F", "COL_DOT_HOT": "#F0605F",
        "CARD_BG": "#FFFFFF", "CARD_STROKE": "#BFE4D3",
        "ITEM_BG": "#EFFBF5", "ITEM_STROKE": "#CFEADD",
        "ACTIVE_BG": "#2FC2A6", "PILL_ON_TEXT": "#FFFFFF", "PILL_OFF_TEXT": "#7FA99A",
        "DANGER_BG": "#FCEBEC", "DANGER_STROKE": "#F2C9CD", "DANGER_TEXT": "#CC5A64",
        "SW_TRACK": "#D8F0E6", "SW_KNOB": "#9CC7B7", "EDGE_W": 1.8,
    },
}

THEME_ORDER = ["dark", "pink", "snow", "mint"]
THEME = "dark"

BG_TOP = "#181F2A"        # 胶囊渐变顶部
BG_BOTTOM = "#0C1117"     # 胶囊渐变底部
BORDER = "#252D3A"        # 胶囊描边
BORDER_DEEP = "#323C4C"   # 外沿亮一档，收边用
TRACK = "#2B3547"         # 圆环轨道
TEXT = "#EDF1F7"          # 主数值
MUTED = "#78849A"
DIVIDER = "#222B38"       # 分隔线
MENU_BG = "#161B24"
MENU_ACTIVE = "#2A3342"
PILL_ON_TEXT = "#FFFFFF"  # 选中胶囊的字
PILL_OFF_TEXT = "#94A2B8" # 未选中胶囊的字
SW_TRACK = "#2B3547"      # 开关轨道
SW_KNOB = "#8C97AC"       # 开关圆钮
EDGE_W = 1.2              # 胶囊描边粗细

ACC = {
    "cpu": "#4E9BFF",
    "mem": "#3FD6C4",
    "gpu": "#6FE39A",
    "disk": "#FFB25C",
    "net": "#B98BFF",
    "bat": "#FF7E9E",
}

COL_DOT_OK = "#4E9BFF"     # 状态球：正常
COL_DOT_WARN = "#FFB25C"   # 状态球：偏忙
COL_DOT_HOT = "#FF5D5D"    # 状态球：高负载


def _cfg_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "LINDONG-EGGY")


def _load_cfg():
    try:
        with open(os.path.join(_cfg_dir(), "config.json"), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_cfg(d):
    try:
        os.makedirs(_cfg_dir(), exist_ok=True)
        with open(os.path.join(_cfg_dir(), "config.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def _cfg_patch(upd):
    """只改几个键，别把别的设置（主题、轮换开关）覆盖掉"""
    d = _load_cfg()
    d.update(upd)
    _save_cfg(d)
    return d


THEME_FIELDS = ("BG_TOP", "BG_BOTTOM", "BORDER", "BORDER_DEEP", "TRACK", "TEXT",
                "MUTED", "DIVIDER", "MENU_BG", "MENU_ACTIVE",
                "COL_DOT_OK", "COL_DOT_WARN", "COL_DOT_HOT",
                "PILL_ON_TEXT", "PILL_OFF_TEXT", "SW_TRACK", "SW_KNOB", "EDGE_W")
PANEL_FIELDS = ("CARD_BG", "CARD_STROKE", "ITEM_BG", "ITEM_STROKE",
                "ACTIVE_BG", "DANGER_BG", "DANGER_STROKE", "DANGER_TEXT")


def apply_theme(name):
    """把某个主题的颜色写进模块级变量 + 设置面板的类属性"""
    global THEME, AVATAR_TINTS
    if name not in THEMES:
        name = THEME_ORDER[0]
    th = THEMES[name]
    THEME = name
    for k in THEME_FIELDS:
        globals()[k] = th[k]
    ACC.clear()
    ACC.update(th["ACC"])
    # 头像色偏跟着当前主题的状态色重建（正常/偏忙/高负载）
    AVATAR_TINTS = {
        COL_DOT_WARN: (1.0, 0.86, 0.62),
        COL_DOT_HOT: (1.0, 0.62, 0.50),
    }
    panel = globals().get("SettingsPanel")
    if panel is not None:
        for k in PANEL_FIELDS:
            setattr(panel, k, th[k])

# ---------------------------------------------------------------- 尺寸（96 DPI 下的逻辑像素）
STRIP_H = 44          # 胶囊高度（不变粗）
PAD_L = 80            # 左侧留白（含蛋仔头像，给左圆头留够空间）
PAD_R = 20
COL_GAP = 10
GEAR_W = 34           # 右侧设置快捷入口的占位宽度
GEAR_D = 22           # 齿轮图标直径
# 列定义模板。电池列仅在检测到电池时显示；网速列常驻。
_ALL_COLUMNS = [
    ("cpu", "CPU", 66),
    ("mem", "内存", 66),
    ("gpu", "GPU", 88),
    ("disk", "磁盘", 66),
    ("bat", "电池", 86),
    ("net", "网速", 96),
]


def build_columns(has_battery: bool):
    """按硬件能力生成实际列：无电池（台式机）去掉电池列，网速列保留。"""
    return [c for c in _ALL_COLUMNS if has_battery or c[0] != "bat"]


def strip_width(columns) -> int:
    """按实际列集合算胶囊总宽。"""
    return (PAD_L + sum(c[2] for c in columns) + COL_GAP * (len(columns) - 1)
            + COL_GAP + GEAR_W + PAD_R)


def fmt_speed(bps: float) -> str:
    """把 bytes/s 压成简短字符串：'0K' / '856K' / '1.2M' / '12M'。"""
    v = max(0.0, float(bps))
    if v < 1024:
        return "0K"
    if v < 1024 * 1024:
        return f"{v / 1024:.0f}K"
    return f"{v / 1024 / 1024:.1f}M" if v < 10 * 1024 * 1024 else f"{v / 1024 / 1024:.0f}M"


# 网速环的满环参考：下行 10 MB/s（约 80 Mbps）时画满一圈；超过封顶
NET_RING_REF = 10 * 1024 * 1024

TOP_OFFSET = 6

# 自适应：胶囊宽度按屏幕逻辑宽度取比例，窄屏缩小、宽屏适度放大
FIT_RATIO = 0.45          # 目标宽度 ≈ 屏幕逻辑宽度的 45%
FIT_MIN = 0.75
FIT_MAX = 1.40
FIT_MAX_PCT = 0.92        # 再宽也不超过屏幕的 92%

# 交互：悬停向下"长"出来一点，带弹簧阻尼，回弹有轻微过冲（苹果式手感）
MORPH_GROW = 7            # 悬停时向下多长出的逻辑高度
MORPH_WIN_PAD = 3         # 窗口比最大胶囊再高一点，动画时边缘不被窗口裁掉
SPRING_K = 190.0          # 弹簧刚度
SPRING_D = 17.0           # 阻尼（越大越不弹）
PRESS_DIP = 1.6           # 按下时内容下沉多少逻辑像素

# 图片轮换：胶囊底色换成程序旁独立目录 carousel/ 里的图，居中裁剪铺满。
# 该目录不打包进程序、只做运行时动态读取；目录缺失或为空时退化为经典黑主题。
PHOTO_DIR = "carousel"
PHOTO_INTERVAL = 25       # 轮换间隔默认值（秒），设置面板里可改，存在配置里
PHOTO_FADE_MS = 320       # 换图时淡入淡出时长
PHOTO_FADE_STEPS = 6      # 淡入淡出分几帧（帧数越多越顺，但每帧要多算一次）
PHOTO_SCRIM_TOP = 0.52    # 顶部压暗强度，保证读数看得清
PHOTO_SCRIM_BOTTOM = 0.66 # 底部再压一点，做出上亮下暗的渐变


# ================================================================ Win32 接口


# 负载 -> 蛋仔头像轻微色偏（亮黄 -> 橙 -> 橙红），保留原来"看颜色判断负载"
# 切主题时 apply_theme() 会按新的状态色重建这张表
AVATAR_TINTS = {
    COL_DOT_WARN: (1.0, 0.86, 0.62),
    COL_DOT_HOT: (1.0, 0.62, 0.50),
}


def load_color(ratio: float) -> str:
    if ratio >= 0.85:
        return COL_DOT_HOT
    if ratio >= 0.65:
        return COL_DOT_WARN
    return COL_DOT_OK


# 设置入口图标：用户提供的蛋仔风齿轮贴纸，已抠掉黑底、缩放到 40x40，
# 存成带透明通道的 PNG（assets/gear_sticker.png），运行时按胶囊每行的渐变色做 alpha 合成。

# ================================================================ 抗锯齿位图
# Tk 画布的 oval / arc / 多边形都没有抗锯齿，圆环、圆点、胶囊边缘都会有明显锯齿。
# 这里用 4x 超采样把它们预渲染成位图：外部像素填成抠色（完全透明），视觉上就是平滑圆角。
def make_panel_image(master, w: int, h: int):
    """整块胶囊：垂直糖果渐变 + 双层描边 + 4x 超采样抗锯齿边缘

    形状是胶囊（圆角半径 = 半高），一行的左右边界能解析算出来，
    所以只有边缘那几个像素要算超采样，中间整段是同一行的渐变色、按字节重复；
    最后拼成 P6 原始像素一次读进 PhotoImage。输出和旧的逐点版本逐位一致。
    """
    key_rgb = _hex_rgb(KEY_COLOR)
    top_rgb = _hex_rgb(BG_TOP)
    bot_rgb = _hex_rgb(BG_BOTTOM)
    rim_rgb = _hex_rgb(BORDER)
    deep_rgb = _hex_rgb(BORDER_DEEP)
    key_bytes = bytes(key_rgb)

    x0, y0, x1, y1 = 1.5, 1.5, w - 1.5, h - 1.5
    r = (y1 - y0) / 2.0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    half = max(0.0, (x1 - x0) / 2.0 - r)

    def shade(sd, base_rgb):
        """按到边缘的有符号距离给颜色：|grad| = 1，所以覆盖率就是 0.5 - sd"""
        if sd <= -2.6:                       # 内部：本行渐变色
            return base_rgb
        if sd >= 1.2:                        # 完全在形状外
            return key_rgb
        cov = 0.5 - sd
        if cov <= 0.0:
            return key_rgb
        if cov > 1.0:
            cov = 1.0
        edge = deep_rgb if sd > -1.0 else rim_rgb
        return _lerp(key_rgb, edge, cov)

    def arc_rgb(x, dy, base_rgb):
        """圆头（曲线段）：边缘带用 2×2 子采样算覆盖率，圆头转角更干净"""
        dxq = abs(x + 0.5 - cx) - half
        if dxq < 0.0:
            dxq = 0.0
        sd = sq(dxq * dxq + dy * dy) - r
        if sd <= -2.6:
            return base_rgb
        if sd >= 1.2:
            return key_rgb
        cnt = 0
        for ox in (0.25, 0.75):
            qdx = abs(x + ox - cx) - half
            if qdx < 0.0:
                qdx = 0.0
            for qdy in (dy - 0.25, dy + 0.25):
                if sq(qdx * qdx + qdy * qdy) <= r:
                    cnt += 1
        cov = cnt * 0.25
        edge = deep_rgb if sd > -1.0 else rim_rgb
        return _lerp(key_rgb, edge, cov if cov < 1.0 else 1.0)

    body = bytearray()
    sq = math.sqrt
    for y in range(h):
        base_rgb = _hex_rgb(panel_row_color(y, h, top_rgb, bot_rgb))
        dy = y + 0.5 - cy
        ady = abs(dy)
        if ady >= r + 1.2:                       # 这一行整行都在形状外
            body += key_bytes * w
            continue
        # 直段（|x-cx| <= half）在这一行里 sd 恒定 = ady - r，整段同一种颜色
        mid_rgb = shade(ady - r, base_rgb)
        mid_bytes = bytes(mid_rgb)
        m_lo = max(0, int(math.ceil(cx - half - 0.5)))
        m_hi = min(w - 1, int(math.floor(cx + half - 0.5)))
        arc = sq((r + 1.2) ** 2 - dy * dy)       # 圆头这一段还要算的横向范围
        o_lo = max(0, int(math.floor(cx - half - arc - 0.5)))
        o_hi = min(w - 1, int(math.ceil(cx + half + arc - 0.5)))

        if o_lo > 0:
            body += key_bytes * o_lo
        for x in range(o_lo, m_lo):              # 左圆头
            body += bytes(arc_rgb(x, dy, base_rgb))
        if m_hi >= m_lo:
            body += mid_bytes * (m_hi - m_lo + 1)
        for x in range(m_hi + 1, o_hi + 1):      # 右圆头
            body += bytes(arc_rgb(x, dy, base_rgb))
        tail = w - (o_hi + 1)
        if tail > 0:
            body += key_bytes * tail

    if len(body) != w * h * 3:
        raise RuntimeError("panel bytes %d != %d" % (len(body), w * h * 3))
    return _photo_from_ppm(master, w, h, bytes(body))


# ---------------------------------------------------------------- PNG：解码 / 写带透明通道的位图
def make_photo_panel_image(master, w: int, h: int, src_w: int, src_h: int,
                           src_rows, scrim_top: float, scrim_bot: float):
    """图片版胶囊底：原图居中裁剪铺满，压一层上亮下暗的遮罩，外形与描边同渐变版一致"""
    key_rgb = _hex_rgb(KEY_COLOR)
    key_bytes = bytes(key_rgb)
    top_rgb = _hex_rgb(BG_TOP)
    bot_rgb = _hex_rgb(BG_BOTTOM)
    rim_rgb = _hex_rgb(BORDER)
    deep_rgb = _hex_rgb(BORDER_DEEP)

    # 居中裁剪窗（cover）：按比例裁掉多出来的一边
    if src_w * h > w * src_h:
        cw, chh = src_h * w / float(h), float(src_h)
        x0, y0 = (src_w - cw) / 2.0, 0.0
    else:
        cw, chh = float(src_w), src_w * h / float(w)
        x0, y0 = 0.0, (src_h - chh) / 2.0

    x0p, y0p, x1p, y1p = 1.5, 1.5, w - 1.5, h - 1.5
    r = (y1p - y0p) / 2.0
    cx, cy = (x0p + x1p) / 2.0, (y0p + y1p) / 2.0
    half = max(0.0, (x1p - x0p) / 2.0 - r)
    sq = math.sqrt

    def shade(sd, base_rgb):
        if sd <= -2.6:
            return base_rgb
        if sd >= 1.2:
            return key_rgb
        cov = 0.5 - sd
        if cov <= 0.0:
            return key_rgb
        if cov > 1.0:
            cov = 1.0
        edge = deep_rgb if sd > -1.0 else rim_rgb
        return _lerp(key_rgb, edge, cov)

    def photo_at(x, y):
        fx = x0 + (x + 0.5) * cw / w
        fy = y0 + (y + 0.5) * chh / h
        ix = int(fx)
        iy = int(fy)
        if ix >= src_w - 1:
            ix = src_w - 2
        if iy >= src_h - 1:
            iy = src_h - 2
        tx, ty = fx - ix, fy - iy
        i0 = (iy * src_w + ix) * 3
        i1 = i0 + src_w * 3
        p00 = src_rows[i0:i0 + 3]
        p10 = src_rows[i0 + 3:i0 + 6]
        p01 = src_rows[i1:i1 + 3]
        p11 = src_rows[i1 + 3:i1 + 6]
        w00 = (1.0 - tx) * (1.0 - ty)
        w10 = tx * (1.0 - ty)
        w01 = (1.0 - tx) * ty
        w11 = tx * ty
        rgb = (int(p00[0] * w00 + p10[0] * w10 + p01[0] * w01 + p11[0] * w11 + 0.5),
               int(p00[1] * w00 + p10[1] * w10 + p01[1] * w01 + p11[1] * w11 + 0.5),
               int(p00[2] * w00 + p10[2] * w10 + p01[2] * w01 + p11[2] * w11 + 0.5))
        return rgb

    body = bytearray()
    for y in range(h):
        t = y / float(max(1, h - 1))
        veilt = scrim_top * (1.0 - t) + scrim_bot * t
        veil = _lerp(top_rgb, bot_rgb, t)
        dy = y + 0.5 - cy
        ady = abs(dy)
        if ady >= r + 1.2:
            body += key_bytes * w
            continue
        arc = sq((r + 1.2) ** 2 - dy * dy)
        o_lo = max(0, int(math.floor(cx - half - arc - 0.5)))
        o_hi = min(w - 1, int(math.ceil(cx + half + arc - 0.5)))
        if o_lo > 0:
            body += key_bytes * o_lo
        for x in range(o_lo, o_hi + 1):
            dxq = abs(x + 0.5 - cx) - half
            if dxq < 0.0:
                dxq = 0.0
            sd = sq(dxq * dxq + dy * dy) - r
            px = photo_at(x, y)
            base = _lerp(px, veil, veilt)
            body += bytes(shade(sd, base))
        tail = w - (o_hi + 1)
        if tail > 0:
            body += key_bytes * tail

    if len(body) != w * h * 3:
        raise RuntimeError("photo panel bytes %d != %d" % (len(body), w * h * 3))
    # 顺带把原始像素交出去：淡入淡出要拿它跟下一张混合
    return _photo_from_ppm(master, w, h, bytes(body)), bytes(body)


def make_morph_frame(master, w: int, h: int, base_w: int, base_h: int, base_rows,
                     margin: int = 3, zoom: bool = True):
    """用预采样好的照片行 + 胶囊遮罩拼一帧：中间段整块拷贝，只有圆头要逐点算

    base_rows 是 h0 高那种胶囊的**成品像素**，外圈 1~2px 是描边 / 抠色。

    zoom=True（默认）：照片跟着胶囊等比放大 —— 取底图居中 (base_w/z, base_h/z)
    区域铺满 w×h（z = h/base_h），所以展开时画面是「放大」而不是「拉长」，
    取景窗本身就排除了底图外圈的描边/抠色，不用再躲 margin。
    zoom=False：把 [margin, base_h-1-margin] 整段拉伸铺满 h 行（老做法）。
    """
    key_rgb = _hex_rgb(KEY_COLOR)
    key_bytes = bytes(key_rgb)
    rim_rgb = _hex_rgb(BORDER)
    deep_rgb = _hex_rgb(BORDER_DEEP)
    x0, y0, x1, y1 = 1.5, 1.5, w - 1.5, h - 1.5
    r = (y1 - y0) / 2.0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    half = max(0.0, (x1 - x0) / 2.0 - r)
    sq = math.sqrt
    lo = max(0, min(base_h - 1, margin))
    hi = max(lo + 1, base_h - margin)
    span = float(hi - lo)
    if zoom:
        z = max(1.0, h / float(base_h))
        bw, bh = base_w / z, base_h / z
        ox, oy = (base_w - bw) / 2.0, (base_h - bh) / 2.0
        xs3 = [min(base_w - 1, max(0, int(ox + (x + 0.5) * bw / w))) * 3 for x in range(w)]
        ys = [min(base_h - 1, max(0, int(oy + (y + 0.5) * bh / h))) for y in range(h)]
    else:
        xs3 = None
        ys = [lo + min(int(span), max(0, int((y + 0.5) * span / h))) for y in range(h)]
    body = bytearray()
    for y in range(h):
        sy = ys[y]
        rstart = sy * base_w * 3
        dy = y + 0.5 - cy
        ady = abs(dy)
        if ady >= r + 1.2:
            body += key_bytes * w
            continue
        arc = sq((r + 1.2) ** 2 - dy * dy)
        o_lo = max(0, int(math.floor(cx - half - arc - 0.5)))
        o_hi = min(w - 1, int(math.ceil(cx + half + arc - 0.5)))
        m_lo = max(0, int(math.ceil(cx - half - 0.5)))
        m_hi = min(w - 1, int(math.floor(cx + half - 0.5)))
        sd_mid = ady - r
        if o_lo > 0:
            body += key_bytes * o_lo

        def arc_px(x):
            """圆头里的像素：够靠内就照抄照片，只有 2.6px 的边带才画描边

            以前这里一律返回描边色、cov 还被夹到 1，等于把两侧圆头整块刷成
            深色 —— 展开时圆头半径变大，看起来就是照片左右各缺一块。
            """
            dxq = abs(x + 0.5 - cx) - half
            if dxq < 0.0:
                dxq = 0.0
            sd = sq(dxq * dxq + dy * dy) - r
            if sd <= -2.6:                        # 圆头内部：照片
                o = xs3[x] if xs3 is not None else x * 3
                return base_rows[rstart + o:rstart + o + 3]
            if sd >= 1.2:
                return key_bytes
            cov = 0.5 - sd
            if cov <= 0.0:
                return key_bytes
            if cov > 1.0:
                cov = 1.0
            return bytes(_lerp(key_rgb, deep_rgb if sd > -1.0 else rim_rgb, cov))

        for x in range(o_lo, m_lo):          # 左圆头
            body += arc_px(x)
        if m_hi >= m_lo:
            if sd_mid <= -2.6:               # 直段整段是照片
                if xs3 is None:
                    body += base_rows[rstart + m_lo * 3:rstart + (m_hi + 1) * 3]
                else:
                    body += b"".join([base_rows[rstart + o:rstart + o + 3]
                                      for o in xs3[m_lo:m_hi + 1]])
            else:                            # 这一行压在上下描边上：整段同色
                cov = 0.5 - sd_mid
                if cov <= 0.0:
                    body += key_bytes * (m_hi - m_lo + 1)
                else:
                    if cov > 1.0:
                        cov = 1.0
                    edge = deep_rgb if sd_mid > -1.0 else rim_rgb
                    body += bytes(_lerp(key_rgb, edge, cov)) * (m_hi - m_lo + 1)
        for x in range(m_hi + 1, o_hi + 1):  # 右圆头
            body += arc_px(x)
        tail = w - (o_hi + 1)
        if tail > 0:
            body += key_bytes * tail

    if len(body) != w * h * 3:
        raise RuntimeError("morph frame bytes %d != %d" % (len(body), w * h * 3))
    return _photo_from_ppm(master, w, h, bytes(body))


def _scan_photos():
    """只扫描程序/脚本旁独立目录 carousel/，按文件名排序返回 PNG 路径。

    轮播图不打包进程序，完全运行时动态读取：源码模式读脚本同级目录，
    exe 模式读 exe 同级目录。目录不存在或里面没有 PNG 时返回空列表，
    调用方据此退化为经典黑主题并关闭图片轮换开关。
    """
    folder = os.path.join(APP_DIR, PHOTO_DIR)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names
            if n.lower().endswith(".png") and os.path.isfile(os.path.join(folder, n))]



AVATAR_PX = 96                 # 头像位图边长（与 avatar.png 数据一致）
AVATAR_D = 30                  # 条上显示的逻辑直径
AVATAR_CX = 34                 # 头像圆心的逻辑 x 坐标（胶囊左端加长后右移 16，屏幕上位置不变）

# 收起后显示的小图：用户提供的蛋仔派对横幅，裁到内容 + 圆角遮罩，
# 存成 PNG（assets/mini.png，Tk 8.6+ 原生可读），四周已在生成时压成抠色。
# 收起小图的抠色：用户在源图上把背景改成了粉色，这里按粉色洪水填充抠成透明，
# 再用这个源图里不存在的洋红做窗口抠色，避免把图案里的黑描边一起抠掉。
MINI_BG = "#FF00FE"


def make_dot_image(master, diameter: int, color_hex: str, bg_rows):
    """糖果球状态点：抗锯齿圆 + 左上高光"""
    ss = 4
    d = max(6, int(diameter))
    cc = d / 2.0
    fg = _hex_rgb(color_hex)
    white = (255, 255, 255)
    hx, hy = cc - cc * 0.32, cc - cc * 0.36
    hr = cc * 0.30
    rows = []
    for y in range(d):
        bg = _hex_rgb(bg_rows[min(y, len(bg_rows) - 1)])
        row = []
        for x in range(d):
            dist = math.hypot(x + 0.5 - cc, y + 0.5 - cc)
            if dist <= cc - 1.0:
                cov = 1.0
            elif dist >= cc + 0.6:
                cov = 0.0
            else:
                cnt = 0
                for sy in range(ss):
                    for sx in range(ss):
                        if math.hypot(x + (sx + 0.5) / ss - cc, y + (sy + 0.5) / ss - cc) <= cc:
                            cnt += 1
                cov = cnt / float(ss * ss)
            if cov <= 0.0:
                row.append(_rgb_hex(bg))
                continue
            col = _lerp(bg, fg, cov)
            hd = math.hypot(x + 0.5 - hx, y + 0.5 - hy)
            if hd <= hr - 0.8:
                hcov = 1.0
            elif hd <= hr + 0.8:
                cnt = 0
                for sy in range(ss):
                    for sx in range(ss):
                        if math.hypot(x + (sx + 0.5) / ss - hx, y + (sy + 0.5) / ss - hy) <= hr:
                            cnt += 1
                hcov = cnt / float(ss * ss)
            else:
                hcov = 0.0
            if hcov > 0.0:
                col = _lerp(col, white, hcov * 0.9 * cov)
            row.append(_rgb_hex(col))
        rows.append("{" + " ".join(row) + "}")
    img = tk.PhotoImage(master=master, width=d, height=d)
    img.put(" ".join(rows))
    return img


_STICKER_CACHE = {}


def _sticker_data():
    """解码原图贴纸：返回 (RGBA 字节, 边长)"""
    if "raw" not in _STICKER_CACHE:
        raw = assets.sticker_raw()
        side = int(round(math.sqrt(len(raw) / 4.0)))
        _STICKER_CACHE["raw"] = raw
        _STICKER_CACHE["side"] = side
    return _STICKER_CACHE["raw"], _STICKER_CACHE["side"]


_AVATAR_CACHE = {}


def _avatar_data():
    """解码蛋仔头像位图：返回 (RGBA 字节, 边长)"""
    if "raw" not in _AVATAR_CACHE:
        raw = assets.avatar_raw()
        _AVATAR_CACHE["raw"] = raw
        _AVATAR_CACHE["side"] = int(round(math.sqrt(len(raw) / 4.0)))
    return _AVATAR_CACHE["raw"], _AVATAR_CACHE["side"]


def make_avatar_image(master, size: int, bg_rows, tint=(1.0, 1.0, 1.0)):
    """把蛋仔头像贴到胶囊上：逐行用该行渐变底色做 alpha 合成，tint 做负载色偏"""
    raw, side = _avatar_data()
    size = max(8, int(size))
    tr, tg, tb = tint
    rows = []
    for y in range(size):
        bg = _hex_rgb(bg_rows[min(y, len(bg_rows) - 1)])
        row = []
        sy = min(side - 1, max(0, int((y + 0.5) * side / size)))
        for x in range(size):
            sx = min(side - 1, max(0, int((x + 0.5) * side / size)))
            i = (sy * side + sx) * 4
            a = raw[i + 3] / 255.0
            if a <= 0.0:
                row.append(_rgb_hex(bg))
                continue
            fg = (min(255, int(raw[i] * tr)),
                  min(255, int(raw[i + 1] * tg)),
                  min(255, int(raw[i + 2] * tb)))
            if a >= 1.0:
                row.append("#%02X%02X%02X" % fg)
            else:
                row.append(_rgb_hex(_lerp(bg, fg, a)))
        rows.append("{" + " ".join(row) + "}")
    img = tk.PhotoImage(master=master, width=size, height=size)
    img.put(" ".join(rows))
    return img


def make_sticker_image(master, size: int, bg_rows, boost: float = 1.0):
    """把原图齿轮贴纸贴到胶囊上：逐行用该行的渐变底色做 alpha 合成，边缘自然过渡"""
    raw, side = _sticker_data()
    size = max(8, int(size))
    rows = []
    for y in range(size):
        bg = _hex_rgb(bg_rows[min(y, len(bg_rows) - 1)])
        row = []
        sy = min(side - 1, max(0, int((y + 0.5) * side / size)))
        for x in range(size):
            sx = min(side - 1, max(0, int((x + 0.5) * side / size)))
            i = (sy * side + sx) * 4
            a = raw[i + 3] / 255.0
            if a <= 0.0 and boost <= 1.0:
                row.append(_rgb_hex(bg))
                continue
            if a >= 1.0 and boost <= 1.0:
                row.append("#%02X%02X%02X" % (raw[i], raw[i + 1], raw[i + 2]))
                continue
            fg = (min(255, int(raw[i] * boost)),
                  min(255, int(raw[i + 1] * boost)),
                  min(255, int(raw[i + 2] * boost)))
            row.append(_rgb_hex(_lerp(bg, fg, a)))
        rows.append("{" + " ".join(row) + "}")
    img = tk.PhotoImage(master=master, width=size, height=size)
    img.put(" ".join(rows))
    return img



def make_avatar_alpha(master, size: int, tint=(1.0, 1.0, 1.0)):
    """头像贴纸（保留透明通道）：底色交给下层，渐变色 / 照片都能干净叠住"""
    raw, side = _avatar_data()
    size = max(8, int(size))
    return _rgba_photo(master, size, size, _alpha_rows(raw, side, size, tint))


def make_sticker_alpha(master, size: int, boost: float = 1.0):
    """齿轮贴纸（保留透明通道）"""
    raw, side = _sticker_data()
    size = max(8, int(size))
    return _rgba_photo(master, size, size,
                       _alpha_rows(raw, side, size, (boost, boost, boost)))


class RingGauge:
    """抗锯齿环形进度：底色按胶囊渐变逐行取色，边缘按屏幕密度做 4 或 6 倍超采样"""

    SS = 4

    def __init__(self, master, diameter: int, band: float,
                 track_hex: str, accent_hex: str, track_a: int = 255, ss: int = 4,
                 halo_hex: str = "#06090E", halo_a: int = 118):
        self.master = master
        self.d = max(8, int(diameter))
        self.ss = 4 if ss < 5 else 6
        self._track = _hex_rgb(track_hex)
        self._track_a = max(0, min(255, int(track_a)))
        self._accent = _hex_rgb(accent_hex)
        # 外圈光晕：照片模式用深色压住花哨背景；浅色主题改用轨道色，
        # 否则那圈深色半透明像素在浅底上会显成一道黑边。
        self._halo = _hex_rgb(halo_hex) if halo_a > 0 else None
        self._halo_a = max(0, min(255, int(halo_a)))
        # 初始位图也用 imaging 构造的 ImageTk.PhotoImage，保证后续 render
        # 的 into.paste 原地更新可用（原生 tk.PhotoImage 没有 paste 方法）。
        blank = [b"\x00\x00\x00\x00" * self.d] * self.d
        self.photo = _rgba_photo(master, self.d, self.d, blank)
        self._rows = self._prepare(float(band))
        self._extent = None
        self.render(0.0)

    def _prepare(self, band: float):
        """只算几何：方位角 + 环覆盖率 + 外圈光晕覆盖率，颜色留到 render"""
        d, ss = self.d, self.ss
        cc = d / 2.0
        r_out = cc - 0.6
        r_in = max(1.0, r_out - band)
        edge = 0.9
        pad = 1.3                       # 光晕比环再宽一点，照片上才分得清
        rows = []
        for y in range(d):
            row = []
            for x in range(d):
                px, py = x + 0.5 - cc, y + 0.5 - cc
                dist = math.hypot(px, py)
                if abs(dist - r_in) <= edge or abs(dist - r_out) <= edge:
                    cnt = 0
                    for sy in range(ss):
                        for sx in range(ss):
                            qx = x + (sx + 0.5) / ss - cc
                            qy = y + (sy + 0.5) / ss - cc
                            qd = math.hypot(qx, qy)
                            if r_in <= qd <= r_out:
                                cnt += 1
                    cov = cnt / float(ss * ss)
                else:
                    cov = 1.0 if r_in <= dist <= r_out else 0.0

                if cov > 0.0:
                    halo = 0.0
                else:
                    lo, hi = r_in - pad, r_out + pad
                    if abs(dist - lo) <= edge or abs(dist - hi) <= edge:
                        cnt = 0
                        for sy in range(ss):
                            for sx in range(ss):
                                qx = x + (sx + 0.5) / ss - cc
                                qy = y + (sy + 0.5) / ss - cc
                                qd = math.hypot(qx, qy)
                                if lo <= qd <= hi:
                                    cnt += 1
                        halo = cnt / float(ss * ss)
                    else:
                        halo = 1.0 if lo <= dist <= hi else 0.0

                ang = math.degrees(math.atan2(px, -py)) % 360.0   # 0 = 正上方，顺时针
                row.append((ang, cov, halo))
            rows.append(row)
        return rows

    def render(self, ratio):
        ratio = max(0.0, min(1.0, ratio))
        extent = min(357.0, max(8.0, 360.0 * ratio))
        if self._extent is not None and abs(extent - self._extent) < 1.0:
            return
        self._extent = extent
        rows = []
        for row in self._rows:
            buf = bytearray()
            for ang, cov, halo in row:
                if cov > 0.0:
                    col = self._accent if ang <= extent else self._track
                    alpha = 255 if ang <= extent else self._track_a
                    buf += bytes((col[0], col[1], col[2], int(alpha * cov + 0.5)))
                elif halo > 0.0 and self._halo is not None:
                    hc = self._halo
                    buf += bytes((hc[0], hc[1], hc[2], int(self._halo_a * halo + 0.5)))
                else:
                    buf += b"\x00\x00\x00\x00"
            rows.append(buf)
        self.photo = _rgba_photo(self.master, self.d, self.d, rows, into=self.photo)


# ================================================================ 主界面
_ROUND_CACHE = {}
_SWITCH_CACHE = {}


def make_round_image(master, w: int, h: int, radius: float, fill_hex: str,
                     stroke_hex: str, bg_hex: str, stroke_w: float = 1.2):
    """抗锯齿圆角矩形：逐行解出水平跨度，只在边缘带算覆盖率，结果带缓存（开面板不卡）"""
    w, h = max(2, int(w)), max(2, int(h))
    radius = max(0.0, min(float(radius), min(w, h) / 2.0))
    key = (w, h, round(radius, 1), fill_hex, stroke_hex or "-", bg_hex, round(stroke_w, 2))
    hit = _ROUND_CACHE.get(key)
    if hit is not None:
        return hit

    bg = _hex_rgb(bg_hex)
    fill = _hex_rgb(fill_hex)
    stroke = _hex_rgb(stroke_hex) if stroke_hex else fill
    cx, cy = w / 2.0, h / 2.0
    bx, by = max(0.0, cx - radius), max(0.0, cy - radius)

    rows = []
    for y in range(h):
        qy = abs(y + 0.5 - cy) - by
        if qy <= 0.0 or radius <= 0.0:
            half = cx
        else:
            half = bx + math.sqrt(max(0.0, radius * radius - qy * qy))
        row = [bg_hex] * w
        if half > 0.0:
            fx0 = int(math.floor(cx - half + stroke_w + 0.5))
            fx1 = int(math.floor(cx + half - stroke_w + 0.5)) + 1
            if fx1 > fx0:
                a0, a1 = max(0, fx0), min(w, fx1)
                if a1 > a0:
                    row[a0:a1] = [fill_hex] * (a1 - a0)
            lo = max(0, int(math.floor(cx - half)) - 1)
            hi = min(w - 1, int(math.ceil(cx + half)) + 1)
            cut0 = min(max(fx0, lo), hi + 1)
            cut1 = min(max(fx1, lo), hi + 1)
            band = list(range(lo, cut0)) + list(range(cut1, hi + 1))
            for x in band:
                dist = abs(x + 0.5 - cx)
                o = half - dist + 0.5
                if o <= 0.0:
                    continue
                o = 1.0 if o > 1.0 else o
                f = half - stroke_w - dist + 0.5
                f = 0.0 if f <= 0.0 else (1.0 if f > 1.0 else f)
                col = _lerp(bg, stroke, o)
                if f > 0.0:
                    col = _lerp(col, fill, f)
                row[x] = _rgb_hex(col)
        rows.append("{" + " ".join(row) + "}")

    img = tk.PhotoImage(master=master, width=w, height=h)
    img.put(" ".join(rows))
    _ROUND_CACHE[key] = img
    return img


def make_switch_image(master, w: int, h: int, on: bool, bg_hex: str,
                      track_off: str = "#2B3547", track_on: str = "#4E9BFF",
                      knob_off: str = "#8C97AC", knob_on: str = "#FFFFFF"):
    """开关：跑道形轨道 + 圆钮，4x 超采样，带缓存"""
    w, h = max(8, int(w)), max(8, int(h))
    key = (w, h, bool(on), bg_hex, track_off, track_on, knob_off, knob_on)
    hit = _SWITCH_CACHE.get(key)
    if hit is not None:
        return hit

    ss = 4
    bg = _hex_rgb(bg_hex)
    track = _hex_rgb(track_on if on else track_off)
    knob = _hex_rgb(knob_on if on else knob_off)
    r = h / 2.0
    kd = h - 4.0
    kx = (2.0 + kd / 2.0) if not on else (w - 2.0 - kd / 2.0)
    ky = h / 2.0

    def in_track(px, py):
        qx = min(max(px, r), w - r)
        return math.hypot(px - qx, py - h / 2.0) <= r

    def in_knob(px, py):
        return math.hypot(px - kx, py - ky) <= kd / 2.0

    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            rs = gs = bs = 0
            for j in range(ss):
                for i in range(ss):
                    px = x + (i + 0.5) / ss
                    py = y + (j + 0.5) / ss
                    if in_knob(px, py):
                        col = knob
                    elif in_track(px, py):
                        col = track
                    else:
                        col = bg
                    rs += col[0]
                    gs += col[1]
                    bs += col[2]
            n = ss * ss
            row.append(_rgb_hex((rs // n, gs // n, bs // n)))
        rows.append("{" + " ".join(row) + "}")

    img = tk.PhotoImage(master=master, width=w, height=h)
    img.put(" ".join(rows))
    _SWITCH_CACHE[key] = img
    return img


class SettingsPanel:
    """设置面板：从右端齿轮点开。控件全部画布自绘，点击由自己做命中测试，
    不走 canvas tag 绑定（底图会盖住控件导致点不动）。"""

    CARD_BG = "#141A24"
    CARD_STROKE = "#2A3445"
    ITEM_BG = "#242D3C"
    ITEM_STROKE = "#303B4E"
    ACTIVE_BG = "#4E9BFF"
    DANGER_BG = "#3A2027"
    DANGER_STROKE = "#5A2A33"
    DANGER_TEXT = "#FF8A8A"
    # 上面这些由 apply_theme() 按主题改写；面板逻辑尺寸放这里，预热位图时也用同一份
    W_LOG = 300
    H_LOG = 502

    def __init__(self, app):
        self.app = app
        S = app.S
        self.w = S(self.W_LOG)
        self.h = S(self.H_LOG)
        self.top = tk.Toplevel(app.root)
        self.top.overrideredirect(True)
        self.top.configure(bg=KEY_COLOR)
        try:
            self.top.wm_attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError:
            pass
        self.top.wm_attributes("-topmost", True)

        x = app.anchor_cx - self.w // 2
        x = max(S(8), min(x, app.root.winfo_screenwidth() - self.w - S(8)))
        y = app.anchor_y + app.h0 + S(9)
        self.top.geometry(f"{self.w}x{self.h}+{x}+{y}")

        fam = app.f_label.cget("family")
        fs = app.fit
        self.f_title = tkfont.Font(family=fam, size=max(9, int(round(11 * fs))),
                                   weight="bold")
        self.f_row = tkfont.Font(family=fam, size=max(7, int(round(8 * fs))))
        self.f_small = tkfont.Font(family=fam, size=max(6, int(round(7 * fs))))

        self.canvas = tk.Canvas(self.top, bg=KEY_COLOR, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._imgs = []
        self._hits = []
        self._cursor = ""
        self._build()
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.top.bind("<Escape>", lambda _e: self.close())
        self.top.lift()

        # 苹果式的淡入：alpha 从 0 走到 1（系统不支持就跳过）
        self._fade_step = 0
        try:
            self.top.wm_attributes("-alpha", 0.0)
        except tk.TclError:
            self._fade_step = 99
        else:
            self.top.after(16, self._fade_in)

    def _fade_in(self):
        if self._fade_step >= 99:
            return
        self._fade_step += 1
        a = min(1.0, self._fade_step / 7.0)
        try:
            self.top.wm_attributes("-alpha", a)
        except tk.TclError:
            self._fade_step = 99
            return
        if a < 1.0:
            self.top.after(16, self._fade_in)

    # ------------------------------------------------------------ 绘制
    def _keep(self, img):
        self._imgs.append(img)
        return img

    def _pill(self, bw, bh, fill, stroke=""):
        return self._keep(make_round_image(self.app.root, bw, bh, bh / 2.0, fill,
                                           stroke, self.CARD_BG, stroke_w=EDGE_W))

    def _hit(self, x0, y0, x1, y1, kind, payload=None):
        self._hits.append((x0, y0, x1, y1, kind, payload))

    def _build(self):
        c = self.canvas
        c.delete("all")
        self._imgs = []
        self._hits = []
        S = self.app.S
        w, h, pad = self.w, self.h, S(18)

        c.create_image(0, 0, anchor="nw", image=self._keep(
            make_round_image(self.app.root, w, h, S(16), self.CARD_BG,
                             self.CARD_STROKE, KEY_COLOR, stroke_w=EDGE_W)))

        c.create_text(pad, S(24), text="设置", anchor="w", font=self.f_title, fill=TEXT)

        cxx = w - pad - S(11)
        c.create_image(cxx, S(24), image=self._keep(
            make_round_image(self.app.root, S(22), S(22), S(11), self.ITEM_BG, "", self.CARD_BG)))
        c.create_text(cxx, S(23), text="×", font=self.f_title, fill=MUTED)
        self._hit(cxx - S(13), S(11), cxx + S(13), S(37), "close")

        # 主题：四枚糖果胶囊
        c.create_text(pad, S(58), text="主题", anchor="w", font=self.f_row, fill=MUTED)
        self._seg_row(S(94), [(k, THEMES[k]["label"]) for k in THEME_ORDER],
                      self.app.theme, "theme")

        # 刷新频率
        c.create_text(pad, S(130), text="刷新频率", anchor="w", font=self.f_row, fill=MUTED)
        cur = int(round(self.app.refresh_interval))
        self._seg_row(S(166), [(1, "1 秒"), (2, "2 秒"), (5, "5 秒")], cur, "rate")

        # 开关行
        self._switch_row(S(204), "图片轮换", self.app._photo_on, "photo")

        # 轮换间隔：三档，选了立刻生效（默认 25 秒）
        c.create_text(pad, S(248), text="轮换间隔", anchor="w", font=self.f_row, fill=MUTED)
        self._seg_row(S(284), [(10, "10 秒"), (25, "25 秒"), (60, "60 秒")],
                      int(self.app._photo_interval), "photo_gap")

        self._switch_row(S(330), "开机自启动", self.app._autostart_enabled(), "auto")
        self._switch_row(S(368), "全屏时自动隐藏", self.app._fs_hide, "fs")

        # 动作按钮
        self._action(S(410), "收起为图标", "collapse")
        self._action(S(452), "退出灵动岛", "quit", danger=True)

        with self.app.sampler.lock:
            up = self.app.sampler.data.get("uptime")
        if up:
            c.create_text(w // 2, S(486), text=fmt_uptime(up), font=self.f_small, fill=MUTED)

    def _seg_row(self, y, items, current, kind):
        """一排等宽胶囊分段：既是主题选择也是刷新频率选择"""
        c = self.canvas
        S = self.app.S
        pad = S(18)
        gap = S(8)
        n = max(1, len(items))
        seg_w = (self.w - pad * 2 - gap * (n - 1)) // n
        seg_h = S(30)
        for i, (val, label) in enumerate(items):
            bx = pad + i * (seg_w + gap) + seg_w // 2
            on = (val == current)
            img = self._pill(seg_w, seg_h, self.ACTIVE_BG if on else self.ITEM_BG,
                             "" if on else self.ITEM_STROKE)
            c.create_image(bx, y, image=img)
            c.create_text(bx, y, text=label, font=self.f_row,
                          fill=PILL_ON_TEXT if on else PILL_OFF_TEXT)
            self._hit(bx - seg_w // 2, y - seg_h // 2,
                      bx + seg_w // 2, y + seg_h // 2, kind, val)

    def _switch_row(self, y, label, on, kind):
        c = self.canvas
        S = self.app.S
        c.create_text(S(18), y, text=label, anchor="w", font=self.f_row, fill=TEXT)
        sw_w, sw_h = S(46), S(26)
        sw_x = self.w - S(18) - sw_w // 2
        c.create_image(sw_x, y, image=self._keep(
            make_switch_image(self.app.root, sw_w, sw_h, on, self.CARD_BG,
                              track_off=SW_TRACK, track_on=self.ACTIVE_BG,
                              knob_off=SW_KNOB, knob_on="#FFFFFF")))
        self._hit(sw_x - sw_w // 2, y - sw_h // 2, sw_x + sw_w // 2, y + sw_h // 2,
                  kind)

    def _action(self, y, label, kind, danger=False):
        c = self.canvas
        S = self.app.S
        bw = self.w - S(36)
        bh = S(34)
        c.create_image(self.w // 2, y, image=self._pill(
            bw, bh, self.DANGER_BG if danger else self.ITEM_BG,
            self.DANGER_STROKE if danger else self.ITEM_STROKE))
        c.create_text(self.w // 2, y, text=label, font=self.f_row,
                      fill=self.DANGER_TEXT if danger else TEXT)
        self._hit(self.w // 2 - bw // 2, y - bh // 2,
                  self.w // 2 + bw // 2, y + bh // 2, kind)

    # ------------------------------------------------------------ 交互
    def _pick(self, x, y):
        for (x0, y0, x1, y1, kind, payload) in self._hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return kind, payload
        return None, None

    def _set_cursor(self, name):
        if name != self._cursor:
            self._cursor = name
            try:
                self.canvas.configure(cursor=name)
            except tk.TclError:
                pass

    def _on_motion(self, e):
        kind, _ = self._pick(e.x, e.y)
        self._set_cursor("hand2" if kind else "")

    def _on_click(self, e):
        kind, payload = self._pick(e.x, e.y)
        if kind == "close":
            self.close()
        elif kind == "theme":
            self.set_theme(payload)
            return
        elif kind == "rate":
            self.set_rate(payload)
        elif kind == "photo":
            self.toggle_photo()
        elif kind == "photo_gap":
            self.set_photo_gap(payload)
        elif kind == "auto":
            self.toggle_autostart()
        elif kind == "fs":
            self.toggle_fullscreen()
        elif kind == "collapse":
            self.close()
            self.app._collapse()
        elif kind == "quit":
            self.quit_app()

    def set_rate(self, val):
        self.app.set_rate_value(val)
        self._build()

    def set_theme(self, name):
        # 换主题要整块重建（面板底色、位图缓存都变了），app.set_theme 会把本面板换成新的
        self.app.set_theme(name)

    def toggle_autostart(self):
        self.app._auto_var = tk.BooleanVar(value=self.app._autostart_enabled())
        self.app._toggle_autostart()
        self._build()

    def toggle_fullscreen(self):
        self.app._fs_hide = not self.app._fs_hide
        if not self.app._fs_hide and self.app._hidden_for_fullscreen:
            self.app._show_island()
        self._build()

    def toggle_photo(self):
        self.app.set_photo_mode(not self.app._photo_on)
        self._build()

    def set_photo_gap(self, sec):
        self.app.set_photo_interval(sec)
        self._build()

    def quit_app(self):
        self.close()
        self.app._quit()

    def close(self):
        try:
            self.top.destroy()
        except Exception:
            pass
        self.app._settings = None


class IslandApp:
    def __init__(self):
        self.dpi = self._init_dpi()
        self.scale = self.dpi / 96.0
        self._scene_cache = {}        # {主题: 位图组}
        self._panel_cache = {}        # {主题: 渐变底图}
        self._photo_src = {}          # {路径: (宽, 高, RGB)}
        self._photo_panel_cache = {}  # {(路径, 宽, 高, 主题): 底图}
        self._photo_idx = 0           # 当前轮到第几张
        self._photo_job = None        # 轮换定时器
        self._photo_interval = PHOTO_INTERVAL
        self._panel_raw = None        # 当前底图的原始 RGB（算过渡帧要用）
        self._fade_frames = []        # 下一张图的过渡位图（提前算好的）
        self._fade_ready_for = None   # 这些过渡帧对应的图片路径
        self._fade_target = None
        self._fade_next_raw = None
        self._fade_step = 0
        self._fade_anim = 0
        self._fade_job = None
        self._prep_job = None

        self.root = tk.Tk()
        # root 就绪后再挂一次钩子，补上 Tk 回调异常捕获（GUI 崩溃的主要来源）
        error_report.install_hooks(self.root)
        self.root.title(APP_NAME)
        self.root.configure(bg=KEY_COLOR)
        self.root.overrideredirect(True)
        try:
            self.root.wm_attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError:
            pass
        self.root.wm_attributes("-topmost", True)
        try:
            self.root.tk.call("tk", "scaling", self.dpi / 72.0)
        except Exception:
            pass

        # 台式机没有电池：同步探测一次，去掉电池列；网速列常驻。
        # 必须在算宽度/缩放（_fit_scale 用 self.strip_w）之前定下来。
        try:
            self.has_battery = _battery_info() is not None
        except Exception:
            self.has_battery = True        # 探测失败时保守显示，避免误隐藏
        self.columns = build_columns(self.has_battery)
        self.strip_w = strip_width(self.columns)

        # 按屏幕大小算出整体缩放系数（窄屏缩、宽屏适度放大）
        self.fit = self._fit_scale()
        self._morph_cache = {}        # 动画各高度底图 {键: 位图}
        self._morph_x = 0.0           # 悬停展开度 0..1
        self._morph_v = 0.0
        self._press_x = 0.0           # 按下压入量 0..1
        self._press_v = 0.0
        self._morph_job = None
        self._body_dy = 0             # 内容当前位移（相对静止位）
        self._hover = False
        self._pressed = False
        self._pw_steps = []           # 预渲染队列
        self._raw_path = None         # 当前 _panel_raw 属于哪张照片

        self._pick_fonts()
        self.w = self.S(self.strip_w)
        self.h = self.S(STRIP_H)
        self.h0 = self.h
        self.grow = self.S(MORPH_GROW)
        self.win_h = self.h + self.grow + self.S(MORPH_WIN_PAD)
        self.anchor_cx = self.root.winfo_screenwidth() // 2
        self.anchor_y = self.S(TOP_OFFSET)

        self.canvas = tk.Canvas(self.root, bg=KEY_COLOR, highlightthickness=0,
                                borderwidth=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self._cols = {}
        self._settings = None
        self._collapsed = False
        self._mini = None
        self._drag_off = None
        self._hidden_for_fullscreen = False
        self._fs_hide = False
        self.refresh_interval = 1.0
        self._cpu_name, self._mhz = _cpu_info()

        self.sampler = Sampler()
        self.sampler.start()

        cfg = _load_cfg()
        self.theme = cfg.get("theme") if cfg.get("theme") in THEMES else THEME_ORDER[0]
        # 配置里始终保存用户真实主题；轮换 / 异常都只改内存、不回写，退出即可还原。
        self._theme_before_photo = self.theme
        apply_theme(self.theme)

        # 轮播图只从程序旁独立目录 carousel/ 动态读取，不打包进程序。
        self._photos = _scan_photos()
        self._photos_ok = bool(self._photos)
        self._photo_on = bool(cfg.get("photos", True)) and self._photos_ok
        if self._photo_on:
            # 轮换开启：锁定经典黑（只改内存，不回写配置）
            if self.theme != "dark":
                apply_theme("dark")
                self.theme = "dark"
        elif not self._photos_ok:
            # 异常态（无 carousel 目录或目录里没有 PNG）：退化为经典黑 + 关闭开关
            self._photo_on = False
            if self.theme != "dark":
                apply_theme("dark")
                self.theme = "dark"
        try:
            self._photo_interval = max(2, min(600, int(cfg.get("photo_interval",
                                                                  PHOTO_INTERVAL))))
        except (TypeError, ValueError):
            self._photo_interval = PHOTO_INTERVAL

        self._bind()
        self._apply_geometry()
        self._build()
        self._update()

        self.root.after(500, self._ui_tick)
        self.root.after(2000, self._fullscreen_tick)
        self._photo_start()

    # ------------------------------------------------------------ 初始化
    @staticmethod
    def _init_dpi() -> int:
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
        try:
            return int(user32.GetDpiForSystem())
        except Exception:
            return 96

    def S(self, v):
        return int(round(v * self.scale * self.fit))

    def _fit_scale(self):
        """按屏幕逻辑宽度决定整体缩放：目标宽度 ≈ 屏宽的 45%，夹在 0.75~1.4 之间"""
        try:
            sw_log = self.root.winfo_screenwidth() / max(1.0, self.scale)
        except Exception:
            return 1.0
        if sw_log <= 0:
            return 1.0
        fit = (sw_log * FIT_RATIO) / float(self.strip_w)
        fit = max(FIT_MIN, min(FIT_MAX, fit))
        fit = min(fit, (sw_log * FIT_MAX_PCT) / float(self.strip_w))
        return round(fit, 3)

    def _pick_fonts(self):
        fams = set(tkfont.families(self.root))
        ui = next((f for f in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI")
                   if f in fams), "TkDefaultFont")
        num = next((f for f in ("Bahnschrift", "DIN Alternate", "Segoe UI")
                    if f in fams), ui)
        self._num_family, self._ui_family = num, ui
        fs = self.fit                                  # 字号跟着整体缩放
        self.f_val = tkfont.Font(family=num, size=max(8, int(round(11 * fs))),
                                 weight="bold")        # 主数值
        self.f_label = tkfont.Font(family=ui, size=max(6, int(round(7 * fs))))
        self.f_menu = tkfont.Font(family=ui, size=max(7, int(round(9 * fs))))

    def _bind(self):
        # 位置固定：左键拖动已禁用，只保留右键菜单
        self.root.bind("<Button-3>", self._on_menu)

    # ------------------------------------------------------------ 窗口几何
    def _apply_geometry(self):
        x = self.anchor_cx - self.w // 2
        self.root.geometry(f"{self.w}x{self.win_h}+{x}+{self.anchor_y}")

    def _panel_row(self, y):
        return panel_row_color(y, self.h, _hex_rgb(BG_TOP), _hex_rgb(BG_BOTTOM))

    # ------------------------------------------------------------ 底图：渐变 / 轮换图片
    def _panel_image(self):
        """当前该用的胶囊底图：开了图片轮换就取轮到的那张，否则用主题渐变"""
        if self._photo_on and self._photos:
            path = self._photos[self._photo_idx % len(self._photos)]
            img, self._panel_raw = self._photo_panel(path)
            self._raw_path = path
            return img
        self._panel_raw = None
        self._raw_path = None
        img = self._panel_cache.get(self.theme)
        if img is None:
            img = make_panel_image(self.root, self.w, self.h0)
            self._panel_cache[self.theme] = img
        return img

    def _photo_panel(self, path):
        """返回 (位图, 原始 RGB 像素)；后者留着做淡入淡出的两端"""
        key = (path, self.w, self.h0, self.theme)
        item = self._photo_panel_cache.get(key)
        if item is not None:
            return item
        sw, sh, rows = self._photo_source(path)
        img, raw = make_photo_panel_image(self.root, self.w, self.h0, sw, sh, rows,
                                          PHOTO_SCRIM_TOP, PHOTO_SCRIM_BOTTOM)
        item = (img, raw)
        self._photo_panel_cache[key] = item
        while len(self._photo_panel_cache) > 6:       # 只留最近几张，别把内存吃满
            self._photo_panel_cache.pop(next(iter(self._photo_panel_cache)))
        return item

    def _photo_source(self, path):
        src = self._photo_src.get(path)
        if src is None:
            with open(path, "rb") as fp:
                src = _png_decode(fp.read())
            self._photo_src[path] = src
        return src

    def _apply_panel(self):
        """只换底图，不重建整条胶囊（轮换时用它，几乎不闪）

        展开态也必须保持「底图高度 == self.h == 内容位移」，否则会出现
        底图还是 91 高、内容却已经下移 7px 的错位（内容探出胶囊底边）。
        """
        if getattr(self, "_panel_item", None) is None:
            return
        self._invalidate_fade()          # 底图变了，之前算的过渡帧就作废
        new = self._panel_image()
        self._morph_purge_photo()        # 新照片的动画帧全部重算，别混着旧图
        if self.h != self.h0:
            img = self._morph_panel(self.h)
            if img is None:              # 取不到就干净地退回静止态
                self._panel = new
                self.h = self.h0
                self._body_to(0)
            else:
                self._panel = img
        else:
            self._panel = new
        try:
            self.canvas.itemconfigure(self._panel_item, image=self._panel)
        except tk.TclError:
            pass
        # 新图的动画帧在空闲时补一遍，下一次悬停就是现成的
        try:
            self.root.after(200, self._morph_prewarm)
        except Exception:
            pass

    def _body_to(self, dy):
        """把 content 移到指定位移（内部只发一次 move，避免重复叠加）"""
        delta = dy - self._body_dy
        if delta:
            try:
                self.canvas.move("body", 0, delta)
            except tk.TclError:
                return
            self._body_dy = dy

    def _invalidate_fade(self):
        self._fade_frames = []
        self._fade_ready_for = None
        self._fade_target = None
        self._fade_next_raw = None

    def _warn_no_photos(self):
        """异常态（无轮播图）下尝试开启开关时的错误提示。"""
        try:
            if hasattr(self, "_photo_var"):
                self._photo_var.set(False)
        except tk.TclError:
            pass
        # 菜单项 / 面板点击时可能有 grab 未释放，错开一拍再弹，避免模态冲突
        self.root.after(60, self._show_no_photos_dialog)

    def _show_no_photos_dialog(self):
        try:
            messagebox.showerror(
                "无法开启图片轮换",
                "未检测到轮播图。\n\n请把 PNG 图片放进程序目录下的 "
                f"{PHOTO_DIR} 文件夹（文件夹名需为英文），\n"
                "或确认该文件夹存在且非空后重试。\n\n当前已退化为经典黑主题。",
                parent=self.root,
            )
        except Exception:
            pass

    def set_photo_mode(self, on):
        """开关图片轮换：底图和图标配色都要换，所以整条重画。
        轮换期间锁定经典黑：进入时记住用户主题并切到 dark，退出时还原。
        轮播图从外部目录动态读取；异常态尝试开启会被拦截并弹出错误提示。"""
        # 每次操作都重新扫描外部目录 —— 运行中补图 / 撤图都能生效
        self._photos = _scan_photos()
        self._photos_ok = bool(self._photos)
        if on and not self._photos_ok:
            # 异常态尝试开启：弹错误提示，保持关闭并维持在经典黑
            self._warn_no_photos()
            on = False
        new_on = bool(on) and self._photos_ok
        if new_on != self._photo_on:
            if new_on:
                # 进入轮换：记住用户主题，切经典黑。只改内存，不写配置。
                self._theme_before_photo = self.theme
                if self.theme != "dark":
                    apply_theme("dark")
                    self.theme = "dark"
                    self._scene_cache.clear()
            else:
                # 退出轮换：还原进入前的主题（配置里一直是它，无需回写）
                restore = getattr(self, "_theme_before_photo", None)
                if restore in THEMES and restore != self.theme:
                    apply_theme(restore)
                    self.theme = restore
                    self._scene_cache.clear()
        self._photo_on = new_on
        _cfg_patch({"photos": self._photo_on})
        if getattr(self, "_panel_item", None) is None:
            return
        self._fade_abort()
        self.canvas.delete("all")
        self._cols = {}
        self._build()
        self._update()
        self._photo_start()

    def set_photo_interval(self, sec):
        """改轮换间隔：存进配置，并把定时器按新间隔重排"""
        try:
            sec = int(sec)
        except (TypeError, ValueError):
            return
        self._photo_interval = max(2, min(600, sec))
        _cfg_patch({"photo_interval": self._photo_interval})
        self._photo_start()

    def advance_photo(self, step=1):
        """切到下一张：过渡帧要是提前算好了，就走一段淡入淡出，否则直接换"""
        if not self._photos:
            return
        new_idx = (self._photo_idx + step) % len(self._photos)
        self._fade_abort()
        self._photo_idx = new_idx
        # 过渡帧只有静止高度那一档，展开态直接换图，否则底图会比胶囊矮一截
        if (self._photo_on and self.h == self.h0 and self._fade_frames
                and self._fade_ready_for == self._photos[new_idx]
                and getattr(self, "_panel_item", None) is not None):
            self._fade_anim = 0
            self._fade_anim_tick()
            return
        self._apply_panel()
        self._prepare_next()

    def _fade_anim_tick(self):
        """每帧只是把算好的位图换上，不做任何像素运算，所以不会卡"""
        frames = self._fade_frames
        i = self._fade_anim
        if self.h != self.h0:            # 中途展开了：过渡帧是 h0 高的，立刻收尾
            self._fade_job = None
            self._apply_panel()
            self._prepare_next()
            return
        if i >= len(frames):
            self._fade_job = None
            self._apply_panel()          # 收尾换回清晰的原图
            self._prepare_next()
            return
        try:
            self.canvas.itemconfigure(self._panel_item, image=frames[i])
        except tk.TclError:
            self._fade_job = None
            return
        self._fade_anim += 1
        self._fade_job = self.root.after(max(12, PHOTO_FADE_MS // (len(frames) + 1)),
                                         self._fade_anim_tick)

    def _prepare_next(self):
        """趁两次轮换之间的空档，把下一张的过渡帧一帧一帧先算好"""
        self._prepare_abort()
        if not (self._photo_on and self._photos and self._panel_raw is not None):
            return
        if self._fade_job is not None:       # 动画还在放，等它放完再算
            self._prep_job = self.root.after(200, self._prepare_next)
            return
        self._prep_job = self.root.after(400, self._prepare_panel)

    def _prepare_panel(self):
        """第一步：把下一张的底图渲出来（没缓存过的话这是最贵的一步）"""
        self._prep_job = None
        if not (self._photo_on and self._photos and self._panel_raw is not None):
            return
        path = self._photos[(self._photo_idx + 1) % len(self._photos)]
        if self._fade_ready_for == path and self._fade_frames:
            return
        try:
            _img, raw = self._photo_panel(path)
        except Exception:
            return
        self._fade_frames = []
        self._fade_target = path
        self._fade_next_raw = raw
        self._fade_step = 1
        self._prep_job = self.root.after(60, self._prepare_frame)

    def _prepare_frame(self):
        """第二步：一帧算一张过渡位图，算完就排下一帧，不占用连续时间"""
        self._prep_job = None
        if self._fade_next_raw is None or self._panel_raw is None:
            return
        i = self._fade_step
        if i >= PHOTO_FADE_STEPS:
            self._fade_ready_for = self._fade_target
            self._fade_next_raw = None
            return
        t = i / float(PHOTO_FADE_STEPS)
        try:
            body = _blend_rgb(self._panel_raw, self._fade_next_raw, t)
            img = _rgb_photo(self.root, self.w, self.h0, body)
        except Exception:
            self._fade_next_raw = None
            return
        self._fade_frames.append(img)
        self._fade_step += 1
        self._prep_job = self.root.after(60, self._prepare_frame)

    def _prepare_abort(self):
        if self._prep_job is not None:
            try:
                self.root.after_cancel(self._prep_job)
            except Exception:
                pass
            self._prep_job = None

    def _fade_abort(self):
        if self._fade_job is not None:
            try:
                self.root.after_cancel(self._fade_job)
            except Exception:
                pass
            self._fade_job = None
        self._fade_anim = 0

    def _photo_start(self):
        self._photo_stop()
        if self._photo_on and self._photos:
            self._photo_job = self.root.after(self._photo_interval * 1000, self._photo_tick)
            self._prepare_next()

    def _photo_stop(self):
        if self._photo_job is not None:
            try:
                self.root.after_cancel(self._photo_job)
            except Exception:
                pass
            self._photo_job = None

    def _photo_tick(self):
        self._photo_job = None
        if not self._collapsed:
            self.advance_photo()
        self._photo_start()

    # ------------------------------------------------------------ 绘制
    def _build(self):
        c = self.canvas
        c.delete("all")
        self._morph_reset()
        self.w = self.S(self.strip_w)
        self.h = self.h0 = self.S(STRIP_H)
        self.grow = self.S(MORPH_GROW)
        self.win_h = self.h + self.grow + self.S(MORPH_WIN_PAD)
        w, h = self.w, self.h
        sc = self._scene()
        self._avatar_imgs = sc["avatars"]
        self._gear_imgs = sc["gears"]

        # 胶囊本体：默认渐变底，开了图片轮换就用当前那张图
        self._fade_abort()
        self._prepare_abort()
        self._invalidate_fade()
        self._panel = self._panel_image()
        self._panel_item = c.create_image(0, 0, image=self._panel, anchor="nw")

        # 左侧蛋仔头像（原图，按负载轻度变色）
        self._avatar = c.create_image(self.S(AVATAR_CX), h // 2,
                                      image=self._avatar_imgs[COL_DOT_OK], anchor="center")

        # 五列：左侧小圆环 + 右侧名称 / 数值
        # 图片模式下底色是照片：文字转白、分隔线用半透明白，才压得住画面
        txt_col = "#FFFFFF" if self._photo_on else TEXT
        div_col = "#FFFFFF" if self._photo_on else DIVIDER
        div_stipple = "gray50" if self._photo_on else ""
        self._cols = {}
        x = self.S(PAD_L)
        for idx, (key, label, cw) in enumerate(self.columns):
            cw_px = self.S(cw)
            if idx:
                gx = x - self.S(COL_GAP) // 2
                c.create_line(gx, self.S(11), gx, h - self.S(11), fill=div_col,
                              width=self.S(1), stipple=div_stipple)
            ring = sc["rings"][key]
            c.create_image(x + self.S(9), h // 2, image=ring.photo, anchor="center")
            tx = x + self.S(23)
            lab = c.create_text(tx, self.S(13), text=label, anchor="w",
                                font=self.f_label, fill=ACC[key])
            val = c.create_text(tx, self.S(28), text="--", anchor="w",
                                font=self.f_val, fill=txt_col)
            self._cols[key] = {"lab": lab, "val": val, "ring": ring}
            x += cw_px + self.S(COL_GAP)

        # 右侧齿轮：单击直接打开设置面板（位置固定，只响应单击）
        gear_cx = w - self.S(PAD_R) - self.S(GEAR_W) // 2
        gx = gear_cx - self.S(GEAR_W) // 2 - self.S(COL_GAP) // 2
        c.create_line(gx, self.S(11), gx, h - self.S(11), fill=div_col,
                      width=self.S(1), stipple=div_stipple)
        self._gear = c.create_image(gear_cx, h // 2, image=self._gear_imgs[False],
                                    anchor="center")
        c.tag_bind(self._gear, "<Enter>", self._gear_hover_on)
        c.tag_bind(self._gear, "<Leave>", self._gear_hover_off)
        c.tag_bind(self._gear, "<Button-1>", self._on_gear_click)

        # 交互动画：除底图外所有内容统一打 body 标签，动画时只平移这一组
        for it in c.find_all():
            if it != self._panel_item:
                c.addtag_withtag("body", it)
        c.bind("<Enter>", self._morph_enter)
        c.bind("<Leave>", self._morph_leave)
        c.bind("<ButtonPress-1>", self._morph_press)
        c.bind("<ButtonRelease-1>", self._morph_release)
        self.root.after(1400, self._morph_prewarm)

    # ------------------------------------------------------------ 交互动画（苹果式弹簧）
    def _morph_reset(self):
        """停掉动画并归位：重建 / 收起前都要回到静止态"""
        if self._morph_job is not None:
            try:
                self.root.after_cancel(self._morph_job)
            except Exception:
                pass
            self._morph_job = None
        self._morph_x = self._morph_v = 0.0
        self._press_x = self._press_v = 0.0
        self._hover = self._pressed = False
        self._body_to(0)
        if getattr(self, "h0", None):
            self.h = self.h0

    def _morph_kick(self):
        if self._morph_job is None and not self._collapsed:
            self._morph_job = self.root.after(16, self._morph_tick)

    def _morph_enter(self, _e=None):
        if self._collapsed:
            return
        self._hover = True
        self._morph_kick()

    def _morph_leave(self, _e=None):
        self._hover = False
        self._pressed = False
        self._morph_kick()

    def _morph_press(self, _e=None):
        if self._collapsed:
            return
        self._hover = True
        self._pressed = True
        self._morph_kick()

    def _morph_release(self, _e=None):
        self._pressed = False
        self._morph_kick()

    def _morph_tick(self):
        """两个弹簧：悬停展开量 + 按下压入量，到点了就停，不空转"""
        self._morph_job = None
        dt = 0.016
        tx = 1.0 if self._hover else 0.0
        px = 1.0 if self._pressed else 0.0
        busy = False
        for name, target in (("_morph", tx), ("_press", px)):
            x = getattr(self, name + "_x")
            v = getattr(self, name + "_v")
            v += ((target - x) * SPRING_K - v * SPRING_D) * dt
            x += v * dt
            if abs(x - target) < 0.004 and abs(v) < 0.04:
                x, v = target, 0.0
            else:
                busy = True
            setattr(self, name + "_x", x)
            setattr(self, name + "_v", v)
        self._apply_morph()
        if busy:
            self._morph_job = self.root.after(16, self._morph_tick)

    def _apply_morph(self):
        if getattr(self, "_panel_item", None) is None or self._collapsed:
            return
        k = max(0.0, min(1.12, self._morph_x))
        h = int(round(self.h0 + self.grow * k))
        h = max(self.h0, min(self.h0 + self.grow + 1, h))
        if h != self.h:
            try:
                img = self._panel_image() if h == self.h0 else self._morph_panel(h)
            except Exception:
                img = None
            if img is not None:          # 底图没就绪就保持原样，绝不让内容漂出胶囊
                self.h = h
                self._panel = img
                try:
                    self.canvas.itemconfigure(self._panel_item, image=img)
                except tk.TclError:
                    return
        dip = int(round(self.S(PRESS_DIP) * self._press_x))
        dy = (self.h - self.h0) // 2 + dip
        self._body_to(dy)

    def _morph_panel(self, h):
        """按高度取一帧胶囊底图；照片模式下直接拿当前底图的像素逐帧裁行套遮罩"""
        if self._photo_on and self._photos:
            base = self._photo_morph_base()
            if base is None:
                return None
            path = self._photos[self._photo_idx % len(self._photos)]
            key = ("p", path, self.theme, self.w, h)
            img = self._morph_cache.get(key)
            if img is None:
                bw, bh, rows = base
                img = make_morph_frame(self.root, self.w, h, bw, bh, rows,
                                       margin=max(3, self.S(1) + 1))
                self._morph_store(key, img)
            return img
        key = ("g", self.theme, self.w, h)
        img = self._morph_cache.get(key)
        if img is None:
            img = make_panel_image(self.root, self.w, h)
            self._morph_store(key, img)
        return img

    def _photo_morph_base(self):
        """照片动画素材：直接用当前已渲好的这张底图（h0 高的原始像素），零额外开销"""
        if not (self._photo_on and self._panel_raw is not None):
            return None
        cur = self._photos[self._photo_idx % len(self._photos)]
        if self._raw_path != cur:
            return None                  # 底图还停在上一张（淡入淡出中），不能当素材
        return (self.w, self.h0, self._panel_raw)

    def _morph_purge_photo(self):
        """照片换了就把照片类缓存全清掉，避免有的高度还是旧图"""
        for k in [k for k in self._morph_cache if k[0] == "p"]:
            self._morph_cache.pop(k, None)

    def _morph_store(self, key, img):
        self._morph_cache[key] = img
        while len(self._morph_cache) > 48:          # 只留最近若干帧，别把内存吃满
            self._morph_cache.pop(next(iter(self._morph_cache)))

    def _morph_prewarm(self):
        """空闲时逐帧预渲染，第一次悬停就不会卡在渲染上"""
        self._pw_steps = ["base"] + list(range(self.h0 + 1, self.h0 + self.grow + 2))
        self._pw_kick()

    def _pw_kick(self):
        if not self._pw_steps:
            return
        step = self._pw_steps.pop(0)
        try:
            if step == "base":
                self._photo_morph_base()
            else:
                self._morph_panel(step)
        except Exception:
            return
        self.root.after(80, self._pw_kick)

    def _scene(self):
        """生成 / 复用整条胶囊的位图组（图标都是带透明通道的，底色交给下层）"""
        cache_key = (self.theme, self._photo_on)
        sc = self._scene_cache.get(cache_key)
        if sc is not None:
            return sc

        old = (self.w, self.h)
        try:
            self.w = self.S(self.strip_w)
            self.h = self.S(STRIP_H)

            av_d = self.S(AVATAR_D)
            avatars = {col: make_avatar_alpha(self.root, av_d,
                                              AVATAR_TINTS.get(col, (1.0, 1.0, 1.0)))
                       for col in (COL_DOT_OK, COL_DOT_WARN, COL_DOT_HOT)}

            gear_d = self.S(GEAR_D)
            gears = {False: make_sticker_alpha(self.root, gear_d),
                     True: make_sticker_alpha(self.root, gear_d, boost=1.12)}

            rd = self.S(16)
            # 高密度屏上圆环采样提到 6×，低密度保持 4×（5 个环一次算完，别拖启动）
            rss = 6 if self.scale * self.fit >= 1.75 else 4
            if self._photo_on:      # 照片上：轨道用半透明白，深色光晕压住花哨背景
                rings = {key: RingGauge(self.root, rd, self.S(2), "#FFFFFF", ACC[key],
                                        track_a=96, ss=rss,
                                        halo_hex="#06090E", halo_a=118)
                         for key, _label, _cw in self.columns}
            else:
                # 纯色底：光晕取轨道色，浅色主题才不会在环外显出一道黑边
                rings = {key: RingGauge(self.root, rd, self.S(2), TRACK, ACC[key], ss=rss,
                                        halo_hex=TRACK, halo_a=48)
                         for key, _label, _cw in self.columns}

            sc = {"avatars": avatars, "gears": gears, "rings": rings}
        finally:
            self.w, self.h = old

        self._scene_cache[cache_key] = sc
        return sc

    def _prewarm_images(self):
        """预热设置面板用到的位图，第一次点齿轮就不卡了"""
        try:
            S, R, P = self.S, self.root, SettingsPanel
            make_round_image(R, S(P.W_LOG), S(P.H_LOG), S(16), P.CARD_BG,
                             P.CARD_STROKE, KEY_COLOR, stroke_w=EDGE_W)
            make_round_image(R, S(22), S(22), S(11), P.ITEM_BG, "", P.CARD_BG)
            make_round_image(R, S(P.W_LOG) - S(36), S(34), S(34) / 2.0,
                             P.ITEM_BG, P.ITEM_STROKE, P.CARD_BG, stroke_w=EDGE_W)
            make_round_image(R, S(P.W_LOG) - S(36), S(34), S(34) / 2.0,
                             P.DANGER_BG, P.DANGER_STROKE, P.CARD_BG, stroke_w=EDGE_W)
            for n in (3, 4):
                seg_w = (S(P.W_LOG) - S(18) * 2 - S(8) * (n - 1)) // n
                make_round_image(R, seg_w, S(30), S(30) / 2.0, P.ACTIVE_BG, "",
                                 P.CARD_BG, stroke_w=EDGE_W)
                make_round_image(R, seg_w, S(30), S(30) / 2.0, P.ITEM_BG,
                                 P.ITEM_STROKE, P.CARD_BG, stroke_w=EDGE_W)
            for on in (True, False):
                make_switch_image(R, S(46), S(26), on, P.CARD_BG,
                                  track_off=SW_TRACK, track_on=P.ACTIVE_BG,
                                  knob_off=SW_KNOB, knob_on="#FFFFFF")
        except Exception:
            pass

    def _gear_hover_on(self, _e=None):
        self.canvas.itemconfigure(self._gear, image=self._gear_imgs[True])
        self.canvas.configure(cursor="hand2")

    def _gear_hover_off(self, _e=None):
        self.canvas.itemconfigure(self._gear, image=self._gear_imgs[False])
        self.canvas.configure(cursor="")

    def _on_gear_click(self, e):
        self._toggle_settings()

    def _toggle_settings(self):
        """齿轮 = 打开 / 收起设置面板"""
        if self._settings is not None:
            self._settings.close()
            return
        self._settings = SettingsPanel(self)

    # ------------------------------------------------------------ 收起 / 展开
    def _collapse(self):
        """收起：长条缩成一枚可以随便拖的小图，右键恢复"""
        if self._collapsed:
            return
        if self._settings is not None:
            self._settings.close()

        self._morph_reset()
        img = tk.PhotoImage(master=self.root, data=assets.mini_b64())
        mw, mh = img.width(), img.height()
        mini = tk.Toplevel(self.root)
        mini.overrideredirect(True)
        mini.configure(bg=MINI_BG)
        try:
            mini.wm_attributes("-transparentcolor", MINI_BG)
        except tk.TclError:
            pass
        mini.wm_attributes("-topmost", True)
        cv = tk.Canvas(mini, width=mw, height=mh, bg=MINI_BG,
                       highlightthickness=0, bd=0)
        cv.pack()
        cv.create_image(0, 0, image=img, anchor="nw")
        self._mini_img = img
        mini.geometry(f"{mw}x{mh}+{self.anchor_cx - mw // 2}+{self.anchor_y}")
        cv.bind("<Button-1>", self._mini_press)
        cv.bind("<B1-Motion>", self._mini_drag)
        cv.bind("<Button-3>", lambda _e: self._expand())
        self._mini = mini
        mini.deiconify()
        mini.lift()
        self._collapsed = True
        self.root.update_idletasks()
        self.root.withdraw()

    def _mini_press(self, e):
        if self._mini is None:
            return
        self._drag_off = (e.x_root - self._mini.winfo_rootx(),
                          e.y_root - self._mini.winfo_rooty())

    def _mini_drag(self, e):
        if self._drag_off is None or self._mini is None:
            return
        self._mini.geometry("+%d+%d" % (e.x_root - self._drag_off[0],
                                        e.y_root - self._drag_off[1]))

    def _expand(self):
        """右键小图标：恢复成原来的长条，并回到屏幕顶部中央原位"""
        if not self._collapsed:
            return
        self._collapsed = False
        if self._mini is not None:
            try:
                self._mini.destroy()
            except Exception:
                pass
            self._mini = None
        self._drag_off = None
        self.anchor_cx = self.root.winfo_screenwidth() // 2
        self.anchor_y = self.S(TOP_OFFSET)
        self._morph_reset()
        self.root.deiconify()
        self._apply_geometry()
        try:
            self.root.wm_attributes("-topmost", True)
            self.root.lift()
        except tk.TclError:
            pass
        self._update()

    def _set_ring(self, key, ratio):
        """占用比例画成环形进度（圆圈），而不是横条"""
        col = self._cols.get(key)
        if col:
            col["ring"].render(ratio)

    # ------------------------------------------------------------ 数值刷新
    def _update(self):
        if self._collapsed:
            return
        with self.sampler.lock:
            d = dict(self.sampler.data)
        if not d or not self._cols:
            return
        c = self.canvas
        cpu = d.get("cpu", 0.0)
        mem_total = d.get("mem_total", 0) or 1
        mem_used = d.get("mem_used", 0)
        mem_ratio = mem_used / mem_total
        disk_total = d.get("disk_total", 0) or 1
        disk_free = d.get("disk_free", 0)
        disk_used = 1.0 - disk_free / disk_total
        gpu_util = d.get("gpu_util")
        gpu_temp = d.get("gpu_temp")
        battery = d.get("battery")
        net_rx = d.get("net_rx", 0.0)
        net_tx = d.get("net_tx", 0.0)

        cols = self._cols

        c.itemconfigure(cols["cpu"]["val"], text=f"{cpu * 100:.0f}%")
        self._set_ring("cpu", cpu)

        c.itemconfigure(cols["mem"]["val"], text=f"{mem_ratio * 100:.0f}%")
        self._set_ring("mem", mem_ratio)

        if gpu_util is None:
            c.itemconfigure(cols["gpu"]["val"], text="—")
        else:
            pct = gpu_util * 100.0
            c.itemconfigure(cols["gpu"]["val"],
                            text=(f"{pct:.1f}%" if pct < 9.95 else f"{pct:.0f}%"))
            self._set_ring("gpu", gpu_util)
        if gpu_temp:
            c.itemconfigure(cols["gpu"]["lab"], text=f"GPU {gpu_temp}°")

        c.itemconfigure(cols["disk"]["val"], text=f"{disk_used * 100:.0f}%")
        self._set_ring("disk", disk_used)

        # 电池列：台式机没有这一列（构建时已移除），有电池才更新
        if "bat" in cols:
            if battery is None:
                c.itemconfigure(cols["bat"]["lab"], text="电池")
                c.itemconfigure(cols["bat"]["val"], text="AC")
                self._set_ring("bat", 0.0)
            else:
                pct, plugged = battery
                c.itemconfigure(cols["bat"]["lab"], text="电池·充" if plugged else "电池")
                c.itemconfigure(cols["bat"]["val"], text=f"{pct}%")
                self._set_ring("bat", pct / 100.0)

        # 网速列：下行 / 上行速率，圆环按下行占参考带宽的比例
        if "net" in cols:
            c.itemconfigure(cols["net"]["val"],
                            text=f"↓{fmt_speed(net_rx)} ↑{fmt_speed(net_tx)}")
            self._set_ring("net", min(1.0, net_rx / NET_RING_REF))

        imgs = self._avatar_imgs
        want = imgs[load_color(cpu)]
        if c.itemcget(self._avatar, "image") != str(want):
            c.itemconfigure(self._avatar, image=want)

    # ------------------------------------------------------------ 定时刷新
    def _ui_tick(self):
        try:
            if not self._hidden_for_fullscreen and not self._collapsed:
                self._update()
        finally:
            self.root.after(int(self.refresh_interval * 1000) // 2 or 500, self._ui_tick)

    # ------------------------------------------------------------ 右键菜单
    def _on_menu(self, e):
        self._open_menu(e.x_root, e.y_root)

    def _open_menu(self, x_root, y_root):
        # 打开菜单时轻扫一次外部目录，让「下一张图片 / 图片轮换」状态跟上文件变化
        self._photos = _scan_photos()
        self._photos_ok = bool(self._photos)
        m = tk.Menu(self.root, tearoff=0, bg=MENU_BG, fg=TEXT,
                    activebackground=MENU_ACTIVE, activeforeground=TEXT,
                    bd=0, font=self.f_menu)
        with self.sampler.lock:
            up = self.sampler.data.get("uptime")
        if up:
            m.add_command(label=fmt_uptime(up), state="disabled")
            m.add_separator()

        sub = tk.Menu(m, tearoff=0, bg=MENU_BG, fg=TEXT,
                      activebackground=MENU_ACTIVE, activeforeground=TEXT, bd=0,
                      font=self.f_menu)
        self._rate_var = tk.IntVar(value=int(self.refresh_interval))
        for txt, val in (("1 秒（流畅）", 1), ("2 秒（省电）", 2), ("5 秒（极省电）", 5)):
            sub.add_radiobutton(label=txt, variable=self._rate_var, value=val,
                                command=self._set_rate)
        m.add_cascade(label="刷新频率", menu=sub)

        tsub = tk.Menu(m, tearoff=0, bg=MENU_BG, fg=TEXT,
                       activebackground=MENU_ACTIVE, activeforeground=TEXT, bd=0,
                       font=self.f_menu)
        self._theme_var = tk.StringVar(value=self.theme)
        # 轮换模式锁定经典黑，主题项禁用，不再提供（点了也不会生效）的假入口
        theme_state = "disabled" if self._photo_on else "normal"
        for key in THEME_ORDER:
            tsub.add_radiobutton(label=THEMES[key]["label"], variable=self._theme_var,
                                 value=key, state=theme_state,
                                 command=lambda k=key: self.set_theme(k))
        m.add_cascade(label="主题", menu=tsub, state=theme_state)

        self._auto_var = tk.BooleanVar(value=self._autostart_enabled())
        m.add_checkbutton(label="开机自启动", variable=self._auto_var,
                          command=self._toggle_autostart)
        self._fs_var = tk.BooleanVar(value=self._fs_hide)
        m.add_checkbutton(label="全屏时自动隐藏", variable=self._fs_var,
                          command=self._set_fs_hide)

        self._photo_var = tk.BooleanVar(value=self._photo_on)
        m.add_separator()
        m.add_checkbutton(label="图片轮换", variable=self._photo_var,
                          command=lambda: self.set_photo_mode(self._photo_var.get()))
        m.add_command(label="下一张图片", state=("normal" if self._photos else "disabled"),
                      command=lambda: self.advance_photo())

        m.add_separator()
        m.add_command(label="问题反馈 / 生成报告",
                      command=lambda: self.root.after(60, self._open_report))
        m.add_command(label="收起为图标", command=self._collapse)
        m.add_command(label="退出", command=self._quit)
        try:
            m.tk_popup(x_root, y_root)
        finally:
            m.grab_release()

    def _open_report(self):
        """主动打开错误报告窗口：无异常时也生成环境信息，方便用户反馈问题。"""
        try:
            error_report.show_report(parent=self.root,
                                     title="{} · 问题反馈 / 错误报告".format(APP_NAME))
        except Exception:
            pass

    def set_rate_value(self, val):
        self.sampler.interval = float(val)
        self.refresh_interval = float(val)

    def set_theme(self, name):
        """切主题：换配色 -> 存配置 -> 长条和面板一起重画。
        轮换模式锁定经典黑：此时一切外部切主题请求都被忽略。"""
        if name not in THEMES:
            return
        if self._photo_on:
            return
        apply_theme(name)
        self.theme = name
        _cfg_patch({"theme": name})
        self._scene_cache.clear()
        self.canvas.delete("all")
        self._cols = {}
        self._build()
        self._update()
        if self._settings is not None:
            try:
                self._settings.close()
            except Exception:
                pass
            self._settings = SettingsPanel(self)

    def _set_rate(self):
        self.set_rate_value(self._rate_var.get())

    def _quit(self):
        if self._settings is not None:
            try:
                self._settings.top.destroy()
            except Exception:
                pass
            self._settings = None
        try:
            self.sampler.stop()
        finally:
            self.root.destroy()

    # ------------------------------------------------------------ 开机自启
    @staticmethod
    def _startup_dir():
        return os.path.join(os.environ.get("APPDATA", ""),
                            r"Microsoft\Windows\Start Menu\Programs\Startup")

    def _autostart_path(self):
        return os.path.join(self._startup_dir(), "动态岛硬件状态.vbs")

    def _autostart_enabled(self):
        try:
            return os.path.exists(self._autostart_path())
        except Exception:
            return False

    def _toggle_autostart(self):
        path = self._autostart_path()
        try:
            if self._autostart_enabled():
                os.remove(path)
            elif FROZEN:
                content = (
                    'Set sh = CreateObject("WScript.Shell")\r\n'
                    f'sh.Run Chr(34) & "{sys.executable}" & Chr(34), 0, False\r\n'
                )
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                pyw = sys.executable.replace("python.exe", "pythonw.exe")
                if not os.path.exists(pyw):
                    pyw = sys.executable
                content = (
                    'Set sh = CreateObject("WScript.Shell")\r\n'
                    f'sh.Run Chr(34) & "{pyw}" & Chr(34) & " " & Chr(34) & '
                    f'"{SCRIPT_PATH}" & Chr(34), 0, False\r\n'
                )
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
        except Exception:
            pass

    # ------------------------------------------------------------ 全屏自动隐藏
    def _set_fs_hide(self):
        self._fs_hide = bool(self._fs_var.get())
        if not self._fs_hide and self._hidden_for_fullscreen:
            self._show_island()

    def _fullscreen_tick(self):
        try:
            if self._fs_hide:
                if self._is_fullscreen_foreground():
                    if not self._hidden_for_fullscreen:
                        self._hidden_for_fullscreen = True
                        self.root.withdraw()
                elif self._hidden_for_fullscreen:
                    self._show_island()
        finally:
            self.root.after(1500, self._fullscreen_tick)

    def _show_island(self):
        self._hidden_for_fullscreen = False
        try:
            self.root.deiconify()
            self.root.wm_attributes("-topmost", True)
            self._apply_geometry()
        except Exception:
            pass

    def _is_fullscreen_foreground(self):
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        own = self.root.winfo_id()
        if hwnd == own or hwnd == user32.GetParent(own):
            return False
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "MultitaskingViewFrame"):
            return False
        rect = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        return rect.left <= 1 and rect.top <= 1 and rect.right >= sw - 1 and rect.bottom >= sh - 1

    # ------------------------------------------------------------ 启动
    def run(self):
        self.root.mainloop()


def main():
    # 注入应用信息 + 安装全局异常钩子：主线程 / Tk 回调 / 子线程的异常都能汇总成报告。
    error_report.set_app_info(APP_NAME, APP_VERSION, FROZEN, APP_DIR)
    error_report.install_hooks()
    try:
        IslandApp().run()
    except Exception:                            # GUI 程序没有控制台，弹错误报告
        error_report.handle_exception(sys.exc_info(), source="启动")


if __name__ == "__main__":
    main()
