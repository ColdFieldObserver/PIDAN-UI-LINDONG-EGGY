# -*- coding: utf-8 -*-
"""轮换图上传：把用户选中的图片处理成胶囊贴图，存进外部 carousel/ 目录。

Tk 原生只认 PNG/GIF，所以 JPG/JPEG/BMP/WEBP 必须先转成 PNG；同时胶囊宽高比
固定 556:44（约 12.6:1），上传的图按这个比例「居中裁一条」再缩到 1200px 宽，
和 make_photos.py 生成的那 16 张保持同样规格，直接能播。

两条处理通道，任一成功即可：
    1) Pillow（项目 requirements.txt 已声明依赖，exe 版 spec 也已 hiddenimport）
    2) PowerShell + System.Drawing（Windows 自带，无需 pip；Pillow 缺失时兜底）

对外接口：
    SUPPORTED_EXTS                     可选文件的扩展名集合
    next_index(folder)                 下一个可用序号（不覆盖已有图）
    save_uploads(paths, folder, ...)   处理并保存 -> (saved_paths, errors)

本模块只做文件处理、不碰界面，方便脱离 Tk 单独测试。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

SUPPORTED_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")
_INDEX_RE = re.compile(r"^(\d+)\.png$", re.IGNORECASE)

# PowerShell System.Drawing 裁剪脚本：参数由调用方拼好后作为 -File 传入。
_PS = r"""
param($src, $dst, $aspect, $tw, $th)
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap($src)
$w = $bmp.Width; $h = $bmp.Height
$ch = [int][Math]::Round($w / $aspect)
if ($ch -gt $h) { $ch = $h; $w2 = [int][Math]::Round($h * $aspect) } else { $w2 = $w }
if ($w2 -gt $w) { $w2 = $w }
if ($w2 -lt 2) { $w2 = 2 }
if ($ch -lt 2) { $ch = 2 }
$x0 = [int](($w - $w2) / 2)
$y0 = [int](($h - $ch) / 2)
$out = New-Object System.Drawing.Bitmap($tw, $th, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
$g = [System.Drawing.Graphics]::FromImage($out)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$g.DrawImage($bmp, (New-Object System.Drawing.Rectangle(0, 0, $tw, $th)), (New-Object System.Drawing.Rectangle($x0, $y0, $w2, $ch)), [System.Drawing.GraphicsUnit]::Pixel)
$out.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $out.Dispose(); $bmp.Dispose()
"""


def next_index(folder: str) -> int:
    """扫描 folder 里的 NN.png，返回下一个可用序号（从现有最大值 +1 开始，不覆盖）。"""
    largest = 0
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    for name in names:
        m = _INDEX_RE.match(name)
        if m:
            try:
                largest = max(largest, int(m.group(1)))
            except ValueError:
                pass
    return largest + 1


def _process_pil(src: str, dst: str, aspect: float, tw: int, th: int) -> None:
    """Pillow 通道：居中裁剪 -> 高质量缩放 -> 存 PNG。"""
    from PIL import Image  # 延迟导入：没装 Pillow 也能 import 本模块

    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        ch = int(round(w / aspect))
        if ch > h:
            ch = h
            w2 = int(round(h * aspect))
        else:
            w2 = w
        w2 = max(2, min(w2, w))
        ch = max(2, ch)
        x0 = (w - w2) // 2
        y0 = (h - ch) // 2
        im = im.crop((x0, y0, x0 + w2, y0 + ch))
        try:
            resample = Image.LANCZOS
        except AttributeError:
            resample = Image.BICUBIC
        im = im.resize((tw, th), resample)
        im.save(dst, "PNG")


def _process_ps(src: str, dst: str, aspect: float, tw: int, th: int) -> None:
    """PowerShell + System.Drawing 通道：Pillow 缺失时兜底（Windows 自带）。"""
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise RuntimeError("系统里既没有 Pillow，也找不到 PowerShell，无法处理图片")
    script = os.path.join(tempfile.gettempdir(), "_di_upload_crop.ps1")
    with open(script, "w", encoding="utf-8") as f:
        f.write(_PS)
    try:
        r = subprocess.run(
            [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
             "-src", src, "-dst", dst, "-aspect", "%.6f" % aspect,
             "-tw", str(tw), "-th", str(th)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    finally:
        try:
            os.remove(script)
        except OSError:
            pass
    if r.returncode != 0 or not os.path.isfile(dst):
        detail = (r.stderr or r.stdout or "").strip()[:200]
        raise RuntimeError("图片处理失败：{}".format(detail or "未知原因"))


def save_uploads(paths, folder: str, aspect_w: float = 556.0,
                 aspect_h: float = 44.0, target_w: int = 1200):
    """把选中的图片处理成规格一致的 PNG 存进 folder。

    返回 (saved_paths, errors)：saved_paths 是新存文件的完整路径列表（按写入顺序），
    errors 是 (文件名, 原因) 列表。单张失败不影响其它图片。
    """
    aspect = aspect_w / aspect_h
    th = max(2, int(round(target_w / aspect)))
    os.makedirs(folder, exist_ok=True)

    # 优先用 Pillow，装了就全程走它；没装则每张都走 PowerShell 兜底。
    try:
        import PIL  # noqa: F401
        use_pil = True
    except Exception:
        use_pil = False

    index = next_index(folder)
    saved, errors = [], []
    for src in paths:
        src = os.path.abspath(src)
        name = os.path.basename(src)
        if not name.lower().endswith(SUPPORTED_EXTS) or not os.path.isfile(src):
            errors.append((name, "不支持的文件类型或文件不存在"))
            continue
        dst = os.path.join(folder, "%02d.png" % index)
        try:
            if use_pil:
                try:
                    _process_pil(src, dst, aspect, target_w, th)
                except Exception:
                    # Pillow 通道失败（如个别格式解码不了）再退到 PowerShell
                    _process_ps(src, dst, aspect, target_w, th)
            else:
                _process_ps(src, dst, aspect, target_w, th)
            if os.path.isfile(dst) and os.path.getsize(dst) > 0:
                saved.append(dst)
                index += 1
            else:
                errors.append((name, "输出文件为空"))
        except Exception as exc:
            errors.append((name, str(exc)[:160]))
    return saved, errors
