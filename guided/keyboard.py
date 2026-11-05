"""
keyboard.py -- the guided KEYBOARD test with real anti-cheat.

THE PROBLEM
    A "press every key" test is worthless if the technician just types on an
    external USB keyboard, a Bluetooth keyboard, a KVM, or a remote-desktop /
    AutoHotkey session. The broken laptop keyboard never gets tested, and the
    report says "keyboard OK".

THE FIX - two independent Windows input layers, both read in-process:
    1. RAW INPUT (WM_INPUT). Every physical key press arrives with the
       hDevice handle of the *exact* device that produced it. We enumerate all
       keyboard devices first (GetRawInputDeviceList), read their interface
       paths (GetRawInputDeviceInfoW -> \\??\\ACPI#PNP0303#... or
       \\??\\USB#VID_046D&PID_C52B#...) and classify them:
           ACPI / PNP03xx ........... built-in keyboard  -> ALLOWED
           USB#VID_................. external USB       -> REJECTED
           BTHENUM#................. external Bluetooth -> REJECTED
           ROOT# / VMBus / Remote ... virtual / RDP      -> REJECTED
           hDevice == NULL .......... injected (SendInput) -> REJECTED
       A key only counts if it came from an ALLOWED handle.
    2. A WH_KEYBOARD_LL low-level hook, which exposes LLKHF_INJECTED. Any
       scripted / synthesised key press is flagged even if raw input is
       unavailable.

    Anything rejected is recorded as a VIOLATION with the offending device
    name, shown on screen in red, and written into the report. The result can
    never be silently "pass".

Everything is ctypes + the standard library. No `keyboard`, no `pynput`, no
pip installs, so the single .exe still contains the whole thing.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
import tkinter as tk
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core import CheckResult, IS_WINDOWS, get
from guided.widgets import COLOURS

WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIM_TYPEKEYBOARD = 1
RI_KEY_BREAK = 1
WH_KEYBOARD_LL = 13
LLKHF_INJECTED = 0x10
LLKHF_LOWER_IL_INJECTED = 0x02
HWND_MESSAGE = -3
GA_ROOT = 2

# --------------------------------------------------------------------------
# key catalogue
# --------------------------------------------------------------------------

# (vk, label, row, required)
KEYMAP: List[Tuple[int, str, int, bool]] = [
    (0x1B, "Esc", 0, True),
    (0x70, "F1", 0, True), (0x71, "F2", 0, True), (0x72, "F3", 0, True),
    (0x73, "F4", 0, True), (0x74, "F5", 0, True), (0x75, "F6", 0, True),
    (0x76, "F7", 0, True), (0x77, "F8", 0, True), (0x78, "F9", 0, True),
    (0x79, "F10", 0, True), (0x7A, "F11", 0, True), (0x7B, "F12", 0, True),

    (0xC0, "`", 1, True),
    (0x31, "1", 1, True), (0x32, "2", 1, True), (0x33, "3", 1, True),
    (0x34, "4", 1, True), (0x35, "5", 1, True), (0x36, "6", 1, True),
    (0x37, "7", 1, True), (0x38, "8", 1, True), (0x39, "9", 1, True),
    (0x30, "0", 1, True),
    (0xBD, "-", 1, True), (0xBB, "=", 1, True), (0x08, "Backspace", 1, True),

    (0x09, "Tab", 2, True),
    (0x51, "Q", 2, True), (0x57, "W", 2, True), (0x45, "E", 2, True),
    (0x52, "R", 2, True), (0x54, "T", 2, True), (0x59, "Y", 2, True),
    (0x55, "U", 2, True), (0x49, "I", 2, True), (0x4F, "O", 2, True),
    (0x50, "P", 2, True),
    (0xDB, "[", 2, True), (0xDD, "]", 2, True), (0xDC, "\\", 2, True),

    (0x14, "Caps", 3, True),
    (0x41, "A", 3, True), (0x53, "S", 3, True), (0x44, "D", 3, True),
    (0x46, "F", 3, True), (0x47, "G", 3, True), (0x48, "H", 3, True),
    (0x4A, "J", 3, True), (0x4B, "K", 3, True), (0x4C, "L", 3, True),
    (0xBA, ";", 3, True), (0xDE, "'", 3, True), (0x0D, "Enter", 3, True),

    (0xA0, "L-Shift", 4, True),
    (0x5A, "Z", 4, True), (0x58, "X", 4, True), (0x43, "C", 4, True),
    (0x56, "V", 4, True), (0x42, "B", 4, True), (0x4E, "N", 4, True),
    (0x4D, "M", 4, True),
    (0xBC, ",", 4, True), (0xBE, ".", 4, True), (0xBF, "/", 4, True),
    (0xA1, "R-Shift", 4, True),

    (0xA2, "L-Ctrl", 5, True), (0x5B, "L-Win", 5, True), (0xA4, "L-Alt", 5, True),
    (0x20, "Space", 5, True),
    (0xA5, "R-Alt", 5, True), (0x5D, "Menu", 5, False), (0xA3, "R-Ctrl", 5, True),
    (0x25, "\u2190", 5, True), (0x28, "\u2193", 5, True), (0x26, "\u2191", 5, True),
    (0x27, "\u2192", 5, True),

    (0x2D, "Ins", 6, False), (0x24, "Home", 6, False), (0x21, "PgUp", 6, False),
    (0x2E, "Del", 6, False), (0x23, "End", 6, False), (0x22, "PgDn", 6, False),
    (0x2C, "PrtSc", 6, False),
]

VK_LABEL = {vk: label for vk, label, _r, _q in KEYMAP}


# --------------------------------------------------------------------------
# win32 structures
# --------------------------------------------------------------------------

if IS_WINDOWS:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    LRESULT = ctypes.c_ssize_t
    WPARAM = ctypes.c_size_t
    LPARAM = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_void_p, ctypes.c_uint, WPARAM, LPARAM)
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)
else:  # pragma: no cover
    user32 = kernel32 = None
    LRESULT = WPARAM = LPARAM = ctypes.c_long
    WNDPROC = HOOKPROC = ctypes.c_void_p


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p), ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p), ("hIconSm", ctypes.c_void_p)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", WPARAM), ("lParam", LPARAM), ("time", ctypes.c_ulong),
                ("x", ctypes.c_long), ("y", ctypes.c_long),
                ("lPrivate", ctypes.c_ulong)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", ctypes.c_ushort), ("usUsage", ctypes.c_ushort),
                ("dwFlags", ctypes.c_uint), ("hwndTarget", ctypes.c_void_p)]


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", ctypes.c_void_p), ("dwType", ctypes.c_uint)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("hDevice", ctypes.c_void_p), ("dwType", ctypes.c_ulong),
                ("wParam", WPARAM)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", ctypes.c_ushort), ("Flags", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort), ("VKey", ctypes.c_ushort),
                ("Message", ctypes.c_uint), ("ExtraInformation", ctypes.c_ulong)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("keyboard", RAWKEYBOARD),
                ("_pad", ctypes.c_byte * 64)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", ctypes.c_ulong), ("scanCode", ctypes.c_ulong),
                ("flags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t)]


# --------------------------------------------------------------------------
# device classification
# --------------------------------------------------------------------------

def classify_device(name: str, friendly: str = "") -> Tuple[str, str]:
    """-> (kind, reason). kind: internal | external-usb | external-bt | virtual | unknown"""
    n = (name or "").upper()
    f = (friendly or "").lower()
    if any(k in f for k in ("remote desktop", "vmware", "virtualbox", "vbox", "teamviewer",
                            "anydesk", "rustdesk", "chrome remote", "virtual")):
        return "virtual", "name matches a remote/virtual keyboard"
    if "BTHENUM" in n or "BTH#" in n or "BLUETOOTH" in f:
        return "external-bt", "Bluetooth-enumerated device"
    if "VMBUS" in n or "ROOT#" in n or "\\ROOT\\" in n or "TERMSRV" in n or "RDPBUS" in n:
        return "virtual", "root/virtual bus enumerated (no physical hardware behind it)"
    if "USB#VID" in n or "USB\\VID" in n:
        return "external-usb", "USB-enumerated device"
    if "PNP03" in n or "ACPI#PNP" in n or "ACPI\\PNP" in n:
        return "internal", "legacy PS/2-style ACPI keyboard (PNP03xx)"
    if "ACPI#" in n or "ACPI\\" in n:
        return "internal", "ACPI-enumerated (built into the motherboard)"
    if n.startswith("\\??\\HID#") or "HID#VID" in n:
        # HID over I2C is the modern built-in laptop keyboard
        if "I2C" in n or "&COL" in n and "USB" not in n and "BTH" not in n:
            return "internal", "HID-over-I2C keyboard (typical built-in laptop keyboard)"
        return "unknown", "HID device with an unrecognised bus"
    if not n:
        return "unknown", "device path could not be read"
    return "unknown", "bus type not recognised"


class KeyboardWatcher:
    """Runs its own thread with a message-only window (raw input) + LL hook."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self.violations: List[Dict[str, Any]] = []
        self.devices: List[Dict[str, Any]] = []
        self.allowed: Dict[int, bool] = {}
        self.lock = threading.Lock()
        self.error = ""
        self.raw_ok = False
        self.hook_ok = False
        self.injected_seen = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._hwnd = None
        self._hook = None
        self._wndproc: Optional[Any] = None
        self._hookproc: Optional[Any] = None
        self._class_atom = 0

    # ------------------------------------------------------------- lifecycle
    def start(self) -> bool:
        if not IS_WINDOWS:
            self.error = "Keyboard capture needs Windows"
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        deadline = time.time() + 5.0
        while time.time() < deadline and not (self.raw_ok or self.hook_ok or self.error):
            time.sleep(0.05)
        return self.raw_ok or self.hook_ok

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._hwnd and user32 is not None:
                user32.PostMessageW(self._hwnd, 0x0012, 0, 0)   # WM_QUIT
        except Exception:
            pass
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)

    # -------------------------------------------------------------- devices
    def enumerate_devices(self) -> List[Dict[str, Any]]:
        self.devices = []
        if not IS_WINDOWS:
            return self.devices
        try:
            n = ctypes.c_uint(0)
            size = ctypes.sizeof(RAWINPUTDEVICELIST)
            user32.GetRawInputDeviceList(None, ctypes.byref(n), size)
            if n.value == 0:
                return self.devices
            buf = (RAWINPUTDEVICELIST * n.value)()
            got = user32.GetRawInputDeviceList(buf, ctypes.byref(n), size)
            for i in range(max(0, got)):
                if buf[i].dwType != RIM_TYPEKEYBOARD:
                    continue
                handle = buf[i].hDevice or 0
                name = self._device_name(handle)
                kind, reason = classify_device(name)
                self.devices.append({
                    "handle": handle, "name": name, "kind": kind, "reason": reason,
                    "allowed": kind == "internal",
                })
        except Exception as exc:
            self.error = self.error or ("device enumeration failed: %s" % exc)
        with self.lock:
            self.allowed = {d["handle"]: d["allowed"] for d in self.devices if d["handle"]}
        return self.devices

    def _device_name(self, handle: int) -> str:
        try:
            size = ctypes.c_uint(0)
            user32.GetRawInputDeviceInfoW(ctypes.c_void_p(handle), RIDI_DEVICENAME,
                                          None, ctypes.byref(size))
            if size.value <= 0:
                return ""
            buf = ctypes.create_unicode_buffer(size.value + 1)
            user32.GetRawInputDeviceInfoW(ctypes.c_void_p(handle), RIDI_DEVICENAME,
                                          buf, ctypes.byref(size))
            return buf.value or ""
        except Exception:
            return ""

    def allow_handle(self, handle: int, allow: bool = True) -> None:
        with self.lock:
            self.allowed[handle] = allow
        for d in self.devices:
            if d["handle"] == handle:
                d["allowed"] = allow

    # ---------------------------------------------------------------- thread
    def _loop(self) -> None:
        try:
            self._wndproc = WNDPROC(self._wnd_proc)
            cls_name = "LCPKeyWatch%d" % os.getpid()
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = kernel32.GetModuleHandleW(None)
            wc.lpszClassName = cls_name
            self._class_atom = user32.RegisterClassExW(ctypes.byref(wc))
            if not self._class_atom:
                self.error = "RegisterClassEx failed (%d)" % ctypes.get_last_error()
            hwnd = user32.CreateWindowExW(0, cls_name, "LaptopCheckProKeyWatch", 0,
                                          0, 0, 0, 0, ctypes.c_void_p(HWND_MESSAGE),
                                          None, wc.hInstance, None)
            if not hwnd:
                self.error = (self.error or "") + " CreateWindowEx(message-only) failed"
            else:
                self._hwnd = hwnd
                rid = RAWINPUTDEVICE()
                rid.usUsagePage = 0x01      # Generic Desktop
                rid.usUsage = 0x06          # Keyboard
                rid.dwFlags = RIDEV_INPUTSINK
                rid.hwndTarget = hwnd
                if user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid)):
                    self.raw_ok = True
                else:
                    self.error = (self.error + " | RegisterRawInputDevices failed (%d)"
                                  % kernel32.GetLastError())
            # low-level hook (installed on this same pumping thread)
            try:
                self._hookproc = HOOKPROC(self._ll_proc)
                h = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hookproc,
                                             kernel32.GetModuleHandleW(None), 0)
                self._hook = h
                self.hook_ok = bool(h)
                if not h:
                    self.error = (self.error or "") + " | SetWindowsHookEx failed (%d)" % kernel32.GetLastError()
            except Exception as exc:
                self.error = (self.error or "") + " | hook: %s" % exc

            if not (self.raw_ok or self.hook_ok):
                return
            self.enumerate_devices()
            msg = MSG()
            while not self._stop.is_set():
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0 or r == -1:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self.error = (self.error or "") + " | thread: %s: %s" % (type(exc).__name__, exc)
        finally:
            try:
                if self._hook:
                    user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            try:
                if self._hwnd:
                    user32.DestroyWindow(self._hwnd)
                if self._class_atom:
                    user32.UnregisterClassW(ctypes.c_wchar_p("LCPKeyWatch%d" % os.getpid()),
                                            kernel32.GetModuleHandleW(None))
            except Exception:
                pass

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_INPUT:
                self._handle_input(lparam)
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle_input(self, lparam: int) -> None:
        size = ctypes.c_uint(ctypes.sizeof(RAWINPUT))
        ri = RAWINPUT()
        got = user32.GetRawInputData(ctypes.c_void_p(lparam), RID_INPUT,
                                     ctypes.byref(ri), ctypes.byref(size),
                                     ctypes.sizeof(RAWINPUTHEADER))
        if got <= 0 or got == 0xFFFFFFFF:
            return
        if ri.header.dwType != RIM_TYPEKEYBOARD:
            return
        vk = int(ri.keyboard.VKey)
        flags = int(ri.keyboard.Flags)
        if flags & RI_KEY_BREAK:
            return                                   # only care about key-down
        handle = int(ri.header.hDevice or 0)
        allowed = self.allowed.get(handle, False) if handle else False
        with self.lock:
            if handle == 0:
                self.injected_seen += 1
            self.events.append({"vk": vk, "handle": handle, "allowed": allowed,
                                "source": "raw", "t": time.time()})
            if not allowed:
                name = ""
                for d in self.devices:
                    if d["handle"] == handle:
                        name = d["name"]
                        break
                self.violations.append({
                    "vk": vk, "label": VK_LABEL.get(vk, hex(vk)), "handle": handle,
                    "device": name or ("injected / no device handle" if handle == 0 else "unknown device"),
                    "reason": "key did not come from the built-in keyboard",
                    "t": time.strftime("%H:%M:%S"),
                })

    def _ll_proc(self, code, wparam, lparam):
        try:
            if code >= 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(ctypes.c_void_p(lparam), ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED):
                    with self.lock:
                        self.injected_seen += 1
                        self.violations.append({
                            "vk": int(kb.vkCode), "label": VK_LABEL.get(int(kb.vkCode), hex(int(kb.vkCode))),
                            "handle": 0, "device": "INJECTED (software-simulated key press)",
                            "reason": "LLKHF_INJECTED flag set - a program sent this key, not a human",
                            "t": time.strftime("%H:%M:%S"),
                        })
        except Exception:
            pass
        try:
            return user32.CallNextHookEx(self._hook, code, wparam, lparam)
        except Exception:
            return 1

    # ----------------------------------------------------------------- state
    def pressed(self) -> Dict[int, Dict[str, Any]]:
        """vk -> {'ok': bool, 'from': str} for keys accepted so far."""
        out: Dict[int, Dict[str, Any]] = {}
        with self.lock:
            for e in self.events:
                vk = e["vk"]
                cur = out.get(vk)
                if e["allowed"]:
                    out[vk] = {"ok": True, "handle": e["handle"]}
                elif cur is None:
                    out[vk] = {"ok": False, "handle": e["handle"]}
        return out

    def snapshot_violations(self) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.violations)


# --------------------------------------------------------------------------
# the dialog
# --------------------------------------------------------------------------

class KeyboardTest:
    def __init__(self, master: tk.Misc, res: CheckResult, ctx: Dict[str, Any],
                 emit: Optional[Callable[[str], None]] = None) -> None:
        self.master = master
        self.res = res
        self.ctx = ctx
        self.emit = emit
        self.watcher = KeyboardWatcher()
        self.labels: Dict[int, tk.Label] = {}
        self.missing: Dict[int, bool] = {}
        self.violation_count = 0
        self.started = time.time()
        self.top: Optional[tk.Toplevel] = None
        self.confirm_var = tk.BooleanVar(value=False)

    # -------------------------------------------------------------------- run
    def run(self) -> CheckResult:
        if not IS_WINDOWS:
            self.res.skipped("Keyboard test needs Windows")
            return self.res

        known_internal = (self.ctx.get("input") or {}).get("internal_keyboards") or []
        external = (self.ctx.get("input") or {}).get("external") or []

        self.top = tk.Toplevel(self.master)
        self.top.title("Keyboard test - built-in keyboard only")
        self.top.configure(bg=COLOURS["bg"])
        self.top.geometry("1080x760")
        self.top.minsize(980, 660)

        head = tk.Frame(self.top, bg=COLOURS["panel2"])
        head.pack(fill="x")
        tk.Label(head, text="KEYBOARD TEST  -  built-in keyboard only",
                 bg=COLOURS["panel2"], fg=COLOURS["text"],
                 font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(head, text="Every key must physically come from the laptop's own keyboard. "
                            "External USB / Bluetooth / remote / simulated keys are detected and rejected.",
                 bg=COLOURS["panel2"], fg=COLOURS["accent"], font=("Segoe UI", 10),
                 anchor="w", justify="left", wraplength=1040).pack(anchor="w", padx=16, pady=(2, 10))

        body = tk.Frame(self.top, bg=COLOURS["bg"])
        body.pack(fill="both", expand=True, padx=12, pady=8)

        # ---- device panel -------------------------------------------------
        dev = tk.LabelFrame(body, text="  Keyboard devices detected by Windows  ",
                            bg=COLOURS["bg"], fg=COLOURS["text"],
                            font=("Segoe UI", 10, "bold"))
        dev.pack(fill="x", pady=(0, 8))
        self.dev_list = tk.Frame(dev, bg=COLOURS["bg"])
        self.dev_list.pack(fill="x", padx=8, pady=6)

        # ---- warning -------------------------------------------------------
        self.warn = tk.Label(body, text="", bg="#2b1d1d", fg="#ffb4ad",
                             font=("Segoe UI", 10, "bold"), anchor="w",
                             justify="left", wraplength=1030, padx=10, pady=6)
        self.warn.pack(fill="x", pady=(0, 8))

        # ---- key grid ------------------------------------------------------
        grid = tk.Frame(body, bg=COLOURS["bg"])
        grid.pack(fill="x")
        rows: Dict[int, tk.Frame] = {}
        for r in range(7):
            f = tk.Frame(grid, bg=COLOURS["bg"])
            f.pack(fill="x", pady=1)
            rows[r] = f
        for vk, label, row, required in KEYMAP:
            width = max(3, len(label) + 1)
            if label == "Space":
                width = 26
            if label in ("Backspace", "Enter", "Tab", "Caps", "L-Shift", "R-Shift"):
                width = max(width, 9)
            lab = tk.Label(rows[row], text=label, width=width,
                           bg=COLOURS["panel"], fg="#7c8798", relief="raised", bd=2,
                           font=("Consolas", 9, "bold"), cursor="hand2",
                           highlightthickness=1, highlightbackground=COLOURS["line"])
            lab.pack(side="left", padx=1, ipady=4)
            lab.bind("<Button-1>", lambda e, v=vk, l=label: self.mark_absent(v, l))
            self.labels[vk] = lab

        # ---- progress ------------------------------------------------------
        prog = tk.Frame(body, bg=COLOURS["bg"])
        prog.pack(fill="x", pady=(10, 0))
        self.prog_label = tk.Label(prog, text="", bg=COLOURS["bg"], fg=COLOURS["text"],
                                   font=("Segoe UI", 12, "bold"))
        self.prog_label.pack(side="left")
        self.vio_label = tk.Label(prog, text="", bg=COLOURS["bg"], fg=COLOURS["bad"],
                                  font=("Segoe UI", 11, "bold"))
        self.vio_label.pack(side="left", padx=18)

        # ---- confirm + buttons ---------------------------------------------
        foot = tk.Frame(self.top, bg=COLOURS["panel"])
        foot.pack(fill="x", side="bottom")
        inner = tk.Frame(foot, bg=COLOURS["panel"])
        inner.pack(fill="x", padx=14, pady=10)
        chk = tk.Checkbutton(inner, text="I confirm: no external keyboard / KVM / remote desktop / "
                                         "key-macro software is connected, and I pressed every key myself.",
                             variable=self.confirm_var, bg=COLOURS["panel"], fg=COLOURS["text"],
                             selectcolor=COLOURS["panel2"], activebackground=COLOURS["panel"],
                             activeforeground=COLOURS["text"], font=("Segoe UI", 9),
                             anchor="w", justify="left", wraplength=720)
        chk.pack(side="left", fill="x", expand=True)
        tk.Button(inner, text="FINISH TEST", bg=COLOURS["accent"], fg="white", relief="flat",
                  font=("Segoe UI", 11, "bold"), padx=16, pady=7, cursor="hand2",
                  command=self._finish).pack(side="right", padx=4)
        tk.Button(inner, text="Reset", bg=COLOURS["panel2"], fg=COLOURS["muted"], relief="flat",
                  font=("Segoe UI", 9), padx=10, cursor="hand2",
                  command=self._reset).pack(side="right", padx=4)

        hint = tk.Label(body, text="Click a key cap to mark it as NOT PRESENT on this keyboard "
                                   "(many laptops have no Ins/Home/PgUp without Fn).",
                        bg=COLOURS["bg"], fg=COLOURS["muted"], font=("Segoe UI", 8), anchor="w")
        hint.pack(fill="x", pady=(4, 0))

        self.top.protocol("WM_DELETE_WINDOW", self._finish)
        self._start_watcher(known_internal, external)
        self._tick()
        try:
            self.top.grab_set()
        except Exception:
            pass
        self.master.wait_window(self.top)
        self.watcher.stop()
        self._write_result()
        return self.res

    # ---------------------------------------------------------------- devices
    def _start_watcher(self, known_internal: List[str], external: List[Dict[str, Any]]) -> None:
        ok = self.watcher.start()
        devices = self.watcher.enumerate_devices()
        for child in self.dev_list.winfo_children():
            child.destroy()
        if not devices:
            tk.Label(self.dev_list, text="Raw Input reported no keyboard devices "
                                         "(falling back to the low-level hook - device origin cannot "
                                         "be verified on this machine).",
                     bg=COLOURS["bg"], fg="#ffd479", font=("Segoe UI", 9),
                     anchor="w", wraplength=1000, justify="left").pack(fill="x")
            self.res.metric("Raw Input device verification", "Unavailable")
        for d in devices:
            colour = COLOURS["good"] if d["allowed"] else COLOURS["bad"]
            state = "ALLOWED" if d["allowed"] else "REJECTED"
            txt = "%-10s  %-58s  %s" % (state, (d["name"] or "(no device path)")[:58], d["reason"])
            lab = tk.Label(self.dev_list, text=txt, bg=COLOURS["bg"], fg=colour,
                           font=("Consolas", 9), anchor="w", cursor="hand2")
            lab.pack(fill="x")
            if not d["allowed"]:
                lab.bind("<Button-1>", lambda e, h=d["handle"]: self._toggle_allow(h))
        self.res.metric("Raw Input devices found", len(devices))
        self.res.metric("Raw Input capture", "Working" if self.watcher.raw_ok else "FAILED")
        self.res.metric("Low-level hook capture", "Working" if self.watcher.hook_ok else "FAILED")
        for d in devices:
            self.res.metric("  Device [%s]" % ("ALLOWED" if d["allowed"] else "REJECTED"),
                            "%s - %s" % (d["name"] or "(no path)", d["reason"]))
        if not ok:
            self.warn.configure(bg="#2b1d1d",
                                text="INPUT CAPTURE FAILED: %s\nThe keyboard test cannot verify key "
                                     "origins. Any result will be flagged in the report." % self.watcher.error)
        elif external:
            names = ", ".join("%s (%s)" % (e.get("name"), e.get("kind")) for e in external[:3])
            self.warn.configure(bg="#2b2416",
                                text="WARNING - Windows reports external/virtual keyboard(s): %s\n"
                                     "UNPLUG them now. Keys typed on them will be REJECTED and recorded "
                                     "as violations." % names)
        else:
            self.warn.configure(bg="#152419",
                                text="Ready. Press every key on the BUILT-IN keyboard. Keys light up green "
                                     "when accepted.")

    def _toggle_allow(self, handle: int) -> None:
        cur = self.watcher.allowed.get(handle, False)
        self.watcher.allow_handle(handle, not cur)
        for d in self.watcher.devices:
            if d["handle"] == handle:
                d["override"] = not cur
        self._start_watcher([], [])
        if not cur:
            self.res.flag("MANUAL OVERRIDE: technician allowed a rejected keyboard device (handle %s). "
                          "This exception is recorded." % handle, "warn")

    # ------------------------------------------------------------------ loop
    def _tick(self) -> None:
        if self.top is None or not self.top.winfo_exists():
            return
        pressed = self.watcher.pressed()
        done = 0
        total_req = 0
        for vk, label, row, required in KEYMAP:
            if self.missing.get(vk):
                self._paint(vk, "#3b2f18", "#e0b25c")
                continue
            info = pressed.get(vk)
            if info and info["ok"]:
                self._paint(vk, "#123b23", "#5ce08a")
                if required:
                    done += 1
            elif info and not info["ok"]:
                self._paint(vk, "#3b1414", "#ff8f86")
            else:
                self._paint(vk, COLOURS["panel"], "#7c8798")
            if required:
                total_req += 1
        vios = self.watcher.snapshot_violations()
        if len(vios) != self.violation_count:
            new = vios[self.violation_count:]
            self.violation_count = len(vios)
            for v in new[-3:]:
                self.warn.configure(bg="#2b1d1d",
                                    text="REJECTED: '%s' came from %s (%s)"
                                         % (v["label"], v["device"][:60], v["reason"]))
                if self.emit:
                    self.emit("Keyboard violation: '%s' from %s" % (v["label"], v["device"][:50]))
        self.prog_label.configure(text="Required keys pressed: %d / %d" % (done, total_req))
        self.vio_label.configure(text=("VIOLATIONS: %d" % len(vios)) if vios else "No violations")
        self.master.after(120, self._tick)

    def _paint(self, vk: int, bg: str, fg: str) -> None:
        lab = self.labels.get(vk)
        if lab is not None:
            try:
                lab.configure(bg=bg, fg=fg)
            except Exception:
                pass

    def mark_absent(self, vk: int, label: str) -> None:
        self.missing[vk] = not self.missing.get(vk, False)
        if self.missing[vk]:
            self._paint(vk, "#3b2f18", "#e0b25c")
            if self.emit:
                self.emit("Key '%s' marked as not present on this keyboard" % label)

    def _reset(self) -> None:
        self.watcher.events.clear()
        self.watcher.violations.clear()
        self.missing.clear()
        self.violation_count = 0
        for vk in self.labels:
            self._paint(vk, COLOURS["panel"], "#7c8798")

    def _finish(self) -> None:
        try:
            self.top.destroy()
        except Exception:
            pass

    # ---------------------------------------------------------------- result
    def _write_result(self) -> None:
        res = self.res
        pressed = self.watcher.pressed()
        vios = self.watcher.snapshot_violations()
        required = [(vk, label) for vk, label, row, req in KEYMAP if req]
        optional = [(vk, label) for vk, label, row, req in KEYMAP if not req]

        ok_req = [(vk, l) for vk, l in required if pressed.get(vk, {}).get("ok")]
        bad_req = [(vk, l) for vk, l in required
                   if vk in pressed and not pressed[vk]["ok"] and not self.missing.get(vk)]
        never = [(vk, l) for vk, l in required if vk not in pressed and not self.missing.get(vk)]
        missing_marked = [(vk, l) for vk, l in required + optional if self.missing.get(vk)]
        ok_opt = [(vk, l) for vk, l in optional if pressed.get(vk, {}).get("ok")]

        res.metric("Required keys", len(required))
        res.metric("Pressed on the built-in keyboard", len(ok_req))
        res.metric("Optional keys also tested", len(ok_opt))
        res.metric("Keys marked as not present", len(missing_marked))
        res.metric("Keys never pressed", len(never))
        res.metric("Keys rejected (wrong device)", len(bad_req))
        res.metric("Total violations recorded", len(vios))
        res.metric("Injected key presses detected", self.watcher.injected_seen)
        res.metric("Technician confirmation ticked", "Yes" if self.confirm_var.get() else "NO")
        res.metric("Test duration", "%.0f s" % (time.time() - self.started))

        if missing_marked:
            res.metric("Not-present keys", ", ".join(l for _vk, l in missing_marked))
        if never:
            res.metric("Never pressed", ", ".join(l for _vk, l in never[:24]))
        if bad_req:
            res.metric("Rejected keys", ", ".join(l for _vk, l in bad_req[:24]))

        for v in vios[:40]:
            res.metric("VIOLATION %s" % v["t"], "key '%s' from %s (%s)"
                       % (v["label"], v["device"][:70], v["reason"]))

        # ---- flags -------------------------------------------------------
        if vios:
            res.flag("%d key press(es) were REJECTED because they did not come from the built-in "
                     "keyboard (%s)" % (len(vios), ", ".join(sorted({v["device"][:34] for v in vios})[:3])),
                     "fail")
        if self.watcher.injected_seen:
            res.flag("%d key press(es) were software-injected (LLKHF_INJECTED) - a script or "
                     "remote session sent them" % self.watcher.injected_seen, "fail")
        if not self.confirm_var.get():
            res.flag("The technician did not tick the 'no external keyboard' confirmation", "warn")
        if not self.watcher.raw_ok:
            res.flag("Raw Input capture was unavailable, so key origins could NOT be verified "
                     "for this test (%s)" % (self.watcher.error or "unknown reason"), "fail")
        for vk, l in bad_req:
            res.flag("Key '%s' was only ever pressed on a rejected device" % l, "fail")
        if never:
            res.flag("%d required key(s) were never pressed: %s"
                     % (len(never), ", ".join(l for _vk, l in never[:12])), "warn")
        if missing_marked:
            res.flag("%d key(s) were marked as not present on this keyboard: %s"
                     % (len(missing_marked), ", ".join(l for _vk, l in missing_marked[:10])), "warn")

        # ---- score -------------------------------------------------------
        denom = len(required)
        covered = len(ok_req) + len(missing_marked)
        score = 100.0 * covered / denom if denom else 0.0
        if bad_req:
            score -= 15 * len(bad_req)
        if vios:
            score -= min(45, 8 * len(vios))
        if not self.watcher.raw_ok:
            score -= 25
        if not self.confirm_var.get():
            score -= 10
        if self.watcher.injected_seen:
            score -= 30
        score = max(0.0, min(100.0, score))

        res.marks = [{"id": "keyboard", "name": "Built-in keyboard",
                      "mark": "good" if score >= 90 else ("bad" if score < 55 else "okay"),
                      "note": "%d/%d required keys verified on the built-in keyboard, %d violation(s)"
                              % (len(ok_req), denom, len(vios))}]
        res.notes.append("Anti-cheat: keys were validated against the Raw Input device handle of the "
                         "built-in keyboard, plus a low-level hook that flags injected key presses.")
        if score >= 90 and not vios:
            res.ok("All %d required keys verified on the built-in keyboard, no violations" % len(ok_req),
                   score=score)
        elif score >= 55:
            res.warn("%d/%d required keys verified, %d violation(s)" % (len(ok_req), denom, len(vios)),
                     score=score)
        else:
            res.fail("Keyboard test incomplete or tampered: %d/%d keys, %d violation(s)"
                     % (len(ok_req), denom, len(vios)), score=score)


def run_keyboard_test(master: tk.Misc, res: CheckResult, ctx: Dict[str, Any],
                      emit: Optional[Callable[[str], None]] = None) -> CheckResult:
    return KeyboardTest(master, res, ctx, emit).run()
