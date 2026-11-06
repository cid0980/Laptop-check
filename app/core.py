"""
core.py -- shared low-level plumbing for Laptop Check Pro.

Design rules for this whole project:
  * STANDARD LIBRARY ONLY. No pip packages. The final product is ONE .exe.
  * Every hardware read goes through a helper that can never raise to the GUI.
  * Anything that cannot be read becomes status "na" / "skipped" with a reason,
    never a crash and never a silent blank.
  * Windows is the target. On any other OS the app runs in DEMO mode so the
    report engine can still be developed and previewed.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

APP_NAME = "Laptop Check Pro"
APP_VERSION = "1.5.4"
IS_WINDOWS = sys.platform.startswith("win")
IS_FROZEN = getattr(sys, "frozen", False)

# Where our data lives. Portable-first: a folder next to the .exe. If that is
# not writable (e.g. exe sits in C:\Program Files) fall back to LOCALAPPDATA.
def _data_dir() -> str:
    if IS_FROZEN:
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cand = os.path.join(base, "LCP_Data")
    try:
        os.makedirs(cand, exist_ok=True)
        probe = os.path.join(cand, ".w")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        return cand
    except Exception:
        pass
    fallback = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "LaptopCheckPro"
    )
    os.makedirs(fallback, exist_ok=True)
    return fallback


DATA_DIR = _data_dir()
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
for _d in (DATA_DIR, REPORTS_DIR, PHOTOS_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Process helpers
# --------------------------------------------------------------------------

CREATE_NO_WINDOW = 0x08000000


def _popen_kwargs() -> Dict[str, Any]:
    kw: Dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if IS_WINDOWS:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kw["startupinfo"] = si
        kw["creationflags"] = CREATE_NO_WINDOW
    return kw


def run_cmd(
    args: List[str], timeout: float = 25.0, decode: str = "utf-8"
) -> Tuple[int, str, str]:
    """Run a command hidden, never raise. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.Popen(args, **_popen_kwargs())
    except FileNotFoundError:
        return 127, "", "not found: %s" % (args[0] if args else "?")
    except Exception as exc:  # pragma: no cover
        return 1, "", str(exc)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            p.kill()
            out, err = p.communicate(timeout=5)
        except Exception:
            out, err = b"", b"timeout"
        return 124, _dec(out, decode), "timeout after %ss" % timeout
    return p.returncode, _dec(out, decode), _dec(err, decode)


def _dec(raw: Optional[bytes], decode: str) -> str:
    if not raw:
        return ""
    for enc in (decode, "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc, "strict")
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def run_shell(cmd: str, timeout: float = 25.0) -> Tuple[int, str, str]:
    """Run through cmd.exe (needed for built-in commands like `netsh`)."""
    return run_cmd(["cmd.exe", "/c", cmd], timeout=timeout)


# --------------------------------------------------------------------------
# PowerShell / CIM (WMI) access
# --------------------------------------------------------------------------

_PS_CANDIDATES = None


def powershell_exe() -> Optional[str]:
    """Locate powershell.exe (prefer 64-bit native, avoid WOW64 redirection)."""
    global _PS_CANDIDATES
    if _PS_CANDIDATES is None:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        cands = [
            os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
            os.path.join(
                root, "Sysnative", "WindowsPowerShell", "v1.0", "powershell.exe"
            ),
            os.path.join(
                root, "SysWOW64", "WindowsPowerShell", "v1.0", "powershell.exe"
            ),
            "powershell.exe",
        ]
        _PS_CANDIDATES = [c for c in cands if (c == cands[-1] or os.path.exists(c))]
    for c in _PS_CANDIDATES:
        if os.path.exists(c) or c == "powershell.exe":
            return c
    return None


def set_dpi_aware() -> None:
    """Tell Windows we draw in PHYSICAL pixels - BEFORE any window exists.

    Shop laptops very often run 125-150% display scaling. If a process does
    not declare DPI awareness before its first window is created, Windows
    virtualises it: "the whole screen" becomes a smaller logical rectangle and
    a full-screen window covers only part of the real display (exactly the
    bug reported from the counter, with the wallpaper visible beside the
    tool). Per-monitor awareness (2) is the correct level; older Windows falls
    back gracefully.
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            return
        except Exception:
            pass
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def screen_size() -> tuple:
    """Physical primary-screen size in pixels, straight from Windows."""
    if IS_WINDOWS:
        try:
            import ctypes
            u = ctypes.windll.user32
            w = u.GetSystemMetrics(0)
            h = u.GetSystemMetrics(1)
            if w > 200 and h > 200:
                return int(w), int(h)
        except Exception:
            pass
    return 0, 0


def diag(text: str) -> None:
    """Append one line to LCP_Data/diagnostics.txt (what the shop sends us)."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "diagnostics.txt"), "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:
        pass


def run_ps(script: str, timeout: float = 60.0) -> str:
    """Run a PowerShell script and return its stdout ('' on error).

    The script is written to a temporary .ps1 and executed with -File. This is
    deliberately NOT one giant -EncodedCommand blob: security software on shop
    PCs (and Windows SmartScreen) frequently kills a process whose command
    line is a huge base64 string, which used to make every single hardware
    check fail at once. A small .ps1 file in %TEMP% is boring and gets through.
    If -File is blocked for some reason we retry with -EncodedCommand.
    """
    if not IS_WINDOWS:
        return ""
    exe = powershell_exe()
    if not exe:
        diag("run_ps: powershell.exe not found")
        return ""
    path = ""
    try:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".ps1", prefix="lcp_")
        with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
            fh.write(script)
        rc, out, err = run_cmd(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", path], timeout=timeout)
        if out.strip():
            return out
        if rc != 0:
            diag("run_ps -File rc=%s err=%s" % (rc, (err or "")[:300]))
    except Exception as exc:
        diag("run_ps -File exception %s: %s" % (type(exc).__name__, exc))
    finally:
        if path:
            try:
                os.remove(path)
            except Exception:
                pass
    # fallback: encoded command (short scripts only - keep under ~6k chars)
    if len(script) <= 6000:
        b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        rc, out, err = run_cmd(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", b64], timeout=timeout)
        if out.strip():
            return out
        diag("run_ps -EncodedCommand rc=%s err=%s" % (rc, (err or "")[:300]))
    return ""


def ps_json(script: str, timeout: float = 60.0) -> Any:
    """Run PowerShell that emits ConvertTo-Json and parse it. Returns None on failure."""
    out = run_ps(script, timeout=timeout)
    return parse_json_loose(out)


def parse_json_loose(text: str) -> Any:
    """PowerShell sometimes prints warnings/banner lines before the JSON."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("\ufeff"):
        t = t[1:].strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i = t.find(opener)
        j = t.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(t[i : j + 1])
            except Exception:
                continue
    try:
        return json.loads(t)
    except Exception:
        return None


def cim(class_name: str, props: Optional[List[str]] = None,
        filter_: Optional[str] = None, ns: str = "cimv2",
        timeout: float = 60.0) -> List[Dict[str, Any]]:
    """Get-CimInstance -> list of dicts. Always returns a list (possibly empty)."""
    if not IS_WINDOWS:
        return []
    sel = ", ".join(props) if props else "*"
    q = f"Get-CimInstance -Namespace root/{ns} -ClassName {class_name}"
    if filter_:
        q += f" -Filter \"{filter_}\""
    script = (
        "$ProgressPreference='SilentlyContinue';"
        "$ErrorActionPreference='SilentlyContinue';"
        f"$r = {q} | Select-Object {sel};"
        "if($r){$r | ConvertTo-Json -Depth 3 -Compress}else{'[]'}"
    )
    data = ps_json(script, timeout=timeout)
    return as_list(data)


def as_list(data: Any) -> List[Dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def first(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return rows[0] if rows else {}


def get(row: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Case-insensitive dict get (WMI property casing varies)."""
    if not isinstance(row, dict):
        return default
    if key in row:
        return row[key]
    lk = key.lower()
    for k, v in row.items():
        if str(k).lower() == lk:
            return v
    return default


def batch_ps(queries: Dict[str, str], timeout: float = 180.0) -> Dict[str, Any]:
    """Run MANY independent PowerShell snippets in ONE process (big speed win).

    queries: {"key": "powershell expression that produces objects"}
    Each snippet is wrapped so one failure cannot kill the others.
    """
    if not IS_WINDOWS or not queries:
        return {}
    def one_call(chunk: Dict[str, str]) -> Dict[str, Any]:
        parts = [
            "$ProgressPreference='SilentlyContinue';",
            "$ErrorActionPreference='SilentlyContinue';",
            "$out = [ordered]@{};",
        ]
        for key, snippet in chunk.items():
            # & { ... } lets a snippet contain several statements safely.
            parts.append(
                "try { $v = & { " + snippet + " }; "
                "if ($null -ne $v) { $out['%s'] = @($v) } else { $out['%s'] = @() } } "
                "catch { $out['%s'] = @() }" % (key, key, key)
            )
        parts.append("$out | ConvertTo-Json -Depth 4 -Compress")
        data = ps_json("\n".join(parts), timeout=timeout)
        return data if isinstance(data, dict) else {}

    # Chunk the snippets: several medium calls beat one enormous script that
    # a timeout, a crash or an over-zealous antivirus can kill in one go.
    chunks: List[Dict[str, str]] = []
    cur: Dict[str, str] = {}
    size = 0
    for key, snippet in queries.items():
        if size and size + len(snippet) > 3500:
            chunks.append(cur)
            cur, size = {}, 0
        cur[key] = snippet
        size += len(snippet) + 120
    if cur:
        chunks.append(cur)

    merged: Dict[str, Any] = {}
    failed_chunks = 0
    for chunk in chunks:
        data = one_call(chunk)
        if not data:
            failed_chunks += 1
            diag("batch_ps: a probe chunk returned nothing (keys: %s)"
                 % ", ".join(list(chunk)[:8]))
            # retry that chunk key-by-key so one poisoned snippet cannot
            # take its neighbours down with it
            for key, snippet in chunk.items():
                solo = one_call({key: snippet})
                if solo.get(key) is not None:
                    merged[key] = solo[key]
            continue
        for k, v in data.items():
            merged[k] = v
    if failed_chunks:
        diag("batch_ps: %d/%d chunks failed outright" % (failed_chunks, len(chunks)))
    data = merged
    result: Dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, list):
            result[k] = [x for x in v if isinstance(x, dict)]
        elif isinstance(v, dict):
            result[k] = [v]
        else:
            result[k] = v
    return result


# --------------------------------------------------------------------------
# Win32 via ctypes
# --------------------------------------------------------------------------

def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Re-run this exe/script elevated. Returns True if the new process launched."""
    if not IS_WINDOWS:
        return False
    try:
        if IS_FROZEN:
            target = sys.executable
            params = " ".join(f'"{a}"' for a in sys.argv[1:])
        else:
            target = powershell_exe() or "python.exe"
            params = '"%s" %s' % (
                sys.executable,
                " ".join(f'"{a}"' for a in sys.argv),
            )
            if not IS_FROZEN:
                target = sys.executable
                params = " ".join(f'"{a}"' for a in sys.argv)
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", target, params, None, 1
        )
        return int(rc) > 32
    except Exception:
        return False


def prevent_sleep(on: bool) -> None:
    """Keep the machine awake during long tests (ES_CONTINUOUS|ES_SYSTEM_REQUIRED|ES_DISPLAY_REQUIRED)."""
    if not IS_WINDOWS:
        return
    try:
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else ES_CONTINUOUS)
    except Exception:
        pass


def get_power_status() -> Dict[str, Any]:
    """SYSTEM_POWER_STATUS via GetSystemPowerStatus (works without admin)."""
    if not IS_WINDOWS:
        return {}

    class SPS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", ctypes.c_uint),
            ("BatteryFullLifeTime", ctypes.c_uint),
        ]

    try:
        sps = SPS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
            return {}
        ac_map = {0: "On battery", 1: "AC connected", 255: "Unknown"}
        flag = sps.BatteryFlag
        flags = []
        for bit, label in (
            (1, "High"), (2, "Low"), (4, "Critical"), (8, "Charging"),
            (32, "No system battery"), (128, "Unknown"),
        ):
            if flag & bit:
                flags.append(label)
        return {
            "ac": ac_map.get(sps.ACLineStatus, "Unknown"),
            "battery_flag": ", ".join(flags) or "None",
            "percent": None if sps.BatteryLifePercent == 255 else int(sps.BatteryLifePercent),
            "remaining_seconds": None if sps.BatteryLifeTime == 0xFFFFFFFF else int(sps.BatteryLifeTime),
            "full_life_seconds": None if sps.BatteryFullLifeTime == 0xFFFFFFFF else int(sps.BatteryFullLifeTime),
            "no_battery": bool(flag & 32),
            "charging": bool(flag & 8),
        }
    except Exception:
        return {}


def kelvin_to_c(value: Any) -> Optional[float]:
    """MSAcpi_ThermalZoneTemperature reports tenths of a degree Kelvin."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return round(v / 10.0 - 273.15, 1)


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------

def fmt_bytes(n: Any, unit: int = 1024) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    if n <= 0:
        return "0 B"
    for suffix in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < unit or suffix == "PB":
            if suffix == "B":
                return "%d B" % n
            return ("%.1f %s" % (n, suffix)).replace(".0 ", " ")
        n /= unit


def fmt_gb(n: Any) -> str:
    try:
        return "%d GB" % round(float(n) / (1024 ** 3))
    except (TypeError, ValueError):
        return "-"


def fmt_secs(s: Any) -> str:
    try:
        s = int(float(s))
    except (TypeError, ValueError):
        return "-"
    if s < 0:
        return "-"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return "%dh %02dm" % (h, m)
    if m:
        return "%dm %02ds" % (m, sec)
    return "%ds" % sec


def fmt_hours(h: Any) -> str:
    try:
        h = float(h)
    except (TypeError, ValueError):
        return "-"
    if h <= 0:
        return "-"
    if h < 1000:
        return "%d hours" % h
    return "%s hours (~%.1f years)" % (f"{int(h):,}", h / 8760.0)


def fmt_inr(n: Any) -> str:
    try:
        n = int(round(float(n)))
    except (TypeError, ValueError):
        return "-"
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("\u20b9" if n >= 0 else "-\u20b9") + s


def pct(part: Any, whole: Any) -> Optional[float]:
    try:
        p, w = float(part), float(whole)
        if w <= 0:
            return None
        return round(100.0 * p / w, 1)
    except (TypeError, ValueError):
        return None


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def file_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def new_scan_id() -> str:
    return "LCP-" + datetime.now().strftime("%y%m%d") + "-" + uuid.uuid4().hex[:6].upper()


def strip_nulls(obj: Any) -> Any:
    """WMI/CIM sometimes returns NUL-padded strings."""
    if isinstance(obj, str):
        return obj.replace("\x00", "").strip()
    if isinstance(obj, list):
        return [strip_nulls(x) for x in obj]
    if isinstance(obj, dict):
        return {k: strip_nulls(v) for k, v in obj.items()}
    return obj


# --------------------------------------------------------------------------
# The result model
# --------------------------------------------------------------------------

STATUS_LABEL = {
    "pass": "PASS",
    "warn": "WARNING",
    "fail": "FAIL",
    "na": "NOT FITTED",
    "skipped": "SKIPPED",
    "unknown": "UNKNOWN",
}


@dataclass
class CheckResult:
    """One inspection item. Everything the report needs lives here."""

    cid: str
    name: str
    group: str
    weight: float = 1.0
    status: str = "unknown"          # pass | warn | fail | na | skipped | unknown
    score: Optional[float] = None    # 0..100 quality score for this item
    summary: str = ""
    details: Dict[str, Any] = field(default_factory=dict)   # shown in the report
    notes: List[str] = field(default_factory=list)          # extra lines
    flags: List[str] = field(default_factory=list)          # headline problems
    subtests: List[Dict[str, Any]] = field(default_factory=list)
    tech: str = "auto"               # auto | guided | mixed
    seconds: float = 0.0
    error: str = ""
    # guided tests only
    marks: List[Dict[str, Any]] = field(default_factory=list)
    photos: List[str] = field(default_factory=list)         # file paths
    required: bool = True

    # ---- builders -------------------------------------------------------
    def metric(self, label: str, value: Any, ok: Optional[bool] = None) -> "CheckResult":
        self.details[str(label)] = "" if value is None else value
        if ok is not None:
            self.subtests.append({"name": str(label), "ok": bool(ok)})
        return self

    def note(self, text: str) -> "CheckResult":
        if text:
            self.notes.append(str(text))
        return self

    def flag(self, text: str, level: str = "warn") -> "CheckResult":
        if text and text not in [f for f, _ in self.flags]:
            self.flags.append((text, level))
        return self

    def set(self, status: str, summary: str = "", score: Optional[float] = None) -> "CheckResult":
        self.status = status
        if summary:
            self.summary = summary
        if score is not None:
            self.score = round(clamp(float(score), 0.0, 100.0), 1)
        return self

    def ok(self, summary: str = "", score: float = 100.0) -> "CheckResult":
        return self.set("pass", summary, score)

    def warn(self, summary: str = "", score: float = 60.0) -> "CheckResult":
        return self.set("warn", summary, score)

    def fail(self, summary: str = "", score: float = 15.0) -> "CheckResult":
        return self.set("fail", summary, score)

    def na(self, summary: str = "") -> "CheckResult":
        """Component is genuinely not present on this machine (not a fault)."""
        self.status = "na"
        self.score = None
        self.summary = summary or "Not fitted on this machine"
        return self

    def skipped(self, reason: str = "") -> "CheckResult":
        self.status = "skipped"
        self.score = None
        self.summary = reason or "Not tested"
        return self

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.cid, "name": self.name, "group": self.group,
            "weight": self.weight, "status": self.status, "score": self.score,
            "summary": self.summary, "details": self.details, "notes": self.notes,
            "flags": [list(f) if isinstance(f, (list, tuple)) else [f, "warn"] for f in self.flags],
            "subtests": self.subtests, "tech": self.tech,
            "seconds": round(self.seconds, 2), "error": self.error,
            "marks": self.marks, "photos": [os.path.basename(p) for p in self.photos],
            "required": self.required,
        }
        return d


# Check catalogue: id -> (display name, group, weight, tech)
CHECKS: List[Tuple[str, str, str, float, str]] = [
    ("identity",   "Machine identity",        "System",    1.0, "auto"),
    ("os_license", "Windows + activation",    "System",    3.0, "auto"),
    ("bios_tpm",   "BIOS / TPM / SecureBoot", "System",    1.5, "auto"),
    ("cpu",        "Processor",               "Core",      3.0, "auto"),
    ("cpu_stress", "CPU load + thermals",     "Core",      2.0, "auto"),
    ("memory",     "RAM (memory)",            "Core",      3.0, "auto"),
    ("memory_test","RAM quick pattern test",  "Core",      1.5, "auto"),
    ("storage",    "Storage health (SMART)",  "Core",      4.0, "auto"),
    ("gpu",        "Graphics",                "Core",      1.5, "auto"),
    ("display",    "Display + panel (EDID)",  "Display",   2.0, "auto"),
    ("battery",    "Battery health",          "Power",     4.0, "auto"),
    ("charger",    "Charging / AC adapter",   "Power",     1.0, "auto"),
    ("wifi",       "Wi-Fi",                   "Wireless",  1.5, "auto"),
    ("bluetooth",  "Bluetooth",               "Wireless",  1.0, "auto"),
    ("ethernet",   "Ethernet (LAN)",          "Wireless",  0.7, "auto"),
    ("audio_dev",  "Audio devices",           "Audio",     1.0, "auto"),
    ("camera_dev", "Camera device",           "Camera",    1.0, "auto"),
    ("ports",      "Ports + expansion",       "Ports",     1.5, "auto"),
    ("input_dev",  "Keyboard / touchpad HW",  "Input",     1.0, "auto"),
    ("biometric",  "Biometric sensor",        "Input",     0.5, "auto"),
    ("privacy_leftovers", "Leftover owner data", "System", 1.5, "auto"),
    ("events",     "Hardware error log",      "System",    2.0, "auto"),
    # guided (human confirms) -------------------------------------------
    ("screen_colours", "Screen colour test",  "Display",   4.0, "guided"),
    ("screen_pixels",  "Dead pixel / bleed",  "Display",   2.5, "guided"),
    ("touch",      "Touch screen taps",       "Display",   1.0, "guided"),
    ("screen_text",    "Text sharpness",      "Display",   1.0, "guided"),
    ("speakers",       "Speakers L / R",      "Audio",     2.0, "guided"),
    ("microphone",     "Microphone",          "Audio",     1.5, "guided"),
    ("camera_capture", "Camera live capture", "Camera",    2.0, "guided"),
    ("keyboard",       "Keyboard (anti-cheat)","Input",    3.0, "guided"),
    ("touchpad",       "Touchpad / pointing", "Input",     1.5, "guided"),
    ("usb_ports",      "USB port plug test",  "Ports",     1.5, "guided"),
    ("web_ports",      "HDMI / audio jack / SD","Ports",   1.2, "guided"),
    ("cosmetic",       "Cosmetic + hinge",    "Body",      2.5, "guided"),
    ("final_check",    "Technician sign-off", "Body",      1.0, "guided"),
]

CHECK_INDEX = {c[0]: c for c in CHECKS}
AUTO_IDS = [c[0] for c in CHECKS if c[4] == "auto"]
GUIDED_IDS = [c[0] for c in CHECKS if c[4] == "guided"]


def make_result(cid: str) -> CheckResult:
    _id, name, group, weight, tech = CHECK_INDEX[cid]
    return CheckResult(cid=cid, name=name, group=group, weight=weight, tech=tech)


# --------------------------------------------------------------------------
# Event bus (worker thread -> GUI)
# --------------------------------------------------------------------------

class Bus:
    """Thread-safe event queue. The GUI polls it; workers only push."""

    def __init__(self) -> None:
        self.q: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self.cancel = threading.Event()

    def emit(self, kind: str, **payload: Any) -> None:
        self.q.put((kind, payload))

    def poll(self) -> List[Tuple[str, Any]]:
        out = []
        try:
            while True:
                out.append(self.q.get_nowait())
        except queue.Empty:
            pass
        return out

    def request_cancel(self) -> None:
        self.cancel.set()


# --------------------------------------------------------------------------
# Safe-check decorator
# --------------------------------------------------------------------------

def safe_check(fn: Callable[[CheckResult, Dict[str, Any]], None]):
    """Wrap a collector so an unexpected error can never kill the scan."""

    def wrapper(res: CheckResult, ctx: Dict[str, Any]) -> CheckResult:
        t0 = time.time()
        try:
            fn(res, ctx)
        except Exception as exc:
            res.error = "%s: %s" % (type(exc).__name__, exc)
            res.status = "skipped"
            res.summary = "Could not read this item"
            res.notes.append("Error: " + res.error)
            if ctx.get("debug"):
                res.notes.append(traceback.format_exc(limit=6))
        finally:
            res.seconds = time.time() - t0
        if res.status == "unknown":
            res.status = "skipped"
            res.summary = res.summary or "No data returned"
        return res

    wrapper.__name__ = getattr(fn, "__name__", "check")
    return wrapper


def temp_path(prefix: str, suffix: str) -> str:
    return os.path.join(tempfile.gettempdir(), prefix + uuid.uuid4().hex[:8] + suffix)


def which(name: str) -> Optional[str]:
    return shutil.which(name)


def read_text(path: str, errors: str = "replace") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors=errors) as fh:
            return fh.read()
    except Exception:
        return ""


def write_text(path: str, text: str) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return True
    except Exception:
        return False


def write_json(path: str, obj: Any) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)
        return True
    except Exception:
        return False


def load_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


RE_NONALNUM = re.compile(r"[^A-Za-z0-9]+")


def slug(text: str, maxlen: int = 40) -> str:
    s = RE_NONALNUM.sub("-", str(text or "")).strip("-").lower()
    return (s or "machine")[:maxlen]
