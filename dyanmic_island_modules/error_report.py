# -*- coding: utf-8 -*-
"""灵动岛 · 错误报告与问题反馈。

发生未处理异常时，自动收集环境信息、异常类型与完整调用堆栈，生成一份
纯文本报告（可直接粘贴到反馈站），提供「一键复制」与「打开反馈网站」。
用户也可以随时通过右键菜单主动生成一份报告，附带复现步骤后提交。

对外接口：
    FEEDBACK_URL                        反馈地址
    set_app_info(name, version, frozen) 由主程序注入应用信息
    record_exception(exc_info, source)  记录一次异常（含子线程，供主动报告汇总）
    build_report(exc_info, note)        生成报告文本 -> str
    show_report(exc_info, parent, ...)  显示报告窗口（带一键复制）
    install_hooks(root)                 安装全局异常钩子
    handle_exception(...)               统一入口：记录 + 弹报告窗口

设计约束：本模块处于崩溃路径的最后一道环节，任何操作都不能再向外抛异常。
所有可能失败的调用都做兜底；子线程异常只记录、不弹窗（避免跨线程操作 Tk）。
"""
from __future__ import annotations

import datetime
import platform
import sys
import threading
import traceback
from collections import deque

FEEDBACK_URL = "https://eggyui.mysxl.cn"

_APP_NAME = "灵动岛"
_APP_VERSION = "?"
_FROZEN = False
_APP_DIR = ""

_HISTORY = deque(maxlen=12)   # 最近异常：[ (时间, 来源, 格式化文本), ... ]
_WINDOW = None                # 当前报告窗口，避免重复弹出


def set_app_info(name: str, version: str, frozen: bool, app_dir: str = "") -> None:
    """由主程序注入应用信息，保证报告里的版本号/运行方式准确。"""
    global _APP_NAME, _APP_VERSION, _FROZEN, _APP_DIR
    _APP_NAME = name or _APP_NAME
    _APP_VERSION = str(version or _APP_VERSION)
    _FROZEN = bool(frozen)
    _APP_DIR = app_dir or ""


def _safe(fn, default="未知"):
    try:
        value = fn()
        return str(value) if value not in (None, "") else default
    except Exception:
        return default


def env_info() -> dict:
    """收集运行环境。每项独立兜底，任何一项失败都不影响报告生成。"""
    run_mode = "exe 打包版" if _FROZEN else "源码 / 脚本版"
    info = {
        "应用": f"{_APP_NAME} {_APP_VERSION}（{run_mode}）",
        "生成时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "操作系统": "{sys} {release} ({ver})".format(
            sys=_safe(platform.system), release=_safe(platform.release),
            ver=_safe(platform.version)),
        "系统架构": "{machine} / {bits}".format(
            machine=_safe(platform.machine),
            bits=_safe(lambda: platform.architecture()[0])),
        "Python": "{ver} ({build})".format(
            ver=_safe(platform.python_version),
            build=_safe(lambda: sys.version.replace("\n", " "))),
    }
    if _APP_DIR:
        info["程序目录"] = _APP_DIR
    return info


def _format_exc(exc_info) -> str:
    try:
        return "".join(traceback.format_exception(*exc_info)).rstrip()
    except Exception:
        return "(调用堆栈格式化失败)"


def record_exception(exc_info=None, source: str = "程序") -> None:
    """把一次异常写入内存历史。子线程也走这里，只记录不弹窗。"""
    try:
        if not exc_info or not any(exc_info):
            exc_info = sys.exc_info()
        if not exc_info or not any(exc_info):
            return
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        text = _format_exc(exc_info)
        _HISTORY.append((stamp, source, text))
        try:
            print(f"[{_APP_NAME}] 捕获异常（{source}）:\n{text}", file=sys.stderr)
        except Exception:
            pass
    except Exception:
        pass


def build_report(exc_info=None, note: str = "") -> str:
    """生成可直接粘贴到反馈站的纯文本报告。"""
    lines = []
    lines.append("=" * 48)
    lines.append(" {} · 错误报告 / 问题反馈".format(_APP_NAME))
    lines.append("=" * 48)
    for key, value in env_info().items():
        lines.append("{}：{}".format(key, value))
    lines.append("")
    lines.append("反馈地址：{}".format(FEEDBACK_URL))
    lines.append("")

    has_exc = bool(exc_info and any(exc_info))
    lines.append("【本次错误】")
    if has_exc:
        etype, evalue, _ = exc_info
        lines.append("类型：{}".format(getattr(etype, "__name__", etype)))
        lines.append("信息：{}".format(evalue))
        lines.append("")
        lines.append("【调用堆栈】")
        lines.append(_format_exc(exc_info))
    else:
        lines.append("（无异常记录 —— 这是一份主动提交的反馈）")
    lines.append("")

    # 崩溃报告排除最后一条（即本次），主动报告则带上全部历史
    history = list(_HISTORY)
    if has_exc and history:
        history = history[:-1]
    if history:
        lines.append("【近期其他错误】")
        for stamp, source, text in history:
            lines.append("--- {} / {} ---".format(stamp, source))
            lines.append(text)
        lines.append("")

    lines.append("【问题说明 / 复现步骤】（请补充你刚才做了什么）")
    lines.append(note.strip() if note else "（在此填写）")
    lines.append("")
    lines.append("-- 报告结束 --")
    return "\n".join(lines)


def _widget_alive(widget) -> bool:
    try:
        return widget is not None and bool(widget.winfo_exists())
    except Exception:
        return False


def show_report(exc_info=None, parent=None, note: str = "", title: str | None = None):
    """显示报告窗口：文本可编辑（便于补充复现步骤），一键复制，打开反馈站。

    有可用的 parent 时用 Toplevel（不干扰主程序的默认根）；崩溃场景 parent 无效
    或缺失，则创建独立 Tk 根，并在关闭时恢复原来的默认根。
    """
    global _WINDOW
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except Exception:
        return None

    # 已有报告窗口：直接置前，避免重复弹出
    try:
        if _widget_alive(_WINDOW):
            _WINDOW.lift()
            _WINDOW.focus_force()
            return _WINDOW
    except Exception:
        pass

    use_parent = _widget_alive(parent)
    old_default = getattr(tk, "_default_root", None)
    try:
        root = tk.Toplevel(parent) if use_parent else tk.Tk()
    except Exception:
        try:
            root = tk.Tk()
            use_parent = False
        except Exception:
            return None

    text_value = build_report(exc_info, note)
    root.title(title or "{} · 错误报告".format(_APP_NAME))
    root.geometry("760x560")
    root.minsize(560, 400)
    try:
        root.configure(bg="#1e1e1e")
    except Exception:
        pass

    # 居中
    try:
        w, h = 760, 560
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry("+{}+{}".format((sw - w) // 2, (sh - h) // 2))
    except Exception:
        pass

    f_normal = tkfont.nametofont("TkDefaultFont")
    f_mono = ("Consolas", 10)
    display_url = FEEDBACK_URL.split("://")[-1]

    try:
        head = tk.Frame(root, bg="#1e1e1e")
        head.pack(fill="x", padx=14, pady=(14, 8))
        tk.Label(head, text="程序遇到问题，报告已自动生成", bg="#1e1e1e",
                 fg="#ffffff", font=(f_normal.cget("family"), 13, "bold")).pack(anchor="w")
        tip = ("点「一键复制」后粘贴到 {} 提交即可。\n"
               "你可以在下方文本框里补充复现步骤，复制内容会一起带上。").format(display_url)
        tk.Label(head, text=tip, bg="#1e1e1e", fg="#9cdcfe",
                 font=f_normal, justify="left", anchor="w").pack(fill="x", pady=(8, 0))
    except Exception:
        head = tk.Frame(root)
        head.pack(fill="x")

    box = tk.Frame(root)
    box.pack(fill="both", expand=True, padx=14, pady=6)
    txt = tk.Text(box, wrap="none", font=f_mono, bg="#252526", fg="#d4d4d4",
                  insertbackground="#ffffff", relief="flat", height=20)
    txt.pack(side="left", fill="both", expand=True)
    scroll = tk.Scrollbar(box, command=txt.yview)
    scroll.pack(side="right", fill="y")
    txt.configure(yscrollcommand=scroll.set)
    try:
        txt.insert("1.0", text_value)
    except Exception:
        pass

    status = tk.StringVar(value="")
    btns = tk.Frame(root, bg="#1e1e1e")
    btns.pack(fill="x", padx=14, pady=(6, 14))

    def _copy():
        try:
            content = txt.get("1.0", "end-1c")
            root.clipboard_clear()
            root.clipboard_append(content)
            root.update_idletasks()  # 把数据真正送到系统剪贴板
            status.set("已复制到剪贴板")
            copy_btn.configure(text="✓ 已复制")
            root.after(1500, lambda: copy_btn.configure(text="一键复制"))
        except Exception as exc:
            status.set("复制失败：{}".format(exc))

    def _open_site():
        try:
            import webbrowser
            webbrowser.open_new_tab(FEEDBACK_URL)
        except Exception:
            pass

    def _close():
        global _WINDOW
        try:
            root.destroy()
        except Exception:
            pass
        _WINDOW = None
        if not use_parent:
            try:
                tk._default_root = old_default
            except Exception:
                pass

    try:
        copy_btn = tk.Button(btns, text="一键复制", command=_copy, width=12,
                             bg="#0e639c", fg="#ffffff", activebackground="#1177bb",
                             activeforeground="#ffffff", relief="flat")
        copy_btn.pack(side="left", padx=(0, 8))
        tk.Button(btns, text="打开反馈网站", command=_open_site, width=14,
                  bg="#3a3d41", fg="#ffffff", activebackground="#4a4d51",
                  activeforeground="#ffffff", relief="flat").pack(side="left", padx=(0, 8))
        tk.Button(btns, text="关闭", command=_close, width=8,
                  bg="#3a3d41", fg="#ffffff", activebackground="#4a4d51",
                  activeforeground="#ffffff", relief="flat").pack(side="left")
        tk.Label(btns, textvariable=status, bg="#1e1e1e", fg="#4ec9b0").pack(side="right")
    except Exception:
        copy_btn = None

    root.protocol("WM_DELETE_WINDOW", _close)
    try:
        root.attributes("-topmost", True)
        root.after(1800, lambda: root.attributes("-topmost", False))
    except Exception:
        pass
    try:
        root.lift()
        root.focus_force()
    except Exception:
        pass

    _WINDOW = root
    # 崩溃场景用独立 Tk：需要自己跑消息循环，否则弹窗一闪而过、用户来不及复制。
    # 主动反馈走 Toplevel，主程序自带 mainloop，这里不阻塞。
    if not use_parent:
        try:
            root.mainloop()
        except Exception:
            pass
    return root


def handle_exception(exc_info=None, source: str = "程序", parent=None) -> None:
    """统一入口：记录异常并弹出报告窗口。重复调用只会置前已有窗口。"""
    try:
        if not exc_info or not any(exc_info):
            exc_info = sys.exc_info()
        record_exception(exc_info, source)
        show_report(exc_info, parent=parent, title="{} · 出错了".format(_APP_NAME))
    except Exception:
        pass


def install_hooks(root=None) -> None:
    """安装全局异常钩子：主线程 / Tk 回调 / 子线程。

    - sys.excepthook：主线程未捕获异常 -> 弹报告
    - root.report_callback_exception：Tk 回调里的异常（GUI 程序最常见的崩溃点）
    - threading.excepthook：子线程异常只记录，供主动报告汇总
    """
    def _sys_hook(etype, evalue, tb):
        handle_exception((etype, evalue, tb), source="主线程")

    def _thread_hook(args):
        try:
            info = (args.exc_type, args.exc_value, args.exc_traceback)
            name = getattr(getattr(args, "thread", None), "name", "后台线程")
            record_exception(info, source="线程：{}".format(name))
        except Exception:
            pass

    def _tk_hook(*args):
        exc = val = tb = None
        win = None
        if len(args) >= 3:
            # 作为 root 的方法赋值时签名是 (root, exc, val, tb)
            if len(args) == 4:
                win, exc, val, tb = args
            else:
                exc, val, tb = args
        if exc is not None:
            handle_exception((exc, val, tb), source="界面回调", parent=win)

    try:
        sys.excepthook = _sys_hook
    except Exception:
        pass
    try:
        threading.excepthook = _thread_hook
    except Exception:
        pass
    if root is not None:
        try:
            root.report_callback_exception = _tk_hook
        except Exception:
            pass
