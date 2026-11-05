"""
misc.py -- the remaining guided tests: touchpad, USB ports, external video /
audio jack / card reader, cosmetics and the technician sign-off.

Where possible these are NOT just questions. The USB-port and HDMI tests
actually watch Windows for a device appearing, so the technician cannot mark a
dead port as working without plugging something in:

    plug a pen drive into port 1  ->  the tool sees a new removable volume,
                                      beeps, and ticks port 1 automatically
    plug an HDMI monitor          ->  the tool sees the display count change
    plug headphones               ->  the tool sees a new audio endpoint
    insert an SD card             ->  the tool sees a new removable volume

A manual override always exists (some machines have odd drivers), but using it
is written into the report as a manual confirmation, not an automatic pass.
"""

from __future__ import annotations

import os
import shutil
import threading
import ctypes
import time
import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core import (CheckResult, IS_WINDOWS, PHOTOS_DIR, cim, fmt_bytes, get,
                      parse_json_loose, run_cmd, run_ps)
from guided.widgets import COLOURS, GuidedDialog, checklist_dialog
from guided.audio import beep

OPT_OK = [("yes", "WORKS", COLOURS["good"]), ("weak", "UNRELIABLE", COLOURS["okay"]),
          ("no", "FAULTY", COLOURS["bad"]), ("na", "NOT FITTED", COLOURS["skip"])]
SCORE_FOR = {"yes": 100.0, "weak": 58.0, "no": 5.0, "na": None}


# ==========================================================================
# helpers: watch Windows for a change
# ==========================================================================

def _usb_signature() -> str:
    rows = cim("Win32_DiskDrive", ["Model", "InterfaceType", "Size", "MediaType"])
    parts = []
    for r in rows:
        it = str(get(r, "InterfaceType") or "").lower()
        mt = str(get(r, "MediaType") or "").lower()
        if "usb" in it or "removable" in mt or "external" in mt:
            parts.append("%s|%s" % (get(r, "Model"), get(r, "Size")))
    return ";".join(sorted(parts))


def _volume_signature() -> str:
    rows = cim("Win32_LogicalDisk", ["DeviceID", "DriveType", "VolumeName", "Size"])
    parts = []
    for r in rows:
        dt = get(r, "DriveType")
        if dt in (2, 5):           # removable / CD
            parts.append("%s|%s|%s" % (get(r, "DeviceID"), get(r, "VolumeName"), get(r, "Size")))
    return ";".join(sorted(parts))


def _display_signature() -> str:
    out = run_ps("$ProgressPreference='SilentlyContinue';Add-Type -AssemblyName System.Windows.Forms;"
                 "$s=[System.Windows.Forms.Screen]::AllScreens;"
                 "[pscustomobject]@{ N=$s.Count; Names=(($s | ForEach-Object { \"$($_.DeviceName):$($_.Bounds.Width)x$($_.Bounds.Height)\" }) -join ',') }"
                 " | ConvertTo-Json -Compress", timeout=25)
    data = parse_json_loose(out) or {}
    if isinstance(data, dict):
        return "%s|%s" % (data.get("N"), data.get("Names"))
    rows = cim("Win32_VideoController", ["CurrentHorizontalResolution", "CurrentVerticalResolution"])
    return ";".join("%sx%s" % (get(r, "CurrentHorizontalResolution"),
                               get(r, "CurrentVerticalResolution")) for r in rows)


def _audio_endpoint_signature() -> str:
    out = run_ps("$ProgressPreference='SilentlyContinue';$ErrorActionPreference='SilentlyContinue';"
                 "(Get-PnpDevice -Class AudioEndpoint -PresentOnly | ForEach-Object { $_.InstanceId }) -join ','",
                 timeout=25)
    return (out or "").strip()


def _new_pnp_signature() -> str:
    out = run_ps("$ProgressPreference='SilentlyContinue';$ErrorActionPreference='SilentlyContinue';"
                 "(Get-PnpDevice -PresentOnly -Status OK | Where-Object { $_.Class -eq 'USB' } "
                 "| ForEach-Object { $_.InstanceId }) -join ','", timeout=30)
    return (out or "").strip()


SIGNATURES: Dict[str, Callable[[], str]] = {
    "usb": lambda: _usb_signature() + "||" + _volume_signature(),
    "display": _display_signature,
    "audio": _audio_endpoint_signature,
    "pnp": _new_pnp_signature,
}


class ChangeWatcher(threading.Thread):
    """Polls a signature in a background thread until it changes."""

    def __init__(self, kind: str, interval: float = 1.6) -> None:
        super().__init__(daemon=True)
        self.kind = kind
        self.interval = interval
        self.baseline = ""
        self.changed = False
        self.detail = ""
        self.stop_flag = threading.Event()
        self.error = ""
        self.polls = 0

    def prime(self) -> None:
        try:
            self.baseline = SIGNATURES[self.kind]()
        except Exception as exc:
            self.error = str(exc)
            self.baseline = ""

    def run(self) -> None:
        while not self.stop_flag.is_set():
            try:
                now = SIGNATURES[self.kind]()
                self.polls += 1
                if now != self.baseline:
                    self.changed = True
                    self.detail = now
                    return
            except Exception as exc:
                self.error = str(exc)
            self.stop_flag.wait(self.interval)

    def stop(self) -> None:
        self.stop_flag.set()


def wait_for_change(master: tk.Misc, kind: str, prompt: str,
                    timeout: float = 90.0,
                    on_event: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
    """Blocking-with-UI wait. Returns (changed, detail)."""
    w = ChangeWatcher(kind)
    w.prime()
    w.start()
    state = {"done": False, "result": (False, ""), "t0": time.time()}

    def poll() -> None:
        if state["done"]:
            return
        if w.changed:
            state["result"] = (True, w.detail)
            state["done"] = True
        elif time.time() - state["t0"] > timeout:
            state["result"] = (False, "timeout after %.0fs" % timeout)
            state["done"] = True
        if state["done"]:
            w.stop()
            if on_event:
                on_event("DETECTED" if state["result"][0] else "TIMEOUT")
            return
        left = int(timeout - (time.time() - state["t0"]))
        if on_event:
            on_event("%s  (%ds left, polling...)" % (prompt, left))
        master.after(500, poll)

    poll()
    return state["result"]


# ==========================================================================
# TOUCHPAD
# ==========================================================================

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def measure_pointer(master: tk.Misc, seconds: float = 12.0) -> Dict[str, Any]:
    """Objective touchpad proof: travel distance + real button presses,
    measured while the technician wiggles and clicks on a full-screen pad."""
    out: Dict[str, Any] = {"travel": 0, "left": 0, "right": 0, "ok": False}
    if not IS_WINDOWS:
        return out
    try:
        user32 = ctypes.windll.user32
        user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
        user32.GetCursorPos.restype = ctypes.c_int
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
    except Exception:
        return out
    top = tk.Toplevel(master)
    top.configure(bg="#0f172a")
    top.overrideredirect(True)
    top.update_idletasks()
    from app.core import screen_size
    sw, sh = screen_size()
    if not sw:
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
    top.geometry("%dx%d+0+0" % (sw, sh))
    try:
        top.attributes("-topmost", True)
    except Exception:
        pass
    from guided.widgets import brand_bar
    brand_bar(top, "Touchpad measurement")
    tk.Label(top, text="MOVE YOUR FINGER ALL OVER THE PAD\nand press LEFT and RIGHT click a few times",
             bg="#0f172a", fg="#e2e8f0", font=("Segoe UI", 22, "bold"),
             justify="center").pack(expand=True)
    bar = tk.Label(top, text="", bg="#0f172a", fg="#3b82f6", font=("Segoe UI", 14, "bold"))
    bar.pack(pady=(0, 60))
    state = {"last": _POINT(), "l": False, "r": False, "t0": time.time()}
    user32.GetCursorPos(ctypes.byref(state["last"]))

    def tick() -> None:
        try:
            p = _POINT()
            user32.GetCursorPos(ctypes.byref(p))
            out["travel"] += int(((p.x - state["last"].x) ** 2 +
                                  (p.y - state["last"].y) ** 2) ** 0.5)
            state["last"] = p
            ld = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
            rd = bool(user32.GetAsyncKeyState(0x02) & 0x8000)
            if ld and not state["l"]:
                out["left"] += 1
            if rd and not state["r"]:
                out["right"] += 1
            state["l"], state["r"] = ld, rd
            left = seconds - (time.time() - state["t0"])
            bar.configure(text="measuring... %ds   travel %d px   L %d   R %d"
                          % (max(0, int(left)), out["travel"], out["left"], out["right"]))
            if left <= 0:
                out["ok"] = True
                top.destroy()
                return
            top.after(50, tick)
        except Exception:
            out["ok"] = True
            try:
                top.destroy()
            except Exception:
                pass
    top.after(120, tick)
    try:
        master.wait_window(top)
    except Exception:
        pass
    return out


def run_touchpad_test(master: tk.Misc, res: CheckResult, ctx: Dict[str, Any],
                      emit: Optional[Callable[[str], None]] = None) -> CheckResult:
    inp = ctx.get("input") or {}
    pads = inp.get("touchpads") or []
    ext_mice = inp.get("ext_mice") or []
    if not pads:
        res.na("No pointing device (touchpad) is registered in Windows")
        res.note("If this laptop should have a touchpad, the driver is missing - install the touchpad driver from the maker's support page and re-test. "
                 "A missing Precision Touchpad driver is common after a fresh Windows install.")
        return res

    res.metric("Pointing devices", ", ".join(pads)[:120])
    if ext_mice:
        res.metric("External mice connected", ", ".join(ext_mice)[:120])
    meas = measure_pointer(master)
    if meas.get("ok"):
        res.metric("Measured pointer travel", "%d px" % meas["travel"])
        res.metric("Measured button presses", "left %d / right %d"
                   % (meas["left"], meas["right"]))
        if meas["travel"] < 800:
            res.flag("Pointer barely moved during the measured pass (%d px) - the pad may "
                     "not track across its whole surface" % meas["travel"], "warn")
        if meas["left"] < 1:
            res.flag("No LEFT click registered during the measured pass", "warn")
        if meas["right"] < 1:
            res.flag("No RIGHT click registered during the measured pass", "warn")

    items = [
        {"id": "move", "label": "Pointer MOVES smoothly",
         "hint": "Move your finger over the whole pad. The pointer must track accurately in every "
                 "corner, with no jumping, dead zones or sudden leaps.", "options": OPT_OK, "note": True},
        {"id": "lclick", "label": "LEFT click (press and tap-to-click)",
         "hint": "Try both the physical press at the bottom-left and a light tap on the pad surface.",
         "options": OPT_OK, "note": True},
        {"id": "rclick", "label": "RIGHT click",
         "hint": "Bottom-right press, and a two-finger tap if that is enabled.", "options": OPT_OK, "note": True},
        {"id": "scroll", "label": "TWO-FINGER SCROLL",
         "hint": "Scroll a web page or folder up and down with two fingers. Must be smooth in both directions.",
         "options": OPT_OK, "note": True},
        {"id": "gesture", "label": "Precision gestures (3-finger swipe / pinch zoom)",
         "hint": "Only applies to Precision Touchpads. Mark NOT FITTED if this is an old "
                 "Synaptics/ELAN pad without gestures.", "options": OPT_OK, "note": True},
        {"id": "drift", "label": "No cursor DRIFT when untouched",
         "hint": "! Take your hands off the machine and watch the pointer for 10 seconds. A pointer "
                 "that creeps on its own means a failing pad or a swollen battery pressing on it.",
         "options": OPT_OK, "note": True},
        {"id": "physical", "label": "Pad surface and buttons are physically sound",
         "hint": "No cracks, no sticky feel, no rattling button, pad is not lifting out of the palm rest.",
         "options": OPT_OK, "note": True},
        {"id": "pointstick", "label": "TrackPoint / pointing stick (if fitted)",
         "hint": "Mark NOT FITTED if this model does not have one.", "options": OPT_OK, "note": True},
    ]
    if ext_mice:
        items.insert(0, {"id": "unplug", "label": "External mouse was UNPLUGGED for this test",
                         "hint": "! Windows reports an external mouse: %s. Unplug it so the marks below "
                                 "really refer to the touchpad." % ", ".join(ext_mice)[:80],
                         "options": [("yes", "UNPLUGGED", COLOURS["good"]),
                                     ("no", "STILL PLUGGED", COLOURS["bad"])],
                         "note": True})

    out = checklist_dialog(master, "Touchpad / pointing device test",
                           "Test the built-in touchpad - not a USB mouse", items, width=860)
    if out.get("cancelled"):
        res.skipped("Touchpad test was cancelled")
        return res

    answers = out.get("answers", {})
    scores: List[float] = []
    for it in items:
        key = answers.get(it["id"])
        note = answers.get(it["id"] + "_note", "")
        label = {o[0]: o[1] for o in it["options"]}.get(key, "not answered")
        res.metric(it["label"][:52], label + ((" - " + note) if note else ""))
        if key is None:
            res.flag("'%s' was never answered" % it["label"], "warn")
            continue
        sc = SCORE_FOR.get(key)
        if sc is not None:
            scores.append(sc)
        if key == "no":
            res.flag("Touchpad: %s - FAULTY" % it["label"], "fail")
        elif key == "weak":
            res.flag("Touchpad: %s - unreliable" % it["label"], "warn")
    if answers.get("unplug") == "no":
        res.flag("An external mouse stayed connected during the touchpad test - the marks are not "
                 "reliable", "fail")
        scores.append(20.0)
    if out.get("note"):
        res.note("Technician note: " + out["note"])
    res.marks = [{"id": it["id"], "name": it["label"], "mark": answers.get(it["id"]),
                  "note": answers.get(it["id"] + "_note", "")} for it in items]
    if not scores:
        res.skipped("Touchpad test not completed")
        return res
    score = sum(scores) / len(scores)
    res.set("pass" if score >= 88 else ("warn" if score >= 55 else "fail"),
            "Touchpad scored %.0f/100 across %d checks" % (score, len(scores)), score=score)
    return res


# ==========================================================================
# USB PORTS
# ==========================================================================

def run_usb_port_test(master: tk.Misc, res: CheckResult, ctx: Dict[str, Any],
                      emit: Optional[Callable[[str], None]] = None) -> CheckResult:
    ports_info = ctx.get("ports") or {}
    suggested = 0
    hubs = ports_info.get("hubs") or 0
    if hubs:
        suggested = max(1, min(6, hubs - 1))
    state: Dict[str, Any] = {"n": tk.IntVar(value=suggested or 3), "results": {},
                             "watcher": None, "running": False}

    def build(dlg: GuidedDialog, parent: tk.Frame) -> None:
        box = tk.Frame(parent, bg=COLOURS["panel"], highlightthickness=1,
                       highlightbackground=COLOURS["line"])
        box.pack(fill="x", pady=(0, 8))
        row = tk.Frame(box, bg=COLOURS["panel"])
        row.pack(fill="x", padx=12, pady=10)
        tk.Label(row, text="How many USB ports (A + C) does this machine have?",
                 bg=COLOURS["panel"], fg=COLOURS["text"], font=("Segoe UI", 10, "bold")).pack(side="left")
        sp = tk.Spinbox(row, from_=1, to=12, textvariable=state["n"], width=5,
                        bg=COLOURS["panel2"], fg=COLOURS["text"], relief="flat",
                        buttonbackground=COLOURS["panel2"], insertbackground=COLOURS["text"],
                        font=("Segoe UI", 10), command=rebuild)
        sp.pack(side="left", padx=8)
        sp.bind("<KeyRelease>", lambda e: rebuild())
        state["grid"] = tk.Frame(box, bg=COLOURS["panel"])
        state["grid"].pack(fill="x", padx=12, pady=(0, 12))
        rebuild()

    def rebuild() -> None:
        g = state.get("grid")
        if g is None:
            return
        for c in g.winfo_children():
            c.destroy()
        try:
            n = int(state["n"].get())
        except Exception:
            n = 3
        n = max(1, min(12, n))
        state["count"] = n
        for i in range(1, n + 1):
            r = tk.Frame(g, bg=COLOURS["panel"])
            r.pack(fill="x", pady=2)
            st = state["results"].get(i)
            colour = {"auto": COLOURS["good"], "manual": COLOURS["okay"],
                      "dead": COLOURS["bad"], "skip": COLOURS["skip"]}.get(st, COLOURS["muted"])
            text = {"auto": "WORKING (device detected automatically)",
                    "manual": "WORKING (confirmed manually)",
                    "dead": "DEAD / LOOSE",
                    "skip": "not tested"}.get(st, "not tested")
            tk.Label(r, text="Port %d" % i, width=8, bg=COLOURS["panel"], fg=COLOURS["text"],
                     font=("Segoe UI", 10, "bold"), anchor="w").pack(side="left")
            tk.Label(r, text=text, bg=COLOURS["panel"], fg=colour, width=38,
                     font=("Segoe UI", 9, "bold"), anchor="w").pack(side="left")
            tk.Button(r, text="Test this port", bg=COLOURS["accent"], fg="white", relief="flat",
                      font=("Segoe UI", 8, "bold"), padx=8, cursor="hand2",
                      command=lambda i=i: test_port(i)).pack(side="left", padx=3)
            tk.Button(r, text="Dead", bg="#5b2320", fg="#ffb4ad", relief="flat",
                      font=("Segoe UI", 8), padx=8, cursor="hand2",
                      command=lambda i=i: mark(i, "dead")).pack(side="left", padx=3)
            tk.Button(r, text="Works (manual)", bg="#3a3320", fg="#fcd34d", relief="flat",
                      font=("Segoe UI", 8), padx=8, cursor="hand2",
                      command=lambda i=i: mark(i, "manual")).pack(side="left", padx=3)
            tk.Button(r, text="Not fitted", bg="#252a33", fg="#9fb0c8", relief="flat",
                      font=("Segoe UI", 8), padx=8, cursor="hand2",
                      command=lambda i=i: mark(i, "skip")).pack(side="left", padx=3)

    def mark(i: int, value: str) -> None:
        state["results"][i] = value
        rebuild()
        beep(660, 90)
        if emit:
            emit("USB port %d marked %s" % (i, value))

    def test_port(i: int) -> None:
        if not IS_WINDOWS:
            mark(i, "manual")
            return
        if state.get("running"):
            return
        state["running"] = True
        d = state.get("dlg")
        if d:
            d.say("Port %d: plug a USB pen drive in NOW. Watching Windows for a new device "
                  "(90 second limit)..." % i, COLOURS["accent"])
        if emit:
            emit("USB port %d test started - waiting for a device to appear" % i)
        changed, detail = wait_for_change(master, "usb",
                                          "Waiting for the USB device on port %d..." % i,
                                          timeout=90,
                                          on_event=lambda m: d.say(m, COLOURS["accent"]) if d else None)
        state["running"] = False
        if changed:
            mark(i, "auto")
            if d:
                d.say("Port %d WORKS - Windows detected the device automatically." % i, COLOURS["good"])
            beep(880, 140)
        else:
            if d:
                d.say("Port %d: nothing appeared. Try the drive again, or mark it Dead / Works "
                      "(manual) if you are sure." % i, "#fcd34d")
            beep(220, 260)

    dlg = GuidedDialog(master, "USB port test", "Every socket tested by plugging a device in",
                       ["Use ONE known-good USB pen drive (or a mouse with a USB receiver).",
                        "Press 'Test this port', then plug the drive into THAT port within 90 seconds.",
                        "The tool watches Windows for a new removable device, so a working port ticks "
                        "itself. A dead or loose port will show nothing.",
                        "! Test EVERY port, including USB-C (use an adapter or a USB-C drive).",
                        "Wiggle the plug gently - a socket that only works at an angle is a repair job."],
                       marks=[("good", "ALL PORTS WORK", COLOURS["good"]),
                              ("weak", "ONE OR MORE FAULTY", COLOURS["okay"]),
                              ("bad", "PORTS LARGELY DEAD", COLOURS["bad"]),
                              ("skip", "COULD NOT TEST", COLOURS["skip"])],
                       body_builder=build, width=880)
    state["dlg"] = dlg
    out = dlg.run()

    n = state.get("count") or 0
    results = state["results"]
    auto = sum(1 for v in results.values() if v == "auto")
    manual = sum(1 for v in results.values() if v == "manual")
    dead = sum(1 for v in results.values() if v == "dead")
    untested = n - len([v for v in results.values() if v != "skip"])
    res.metric("Ports declared by technician", n)
    res.metric("Verified automatically (device appeared)", auto)
    res.metric("Confirmed manually", manual)
    res.metric("Dead / loose", dead)
    res.metric("Not tested", max(0, untested))
    for i in sorted(results):
        res.metric("  Port %d" % i, results[i])
    res.metric("USB host controllers seen", ports_info.get("usb_controllers"))
    res.metric("USB 3.x present", "Yes" if ports_info.get("usb3") else "No")

    if auto == 0 and manual == 0 and dead == 0:
        res.skipped("No USB port was actually tested")
        return res
    if dead:
        res.flag("%d USB port(s) are dead or loose" % dead, "fail" if dead >= 2 else "warn")
    if manual and not auto:
        res.flag("No USB port could be verified automatically - all marks are manual "
                 "(check the drive and try again)", "warn")
    if untested > 0:
        res.flag("%d declared USB port(s) were never tested" % untested, "warn")
    if out.get("cancelled"):
        res.skipped("USB port test was cancelled")
        return res
    mark_key = out.get("mark")
    res.marks = [{"id": "usb_ports", "name": "USB ports", "mark": mark_key, "note": out.get("note", "")}]
    if out.get("note"):
        res.note("Technician note: " + out["note"])
    score = SCORE_FOR.get(mark_key, 60.0) or 60.0
    # blend with what we actually observed
    observed = 100.0 * (auto + manual) / max(1, n)
    if dead:
        observed -= 25 * dead
    score = 0.5 * score + 0.5 * max(0.0, observed)
    res.set("pass" if score >= 88 else ("warn" if score >= 55 else "fail"),
            "%d/%d USB port(s) verified working, %d dead" % (auto + manual, n, dead), score=score)
    return res


# ==========================================================================
# HDMI / AUDIO JACK / CARD READER
# ==========================================================================

def run_web_ports_test(master: tk.Misc, res: CheckResult, ctx: Dict[str, Any],
                       emit: Optional[Callable[[str], None]] = None) -> CheckResult:
    ports_info = ctx.get("ports") or {}
    state: Dict[str, Any] = {"results": {}, "running": None}

    def watch(kind: str, key: str, prompt: str, seconds: float = 75.0) -> None:
        if state.get("running"):
            return
        state["running"] = key
        d = state.get("dlg")
        if d:
            d.say(prompt, COLOURS["accent"])
        changed, detail = wait_for_change(master, kind, prompt, timeout=seconds,
                                          on_event=lambda m: d.say(m, COLOURS["accent"]) if d else None)
        state["running"] = None
        state["results"][key] = "auto" if changed else "timeout"
        if d:
            d.say("%s -> %s" % (key.upper(), "DETECTED automatically" if changed
                                else "no change detected; mark manually"),
                  COLOURS["good"] if changed else "#fcd34d")
        beep(880 if changed else 220, 160)
        refresh()

    def refresh() -> None:
        for key, lab in state.get("labels", {}).items():
            v = state["results"].get(key)
            txt = {"auto": "DETECTED automatically", "timeout": "no change seen",
                   "yes": "WORKS (confirmed)", "no": "FAULTY", "na": "NOT FITTED"}.get(v, "not tested")
            colour = {"auto": COLOURS["good"], "yes": COLOURS["good"], "timeout": COLOURS["okay"],
                      "no": COLOURS["bad"], "na": COLOURS["skip"]}.get(v, COLOURS["muted"])
            lab.configure(text=txt, fg=colour)

    def mark(key: str, value: str) -> None:
        state["results"][key] = value
        refresh()

    def build(dlg: GuidedDialog, parent: tk.Frame) -> None:
        state["labels"] = {}
        rows = [
            ("hdmi", "HDMI / DisplayPort video output",
             "Plug in an external monitor or TV. The tool watches the display count and will tick "
             "automatically. Check the picture is stable - flicker means a worn socket.",
             lambda: watch("display", "hdmi", "Waiting for an external display on HDMI...")),
            ("jack", "3.5 mm headphone / headset jack",
             "Plug in headphones. The tool watches for a new audio endpoint. Sound must move to the "
             "headphones and stop on the speakers.",
             lambda: watch("audio", "jack", "Waiting for the headphone endpoint to appear...")),
            ("sd", "SD / microSD card reader",
             "Insert a card. The tool watches for a new removable volume. Mark NOT FITTED if this "
             "model has no reader.",
             lambda: watch("usb", "sd", "Waiting for the card to appear...")),
        ]
        for key, label, hint, action in rows:
            box = tk.Frame(parent, bg=COLOURS["panel"], highlightthickness=1,
                           highlightbackground=COLOURS["line"])
            box.pack(fill="x", pady=4)
            tk.Label(box, text=label, bg=COLOURS["panel"], fg=COLOURS["text"],
                     font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", padx=12, pady=(8, 0))
            tk.Label(box, text=hint, bg=COLOURS["panel"], fg=COLOURS["muted"],
                     font=("Segoe UI", 9), anchor="w", justify="left",
                     wraplength=800).pack(fill="x", padx=12)
            lab = tk.Label(box, text="not tested", bg=COLOURS["panel"], fg=COLOURS["muted"],
                           font=("Segoe UI", 10, "bold"), anchor="w")
            lab.pack(fill="x", padx=12, pady=(4, 2))
            state["labels"][key] = lab
            r = tk.Frame(box, bg=COLOURS["panel"])
            r.pack(fill="x", padx=12, pady=(0, 10))
            tk.Button(r, text="Auto-detect now", bg=COLOURS["accent"], fg="white", relief="flat",
                      font=("Segoe UI", 9, "bold"), padx=10, cursor="hand2",
                      command=action).pack(side="left", padx=(0, 4))
            for text, value, colour in (("Works", "yes", COLOURS["good"]),
                                        ("Faulty", "no", COLOURS["bad"]),
                                        ("Not fitted", "na", COLOURS["skip"])):
                tk.Button(r, text=text, bg=COLOURS["panel2"], fg=colour, relief="flat",
                          font=("Segoe UI", 9, "bold"), padx=10, cursor="hand2",
                          command=lambda k=key, v=value: mark(k, v)).pack(side="left", padx=3)

    dlg = GuidedDialog(master, "HDMI, headphone jack and card reader",
                       "Auto-detected where possible, confirmed by you",
                       ["Have ready: an HDMI monitor or TV, a pair of headphones, and an SD card.",
                        "Press 'Auto-detect now' for each item and plug the device in within 75 seconds.",
                        "If auto-detect sees nothing but the port works (some docks and adapters do not "
                        "report), press 'Works' yourself - the report records that it was manual.",
                        "! Mark 'Not fitted' if the machine genuinely does not have the port."],
                       marks=[("good", "ALL PRESENT PORTS WORK", COLOURS["good"]),
                              ("weak", "SOME FAULTY", COLOURS["okay"]),
                              ("bad", "MAJOR PORT FAILURE", COLOURS["bad"]),
                              ("skip", "COULD NOT TEST", COLOURS["skip"])],
                       body_builder=build, width=880)
    state["dlg"] = dlg
    out = dlg.run()

    results = state["results"]
    labels = {"hdmi": "HDMI / DisplayPort", "jack": "Headphone jack", "sd": "Card reader"}
    tested = 0
    faulty = 0
    for key, lab in labels.items():
        v = results.get(key)
        res.metric(lab, {"auto": "WORKS - detected automatically", "timeout": "no change detected",
                         "yes": "WORKS - confirmed manually", "no": "FAULTY",
                         "na": "Not fitted", None: "not tested"}.get(v, v or "not tested"))
        if v in ("auto", "yes"):
            tested += 1
        if v == "no":
            faulty += 1
            res.flag("%s is faulty" % lab, "fail")
        if v is None:
            res.flag("%s was never tested" % lab, "warn")
        if v == "timeout":
            res.flag("%s: nothing was detected automatically (marked by hand or untested)" % lab, "warn")
    res.metric("Card reader present (from hardware scan)", "Yes" if ports_info.get("card_reader") else "No")
    if out.get("cancelled"):
        res.skipped("External port test cancelled")
        return res
    mk = out.get("mark")
    res.marks = [{"id": "web_ports", "name": "HDMI / jack / card reader", "mark": mk,
                  "note": out.get("note", "")}]
    if out.get("note"):
        res.note("Technician note: " + out["note"])
    score = SCORE_FOR.get(mk, 60.0) or 60.0
    if faulty:
        score = min(score, 40.0)
    res.set("pass" if score >= 88 else ("warn" if score >= 55 else "fail"),
            "%d of 3 external port groups verified, %d faulty" % (tested, faulty), score=score)
    return res


# ==========================================================================
# COSMETIC
# ==========================================================================

COSMETIC_ITEMS: List[Dict[str, Any]] = [
    {"id": "lid", "label": "Lid / top cover", "options": OPT_OK, "note": True,
     "hint": "Cracks, dents, deep scratches, bent corners, stickers that hide damage."},
    {"id": "bezel", "label": "Screen bezel and hinges area", "options": OPT_OK, "note": True,
     "hint": "Cracked bezel, missing rubber bumpers, cracks near the hinge mounts."},
    {"id": "hinge", "label": "HINGE - open and close 5 times", "options": OPT_OK, "note": True,
     "hint": "! Must hold any angle without sagging, no cracking sound, no case flexing or "
             "splitting at the hinge. A broken hinge is an expensive repair."},
    {"id": "base", "label": "Bottom case", "options": OPT_OK, "note": True,
     "hint": "Cracks, missing screws, missing rubber feet, warped plastic, scratched-through paint."},
    {"id": "palmrest", "label": "Palm rest and touchpad area", "options": OPT_OK, "note": True,
     "hint": "Worn/shiny plastic, cracks, swelling (a swollen battery lifts this area!)."},
    {"id": "keys", "label": "Keycaps - wear and shine", "options": OPT_OK, "note": True,
     "hint": "Letters worn off means heavy use. Sticky or loose keys."},
    {"id": "ports_cos", "label": "Port openings - damage", "options": OPT_OK, "note": True,
     "hint": "Bent/torn USB tongues, a pushed-in HDMI socket, corroded contacts (green/white powder)."},
    {"id": "screws", "label": "Screws all present and undamaged", "options": OPT_OK, "note": True,
     "hint": "Stripped screw heads mean it has been opened badly. Missing screws mean loose internals."},
    {"id": "liquid", "label": "NO signs of liquid damage", "options": OPT_OK, "note": True,
     "hint": "! Check under the keyboard if possible: corrosion, white/green residue, sticky keys, "
             "a sweet smell, or a tripped liquid-damage indicator. Liquid damage = do not buy."},
    {"id": "smell", "label": "No burning / chemical smell", "options": OPT_OK, "note": True,
     "hint": "Smell the vents. A burnt smell means a failed fan, board or charger."},
    {"id": "screen_cos", "label": "Screen glass/panel surface", "options": OPT_OK, "note": True,
     "hint": "Scratches, pressure marks, coating peel, cracks (a crack that still lights up is still "
             "a panel replacement)."},
    {"id": "charger", "label": "Charger / adapter supplied", "options": OPT_OK, "note": True,
     "hint": "Original or compatible? Check the cable for cuts and the brick for heat damage."},
    {"id": "battery_phys", "label": "Battery not swollen", "options": OPT_OK, "note": True,
     "hint": "! A swollen battery is a FIRE RISK. Look for a lifted palm rest, a wobbling chassis "
             "on a flat table, or a bulging bottom case."},
    {"id": "box", "label": "Box / bill / accessories", "options": OPT_OK, "note": True,
     "hint": "Original box, invoice and warranty papers raise the resale price."},
]


def run_cosmetic_test(master: tk.Misc, res: CheckResult, ctx: Dict[str, Any],
                      emit: Optional[Callable[[str], None]] = None) -> CheckResult:
    state: Dict[str, Any] = {"photos": []}

    def build(dlg: GuidedDialog, parent: tk.Frame) -> None:
        row = tk.Frame(parent, bg=COLOURS["bg"])
        row.pack(fill="x", pady=(0, 6))
        tk.Button(row, text="Attach a damage photo...", bg=COLOURS["panel2"], fg=COLOURS["text"],
                  relief="flat", font=("Segoe UI", 9, "bold"), padx=10, pady=5, cursor="hand2",
                  command=attach).pack(side="left")
        state["plabel"] = tk.Label(row, text="no photo attached", bg=COLOURS["bg"],
                                   fg=COLOURS["muted"], font=("Segoe UI", 9))
        state["plabel"].pack(side="left", padx=8)

    def attach() -> None:
        paths = filedialog.askopenfilenames(
            title="Attach photos of the damage (you can select several)",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
        if not paths:
            return
        scan = ctx.get("scan_id", "scan")
        saved = []
        for i, p in enumerate(paths[:6], 1):
            dest = os.path.join(PHOTOS_DIR, "%s_cosmetic_%d%s" % (
                scan, i, os.path.splitext(p)[1].lower() or ".png"))
            try:
                shutil.copyfile(p, dest)
                saved.append(dest)
            except Exception:
                pass
        state["photos"].extend(saved)
        res.photos.extend(saved)
        if state.get("plabel") is not None:
            state["plabel"].configure(text="%d photo(s) attached" % len(state["photos"]),
                                      fg=COLOURS["good"])

    items = []
    for it in COSMETIC_ITEMS:
        d = dict(it)
        items.append(d)

    out = checklist_dialog(master, "Cosmetic and physical condition",
                           "Hands-on inspection - this cannot be automated", items, width=900)
    if out.get("cancelled"):
        res.skipped("Cosmetic inspection was cancelled")
        return res
    answers = out.get("answers", {})
    scores: List[float] = []
    grade_points = 0
    for it in items:
        key = answers.get(it["id"])
        note = answers.get(it["id"] + "_note", "")
        label = {o[0]: o[1] for o in it["options"]}.get(key, "not answered")
        res.metric(it["label"][:50], label + ((" - " + note) if note else ""))
        if key is None:
            res.flag("Cosmetic item '%s' was never answered" % it["label"], "warn")
            continue
        sc = SCORE_FOR.get(key)
        if sc is not None:
            scores.append(sc)
            grade_points += {"yes": 4, "weak": 2, "no": 0}.get(key, 0)
        if key == "no":
            level = "fail" if it["id"] in ("liquid", "battery_phys", "hinge") else "warn"
            res.flag("Cosmetic fault: %s%s" % (it["label"], (" - " + note) if note else ""), level)
        elif key == "weak":
            res.flag("Cosmetic issue: %s%s" % (it["label"], (" - " + note) if note else ""), "warn")

    # cosmetic grade A-D
    maxpts = 4 * len([s for s in scores])
    ratio = (grade_points / maxpts) if maxpts else 0
    cos_grade = "A" if ratio >= 0.92 else ("B" if ratio >= 0.75 else ("C" if ratio >= 0.5 else "D"))
    res.metric("Cosmetic grade", cos_grade + " (%.0f%% of items clean)" % (ratio * 100))
    res.metric("Damage photos attached", len(state["photos"]))
    ctx["cosmetic_grade"] = cos_grade
    if out.get("note"):
        res.note("Technician note: " + out["note"])
    res.marks = [{"id": it["id"], "name": it["label"], "mark": answers.get(it["id"]),
                  "note": answers.get(it["id"] + "_note", "")} for it in items]
    if not scores:
        res.skipped("Cosmetic inspection not completed")
        return res
    score = sum(scores) / len(scores)
    res.set("pass" if score >= 88 else ("warn" if score >= 55 else "fail"),
            "Cosmetic grade %s (%.0f/100), %d photo(s)" % (cos_grade, score, len(state["photos"])),
            score=score)
    return res


# ==========================================================================
# SIGN-OFF
# ==========================================================================

def run_signoff(master: tk.Misc, res: CheckResult, ctx: Dict[str, Any],
                pending: List[str], all_flags: List[Tuple[str, str]],
                emit: Optional[Callable[[str], None]] = None) -> CheckResult:
    state: Dict[str, Any] = {"name": tk.StringVar(value=ctx.get("technician", ""))}

    def build(dlg: GuidedDialog, parent: tk.Frame) -> None:
        box = tk.Frame(parent, bg=COLOURS["panel"], highlightthickness=1,
                       highlightbackground=COLOURS["line"])
        box.pack(fill="both", expand=True, pady=(0, 8))
        r = tk.Frame(box, bg=COLOURS["panel"])
        r.pack(fill="x", padx=12, pady=10)
        tk.Label(r, text="Technician name:", bg=COLOURS["panel"], fg=COLOURS["text"],
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        e = tk.Entry(r, textvariable=state["name"], bg=COLOURS["panel2"], fg=COLOURS["text"],
                     relief="flat", insertbackground=COLOURS["text"], font=("Segoe UI", 11), width=34)
        e.pack(side="left", padx=8, ipady=4)
        e.focus_set()

        tk.Label(box, text="Tests still NOT completed:", bg=COLOURS["panel"],
                 fg=COLOURS["bad"] if pending else COLOURS["good"],
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", padx=12)
        txt = tk.Text(box, height=min(7, max(2, len(pending) or 1)), bg="#0d1016",
                      fg="#fcd34d" if pending else "#7ee2a8", relief="flat",
                      font=("Consolas", 9), wrap="word")
        txt.pack(fill="both", expand=True, padx=12, pady=(2, 6))
        txt.insert("1.0", "\n".join("  - " + p for p in pending) if pending
                   else "  All required tests are complete.")
        txt.configure(state="disabled")

        tk.Label(box, text="Problems found that affect the price (%d):" % len(all_flags),
                 bg=COLOURS["panel"], fg=COLOURS["text"], font=("Segoe UI", 10, "bold"),
                 anchor="w").pack(fill="x", padx=12)
        f = tk.Text(box, height=min(9, max(2, len(all_flags))), bg="#0d1016", fg="#ffc9c2",
                    relief="flat", font=("Consolas", 9), wrap="word")
        f.pack(fill="both", expand=True, padx=12, pady=(2, 10))
        f.insert("1.0", "\n".join("  [%s] %s" % (lvl.upper(), text) for text, lvl in all_flags)
                 if all_flags else "  No problems were found.")
        f.configure(state="disabled")
        state["pending_widget"] = txt

    dlg = GuidedDialog(master, "Technician sign-off",
                       "The last step before the report is generated",
                       ["Read the outstanding tests and the problem list above.",
                        "Type the technician name - it is printed on the report and the customer sees it.",
                        "! Signing off means: the machine was physically inspected, the data was handled "
                        "responsibly, and the report matches what you saw.",
                        "Mark GOOD to sign off, or BAD if the inspection is incomplete."],
                       marks=[("good", "SIGN OFF - INSPECTION COMPLETE", COLOURS["good"]),
                              ("bad", "NOT COMPLETE - report will be marked DRAFT", COLOURS["bad"]),
                              ("skip", "Skip", COLOURS["skip"])],
                       body_builder=build, width=860)
    out = dlg.run()

    name = (state["name"].get() or "").strip()
    ctx["technician"] = name
    res.metric("Technician", name or "(not given)")
    res.metric("Tests left incomplete", len(pending))
    res.metric("Problem flags at sign-off", len(all_flags))
    for p in pending:
        res.metric("  Incomplete", p)
    if not name:
        res.flag("No technician name was recorded - the report is unsigned", "warn")
    if pending:
        res.flag("%d required test(s) were never completed: %s" % (len(pending), ", ".join(pending[:6])),
                 "warn")
    if out.get("cancelled") or out.get("mark") != "good":
        res.warn("Inspection was NOT signed off as complete - the report is marked DRAFT", score=30.0)
        res.flag("Report is a DRAFT: the technician did not sign off", "fail")
        ctx["signed_off"] = False
    else:
        res.ok("Signed off by %s" % (name or "technician"), score=100.0)
        ctx["signed_off"] = True
    res.marks = [{"id": "signoff", "name": "Sign-off", "mark": out.get("mark"),
                  "note": out.get("note", "")}]
    if out.get("note"):
        res.note("Technician note: " + out["note"])
    return res
