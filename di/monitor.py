# -*- coding: utf-8 -*-
"""系统信息采集：CPU / 内存 / GPU / 磁盘 / 电池 / 网速 / 运行时间。

基于标准库 ctypes / wintypes / time / threading / winreg，网速借助 psutil 的
net_io_counters()。不碰 tkinter，也不引用任何主题色变量。对外暴露 Sampler
线程与各采集函数，主模块按需显式导入。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
import threading
import winreg

import psutil

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wt.DWORD),
        ("dwMemoryLoad", wt.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", wt.BYTE),
        ("BatteryFlag", wt.BYTE),
        ("BatteryLifePercent", wt.BYTE),
        ("SystemStatusFlag", wt.BYTE),
        ("BatteryLifeTime", wt.DWORD),
        ("BatteryFullLifeTime", wt.DWORD),
    ]


kernel32.GetSystemTimes.argtypes = [
    ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)
]
kernel32.GetSystemTimes.restype = wt.BOOL
kernel32.GetTickCount64.restype = ctypes.c_ulonglong
kernel32.GetLogicalDrives.restype = wt.DWORD
kernel32.GetDriveTypeW.argtypes = [wt.LPCWSTR]
kernel32.GetDriveTypeW.restype = wt.UINT
kernel32.GetDiskFreeSpaceExW.argtypes = [
    wt.LPCWSTR,
    ctypes.POINTER(ctypes.c_ulonglong),
    ctypes.POINTER(ctypes.c_ulonglong),
    ctypes.POINTER(ctypes.c_ulonglong),
]
kernel32.GetDiskFreeSpaceExW.restype = wt.BOOL
kernel32.GetSystemPowerStatus.argtypes = [ctypes.POINTER(SYSTEM_POWER_STATUS)]
kernel32.GetSystemPowerStatus.restype = wt.BOOL

def _ft_value(ft: FILETIME) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


# ================================================================ 数据采集
class CpuProbe:
    """整体 CPU 占用率（GetSystemTimes 差值法）"""

    def __init__(self):
        self._last = self._read()

    def _read(self):
        idle, kern, user = FILETIME(), FILETIME(), FILETIME()
        kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        return (_ft_value(idle), _ft_value(kern), _ft_value(user))

    def usage(self) -> float:
        idle, kern, user = self._read()
        d_idle = idle - self._last[0]
        d_total = (kern - self._last[1]) + (user - self._last[2])
        self._last = (idle, kern, user)
        if d_total <= 0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - d_idle / d_total))


def _mem_info():
    ms = MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
    return ms.ullTotalPhys, ms.ullTotalPhys - ms.ullAvailPhys


def _disk_info():
    total = free = 0
    mask = kernel32.GetLogicalDrives()
    for i in range(26):
        if not (mask & (1 << i)):
            continue
        root = f"{chr(65 + i)}:\\"
        try:
            if kernel32.GetDriveTypeW(root) != 3:      # DRIVE_FIXED
                continue
        except Exception:
            continue
        fb, tb, _ab = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
        if kernel32.GetDiskFreeSpaceExW(root, ctypes.byref(fb), ctypes.byref(tb), ctypes.byref(_ab)):
            total += tb.value
            free += fb.value
    return total, free


def _battery_info():
    st = SYSTEM_POWER_STATUS()
    if not kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return None
    has_battery = not (st.BatteryFlag & 0x80) and st.BatteryLifePercent != 255
    if not has_battery:
        return None
    return int(st.BatteryLifePercent), st.ACLineStatus == 1


class NetRateProbe:
    """网速采样：用 psutil 的累计字节数差分算每秒速率。

    首次调用没有基准，返回 (0.0, 0.0)；之后按两次采样的时间差换算成
    bytes/s。拔插网卡、计数器归零（重启/重置）时也会回到 0，不产生负数尖峰。
    """

    def __init__(self):
        self._prev = None      # (monotonic, bytes_sent, bytes_recv)
        self.rx = 0.0          # 下行 bytes/s
        self.tx = 0.0          # 上行 bytes/s

    def sample(self):
        try:
            c = psutil.net_io_counters()
            now = time.monotonic()
            sent, recv = c.bytes_sent, c.bytes_recv
        except Exception:
            self.rx = self.tx = 0.0
            return self.rx, self.tx
        if self._prev is not None:
            dt = now - self._prev[0]
            if dt > 0:
                ds = sent - self._prev[1]
                dr = recv - self._prev[2]
                # 计数器回绕/重置：差值为负则本轮记 0
                self.tx = max(0.0, ds / dt)
                self.rx = max(0.0, dr / dt)
        self._prev = (now, sent, recv)
        return self.rx, self.tx


def _cpu_info():
    """CPU 名称 / 主频"""
    name, mhz = "", 0
    try:
        key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            name = str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
            mhz = int(winreg.QueryValueEx(k, "~MHz")[0])
    except Exception:
        pass
    return name, mhz


# ---------------------------------------------------------------- GPU
class NVMLUtil(ctypes.Structure):
    _fields_ = [("gpu", wt.UINT), ("memory", wt.UINT)]


class GpuProbe:
    """GPU：名称走注册表，利用率走 PDH 性能计数器（N/A/I 卡通用），温度走 NVML（仅 NVIDIA）"""

    def __init__(self):
        self.name = self._read_name()
        self.short = self._shorten(self.name)
        self.util = None
        self.temp = None
        self._pdh = None
        self._nvml_handle = None
        self._nvml = None
        self._init_pdh()
        self._init_nvml()

    # -- 注册表取显卡名称（取显存最大的那块，通常是独显）
    @staticmethod
    def _read_name() -> str:
        key = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        best, best_mem = "", -1
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                for i in range(24):
                    try:
                        sub = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    if not sub.isdigit():
                        continue
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key + "\\" + sub) as sk:
                            desc = str(winreg.QueryValueEx(sk, "DriverDesc")[0]).strip()
                            try:
                                mem = int(winreg.QueryValueEx(sk, "HardwareInformation.qwMemorySize")[0])
                            except OSError:
                                mem = 0
                    except OSError:
                        continue
                    if not desc:
                        continue
                    if mem >= best_mem:
                        best, best_mem = desc, mem
        except Exception:
            pass
        return best

    @staticmethod
    def _shorten(name: str) -> str:
        if not name:
            return "显卡"
        for junk in ("NVIDIA ", "GeForce ", "AMD ", "Radeon ", "Intel(R) ", "Intel ",
                     "(R)", "(TM)", "Graphics", "显卡"):
            name = name.replace(junk, "")
        name = " ".join(name.split())
        return name[:16] if name else "显卡"

    # -- PDH
    def _init_pdh(self):
        try:
            pdh = ctypes.WinDLL("pdh")
            hq = ctypes.c_void_p()
            if pdh.PdhOpenQueryW(None, 0, ctypes.byref(hq)) != 0:
                return
            hc = ctypes.c_void_p()
            path = r"\GPU Engine(*)\Utilization Percentage"
            if pdh.PdhAddEnglishCounterW(hq, path, 0, ctypes.byref(hc)) != 0:
                pdh.PdhCloseQuery(hq)
                return
            pdh.PdhCollectQueryData(hq)
            self._pdh = (pdh, hq, hc)
        except Exception:
            self._pdh = None

    def _pdh_util(self):
        pdh, hq, hc = self._pdh

        class FMT_VALUE(ctypes.Union):
            _fields_ = [("longValue", ctypes.c_long),
                        ("doubleValue", ctypes.c_double),
                        ("largeValue", ctypes.c_longlong),
                        ("longLongValue", ctypes.c_longlong),
                        ("AnsiStringValue", ctypes.c_char_p),
                        ("WideStringValue", ctypes.c_wchar_p)]

        class FMT_ITEM(ctypes.Structure):
            # 必须带上 CStatus：PDH_FMT_COUNTERVALUE 是 {DWORD CStatus; union value}
            _fields_ = [("szName", ctypes.c_wchar_p),
                        ("CStatus", wt.DWORD),
                        ("FmtValue", FMT_VALUE)]

        pdh.PdhCollectQueryData(hq)
        pdh.PdhGetFormattedCounterArrayW.argtypes = [
            ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
            ctypes.POINTER(wt.DWORD), ctypes.POINTER(FMT_ITEM)]
        size, count = wt.DWORD(0), wt.DWORD(0)
        rc = pdh.PdhGetFormattedCounterArrayW(hc, 512, ctypes.byref(size),
                                             ctypes.byref(count), None)
        if rc != 0 and not size.value:
            return None
        buf = (ctypes.c_char * (size.value + 16))()
        items = ctypes.cast(buf, ctypes.POINTER(FMT_ITEM))
        pdh.PdhGetFormattedCounterArrayW(hc, 512, ctypes.byref(size),
                                         ctypes.byref(count), items)
        # 同一块显卡每个进程各占一条计数器：按引擎类型（3D / Copy / VideoDecode…）
        # 跨进程求和，再取各引擎里的最大值，才是任务管理器那种「显卡占用」
        per_engine = {}
        best = 0.0
        for i in range(count.value):
            if items[i].CStatus != 0:             # 0 = PDH_CSTATUS_VALID_DATA
                continue
            try:
                v = items[i].FmtValue.doubleValue
            except Exception:
                continue
            if v is None or v != v:                # 过滤 NaN
                continue
            name = items[i].szName or ""
            tag = ""
            pos = name.find("engtype_")
            if pos >= 0:
                tag = name[pos:]
            per_engine[tag] = per_engine.get(tag, 0.0) + v
            if v > best:
                best = v
        if per_engine:
            best = max(best, max(per_engine.values()))
        return min(1.0, best / 100.0)

    # -- NVML（仅 NVIDIA 提供温度）
    def _init_nvml(self):
        try:
            nvml = ctypes.WinDLL("nvml.dll")
            nvml.nvmlInit_v2()
            handle = ctypes.c_void_p()
            if nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
                return
            self._nvml, self._nvml_handle = nvml, handle
        except Exception:
            self._nvml = None

    def sample(self):
        if self._pdh is not None:
            try:
                self.util = self._pdh_util()
            except Exception:
                self.util = None
        if self._nvml is not None:
            try:
                temp = wt.UINT()
                if self._nvml.nvmlDeviceGetTemperature(self._nvml_handle, 0, ctypes.byref(temp)) == 0:
                    self.temp = int(temp.value)
                u = NVMLUtil()
                if self._nvml.nvmlDeviceGetUtilizationRates(self._nvml_handle, ctypes.byref(u)) == 0:
                    if self.util is None:
                        self.util = u.gpu / 100.0
            except Exception:
                pass


# ================================================================ 采样线程
class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.daemon = True
        self.lock = threading.Lock()
        self.data = {}
        self._stop = threading.Event()
        self.cpu = CpuProbe()
        self.gpu = GpuProbe()
        self.net = NetRateProbe()
        self._tick = 0
        self.interval = 1.0

    def stop(self):
        self._stop.set()

    def run(self):
        time.sleep(0.2)
        while not self._stop.is_set():
            try:
                self._collect()
            except Exception:
                pass
            self._stop.wait(max(0.5, self.interval))

    def _collect(self):
        d = {}
        d["cpu"] = self.cpu.usage()
        d["mem_total"], d["mem_used"] = _mem_info()
        d["disk_total"], d["disk_free"] = _disk_info()
        d["battery"] = _battery_info()
        d["net_rx"], d["net_tx"] = self.net.sample()
        d["uptime"] = kernel32.GetTickCount64() / 1000.0
        if self._tick % 3 == 0:
            self.gpu.sample()
        self._tick += 1
        d["gpu_util"] = self.gpu.util
        d["gpu_temp"] = self.gpu.temp
        d["gpu_name"] = self.gpu.short
        with self.lock:
            self.data = d


# ================================================================ 文本格式
def fmt_uptime(sec: float) -> str:
    sec = int(sec)
    days, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if days:
        return f"已运行 {days} 天 {h:02d}:{m:02d}"
    return f"已运行 {h:02d}:{m:02d}:{rem % 60:02d}"
