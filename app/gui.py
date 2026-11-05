"""
gui.py -- the shop-facing interface: ONE full-screen guided flow.

Designed for a technician standing at the counter, not for a programmer:

  WELCOME   one big button: START INSPECTION
  AUTO      the 20 automatic checks run themselves. The screen shows, in huge
            type: what is being checked right now, then its result in plain
            words (GOOD / NEEDS ATTENTION / PROBLEM / NOT FITTED), then the
            next one. Nothing to click, nothing blank, a live log at the
            bottom for the curious.
  HANDS-ON  the 12 human tests open themselves one after another, each with a
            "what you are about to do" card first. One pass through the
            colour screens fills all three display line items.
  FINISH    grade + resale price in huge type, reports written automatically,
            PDFs printed automatically through the browser already installed
            on the machine, and buttons to open them.

Everything else (shop branding, history, settings) hangs off small buttons on
the welcome and finish screens. The whole window is borderless full-screen;
F11 toggles windowed mode, F2 toggles the technician log drawer.
"""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from app.core import (APP_NAME, APP_VERSION, AUTO_IDS, CHECKS, CHECK_INDEX,
                      DATA_DIR, GUIDED_IDS, IS_WINDOWS, PHOTOS_DIR,
                      REPORTS_DIR, Bus, is_admin, load_json, prevent_sleep,
                      relaunch_as_admin, screen_size, set_dpi_aware)
from app.branding import Branding, Settings
from app.grading import grade_all
from app.scan import Scan, list_history
from app.valuation import estimate, load_model
from app import debug
from app import uikit
from app import pdf as pdf_mod
from app import report as report_mod

C = {
    "bg": "#0b0f16", "card": "#141b26", "card2": "#1b2432", "line": "#26303f",
    "text": "#e8edf5", "muted": "#8d99ac", "accent": "#3b82f6",
    "good": "#22c55e", "warn": "#f59e0b", "bad": "#ef4444",
    "na": "#64748b", "skip": "#a78bfa",
}
PLAIN = {
    "pass": ("GOOD", C["good"]),
    "warn": ("NEEDS ATTENTION", C["warn"]),
    "fail": ("PROBLEM", C["bad"]),
    "na": ("NOT FITTED", C["na"]),
    "skipped": ("NOT TESTED", C["skip"]),
    "missing": ("NOT TESTED", C["skip"]),
    "unknown": ("WORKING...", C["muted"]),
}
GUIDED_INFO = {
    "screen_colours": ("Display / screen quality",
                       "The screen fills with pure colours one by one. Look at the real "
                       "panel and press 1 = good, 2 = okay, 3 = bad. Space = next."),
    "screen_pixels": ("Dead pixels & backlight bleed", "Covered by the display test above."),
    "screen_text": ("Text sharpness", "Covered by the display test above."),
    "speakers": ("Speakers", "The laptop plays four test tones. You simply listen and "
                             "answer what you heard. No other program is involved."),
    "microphone": ("Microphone", "Speak for six seconds while the live meter shows what "
                                 "the microphone actually receives."),
    "camera_capture": ("Camera", "A live preview opens. Check the picture is clear and "
                                 "keep one photo for the report."),
    "keyboard": ("Keyboard", "Press every key shown on the screen map. Only the built-in "
                             "keyboard counts - external keyboards are refused."),
    "touch": ("Touch screen", "Nine circles appear across the panel. Tap every one with a "
                              "finger - the tool measures which areas of the digitiser answer."),
    "touchpad": ("Touchpad", "Move, click, scroll and tap as instructed."),
    "usb_ports": ("USB ports", "Plug a pen drive into each USB port. The tool detects it "
                               "by itself - no trusting needed."),
    "web_ports": ("HDMI, headphone jack, card reader", "Plug something into each port; "
                                                       "the tool watches for it."),
    "cosmetic": ("Body, hinges, safety", "A 14-point hands-on look: cracks, hinge play, "
                                         "liquid signs, swollen battery. Photos optional."),
    "final_check": ("Sign-off", "Your name on the inspection, and a last confirmation."),
}


def _f(size: int, bold: bool = True) -> tuple:
    return ("Segoe UI", size, "bold") if bold else ("Segoe UI", size)


class Wizard:
    def __init__(self, root: tk.Tk, demo: bool = False) -> None:
        self.root = root
        self.bus = Bus()
        self.settings = Settings()
        self.branding = Branding()
        self.scan = Scan()
        self.scanning = False
        self.demo = demo
        self.log_lines: List[str] = []
        self._fx: Any = __import__("collections").deque()
        self._fx_at = 0.0
        self._fx_fin = False
        self._guided_index = 0
        self._jobs: List[str] = []
        self.pdf_paths: Dict[str, str] = {}
        self._build_shell()
        self._poll()
        if demo:
            self.root.after(200, self._load_demo)
        else:
            self.show_welcome()

    # ================================================================ shell
    def _build_shell(self) -> None:
        self.root.title("%s %s" % (APP_NAME, APP_VERSION))
        self.root.configure(bg=C["bg"])
        # Full screen WITHOUT trusting the window manager: borderless plus an
        # explicit geometry of exactly the screen size. Window-manager flags
        # like -fullscreen silently do nothing on some drivers/VMs, which is
        # how the old build ended up in a corner window.
        self._fs = True
        self._apply_fs()
        self.root.bind("<F11>", lambda e: self._toggle_fullscreen())
        self.root.bind("<F2>", lambda e: self._toggle_drawer())
        self.stage = tk.Frame(self.root, bg=C["bg"])
        self.stage.pack(fill="both", expand=True)
        # technician drawer (hidden)
        self.drawer = tk.Frame(self.root, bg="#070a0f")
        self.drawer_text = tk.Text(self.drawer, bg="#070a0f", fg="#9fb0c6", relief="flat",
                                   font=("Consolas", 9), wrap="word", padx=10, pady=8)
        self.drawer_text.pack(fill="both", expand=True)

    def _apply_fs(self) -> None:
        try:
            self.root.overrideredirect(self._fs)
            self.root.update_idletasks()
            if self._fs:
                sw, sh = screen_size()
                if not sw:
                    sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                self.root.geometry("%dx%d+0+0" % (sw, sh))
                try:
                    self.root.attributes("-topmost", True)
                except Exception:
                    pass
            else:
                w, h = 1280, 820
                self.root.geometry("%dx%d+%d+%d" % (
                    w, h, max(0, (self.root.winfo_screenwidth() - w) // 2),
                    max(0, (self.root.winfo_screenheight() - h) // 2)))
            self.root.update()
        except Exception:
            pass

    def _toggle_fullscreen(self) -> None:
        self._fs = not self._fs
        self._apply_fs()

    def _toggle_drawer(self) -> None:
        if self.drawer.winfo_ismapped():
            self.drawer.pack_forget()
        else:
            self.drawer_text.configure(state="normal")
            self.drawer_text.delete("1.0", "end")
            self.drawer_text.configure(state="normal")
            self.drawer_text.delete("1.0", "end")
            self.drawer_text.tag_configure("err", foreground="#ff8f86")
            self.drawer_text.tag_configure("warn", foreground="#ffd479")
            self.drawer_text.tag_configure("ok", foreground="#7ce8a5")
            for ln in self.log_lines[-200:]:
                u = ln.upper()
                tag = ""
                if any(k in u for k in ("ERROR", "FAILED", "CRASH", "EXCEPTION", "VIOLATION")):
                    tag = "err"
                elif any(k in ln for k in ("Auto-fix", "WARN", "fix:")):
                    tag = "warn"
                elif any(k in u for k in ("READY", "WROTE", "OK", "PASS")):
                    tag = "ok"
                self.drawer_text.insert("end", ln + "\n", tag)
            self.drawer_text.see("end")
            self.drawer_text.configure(state="disabled")
            self.drawer.pack(fill="both", expand=True)

    def _log(self, text: str) -> None:
        debug.log(text)
        line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
        self.log_lines.append(line)
        if len(self.log_lines) > 900:
            del self.log_lines[:300]
        try:
            if self.drawer.winfo_ismapped():
                self.drawer_text.configure(state="normal")
                self.drawer_text.insert("end", line + "\n")
                self.drawer_text.see("end")
                self.drawer_text.configure(state="disabled")
        except Exception:
            pass

    def clear(self) -> None:
        for j in self._jobs:
            try:
                self.root.after_cancel(j)
            except Exception:
                pass
        self._jobs = []
        for w in self.stage.winfo_children():
            w.destroy()

    def later(self, ms: int, fn: Callable[[], Any]) -> None:
        self._jobs.append(self.root.after(ms, fn))

    def button(self, parent: tk.Widget, text: str, cmd: Callable[[], Any],
               colour: str = C["accent"], fg: str = "white", size: int = 13,
               padx: int = 26, pady: int = 12) -> tk.Button:
        b = tk.Button(parent, text=text, bg=colour, fg=fg, relief="flat", font=_f(size),
                      padx=padx, pady=pady, cursor="hand2", activebackground=colour,
                      activeforeground=fg, command=cmd)
        return b

    # ============================================================== welcome
    def show_welcome(self) -> None:
        self.clear()
        s = self.stage
        mid = tk.Frame(s, bg=C["bg"])
        mid.pack(expand=True)
        tk.Label(mid, text=APP_NAME.upper(), bg=C["bg"], fg=C["text"],
                 font=_f(52)).pack()
        shop = self.branding.get("shop_name") or ""
        tk.Label(mid, text=(shop + "  |  " if shop else "") + "used-laptop inspection & pricing",
                 bg=C["bg"], fg=C["muted"], font=_f(16, False)).pack(pady=(2, 26))
        ident = self.scan.ctx.get("identity") or {}
        if ident.get("model"):
            tk.Label(mid, text="This machine:  %s %s   (S/N %s)"
                     % (ident.get("manufacturer", ""), ident.get("model", ""),
                        ident.get("serial", "?")),
                     bg=C["bg"], fg=C["accent"], font=_f(15, False)).pack(pady=(0, 18))
        big = self.button(mid, "\u25b6   START INSPECTION", self.begin, C["good"], "#06230f",
                          size=26, padx=60, pady=26)
        big.pack(pady=(0, 10))
        ip = self._load_inprogress()
        if ip:
            done_g = int(ip.get("guided_index") or 0)
            self.button(mid, "\u21bb   RESUME LAST INSPECTION  (hands-on %d/%d done - the machine "
                               "died mid-run)" % (done_g, len(GUIDED_IDS)),
                        self._resume, C["accent"], "white", 15, 30, 12).pack(pady=(0, 10))
        tk.Label(mid, text="About 5 minutes.  The tool does the hardware checks itself,\n"
                           "then walks you through the hands-on tests one by one,\n"
                           "then writes the report and the PDF automatically.",
                 bg=C["bg"], fg=C["muted"], font=_f(13, False), justify="center").pack()
        row = tk.Frame(s, bg=C["bg"])
        row.pack(side="bottom", pady=22)
        for text, cmd, col in (("Shop & report settings", self.settings_dialog, C["card2"]),
                               ("Past inspections", self.history_dialog, C["card2"]),
                               ("Create debug file for support", self.make_bundle, C["card2"]),
                               ("Try with demo data", self._load_demo, C["card2"]),
                               ("Exit", self._exit, C["card2"])):
            self.button(row, text, cmd, col, C["text"], 11, 16, 8).pack(side="left", padx=6)
        if IS_WINDOWS and not is_admin():
            tk.Label(s, text="Not running as Administrator - some hardware data will be missing. "
                             "Right-click the exe and choose Run as administrator for full detail.",
                     bg=C["bg"], fg=C["warn"], font=_f(11, False)).pack(side="bottom", pady=(0, 6))
        tk.Label(s, text="v%s   |   F11 window / full screen   |   F2 technician log   |   "
                         "nothing ever leaves this computer" % APP_VERSION,
                 bg=C["bg"], fg="#4d5a6d", font=_f(10, False)).pack(side="bottom", pady=(0, 8))

    # ------------------------------------------------------- crash-resume
    def _persist(self) -> None:
        """Keep an on-disk copy of the running inspection so a dead laptop
        (shop power cut, battery pull, BSOD) never costs a full re-run."""
        try:
            from app.core import write_json
            write_json(os.path.join(DATA_DIR, "inprogress.json"),
                       {"scan": self.scan.to_dict(), "guided_index": self._guided_index,
                        "saved_at": time.time()})
        except Exception:
            pass

    @staticmethod
    def _load_inprogress() -> Optional[Dict[str, Any]]:
        try:
            from app.core import load_json
            d = load_json(os.path.join(DATA_DIR, "inprogress.json")) or {}
            if not d.get("scan") or time.time() - (d.get("saved_at") or 0) > 7 * 86400:
                return None
            scan = d["scan"]
            done = [c for c in AUTO_IDS if c in (scan.get("results") or {})]
            if len(done) < len(AUTO_IDS):
                return None          # died during the automatic phase: start fresh
            return d
        except Exception:
            return None

    def _resume(self) -> None:
        d = self._load_inprogress()
        if not d:
            return
        try:
            from app.scan import Scan
            self.scan = Scan.from_dict(d["scan"])
            self._guided_index = int(d.get("guided_index") or 0)
            self.demo = False
            self._log("Resumed inspection %s at hands-on test %d"
                      % (self.scan.id, self._guided_index + 1))
            self.show_guided_card()
        except Exception as exc:
            self._log("resume failed, starting fresh: %s" % exc)

    def _exit(self) -> None:
        try:
            from guided.keyboard import stop_win_blocker
            stop_win_blocker()
        except Exception:
            pass
        self.root.destroy()

    def _load_demo(self) -> None:
        from app.demo import build_demo_scan
        self.scan = build_demo_scan()
        self.demo = True
        self._recompute()
        self._log("Demo scan loaded (simulated data).")
        self.show_finish()

    # ================================================================== auto
    def begin(self) -> None:
        if getattr(self, "_begin_busy", False):
            return
        self._begin_busy = True
        try:
            self._begin_inner()
        finally:
            self._begin_busy = False

    def _begin_inner(self) -> None:
        if not IS_WINDOWS:
            if messagebox.askyesno(APP_NAME, "This is not Windows, so no real hardware can be "
                                             "read.\nLoad the DEMO inspection instead?"):
                self._load_demo()
            return
        self.scan = Scan()
        self.demo = False
        self.bus.cancel.clear()
        self.show_auto()

    def show_auto(self) -> None:
        self.clear()
        s = self.stage
        top = tk.Frame(s, bg=C["bg"])
        top.pack(fill="x", padx=40, pady=(28, 0))
        row0 = tk.Frame(top, bg=C["bg"])
        row0.pack(fill="x")
        self.auto_title = tk.Label(row0, text="AUTOMATIC HARDWARE CHECKS", bg=C["bg"],
                                   fg=C["muted"], font=_f(15))
        self.auto_title.pack(side="left")
        self.auto_count = tk.Label(row0, text="0 / %d" % len(AUTO_IDS), bg=C["bg"],
                                   fg=C["text"], font=_f(15))
        self.auto_count.pack(side="right")
        self.auto_pct = tk.Label(row0, text="0%", bg=C["bg"], fg=C["accent"],
                                 font=_f(15))
        self.auto_pct.pack(side="right", padx=(0, 16))
        self.bar = tk.Frame(top, bg=C["card2"], height=10)
        self.bar.pack(fill="x", pady=(10, 0))
        self.bar_fill = tk.Frame(self.bar, bg=C["accent"], width=0, height=10)
        self.bar_fill.pack(side="left", fill="y")

        card = tk.Frame(s, bg=C["card"], highlightthickness=1, highlightbackground=C["line"])
        card.pack(fill="both", expand=True, padx=40, pady=24)
        self.now_label = tk.Label(card, text="Starting...", bg=C["card"], fg=C["muted"],
                                  font=_f(15))
        self.now_label.pack(pady=(46, 4))
        self.now_name = tk.Label(card, text="", bg=C["card"], fg=C["text"], font=_f(40),
                                 wraplength=1100, justify="center")
        self.now_name.pack(pady=(0, 10))
        self.now_icon_lab = tk.Label(card, text="", bg=C["card"])
        self.now_icon_lab.pack(pady=(6, 0))
        self.now_badge = tk.Label(card, text="", bg=C["card"], fg=C["muted"], font=_f(46))
        self.now_badge.pack(pady=(6, 6))
        self.now_summary = tk.Label(card, text="", bg=C["card"], fg=C["text"], font=_f(17, False),
                                    wraplength=1050, justify="center")
        self.now_summary.pack(pady=(0, 10))
        self.now_hint = tk.Label(card, text="", bg=C["card"], fg=C["muted"], font=_f(12, False),
                                 wraplength=1000, justify="center")
        self.now_hint.pack(pady=(0, 20))

        bottom = tk.Frame(s, bg=C["bg"])
        bottom.pack(fill="x", padx=40, pady=(0, 18))
        self.mini_log = tk.Label(bottom, text="", bg=C["bg"], fg="#5c6a7d", font=("Consolas", 9),
                                 anchor="sw", justify="left")
        self.mini_log.pack(side="left", fill="x", expand=True)
        self.button(bottom, "Skip the rest of the\nautomatic checks", self._skip_auto,
                    C["card2"], C["muted"], 11, 14, 8).pack(side="right", padx=4)
        self.button(bottom, "Stop and go\nto results", self._stop_to_results,
                    C["card2"], C["muted"], 11, 14, 8).pack(side="right", padx=4)

        self.scanning = True
        prevent_sleep(True)
        self.worker = threading.Thread(target=self._scan_worker, daemon=True)
        self.worker.start()

    def _scan_worker(self) -> None:
        import collectors
        ctx = collectors.build_ctx(bus=self.bus, settings=self.settings.as_dict(),
                                   scan_id=self.scan.id,
                                   emit=lambda m: self.bus.emit("log", text=m))
        self.scan.ctx = ctx
        ctx["signed_off"] = False
        collectors.full_scan(ctx, self.bus, results=self.scan.results)

    def _skip_auto(self) -> None:
        self.bus.request_cancel()
        self._log("User skipped the remaining automatic checks.")

    def _stop_to_results(self) -> None:
        self.bus.request_cancel()
        self.scanning = False
        prevent_sleep(False)
        self._begin_guided()

    # ============================================================ poll loop
    def _poll(self) -> None:
        for kind, p in self.bus.poll():
            if kind == "log":
                self._log(p.get("text", ""))
                mini = getattr(self, "mini_log", None)
                if mini is not None and mini.winfo_exists():
                    try:
                        tail = self.log_lines[-4:]
                        mini.configure(text="\n".join(t.split("  ", 1)[-1] for t in tail))
                    except Exception:
                        pass
                hint = getattr(self, "now_hint", None)
                if hint is not None and hint.winfo_exists():
                    try:
                        hint.configure(text=p.get("text", "")[:150])
                    except Exception:
                        pass
            elif kind in ("check_start", "check_done"):
                # Paced presentation: fast checks must not flash past unread.
                # Each result stays on screen at least HOLD seconds before the
                # next one replaces it, even if the worker is already ahead.
                self._fx.append((kind, p))
            elif kind == "scan_done":
                self.scanning = False
                prevent_sleep(False)
                self._log("Automatic checks finished.")
                self._fx_fin = True
            elif kind == "scan_failed":
                self.scanning = False
                prevent_sleep(False)
                self._log("SCAN FAILED: %s" % p.get("error"))
                bpath = debug.bundle(results=debug.results_dump(self.scan.results))
                messagebox.showerror(APP_NAME, "The automatic scan stopped early:\n%s\n\n"
                                               "A debug file with everything the tool saw has "
                                               "been written:\n%s\nSend it for support."
                                     % (p.get("error"), bpath or "LCP_Data\\diagnostics.txt"))
                self._begin_guided()
        self._pump()
        self.root.after(90, self._poll)

    def _pump(self) -> None:
        """Release queued auto-check events at a human-readable pace."""
        now = time.time()
        if self._fx:
            kind, p = self._fx[0]
            hold = 1.7 if kind == "check_done" else 0.6
            if now - self._fx_at >= hold:
                self._fx.popleft()
                self._fx_at = now
                self._render_fx(kind, p)
        elif getattr(self, "_fx_fin", False):
            self._fx_fin = False
            self.later(900, self._begin_guided)

    def _render_fx(self, kind: str, p: Dict[str, Any]) -> None:
        if kind == "check_start":
            if not getattr(self, "now_name", None) or not self.now_name.winfo_exists():
                return
            cid = p["cid"]
            self.now_label.configure(text="CHECK %d OF %d   -   NOW CHECKING"
                                     % (p.get("index", 0), p.get("total", len(AUTO_IDS))))
            self.now_name.configure(text=CHECK_INDEX[cid][1])
            self.now_badge.configure(text="working...", fg=C["muted"])
            self.now_summary.configure(text="")
            try:
                self.now_icon_lab.configure(image="")
            except Exception:
                pass
            return
        if not getattr(self, "now_badge", None) or not self.now_badge.winfo_exists():
            return
        cid = p["cid"]
        status = p.get("status", "unknown")
        word, colour = PLAIN.get(status, ("...", C["muted"]))
        self.now_badge.configure(text=word, fg=colour)
        _STATUS_ICON = {"pass": ("check-circle", "good"), "warn": ("alert-triangle", "okay"),
                        "fail": ("x-circle", "bad"), "na": ("minus-circle", "muted"),
                        "skipped": ("slash", "muted")}
        iname, icol = _STATUS_ICON.get(status, ("help-circle", "muted"))
        img = uikit.icon(iname, icol)
        if img:
            self.now_icon_lab.configure(image=img)
            self.now_icon_lab.image = img
        self.now_summary.configure(text=p.get("summary", "") or "")
        self.now_label.configure(text="CHECK %d OF %d   -   RESULT"
                                 % (p.get("index", 0), p.get("total", len(AUTO_IDS))))
        self.now_name.configure(text=CHECK_INDEX[cid][1])
        try:
            frac = p.get("index", 0) / max(1, p.get("total", 1))
            self.bar.update_idletasks()
            self.bar_fill.place(relheight=1.0, relwidth=max(0.004, frac))
            try:
                self.auto_pct.configure(text="%d%%" % round(frac * 100))
                self.auto_count.configure(text="%d / %d"
                                          % (p.get("index", 0), p.get("total", len(AUTO_IDS))))
            except Exception:
                pass
        except Exception:
            pass
        self.auto_count.configure(text="%d / %d" % (p.get("index", 0),
                                                    p.get("total", len(AUTO_IDS))))
        self._log("%s -> %s | %s" % (CHECK_INDEX[cid][1], word, p.get("summary", "")))

    # ================================================================ guided
    def _probe_warning(self) -> str:
        """Plain-words banner when Windows refused to hand over hardware data."""
        if not IS_WINDOWS:
            return ""
        if self.scan.ctx.get("probe_ok", True):
            return ""
        bad = sum(1 for c in AUTO_IDS
                  if self.scan.status_of(c) in ("skipped", "missing", "unknown"))
        if not bad:
            return ""
        return ("Windows refused some hardware reads on this PC, so %d check(s) show as "
                "NOT TESTED instead of a result. The exact reason is written in "
                "LCP_Data\\diagnostics.txt - send us that file and we will make the tool "
                "work on this machine." % bad)

    def _begin_guided(self) -> None:
        # keep the Start menu from ever covering a test window
        try:
            from guided.keyboard import start_win_blocker
            start_win_blocker()
        except Exception:
            pass
        self._guided_index = 0
        self.show_guided_card()

    def show_guided_card(self) -> None:
        self.clear()
        s = self.stage
        done_auto = sum(1 for c in AUTO_IDS if c in self.scan.results)
        mid = tk.Frame(s, bg=C["bg"])
        mid.pack(expand=True)
        tk.Label(mid, text="AUTOMATIC CHECKS FINISHED", bg=C["bg"], fg=C["good"],
                 font=_f(20)).pack()
        tk.Label(mid, text="%d checks ran by themselves. Now the hands-on tests:\n"
                           "the tool opens each one for you, one after another.\n\n"
                           "Keep the charger connected. Say yes to any Windows prompt."
                 % done_auto, bg=C["bg"], fg=C["text"], font=_f(17, False),
                 justify="center").pack(pady=(10, 14))
        warn = self._probe_warning()
        if warn:
            tk.Label(mid, text=warn, bg=C["bg"], fg=C["warn"], font=_f(14, False),
                     wraplength=900, justify="center").pack(pady=(0, 18))
        self.button(mid, "START THE HANDS-ON TESTS", self._guided_step, C["accent"], "white",
                    22, 50, 20).pack()
        self.button(mid, "Skip - go straight to the result", self.show_finish, C["card2"],
                    C["muted"], 12, 18, 10).pack(pady=(14, 0))

    def _guided_step(self) -> None:
        if self._guided_index >= len(GUIDED_IDS):
            self.show_finish()
            return
        cid = GUIDED_IDS[self._guided_index]
        try:
            from guided.widgets import set_step
            set_step(self._guided_index + 1, len(GUIDED_IDS))
        except Exception:
            pass
        # display line items 2 and 3 come free with the colour test
        if cid in ("screen_pixels", "screen_text") and \
                self.scan.status_of("screen_colours") not in ("missing", "skipped", "unknown"):
            self._guided_index += 1
            self._guided_step()
            return
        title, how = GUIDED_INFO.get(cid, (cid, ""))
        self.clear()
        s = self.stage
        mid = tk.Frame(s, bg=C["bg"])
        mid.pack(expand=True)
        tk.Label(mid, text="HANDS-ON TEST %d OF %d" % (self._guided_index + 1, len(GUIDED_IDS)),
                 bg=C["bg"], fg=C["muted"], font=_f(16)).pack()
        tk.Label(mid, text=title, bg=C["bg"], fg=C["text"], font=_f(40),
                 wraplength=1100).pack(pady=(6, 10))
        tk.Label(mid, text=how, bg=C["bg"], fg=C["muted"], font=_f(16, False),
                 wraplength=900, justify="center").pack(pady=(0, 26))
        self.button(mid, "OPEN THIS TEST", lambda: self._run_guided(cid), C["accent"],
                    "white", 20, 44, 18).pack()
        self.button(mid, "Skip this test", self._next_guided, C["card2"], C["muted"],
                    12, 18, 10).pack(pady=(12, 0))
        self._count = 3
        self._count_lbl = tk.Label(mid, text="opens by itself in 3", bg=C["bg"],
                                   fg="#4d5a6d", font=_f(12, False))
        self._count_lbl.pack(pady=(16, 0))
        self._tick_count(cid)

    def _tick_count(self, cid: str) -> None:
        self._count -= 1
        if self._count <= 0:
            self._run_guided(cid)
            return
        try:
            self._count_lbl.configure(text="opens by itself in %d" % self._count)
        except Exception:
            return
        self.later(1000, lambda: self._tick_count(cid))

    def _next_guided(self) -> None:
        self._guided_index += 1
        self._guided_step()

    def _run_guided(self, cid: str) -> None:
        self.clear()
        wait = tk.Frame(self.stage, bg=C["bg"])
        wait.pack(expand=True)
        tk.Label(wait, text="Running: %s" % GUIDED_INFO.get(cid, (cid, ""))[0],
                 bg=C["bg"], fg=C["text"], font=_f(22)).pack()
        tk.Label(wait, text="follow the instructions on the test window",
                 bg=C["bg"], fg=C["muted"], font=_f(13, False)).pack(pady=(6, 0))
        self.root.update_idletasks()
        ctx = self.scan.ctx
        ctx.setdefault("settings", self.settings.as_dict())
        ctx["scan_id"] = self.scan.id
        ctx["emit"] = lambda m: self._log("[hands-on] %s" % m)
        res = self.scan.result(cid)
        try:
            if cid in ("screen_colours", "screen_pixels", "screen_text"):
                from guided.screen import run_screen_test, fan_out_screen, QUICK_SUBSET
                src = self.scan.result("screen_colours")
                run_screen_test(self.root, src, emit=ctx["emit"],
                                subset=QUICK_SUBSET if self.settings.get("quick_mode") else None)
                fan_out_screen(src, self.scan.result("screen_pixels"),
                               self.scan.result("screen_text"), emit=ctx["emit"])
            elif cid == "speakers":
                from guided.audio import run_speaker_test
                run_speaker_test(self.root, res, ctx, emit=ctx["emit"])
            elif cid == "microphone":
                from guided.audio import run_mic_test
                run_mic_test(self.root, res, ctx, emit=ctx["emit"])
            elif cid == "camera_capture":
                from guided.camera import run_camera_test
                run_camera_test(self.root, res, ctx, emit=ctx["emit"])
            elif cid == "keyboard":
                from guided.keyboard import run_keyboard_test
                run_keyboard_test(self.root, res, ctx, emit=ctx["emit"])
            elif cid == "touch":
                from guided.screen import run_touch_test
                run_touch_test(self.root, res, ctx, emit=ctx["emit"])
            elif cid == "touchpad":
                from guided.misc import run_touchpad_test
                run_touchpad_test(self.root, res, ctx, emit=ctx["emit"])
            elif cid == "usb_ports":
                from guided.misc import run_usb_port_test
                run_usb_port_test(self.root, res, ctx, emit=ctx["emit"])
            elif cid == "web_ports":
                from guided.misc import run_web_ports_test
                run_web_ports_test(self.root, res, ctx, emit=ctx["emit"])
            elif cid == "cosmetic":
                from guided.misc import run_cosmetic_test
                run_cosmetic_test(self.root, res, ctx, emit=ctx["emit"])
            elif cid == "final_check":
                from guided.misc import run_signoff
                pending, flags = self._pending_and_flags()
                run_signoff(self.root, res, ctx, pending, flags, emit=ctx["emit"])
            else:
                res.skipped("No test implemented")
        except Exception as exc:
            res.status = "skipped"
            res.error = "%s: %s" % (type(exc).__name__, exc)
            res.summary = "The test window crashed - recorded as NOT TESTED"
            debug.exception("guided test %s" % cid, exc)
            self._log("hands-on test %s crashed: %s" % (cid, exc))
            bpath = debug.bundle(results=debug.results_dump(self.scan.results))
            messagebox.showerror(APP_NAME, "That test window stopped with an error:\n%s\n\n"
                                           "It is recorded as NOT TESTED so the report stays "
                                           "honest. The flow continues.\n\n"
                                           "A debug file with the full error was written to:\n%s\n"
                                           "Send it over and the next update fixes this test."
                                 % (exc, bpath or "LCP_Data\\diagnostics.txt"))
        self._persist()
        word, colour = PLAIN.get(res.status, ("...", C["muted"]))
        self._log("hands-on %s -> %s | %s" % (cid, word, res.summary))
        self._guided_index += 1
        self._show_guided_result(cid, word, colour, res.summary)

    def _show_guided_result(self, cid: str, word: str, colour: str, summary: str) -> None:
        self.clear()
        mid = tk.Frame(self.stage, bg=C["bg"])
        mid.pack(expand=True)
        tk.Label(mid, text=GUIDED_INFO.get(cid, (cid, ""))[0], bg=C["bg"], fg=C["muted"],
                 font=_f(18)).pack()
        tk.Label(mid, text=word, bg=C["bg"], fg=colour, font=_f(52)).pack(pady=(8, 6))
        tk.Label(mid, text=summary or "", bg=C["bg"], fg=C["text"], font=_f(16, False),
                 wraplength=950, justify="center").pack()
        self.later(1500, self._guided_step)

    def _pending_and_flags(self):
        pending = [name for cid, name, _g, _w, _t in CHECKS
                   if self.scan.status_of(cid) in ("missing", "skipped", "unknown")]
        flags = []
        for res in self.scan.results.values():
            for f in res.flags:
                flags.append((f[0] if isinstance(f, (list, tuple)) else str(f),
                              f[1] if isinstance(f, (list, tuple)) else "warn"))
        return pending, flags

    # ================================================================ finish
    def _recompute(self) -> None:
        self.scan.grade = grade_all(self.scan.results, self.scan.ctx)
        try:
            self.scan.valuation = estimate(self.scan.results, self.scan.ctx,
                                           self.scan.grade, load_model())
        except Exception as exc:
            self.scan.valuation = {}
            self._log("valuation failed: %s" % exc)

    def show_finish(self) -> None:
        try:
            from guided.keyboard import stop_win_blocker
            stop_win_blocker()
        except Exception:
            pass
        try:
            os.remove(os.path.join(DATA_DIR, "inprogress.json"))
        except Exception:
            pass
        self.clear()
        s = self.stage
        self._recompute()
        self.scan.finished = time.time()
        g = self.scan.grade or {}
        v = self.scan.valuation or {}

        top = tk.Frame(s, bg=C["bg"])
        top.pack(fill="x", padx=50, pady=(30, 0))
        grade = g.get("grade", "?")
        gcol = {"A+": C["good"], "A": C["good"], "B": C["accent"],
                "C": C["warn"], "D": "#fb923c", "E": C["bad"]}.get(grade, C["muted"])
        box = tk.Frame(top, bg=gcol, width=190, height=190)
        box.pack(side="left")
        box.pack_propagate(False)
        tk.Label(box, text=grade, bg=gcol, fg="white", font=_f(84)).pack()
        tk.Label(box, text="%s / 100" % g.get("score", 0), bg=gcol, fg="white",
                 font=_f(16)).pack()
        price = tk.Frame(top, bg=C["card"], highlightthickness=2, highlightbackground=C["accent"])
        price.pack(side="right")
        tk.Label(price, text="SUGGESTED RESALE PRICE", bg=C["card"], fg=C["muted"],
                 font=_f(13)).pack(padx=30, pady=(16, 0))
        tk.Label(price, text=_inr(v.get("suggested")), bg=C["card"], fg=C["accent"],
                 font=_f(46)).pack(padx=30)
        tk.Label(price, text="sell between %s and %s" % (_inr(v.get("low")), _inr(v.get("high"))),
                 bg=C["card"], fg=C["text"], font=_f(14, False)).pack(padx=30)
        tk.Label(price, text="offer the seller %s   |   your margin %s (%.0f%%)"
                 % (_inr(v.get("buy_price")), _inr(v.get("margin")), v.get("margin_pct", 0)),
                 bg=C["card"], fg=C["muted"], font=_f(12, False)).pack(padx=30, pady=(4, 16))
        info = tk.Frame(top, bg=C["bg"])
        info.pack(side="left", fill="x", expand=True, padx=26)
        ident = self.scan.ctx.get("identity") or {}
        tk.Label(info, text=("%s %s" % (ident.get("manufacturer", ""),
                                        ident.get("model", ""))).strip() or "This laptop",
                 bg=C["bg"], fg=C["text"], font=_f(30)).pack(anchor="w")
        tk.Label(info, text=g.get("meaning", ""), bg=C["bg"], fg=gcol,
                 font=_f(18), wraplength=540, justify="left").pack(anchor="w", fill="x")
        tk.Label(info, text=g.get("verdict", ""), bg=C["bg"], fg=C["muted"],
                 font=_f(14, False), wraplength=540, justify="left").pack(anchor="w",
                                                                          fill="x", pady=(6, 0))
        if self.demo:
            tk.Label(info, text="DEMO DATA - nothing was read from this machine",
                     bg=C["bg"], fg=C["warn"], font=_f(13)).pack(anchor="w", pady=(8, 0))
        flags = g.get("flags") or []
        mid = tk.Frame(s, bg=C["bg"])
        mid.pack(fill="both", expand=True, padx=50, pady=(18, 0))
        warn = self._probe_warning()
        if warn:
            tk.Label(mid, text=warn, bg=C["bg"], fg=C["warn"], font=_f(13, False),
                     wraplength=1200, justify="left", anchor="w").pack(anchor="w",
                                                                       pady=(0, 10))
        tk.Label(mid, text="WHAT WE FOUND" if flags else "NOTHING WRONG FOUND",
                 bg=C["bg"], fg=C["text"], font=_f(16)).pack(anchor="w")
        for text, level in flags[:6]:
            tk.Label(mid, text=("\u25b8  " + text), bg=C["bg"],
                     fg=C["bad"] if level == "fail" else C["warn"],
                     font=_f(14, False), anchor="w", wraplength=1200,
                     justify="left").pack(anchor="w", pady=1)
        self.pdf_label = tk.Label(mid, text="Writing report and PDF...", bg=C["bg"],
                                  fg=C["muted"], font=_f(14, False), anchor="w")
        self.pdf_label.pack(anchor="w", pady=(14, 0))
        self.export_bar = uikit.ProgressBar(s, width=980, height=22,
                                            caption="Building the report and PDFs...")
        self.export_bar.pack(pady=(10, 0))

        row = tk.Frame(s, bg=C["bg"])
        row.pack(side="bottom", pady=26)
        self.btn_pdf = self.button(row, "Open customer PDF", self._open_customer_pdf,
                                   C["good"], "#06230f", 15, 26, 12)
        self.btn_pdf.pack(side="left", padx=6)
        self.button(row, "Open internal report", self._open_internal, C["accent"], "white",
                    13, 20, 10).pack(side="left", padx=6)
        self.button(row, "Open folder", self._open_folder, C["card2"], C["text"], 13, 20,
                    10).pack(side="left", padx=6)
        self.button(row, "See every check", self._all_checks_dialog, C["card2"], C["text"],
                    13, 20, 10).pack(side="left", padx=6)
        self.button(row, "Debug file", self.make_bundle, C["card2"], C["text"],
                    13, 20, 10).pack(side="left", padx=6)
        self.button(row, "Check another laptop", self.show_welcome, C["card2"], C["text"],
                    13, 20, 10).pack(side="left", padx=6)
        self.button(row, "Exit", self._exit, C["card2"], C["muted"], 13, 20, 10).pack(
            side="left", padx=6)
        threading.Thread(target=self._export_thread, daemon=True).start()

    def _export_progress(self, frac: float, caption: str) -> None:
        try:
            self.root.after(0, lambda: self.export_bar.set(frac, caption)
                            if getattr(self, "export_bar", None)
                            and self.export_bar.winfo_exists() else None)
        except Exception:
            pass

    def _export_thread(self) -> None:
        paths: Dict[str, str] = {}
        try:
            paths = report_mod.write_reports(self.scan, self.branding, folder=REPORTS_DIR)
        except Exception as exc:
            self._log("report write failed: %s" % exc)
        self._export_progress(0.30, "Report pages built - printing PDFs (two at once)...")
        pdfs: Dict[str, str] = {}
        jobs = []
        lock = threading.Lock()

        def one_pdf(key: str, tag: str, frac: float) -> None:
            html = paths.get(key)
            if not html:
                return
            out = os.path.splitext(html)[0] + ("_customer.pdf" if tag == "customer" else ".pdf")
            ok = pdf_mod.html_to_pdf(html, out)
            if ok:
                with lock:
                    pdfs[tag] = out
            self._log("pdf %s: %s" % (tag, "ok" if ok else "FAILED"))
            self._export_progress(frac, "PDF %s printed" % tag)
        for key, tag, frac in (("internal", "report", 0.62), ("customer", "customer", 0.88)):
            t = threading.Thread(target=one_pdf, args=(key, tag, frac), daemon=True)
            jobs.append(t)
            t.start()
        for t in jobs:
            t.join()
        self._export_progress(1.0, "Report and both PDFs are ready below.")
        self.pdf_paths = pdfs
        self.report_paths = paths
        try:
            self.root.after(0, self._finish_export_ui)
        except Exception:
            pass

    def _finish_export_ui(self) -> None:
        try:
            if self.pdf_paths.get("customer"):
                self.pdf_label.configure(
                    text="PDF ready:  %s" % self.pdf_paths["customer"], fg=C["good"])
            elif self.pdf_paths.get("internal"):
                self.pdf_label.configure(text="PDF ready:  %s" % self.pdf_paths["internal"],
                                         fg=C["good"])
                self.btn_pdf.configure(text="Open PDF",
                                       command=lambda: self._open(self.pdf_paths["internal"]))
            else:
                html = getattr(self, "report_paths", {}).get("customer") or ""
                self.pdf_label.configure(
                    text="Automatic PDF was not possible on this PC - the report is ready "
                         "as HTML: press 'Open internal report' and use Ctrl+P to save a PDF.",
                    fg=C["warn"])
                if html:
                    self.btn_pdf.configure(text="Open report (Ctrl+P for PDF)",
                                           command=lambda: report_mod.open_in_browser(html))
        except Exception:
            pass

    def make_bundle(self) -> None:
        """Pack everything the developer needs into one zip the shop sends."""
        ov = uikit.BusyOverlay(self.root, "Collecting debug information",
                               "hardware answers, session log, error traces - one zip file",
                               icon_name="file-text")
        try:
            self.root.update_idletasks()
            self.root.update()
            path = debug.bundle(results=debug.results_dump(self.scan.results))
        except Exception as exc:
            debug.exception("make_bundle", exc)
            path = ""
        finally:
            ov.close()
        if path:
            messagebox.showinfo(APP_NAME, "Debug file ready:\n\n%s\n\nSend this one file "
                                          "(WhatsApp, mail, anything). It contains no customer "
                                          "data - only what Windows reported, the log and the "
                                          "error traces." % path)
        else:
            messagebox.showerror(APP_NAME, "Could not write the debug file. The raw log is at "
                                           "LCP_Data\\diagnostics.txt")
        self.show_welcome()

    def _open(self, path: str) -> None:
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            report_mod.open_in_browser(path)

    def _open_customer_pdf(self) -> None:
        p = self.pdf_paths.get("customer") or self.pdf_paths.get("internal")
        if p:
            self._open(p)
        else:
            messagebox.showinfo(APP_NAME, "The PDF is still being written - try again in a "
                                          "few seconds.")

    def _open_internal(self) -> None:
        p = getattr(self, "report_paths", {}).get("internal")
        if p:
            report_mod.open_in_browser(p)

    def _open_folder(self) -> None:
        try:
            os.startfile(REPORTS_DIR)  # type: ignore[attr-defined]
        except Exception:
            pass

    def _all_checks_dialog(self) -> None:
        top = tk.Toplevel(self.root)
        top.configure(bg=C["bg"])
        top.title("Every check")
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        top.geometry("%dx%d+%d+%d" % (min(1000, sw - 60), min(760, sh - 60), 30, 20))
        top.attributes("-topmost", True)
        txt = tk.Text(top, bg="#0d1219", fg="#cfd8e6", relief="flat", font=("Consolas", 10),
                      wrap="word", padx=14, pady=12)
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        lines = []
        for cid, name, group, weight, tech in CHECKS:
            res = self.scan.results.get(cid)
            if res is None:
                lines.append("%-34s %-14s %s" % (name, "NOT TESTED", ""))
                continue
            word, _c = PLAIN.get(res.status, (res.status, ""))
            lines.append("%-34s %-14s %s" % (name, word, res.summary or ""))
            for f in res.flags:
                t = f[0] if isinstance(f, (list, tuple)) else str(f)
                lines.append("      ! %s" % t)
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")

    # ============================================================== dialogs
    def settings_dialog(self) -> None:
        top = tk.Toplevel(self.root)
        top.configure(bg=C["bg"])
        top.title("Shop & report settings")
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        top.geometry("%dx%d+%d+%d" % (min(760, sw - 60), min(700, sh - 60),
                                      (sw - min(760, sw - 60)) // 2, 20))
        top.attributes("-topmost", True)
        tk.Label(top, text="SHOP & REPORT SETTINGS", bg=C["bg"], fg=C["text"],
                 font=_f(20)).pack(pady=(18, 4))
        tk.Label(top, text="Saved on this computer and printed on every report.",
                 bg=C["bg"], fg=C["muted"], font=_f(12, False)).pack()
        body = tk.Frame(top, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=40, pady=14)
        fields = [("shop_name", "Shop name"), ("phone", "Phone"), ("address", "Address"),
                  ("website", "Website (for the QR code on the report)"),
                  ("warranty_text", "Warranty line printed on the customer report"),
                  ("gst", "GSTIN (optional)")]
        vars_: Dict[str, tk.StringVar] = {}
        for key, label in fields:
            r = tk.Frame(body, bg=C["bg"])
            r.pack(fill="x", pady=3)
            tk.Label(r, text=label, bg=C["bg"], fg=C["muted"], font=_f(11, False),
                     width=34, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(self.branding.data.get(key, "")))
            tk.Entry(r, textvariable=var, bg=C["card2"], fg=C["text"], relief="flat",
                     insertbackground=C["text"], font=_f(12, False)).pack(side="left",
                                                                          fill="x", expand=True,
                                                                          ipady=5)
            vars_[key] = var
        r = tk.Frame(body, bg=C["bg"])
        r.pack(fill="x", pady=6)
        tk.Label(r, text="Logo image", bg=C["bg"], fg=C["muted"], font=_f(11, False),
                 width=34, anchor="w").pack(side="left")
        self.button(r, "Choose logo...", lambda: self._pick_logo(top), C["card2"], C["text"],
                    11, 14, 6).pack(side="left")
        row = tk.Frame(top, bg=C["bg"])
        row.pack(pady=(0, 20))
        self.button(row, "SAVE", lambda: self._save_settings(top, vars_), C["good"],
                    "#06230f", 15, 34, 10).pack(side="left", padx=6)
        self.button(row, "Close", top.destroy, C["card2"], C["text"], 12, 20, 8).pack(
            side="left", padx=6)

    def _pick_logo(self, top: tk.Toplevel) -> None:
        path = filedialog.askopenfilename(parent=top, title="Choose a logo image",
                                          filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp")])
        if path:
            saved = self.branding.set_logo_from_file(path)
            messagebox.showinfo(APP_NAME, "Logo saved." if saved else "Could not use that file.",
                                parent=top)

    def _save_settings(self, top: tk.Toplevel, vars_: Dict[str, tk.StringVar]) -> None:
        for key, var in vars_.items():
            self.branding.set(key, var.get())
        self.branding.save()
        top.destroy()
        messagebox.showinfo(APP_NAME, "Saved. Every report from now on carries your branding.")

    def history_dialog(self) -> None:
        top = tk.Toplevel(self.root)
        top.configure(bg=C["bg"])
        top.title("Past inspections")
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        top.geometry("%dx%d+%d+%d" % (min(1050, sw - 60), min(700, sh - 60), 30, 20))
        top.attributes("-topmost", True)
        cols = ("scan_id", "date", "machine", "grade", "score", "price", "tech")
        tree = ttk.Treeview(top, columns=cols, show="headings", height=18)
        for c, w in zip(cols, (140, 130, 260, 60, 60, 100, 120)):
            tree.heading(c, text=c.replace("_", " ").title())
            tree.column(c, width=w, anchor="w")
        tree.pack(fill="both", expand=True, padx=14, pady=10)
        if not list_history():
            tree.pack_forget()
            uikit.state_card(top, "clock", "muted", "No inspections yet",
                             "Every finished inspection appears here with its grade, price "
                             "and a one-click link to its report and PDF.",
                             "Close", top.destroy).pack(fill="both", expand=True,
                                                        padx=30, pady=30)
            return
        index: Dict[str, Dict[str, Any]] = {}
        for item in list_history():
            iid = tree.insert("", "end", values=(item["scan_id"], item["date"], item["machine"],
                                                 item["grade"], item["score"],
                                                 _inr(item["suggested"]) if item["suggested"]
                                                 else "", item["technician"]))
            index[iid] = item

        def open_sel() -> None:
            sel = tree.selection()
            if not sel:
                return
            item = index.get(sel[0])
            if item and os.path.exists(item.get("file", "")):
                data = load_json(item["file"])
                if data:
                    self.scan = Scan.from_dict(data)
                    self.demo = bool(data.get("demo"))
                    self._recompute()
                    top.destroy()
                    self.show_finish()

        tk.Button(top, text="Open this inspection", bg=C["accent"], fg="white", relief="flat",
                  font=_f(12), padx=18, pady=8, command=open_sel).pack(pady=(0, 14))


def _inr(value: Any) -> str:
    from app.core import fmt_inr
    return fmt_inr(value) if value not in (None, "") else "-"


def main(demo: bool = False) -> int:
    debug.install_hooks()
    set_dpi_aware()          # MUST happen before the first window is created
    root = tk.Tk()
    Wizard(root, demo=demo)
    root.mainloop()
    return 0
