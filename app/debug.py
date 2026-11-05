"""
debug.py -- the built-in debugger and support bundle.

Shop PCs are unpredictable: stripped Windows builds, security software that
kills child processes, stopped services, 150% scaling, driver quirks. Instead
of guessing from a photo, the tool now records EVERYTHING while it runs and
can pack it into one file:

    LCP_Data\\LCP_debug_<stamp>.zip
        env.json          Windows build, scaling/DPI, admin, PowerShell test,
                          services, browsers, camera child-process answer,
                          security software, locale, disk space
        session.log       every line the app logged, in order
        tracebacks.log    every exception with a full stack trace
        diagnostics.txt   the raw probe/pdf failure log
        probe_raw.json    the LAST hardware probe exactly as Windows sent it
        results.json      every check result of the current scan
        inventory.txt     what is in LCP_Data and the reports folder

Three ways to get it:
  * button "Create debug file for support" on the first and last screen
  * automatically, whenever a test window crashes or the scan fails - the
    error message shows the file name
  * headless, even if the GUI will not start:
        LaptopCheckPro.exe --debugbundle
    which also runs a fresh hardware probe so we see exactly what Windows
    returns on that machine.

Send the zip over WhatsApp/mail and the next release fixes that machine.
"""

from __future__ import annotations

import io
import json
import os
import platform
import re
import sys
import threading
import time
import traceback
import zipfile
from typing import Any, Dict, List, Optional

from app.core import (APP_NAME, APP_VERSION, DATA_DIR, IS_FROZEN, IS_WINDOWS,
                      REPORTS_DIR, diag, load_json, now_stamp, run_cmd, run_ps,
                      run_shell, write_json)

_LOCK = threading.Lock()
SESSION: List[str] = []
TRACES: List[str] = []
_MAX = 4000


def log(text: str, tag: str = "") -> None:
    line = "%s  %s%s" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                         ("[%s] " % tag) if tag else "", text)
    with _LOCK:
        SESSION.append(line)
        if len(SESSION) > _MAX:
            del SESSION[:500]


def exception(where: str, exc: Optional[BaseException] = None) -> str:
    tb = traceback.format_exc() if exc is None else \
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    block = "=== %s : %s ===\n%s" % (time.strftime("%H:%M:%S"), where, tb)
    with _LOCK:
        TRACES.append(block)
        if len(TRACES) > 200:
            del TRACES[:50]
    log("EXCEPTION in %s: %s" % (where, exc), "error")
    diag("exception in %s: %s" % (where, exc))
    return block


def install_hooks() -> None:
    """Catch EVERY uncaught exception, GUI thread or worker thread."""
    def sys_hook(etype, value, tb):
        block = "".join(traceback.format_exception(etype, value, tb))
        with _LOCK:
            TRACES.append("=== uncaught %s ===\n%s" % (time.strftime("%H:%M:%S"), block))
        log("UNCAUGHT %s: %s" % (etype.__name__, value), "error")
        try:
            sys.__excepthook__(etype, value, tb)
        except Exception:
            pass

    def thread_hook(args):
        block = "".join(traceback.format_exception(args.exc_type, args.exc_value,
                                                   args.exc_traceback))
        with _LOCK:
            TRACES.append("=== thread %s %s ===\n%s"
                          % (args.thread.name if args.thread else "?",
                             time.strftime("%H:%M:%S"), block))
        log("THREAD %s crashed: %s" % (args.thread.name if args.thread else "?",
                                       args.exc_value), "error")

    sys.excepthook = sys_hook
    try:
        threading.excepthook = thread_hook
    except Exception:
        pass
    log("%s %s starting (python %s, frozen=%s, platform=%s)"
        % (APP_NAME, APP_VERSION, sys.version.split()[0], IS_FROZEN, sys.platform), "boot")


# --------------------------------------------------------------------------
# environment snapshot
# --------------------------------------------------------------------------

def _ps(text: str, timeout: float = 25.0) -> str:
    try:
        return (run_ps(text, timeout=timeout) or "").strip()
    except Exception as exc:
        return "ERROR %s: %s" % (type(exc).__name__, exc)


def environment() -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "app": "%s %s" % (APP_NAME, APP_VERSION),
        "frozen_exe": IS_FROZEN,
        "exe_path": sys.executable,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "data_dir": DATA_DIR,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if IS_WINDOWS:
        try:
            v = sys.getwindowsversion()
            env["windows"] = "%s.%s.%s" % (v.major, v.minor, v.build)
        except Exception:
            pass
        try:
            import ctypes
            u = ctypes.windll.user32
            env["screen_physical"] = [u.GetSystemMetrics(0), u.GetSystemMetrics(1)]
            try:
                env["dpi_system"] = u.GetDpiForSystem()
                env["scaling_percent"] = round(100.0 * u.GetDpiForSystem() / 96.0)
            except Exception:
                pass
            try:
                aware = ctypes.c_int(0)
                ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(aware))
                env["dpi_awareness"] = aware.value
            except Exception:
                pass
        except Exception as exc:
            env["ctypes_error"] = str(exc)
        try:
            from app.core import is_admin
            env["admin"] = is_admin()
        except Exception:
            pass
        env["powershell_version"] = _ps("$PSVersionTable.PSVersion.ToString()")
        env["powershell_probe_test"] = _ps(
            "(Get-CimInstance Win32_ComputerSystem).Model", timeout=30)
        rc, out, err = run_shell("sc query Audiosrv | findstr STATE", timeout=20)
        env["service_audio"] = (out or err or "").strip()
        rc, out, err = run_shell("sc query WlanSvc | findstr STATE", timeout=20)
        env["service_wlan"] = (out or err or "").strip()
        env["services_ps"] = _ps(
            "Get-Service Audiosrv,AudioEndpointBuilder,WlanSvc,CamSvc,bthserv "
            "-ErrorAction SilentlyContinue | "
            "ForEach-Object { $_.Name + '=' + $_.Status } | Out-String")
        try:
            from app import pdf as pdf_mod
            b = pdf_mod.find_browser()
            env["pdf_browser"] = b or "NONE FOUND"
        except Exception as exc:
            env["pdf_browser"] = "error %s" % exc
        env["security_products"] = _ps(
            "try { Get-CimInstance -Namespace root/SecurityCenter2 -ClassName "
            "AntiVirusProduct -ErrorAction Stop | Select-Object -ExpandProperty "
            "displayName | Out-String } catch { 'unavailable: ' + "
            "$_.Exception.Message }", timeout=20)
        env["camera_child"] = _camera_child_test()
        env["locale"] = _ps("[System.Globalization.CultureInfo]::CurrentCulture.Name")
        try:
            st = os.statvfs(DATA_DIR) if hasattr(os, "statvfs") else None
            if st:
                env["disk_free_gb"] = round(st.f_bavail * st.f_frsize / 1e9, 1)
            elif IS_WINDOWS:
                import ctypes
                free = ctypes.c_ulonglong(0)
                ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                    ctypes.c_wchar_p(os.path.abspath(DATA_DIR)),
                    ctypes.byref(free), None, None)
                env["disk_free_gb"] = round(free.value / 1e9, 1)
        except Exception:
            pass
    return env


def _camera_child_test() -> str:
    """Ask the camera path (the same child process the GUI uses) for a list."""
    try:
        if IS_FROZEN:
            cmd = [sys.executable, "--camera-list"]
        else:
            cmd = [sys.executable, os.path.join(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))), "run.py"), "--camera-list"]
        rc, out, err = run_cmd(cmd, timeout=30)
        return ("rc=%s out=%s err=%s" % (rc, (out or "")[:400], (err or "")[:300]))
    except Exception as exc:
        return "child launch failed: %s: %s" % (type(exc).__name__, exc)


# --------------------------------------------------------------------------
# the bundle
# --------------------------------------------------------------------------

def bundle(results: Optional[Dict[str, Any]] = None,
           path: Optional[str] = None) -> str:
    """Pack everything a developer needs into one zip. Returns the path."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not path:
        # Windows filenames cannot contain ':' - the raw stamp has colons and
        # that made the bundle write fail with [Errno 22] on shop PCs.
        safe = re.sub(r"[\\/:*?\"<>|]", "-", now_stamp()).replace(" ", "_")
        path = os.path.join(DATA_DIR, "LCP_debug_%s.zip" % safe)
    env = environment()
    parts: Dict[str, str] = {}
    parts["env.json"] = json.dumps(env, indent=2, default=str)
    with _LOCK:
        parts["session.log"] = "\n".join(SESSION[-_MAX:])
        parts["tracebacks.log"] = "\n".join(TRACES) or "(no exceptions recorded)"
    dfile = os.path.join(DATA_DIR, "diagnostics.txt")
    if os.path.exists(dfile):
        try:
            with open(dfile, encoding="utf-8", errors="replace") as fh:
                parts["diagnostics.txt"] = fh.read()[-200000:]
        except Exception:
            pass
    probe = os.path.join(DATA_DIR, "last_probe_raw.json")
    if os.path.exists(probe):
        try:
            with open(probe, encoding="utf-8", errors="replace") as fh:
                parts["probe_raw.json"] = fh.read()[-3000000:]
        except Exception:
            pass
    if results:
        parts["results.json"] = json.dumps(results, indent=1, default=str)[-3000000:]
    listing: List[str] = []
    for folder in (DATA_DIR, REPORTS_DIR):
        for root, _dirs, files in os.walk(folder):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    listing.append("%10d  %s" % (os.path.getsize(fp), fp))
                except Exception:
                    listing.append("       ?  %s" % fp)
    parts["inventory.txt"] = "\n".join(listing) or "(empty)"
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, text in parts.items():
                z.writestr(name, text)
        log("debug bundle written: %s (%d bytes)" % (path, os.path.getsize(path)), "debug")
    except Exception as exc:
        exception("debug.bundle", exc)
        return ""
    return path


def results_dump(scan_results: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for cid, res in (scan_results or {}).items():
        try:
            out[cid] = res.to_dict() if hasattr(res, "to_dict") else dict(res)
        except Exception:
            out[cid] = str(res)
    return out
