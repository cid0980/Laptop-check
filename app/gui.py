"""
gui.py -- the whole application window.

One window, five pages, no blank screens:

  1. SCAN      every automatic check runs here and you watch it happen:
               live status per item, what was found, how long it took, and a
               running log at the bottom.
  2. GUIDED    the tests a human must judge (screen colours, speakers, mic,
               camera, keyboard, ports, cosmetics, sign-off). Each card shows
               PASS / WARNING / FAIL / NOT TESTED.
  3. RESULT    the grade, the score breakdown by group, and the resale price
               with every rupee explained.
  4. REPORT    shop branding, export internal + customer reports, open them.
  5. HISTORY   every laptop this shop has ever scanned, re-openable.

tkinter/ttk only - it ships inside Python, so the single .exe needs no
installs and no downloads at run time.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from app.core import (APP_NAME, APP_VERSION, AUTO_IDS, CHECKS, GUIDED_IDS,
                      DATA_DIR, IS_WINDOWS, PHOTOS_DIR, REPORTS_DIR, Bus,
                      CheckResult, get_power_status, is_admin, load_json,
                      make_result, new_scan_id, prevent_sleep, relaunch_as_admin)
from app.branding import Branding, Settings
from app.grading import grade_all
from app.scan import Scan, list_history
from app.valuation import estimate, load_model, save_model
from app import report as report_mod

COL = {
    "bg": "#0e1117", "panel": "#151a23", "panel2": "#1c2330", "line": "#262e3d",
    "text": "#e6ebf4", "muted": "#8e9bb0", "accent": "#3b82f6", "good": "#22c55e",
    "warn": "#f59e0b", "bad": "#ef4444", "na": "#64748b", "purple": "#a78bfa",
}
STATUS_COLOUR = {"pass": COL["good"], "warn": COL["warn"], "fail": COL["bad"],
                 "na": COL["na"], "skipped": COL["purple"], "missing": COL["purple"],
                 "unknown": COL["muted"]}
STATUS_TEXT = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "na": "NOT FITTED",
               "skipped": "NOT TESTED", "missing": "NOT RUN", "unknown": "..."}

GUIDED_LABELS = {
    "screen_colours": ("Screen colour test", "Black, white, red, green, blue, grey, gradients - marked by eye"),
    "screen_pixels": ("Dead pixel / backlight bleed", "Quick re-check on white and black if you saw a defect"),
    "screen_text": ("Text sharpness", "Is text crisp, or blurred by wrong scaling?"),
    "speakers": ("Speakers left / right / sweep", "Tones generated inside the app - listen and mark"),
    "microphone": ("Microphone", "Live level meter while you speak"),
    "camera_capture": ("Camera live capture", "Live preview, photo stored in the report"),
    "keyboard": ("Keyboard (anti-cheat)", "Built-in keyboard only - external keyboards are rejected"),
    "touchpad": ("Touchpad / pointing device", "Move, click, scroll, drift, gestures"),
    "usb_ports": ("USB port plug test", "Plug a pen drive in - the tool detects it automatically"),
    "web_ports": ("HDMI / headphone jack / card reader", "Auto-detected when you plug something in"),
    "cosmetic": ("Cosmetic, hinge and safety check", "14-point hands-on inspection + damage photos"),
    "final_check": ("Technician sign-off", "Name, outstanding tests and the final confirmation"),
}


class App:
    def __init__(self, root: tk.Tk, demo: bool = False) -> None:
        self.root = root
        self.demo_requested = demo
        self.bus = Bus()
        self.settings = Settings()
        self.branding = Branding()
        self.scan = Scan()
        self.worker: Optional[threading.Thread] = None
        self.scanning = False
        self.started_scan = False
        self.page_widgets: Dict[str, tk.Widget] = {}
        self.check_rows: Dict[str, Dict[str, tk.Widget]] = {}
        self.guided_rows: Dict[str, Dict[str, tk.Widget]] = {}
        self._build()
        self._poll()
        if self.demo_requested:
            self.root.after(300, self.load_demo)

    # ================================================================== build
    def _build(self) -> None:
        self.root.title("%s v%s" % (APP_NAME, APP_VERSION))
        self.root.configure(bg=COL["bg"])
        self.root.geometry("1280x820")
        self.root.minsize(1100, 700)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=COL["bg"], foreground=COL["text"],
                        fieldbackground=COL["panel2"], bordercolor=COL["line"],
                        font=("Segoe UI", 10))
        style.configure("TNotebook", background=COL["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=COL["panel"], foreground=COL["muted"],
                        padding=(18, 9), font=("Segoe UI", 11, "bold"), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", COL["accent"])],
                  foreground=[("selected", "white")])
        style.configure("TProgressbar", background=COL["accent"], troughcolor=COL["panel2"],
                        bordercolor=COL["panel2"], lightcolor=COL["accent"], darkcolor=COL["accent"])
        style.configure("Treeview", background=COL["panel"], fieldbackground=COL["panel"],
                        foreground=COL["text"], rowheight=26, borderwidth=0)
        style.configure("Treeview.Heading", background=COL["panel2"], foreground=COL["muted"],
                        font=("Segoe UI", 9, "bold"))

        # ---- header ----------------------------------------------------
        head = tk.Frame(self.root, bg=COL["panel"])
        head.pack(fill="x")
        left = tk.Frame(head, bg=COL["panel"])
        left.pack(side="left", padx=16, pady=10)
        tk.Label(left, text=APP_NAME, bg=COL["panel"], fg=COL["text"],
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        self.head_sub = tk.Label(left, text="", bg=COL["panel"], fg=COL["muted"],
                                 font=("Segoe UI", 9))
        self.head_sub.pack(anchor="w")
        right = tk.Frame(head, bg=COL["panel"])
        right.pack(side="right", padx=16, pady=10)
        self.admin_lbl = tk.Label(right, text="", bg=COL["panel"], fg=COL["muted"],
                                  font=("Segoe UI", 9, "bold"))
        self.admin_lbl.pack(anchor="e")
        self.power_lbl = tk.Label(right, text="", bg=COL["panel"], fg=COL["muted"],
                                  font=("Segoe UI", 9))
        self.power_lbl.pack(anchor="e")
        self.scan_id_lbl = tk.Label(right, text="", bg=COL["panel"], fg=COL["accent"],
                                    font=("Consolas", 10, "bold"))
        self.scan_id_lbl.pack(anchor="e")
        if IS_WINDOWS and not is_admin():
            tk.Button(right, text="Relaunch as Administrator", bg="#7c3aed", fg="white",
                      relief="flat", font=("Segoe UI", 9, "bold"), padx=10, pady=4,
                      cursor="hand2", command=self._elevate).pack(anchor="e", pady=(6, 0))

        # ---- notebook ----------------------------------------------------
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(6, 4))
        self.tab_scan = tk.Frame(self.nb, bg=COL["bg"])
        self.tab_guided = tk.Frame(self.nb, bg=COL["bg"])
        self.tab_result = tk.Frame(self.nb, bg=COL["bg"])
        self.tab_report = tk.Frame(self.nb, bg=COL["bg"])
        self.tab_history = tk.Frame(self.nb, bg=COL["bg"])
        self.nb.add(self.tab_scan, text="  1. Scan  ")
        self.nb.add(self.tab_guided, text="  2. Guided tests  ")
        self.nb.add(self.tab_result, text="  3. Result & price  ")
        self.nb.add(self.tab_report, text="  4. Report  ")
        self.nb.add(self.tab_history, text="  5. History  ")

        self._build_scan_page()
        self._build_guided_page()
        self._build_result_page()
        self._build_report_page()
        self._build_history_page()

        # ---- status bar ---------------------------------------------------
        self.status = tk.Label(self.root, text="Ready.", bg=COL["panel"], fg=COL["muted"],
                               anchor="w", font=("Segoe UI", 9), padx=12, pady=5)
        self.status.pack(fill="x", side="bottom")
        self._refresh_header()

    def _refresh_header(self) -> None:
        ident = self.scan.ctx.get("identity") or {}
        name = ("%s %s" % (ident.get("manufacturer", ""), ident.get("model", ""))).strip()
        suffix = "" if IS_WINDOWS else "   (demo mode - not Windows)"
        self.head_sub.configure(text=(name + "   |   ") if name else "",
                                fg=COL["muted"])
        self.head_sub.configure(text="%s%s%s" % ((name + "   |   ") if name else "",
                                                 self.branding.get("shop_name"), suffix))
        admin = is_admin() if IS_WINDOWS else False
        self.admin_lbl.configure(
            text=("Administrator rights: YES" if admin else "Administrator rights: NO (some data hidden)"),
            fg=COL["good"] if admin else COL["warn"])
        self.scan_id_lbl.configure(text=self.scan.id)
        if IS_WINDOWS:
            ps = get_power_status()
            if ps:
                self.power_lbl.configure(text="Power: %s%s" % (
                    ps.get("ac", "?"), ("  |  battery %d%%" % ps["percent"]) if ps.get("percent") is not None else ""))
        self.root.title("%s v%s - %s" % (APP_NAME, APP_VERSION,
                                         (ident.get("model") or "no machine yet")))

    # ============================================================== scan page
    def _build_scan_page(self) -> None:
        top = tk.Frame(self.tab_scan, bg=COL["bg"])
        top.pack(fill="x", padx=14, pady=10)
        self.start_btn = tk.Button(top, text="\u25b6   START FULL SCAN", bg=COL["good"], fg="#06230f",
                                   relief="flat", font=("Segoe UI", 13, "bold"), padx=22, pady=10,
                                   cursor="hand2", command=self.start_scan)
        self.start_btn.pack(side="left")
        self.stop_btn = tk.Button(top, text="Stop", bg=COL["panel2"], fg=COL["muted"], relief="flat",
                                  font=("Segoe UI", 10, "bold"), padx=14, cursor="hand2",
                                  command=self.stop_scan, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        tk.Button(top, text="New scan", bg=COL["panel2"], fg=COL["muted"], relief="flat",
                  font=("Segoe UI", 10), padx=12, cursor="hand2",
                  command=self.new_scan).pack(side="left", padx=4)
        tk.Button(top, text="Load demo scan", bg=COL["panel2"], fg=COL["muted"], relief="flat",
                  font=("Segoe UI", 10), padx=12, cursor="hand2",
                  command=self.load_demo).pack(side="left", padx=4)
        self.phase_lbl = tk.Label(top, text="Press START. The tool reads the hardware live - "
                                            "nothing is installed or downloaded.",
                                  bg=COL["bg"], fg=COL["muted"], font=("Segoe UI", 10), anchor="e")
        self.phase_lbl.pack(side="right", fill="x", expand=True, padx=10)

        prog = tk.Frame(self.tab_scan, bg=COL["bg"])
        prog.pack(fill="x", padx=14)
        self.progress = ttk.Progressbar(prog, maximum=100, value=0)
        self.progress.pack(fill="x", side="left", expand=True)
        self.prog_lbl = tk.Label(prog, text="0 / %d" % len(AUTO_IDS), bg=COL["bg"],
                                 fg=COL["muted"], font=("Segoe UI", 9, "bold"), width=12)
        self.prog_lbl.pack(side="left", padx=8)

        body = tk.Frame(self.tab_scan, bg=COL["bg"])
        body.pack(fill="both", expand=True, padx=14, pady=10)

        # left: live checklist
        listf = tk.Frame(body, bg=COL["panel"], highlightthickness=1, highlightbackground=COL["line"])
        listf.pack(side="left", fill="both", expand=True)
        tk.Label(listf, text="AUTOMATIC CHECKS - live", bg=COL["panel"], fg=COL["text"],
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", padx=12, pady=(10, 4))
        wrap = tk.Frame(listf, bg=COL["panel"])
        wrap.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        cv = tk.Canvas(wrap, bg=COL["panel"], highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=cv.yview)
        self.scan_inner = tk.Frame(cv, bg=COL["panel"])
        self.scan_inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=self.scan_inner, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._bind_wheel(cv)

        last_group = None
        for cid, name, group, weight, tech in CHECKS:
            if tech != "auto":
                continue
            if group != last_group:
                tk.Label(self.scan_inner, text=group.upper(), bg=COL["panel"], fg=COL["accent"],
                         font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=10, pady=(8, 1))
                last_group = group
            row = tk.Frame(self.scan_inner, bg=COL["panel"])
            row.pack(fill="x", padx=6, pady=1)
            dot = tk.Label(row, text="\u25cf", bg=COL["panel"], fg="#39424f",
                           font=("Segoe UI", 10), width=2)
            dot.pack(side="left")
            nm = tk.Label(row, text=name, bg=COL["panel"], fg=COL["text"],
                          font=("Segoe UI", 10), anchor="w", width=27)
            nm.pack(side="left")
            st = tk.Label(row, text="waiting", bg=COL["panel"], fg=COL["muted"],
                          font=("Segoe UI", 9, "bold"), width=11, anchor="w")
            st.pack(side="left")
            summ = tk.Label(row, text="", bg=COL["panel"], fg=COL["muted"],
                            font=("Segoe UI", 9), anchor="w")
            summ.pack(side="left", fill="x", expand=True)
            tm = tk.Label(row, text="", bg=COL["panel"], fg="#4d5a6d",
                          font=("Consolas", 8), width=7, anchor="e")
            tm.pack(side="left")
            det = tk.Button(row, text="details", bg=COL["panel2"], fg=COL["muted"], relief="flat",
                            font=("Segoe UI", 8), padx=6, cursor="hand2",
                            command=lambda c=cid: self.show_detail(c))
            det.pack(side="left", padx=2)
            self.check_rows[cid] = {"row": row, "dot": dot, "name": nm, "status": st,
                                    "summary": summ, "time": tm, "detail": det}

        # right: live log
        logf = tk.Frame(body, bg=COL["panel"], highlightthickness=1, highlightbackground=COL["line"])
        logf.pack(side="right", fill="both", padx=(10, 0))
        logf.configure(width=430)
        tk.Label(logf, text="LIVE PROCESS LOG", bg=COL["panel"], fg=COL["text"],
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", padx=12, pady=(10, 4))
        self.log = tk.Text(logf, width=52, bg="#0b0e14", fg="#b9c6d8", relief="flat",
                           font=("Consolas", 9), wrap="word", padx=8, pady=8)
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log.tag_configure("t", foreground="#5f6b7d")
        self.log.tag_configure("ok", foreground="#4ade80")
        self.log.tag_configure("warn", foreground="#fbbf24")
        self.log.tag_configure("bad", foreground="#f87171")
        self.log.tag_configure("hd", foreground="#60a5fa", font=("Consolas", 9, "bold"))
        self.emit_log("%s v%s ready." % (APP_NAME, APP_VERSION), "hd")
        self.emit_log("Data folder: %s" % DATA_DIR)
        if not IS_WINDOWS:
            self.emit_log("NOT WINDOWS - running in demo mode. Real hardware data needs Windows.", "warn")
        elif not is_admin():
            self.emit_log("Not running as Administrator: SMART details, temperatures and panel EDID "
                          "may be unavailable. Use the button at the top right to relaunch elevated.", "warn")

    def _bind_wheel(self, canvas: tk.Canvas) -> None:
        def wheel(event: Any) -> None:
            try:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                try:
                    canvas.yview_scroll(int(-event.delta), "units")
                except Exception:
                    pass
        canvas.bind_all("<MouseWheel>", wheel)

    # =========================================================== guided page
    def _build_guided_page(self) -> None:
        head = tk.Frame(self.tab_guided, bg=COL["bg"])
        head.pack(fill="x", padx=14, pady=10)
        tk.Label(head, text="Technician-verified tests", bg=COL["bg"], fg=COL["text"],
                 font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(head, text="Software cannot see a dead pixel, hear a rattling speaker or feel a "
                            "broken hinge. These tests show you the evidence and record your verdict.",
                 bg=COL["bg"], fg=COL["muted"], font=("Segoe UI", 10), anchor="w",
                 justify="left", wraplength=900).pack(anchor="w")
        self.guided_prog = tk.Label(head, text="", bg=COL["bg"], fg=COL["accent"],
                                    font=("Segoe UI", 10, "bold"))
        self.guided_prog.pack(anchor="w", pady=(4, 0))

        wrap = tk.Frame(self.tab_guided, bg=COL["bg"])
        wrap.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        cv = tk.Canvas(wrap, bg=COL["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=cv.yview)
        inner = tk.Frame(cv, bg=COL["bg"])
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=inner, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        grid = tk.Frame(inner, bg=COL["bg"])
        grid.pack(fill="both", expand=True)
        for i, cid in enumerate(GUIDED_IDS):
            title, hint = GUIDED_LABELS.get(cid, (cid, ""))
            card = tk.Frame(grid, bg=COL["panel"], highlightthickness=1,
                            highlightbackground=COL["line"])
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=5, pady=5)
            grid.columnconfigure(i % 2, weight=1)
            top = tk.Frame(card, bg=COL["panel"])
            top.pack(fill="x", padx=12, pady=(10, 2))
            tk.Label(top, text=title, bg=COL["panel"], fg=COL["text"],
                     font=("Segoe UI", 11, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
            badge = tk.Label(top, text="NOT TESTED", bg=COL["panel2"], fg=COL["muted"],
                             font=("Segoe UI", 8, "bold"), padx=8, pady=2)
            badge.pack(side="right")
            tk.Label(card, text=hint, bg=COL["panel"], fg=COL["muted"], font=("Segoe UI", 9),
                     anchor="w", justify="left", wraplength=470).pack(fill="x", padx=12)
            summ = tk.Label(card, text="", bg=COL["panel"], fg=COL["text"], font=("Segoe UI", 9),
                            anchor="w", justify="left", wraplength=470)
            summ.pack(fill="x", padx=12, pady=(4, 0))
            btns = tk.Frame(card, bg=COL["panel"])
            btns.pack(fill="x", padx=12, pady=(8, 10))
            run = tk.Button(btns, text="Run test", bg=COL["accent"], fg="white", relief="flat",
                            font=("Segoe UI", 9, "bold"), padx=12, pady=4, cursor="hand2",
                            command=lambda c=cid: self.run_guided(c))
            run.pack(side="left")
            tk.Button(btns, text="Details", bg=COL["panel2"], fg=COL["muted"], relief="flat",
                      font=("Segoe UI", 9), padx=10, cursor="hand2",
                      command=lambda c=cid: self.show_detail(c)).pack(side="left", padx=4)
            tk.Button(btns, text="Mark not fitted", bg=COL["panel2"], fg=COL["na"], relief="flat",
                      font=("Segoe UI", 9), padx=10, cursor="hand2",
                      command=lambda c=cid: self.mark_na(c)).pack(side="left", padx=4)
            self.guided_rows[cid] = {"card": card, "badge": badge, "summary": summ, "run": run}
        self._refresh_guided()

    def _refresh_guided(self) -> None:
        done = 0
        for cid in GUIDED_IDS:
            row = self.guided_rows.get(cid)
            if not row:
                continue
            res = self.scan.results.get(cid)
            status = res.status if res is not None else "missing"
            colour = STATUS_COLOUR.get(status, COL["muted"])
            row["badge"].configure(text=STATUS_TEXT.get(status, status.upper()),
                                   fg=colour, bg=COL["panel2"])
            row["card"].configure(highlightbackground=colour if status != "missing" else COL["line"])
            row["summary"].configure(text=(res.summary if res and res.summary else ""))
            if status in ("pass", "warn", "fail", "na"):
                done += 1
        self.guided_prog.configure(text="%d of %d guided tests completed" % (done, len(GUIDED_IDS)))

    # ========================================================== result page
    def _build_result_page(self) -> None:
        wrap = tk.Frame(self.tab_result, bg=COL["bg"])
        wrap.pack(fill="both", expand=True, padx=14, pady=10)
        cv = tk.Canvas(wrap, bg=COL["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=cv.yview)
        self.result_inner = tk.Frame(cv, bg=COL["bg"])
        self.result_inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        result_win = cv.create_window((0, 0), window=self.result_inner, anchor="nw")

        def _fit(_e=None, _cv=cv, _win=result_win):
            _cv.itemconfigure(_win, width=max(700, _cv.winfo_width() - 22))
        cv.bind("<Configure>", _fit)
        self.root.after(120, _fit)
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._placeholder(self.result_inner,
                          "No result yet.\n\nRun the automatic scan (page 1) and the guided tests "
                          "(page 2), then the grade, the score breakdown and the resale price appear here.")

    def _placeholder(self, parent: tk.Widget, text: str) -> None:
        for c in parent.winfo_children():
            c.destroy()
        tk.Label(parent, text=text, bg=COL["bg"], fg=COL["muted"], font=("Segoe UI", 12),
                 justify="center").pack(pady=90)

    def _refresh_result(self) -> None:
        parent = self.result_inner
        for c in parent.winfo_children():
            c.destroy()
        g = self.scan.grade or {}
        v = self.scan.valuation or {}
        if not g:
            self._placeholder(parent, "No result yet.")
            return
        ident = self.scan.ctx.get("identity") or {}

        top = tk.Frame(parent, bg=COL["panel"], highlightthickness=1, highlightbackground=COL["line"])
        top.pack(fill="x", pady=(0, 10))
        grade = g.get("grade", "?")
        box = tk.Frame(top, bg=GRADE_HEX.get(grade, COL["accent"]), width=130, height=130)
        box.pack(side="left", padx=16, pady=16)
        box.pack_propagate(False)
        tk.Label(box, text=grade, bg=GRADE_HEX.get(grade, COL["accent"]), fg="white",
                 font=("Segoe UI", 40, "bold")).pack()
        tk.Label(box, text="%s / 100" % g.get("score", 0), bg=GRADE_HEX.get(grade, COL["accent"]),
                 fg="white", font=("Segoe UI", 10, "bold")).pack()
        info = tk.Frame(top, bg=COL["panel"])
        info.pack(side="left", fill="both", expand=True, pady=14)
        tk.Label(info, text="%s %s" % (ident.get("manufacturer", ""), ident.get("model", "")),
                 bg=COL["panel"], fg=COL["text"], font=("Segoe UI", 16, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(info, text=g.get("meaning", ""), bg=COL["panel"], fg=COL["muted"],
                 font=("Segoe UI", 10), anchor="w").pack(fill="x")
        tk.Label(info, text=g.get("verdict", ""), bg=COL["panel"], fg=COL["text"],
                 font=("Segoe UI", 11, "bold"), anchor="w", justify="left",
                 wraplength=700).pack(fill="x", pady=(6, 0))
        tk.Label(info, text="Completeness %.0f%%   |   %d scored checks   |   %d problem flag(s)   |   %s"
                 % (g.get("completeness", 0), g.get("scored_items", 0), len(g.get("flags") or []),
                    "SIGNED OFF" if self.scan.ctx.get("signed_off") else "NOT SIGNED OFF (DRAFT)"),
                 bg=COL["panel"], fg=COL["muted"], font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(6, 0))

        pricebox = tk.Frame(top, bg=COL["panel2"], highlightthickness=2,
                            highlightbackground=COL["accent"])
        pricebox.pack(side="right", padx=16, pady=16)
        tk.Label(pricebox, text="SUGGESTED RESALE", bg=COL["panel2"], fg=COL["muted"],
                 font=("Segoe UI", 9, "bold")).pack(padx=18, pady=(10, 0))
        tk.Label(pricebox, text=_inr(v.get("suggested")), bg=COL["panel2"], fg=COL["accent"],
                 font=("Segoe UI", 26, "bold")).pack(padx=18)
        tk.Label(pricebox, text="range %s - %s" % (_inr(v.get("low")), _inr(v.get("high"))),
                 bg=COL["panel2"], fg=COL["muted"], font=("Segoe UI", 9)).pack(padx=18)
        for lbl, val in (("Buy / trade-in offer", _inr(v.get("buy_price"))),
                         ("Your margin", "%s (%.0f%%)" % (_inr(v.get("margin")), v.get("margin_pct", 0))),
                         ("Repair estimate", _inr(v.get("repair_estimate")))):
            r = tk.Frame(pricebox, bg=COL["panel2"])
            r.pack(fill="x", padx=18, pady=1)
            tk.Label(r, text=lbl, bg=COL["panel2"], fg=COL["muted"],
                     font=("Segoe UI", 9), anchor="w", width=18).pack(side="left")
            tk.Label(r, text=val, bg=COL["panel2"], fg=COL["text"],
                     font=("Segoe UI", 9, "bold"), anchor="e").pack(side="right")
        tk.Label(pricebox, text="", bg=COL["panel2"]).pack(pady=4)

        # ---- group breakdown -------------------------------------------
        card = tk.Frame(parent, bg=COL["panel"], highlightthickness=1, highlightbackground=COL["line"])
        card.pack(fill="x", pady=(0, 10))
        tk.Label(card, text="SCORE BREAKDOWN BY GROUP", bg=COL["panel"], fg=COL["text"],
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", padx=14, pady=(10, 4))
        for row in g.get("groups", []):
            r = tk.Frame(card, bg=COL["panel"])
            r.pack(fill="x", padx=14, pady=2)
            tk.Label(r, text=row["group"], bg=COL["panel"], fg=COL["text"], font=("Segoe UI", 10),
                     width=14, anchor="w").pack(side="left")
            sc = row.get("score")
            bar = tk.Frame(r, bg=COL["panel2"], height=16)
            bar.pack(side="left", fill="x", expand=True, padx=8)
            bar.pack_propagate(False)
            if sc is not None:
                fill = tk.Frame(bar, bg=score_colour(sc), height=16)
                fill.pack(side="left", fill="y")
                fill.place(relheight=1.0, relwidth=max(0.0, min(1.0, sc / 100.0)))
                tk.Label(bar, text="%.0f" % sc, bg=COL["panel2"], fg=COL["text"],
                         font=("Segoe UI", 8, "bold")).place(relx=0.99, rely=0.5, anchor="e")
            tk.Label(r, text="%dP %dW %dF %dNA" % (row.get("pass", 0), row.get("warn", 0),
                                                   row.get("fail", 0), row.get("na", 0)),
                     bg=COL["panel"], fg=COL["muted"], font=("Consolas", 8), width=18,
                     anchor="e").pack(side="left")
        tk.Label(card, text="", bg=COL["panel"]).pack(pady=4)

        # ---- flags ------------------------------------------------------
        flags = g.get("flags") or []
        card2 = tk.Frame(parent, bg=COL["panel"], highlightthickness=1, highlightbackground=COL["line"])
        card2.pack(fill="x", pady=(0, 10))
        tk.Label(card2, text="PROBLEMS FOUND (%d)" % len(flags), bg=COL["panel"], fg=COL["text"],
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", padx=14, pady=(10, 4))
        if not flags:
            tk.Label(card2, text="No problems were found.", bg=COL["panel"], fg=COL["good"],
                     font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=(0, 10))
        for text, level in flags[:40]:
            colour = COL["bad"] if level == "fail" else COL["warn"]
            tk.Label(card2, text="\u25b8  " + text, bg=COL["panel"], fg=colour,
                     font=("Segoe UI", 10), anchor="w", justify="left",
                     wraplength=1100).pack(fill="x", padx=14, pady=1)
        tk.Label(card2, text="", bg=COL["panel"]).pack(pady=4)

        # ---- valuation lines ---------------------------------------------
        if v.get("rows"):
            card3 = tk.Frame(parent, bg=COL["panel"], highlightthickness=1,
                             highlightbackground=COL["line"])
            card3.pack(fill="x", pady=(0, 10))
            tk.Label(card3, text="HOW THE PRICE WAS CALCULATED  (model %s)" % v.get("model_version", "?"),
                     bg=COL["panel"], fg=COL["text"], font=("Segoe UI", 11, "bold"),
                     anchor="w").pack(fill="x", padx=14, pady=(10, 4))
            for row in v["rows"]:
                r = tk.Frame(card3, bg=COL["panel"])
                r.pack(fill="x", padx=14, pady=1)
                tk.Label(r, text=row["label"], bg=COL["panel"], fg=COL["text"],
                         font=("Segoe UI", 9), anchor="w", wraplength=820,
                         justify="left").pack(side="left", fill="x", expand=True)
                amt = row["amount"]
                if row["kind"] == "mult":
                    txt = "\u00d7 %.2f" % amt
                    col = COL["text"]
                elif row["kind"] == "pct":
                    txt = "%.0f%%" % (amt * 100)
                    col = COL["bad"] if amt < 0 else COL["good"]
                else:
                    txt = ("+" if amt >= 0 else "") + _inr(amt)
                    col = COL["bad"] if amt < 0 else (COL["good"] if amt else COL["muted"])
                tk.Label(r, text=txt, bg=COL["panel"], fg=col, font=("Consolas", 9, "bold"),
                         width=14, anchor="e").pack(side="right")
            tk.Label(card3, text="Edit pricing.json in %s to match your own street prices." % DATA_DIR,
                     bg=COL["panel"], fg=COL["muted"], font=("Segoe UI", 8),
                     anchor="w").pack(fill="x", padx=14, pady=(6, 10))

        # ---- recommendations ---------------------------------------------
        recs = report_mod.recommendations(self.scan)
        card4 = tk.Frame(parent, bg=COL["panel"], highlightthickness=1, highlightbackground=COL["line"])
        card4.pack(fill="x", pady=(0, 10))
        tk.Label(card4, text="WHAT TO DO BEFORE SELLING IT", bg=COL["panel"], fg=COL["text"],
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", padx=14, pady=(10, 4))
        for rec in recs:
            r = tk.Frame(card4, bg=COL["panel"])
            r.pack(fill="x", padx=14, pady=2)
            prio = {"SAFETY": COL["bad"], "MUST": "#fb923c", "SHOULD": COL["warn"],
                    "NICE": COL["good"]}.get(rec["priority"], COL["muted"])
            tk.Label(r, text=rec["priority"], bg=COL["panel"], fg=prio,
                     font=("Segoe UI", 8, "bold"), width=8, anchor="w").pack(side="left")
            tk.Label(r, text=rec["action"], bg=COL["panel"], fg=COL["text"],
                     font=("Segoe UI", 9), anchor="w", wraplength=820,
                     justify="left").pack(side="left", fill="x", expand=True)
            tk.Label(r, text=rec["cost"], bg=COL["panel"], fg=COL["muted"],
                     font=("Consolas", 9), width=18, anchor="e").pack(side="right")
        tk.Label(card4, text="", bg=COL["panel"]).pack(pady=4)

    # ========================================================== report page
    def _build_report_page(self) -> None:
        wrap = tk.Frame(self.tab_report, bg=COL["bg"])
        wrap.pack(fill="both", expand=True, padx=14, pady=10)
        rcv = tk.Canvas(wrap, bg=COL["bg"], highlightthickness=0)
        rsb = ttk.Scrollbar(wrap, orient="vertical", command=rcv.yview)
        rwrap = tk.Frame(rcv, bg=COL["bg"])
        rwrap.bind("<Configure>", lambda e: rcv.configure(scrollregion=rcv.bbox("all")))
        rwin = rcv.create_window((0, 0), window=rwrap, anchor="nw")
        rcv.configure(yscrollcommand=rsb.set)
        rcv.pack(side="left", fill="both", expand=True)
        rsb.pack(side="right", fill="y")

        def _rfit(_e=None, _cv=rcv, _win=rwin):
            _cv.itemconfigure(_win, width=max(800, _cv.winfo_width() - 22))
        rcv.bind("<Configure>", _rfit)
        self.root.after(120, _rfit)

        left = tk.LabelFrame(rwrap, text="  Shop branding (saved for every future report)  ",
                             bg=COL["bg"], fg=COL["text"], font=("Segoe UI", 10, "bold"))
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.brand_fields: Dict[str, Any] = {}
        fields = [("shop_name", "Shop name"), ("tagline", "Tagline shown under the name"),
                  ("address", "Address"), ("phone", "Phone"), ("email", "E-mail"),
                  ("website", "Website (used for the QR code)"), ("gst", "GSTIN"),
                  ("report_title", "Report title"), ("warranty_text", "Warranty line on the report"),
                  ("qr_url_template", "QR target URL, e.g. https://shop.example/verify/{scan_id}"),
                  ("footer_note", "Footer note (internal report)"),
                  ("customer_report_note", "Footer note (customer report)")]
        for key, label in fields:
            r = tk.Frame(left, bg=COL["bg"])
            r.pack(fill="x", padx=10, pady=2)
            tk.Label(r, text=label, bg=COL["bg"], fg=COL["muted"], font=("Segoe UI", 9),
                     width=34, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(self.branding.data.get(key, "")))
            ent = tk.Entry(r, textvariable=var, bg=COL["panel2"], fg=COL["text"], relief="flat",
                           insertbackground=COL["text"], font=("Segoe UI", 10))
            ent.pack(side="left", fill="x", expand=True, ipady=4)
            self.brand_fields[key] = var

        r = tk.Frame(left, bg=COL["bg"])
        r.pack(fill="x", padx=10, pady=6)
        tk.Label(r, text="Logo", bg=COL["bg"], fg=COL["muted"], font=("Segoe UI", 9),
                 width=34, anchor="w").pack(side="left")
        tk.Button(r, text="Choose image...", bg=COL["panel2"], fg=COL["text"], relief="flat",
                  font=("Segoe UI", 9), padx=10, cursor="hand2",
                  command=self.pick_logo).pack(side="left")
        tk.Button(r, text="Remove", bg=COL["panel2"], fg=COL["muted"], relief="flat",
                  font=("Segoe UI", 9), padx=10, cursor="hand2",
                  command=self.clear_logo).pack(side="left", padx=4)
        self.logo_lbl = tk.Label(r, text="", bg=COL["bg"], fg=COL["good"], font=("Segoe UI", 9))
        self.logo_lbl.pack(side="left", padx=6)

        r = tk.Frame(left, bg=COL["bg"])
        r.pack(fill="x", padx=10, pady=2)
        tk.Label(r, text="Brand colour", bg=COL["bg"], fg=COL["muted"], font=("Segoe UI", 9),
                 width=34, anchor="w").pack(side="left")
        self.colour_var = tk.StringVar(value=self.branding.data.get("primary", "#1b6ef3"))
        tk.Entry(r, textvariable=self.colour_var, bg=COL["panel2"], fg=COL["text"], relief="flat",
                 insertbackground=COL["text"], font=("Consolas", 10), width=12).pack(side="left", ipady=3)
        self.colour_preview = tk.Label(r, text="    ", bg=self.colour_var.get())
        self.colour_preview.pack(side="left", padx=6)

        r = tk.Frame(left, bg=COL["bg"])
        r.pack(fill="x", padx=10, pady=2)
        tk.Label(r, text="Technicians", bg=COL["bg"], fg=COL["muted"], font=("Segoe UI", 9),
                 width=34, anchor="w").pack(side="left")
        self.tech_var = tk.StringVar(value=", ".join(self.branding.data.get("technicians") or []))
        tk.Entry(r, textvariable=self.tech_var, bg=COL["panel2"], fg=COL["text"], relief="flat",
                 insertbackground=COL["text"], font=("Segoe UI", 10)).pack(side="left", fill="x",
                                                                          expand=True, ipady=4)

        btns = tk.Frame(left, bg=COL["bg"])
        btns.pack(fill="x", padx=10, pady=10)
        tk.Button(btns, text="Save branding", bg=COL["good"], fg="#06230f", relief="flat",
                  font=("Segoe UI", 10, "bold"), padx=14, pady=6, cursor="hand2",
                  command=self.save_branding).pack(side="left")
        tk.Button(btns, text="Preview branding on a sample report", bg=COL["panel2"],
                  fg=COL["text"], relief="flat", font=("Segoe UI", 10), padx=14, cursor="hand2",
                  command=lambda: self.load_demo(open_report=True)).pack(side="left", padx=6)

        right = tk.LabelFrame(rwrap, text="  Export this scan  ", bg=COL["bg"], fg=COL["text"],
                              font=("Segoe UI", 10, "bold"))
        right.pack(side="right", fill="both", padx=(8, 0))
        right.configure(width=430)
        tk.Label(right, text="Two reports are produced from the same scan:\n"
                             "\u2022 INTERNAL - faults, buy price, margin, repair estimates\n"
                             "\u2022 CUSTOMER - clean copy to print or WhatsApp to the buyer\n"
                             "Open either in a browser and press Ctrl+P to save as PDF.",
                 bg=COL["bg"], fg=COL["muted"], font=("Segoe UI", 10), anchor="w",
                 justify="left", wraplength=400).pack(fill="x", padx=12, pady=(10, 8))
        for text, cmd, colour in (("Generate INTERNAL report", lambda: self.export_report(False), COL["accent"]),
                                  ("Generate CUSTOMER report", lambda: self.export_report(True), COL["accent"]),
                                  ("Generate BOTH + JSON", lambda: self.export_report(None), COL["good"]),
                                  ("Open reports folder", self.open_reports_folder, COL["panel2"]),
                                  ("Open photos folder", self.open_photos_folder, COL["panel2"]),
                                  ("Open data folder", self.open_data_folder, COL["panel2"])):
            tk.Button(right, text=text, bg=colour, fg=("white" if colour != COL["panel2"] else COL["text"]),
                      relief="flat", font=("Segoe UI", 10, "bold"), padx=14, pady=7, cursor="hand2",
                      command=cmd).pack(fill="x", padx=12, pady=3)
        self.export_lbl = tk.Label(right, text="", bg=COL["bg"], fg=COL["good"],
                                   font=("Segoe UI", 9), anchor="w", justify="left", wraplength=390)
        self.export_lbl.pack(fill="x", padx=12, pady=8)

        tk.Label(right, text="Settings", bg=COL["bg"], fg=COL["text"],
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", padx=12, pady=(14, 2))
        self.setting_vars: Dict[str, Any] = {}
        for key, label, kind in (("stress_seconds", "CPU stress test length (seconds)", "int"),
                                 ("memtest_mb", "RAM test size (MB)", "int"),
                                 ("memtest_passes", "RAM test passes", "int"),
                                 ("camera_seconds", "Camera preview length (seconds)", "int"),
                                 ("camera_index", "Camera index (0 = first)", "int")):
            r = tk.Frame(right, bg=COL["bg"])
            r.pack(fill="x", padx=12, pady=2)
            tk.Label(r, text=label, bg=COL["bg"], fg=COL["muted"], font=("Segoe UI", 9),
                     width=32, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(self.settings.get(key)))
            tk.Entry(r, textvariable=var, bg=COL["panel2"], fg=COL["text"], relief="flat",
                     insertbackground=COL["text"], font=("Segoe UI", 9), width=7).pack(side="left", ipady=2)
            self.setting_vars[key] = (var, kind)
        for key, label in (("skip_stress", "Skip the CPU stress test"),
                           ("skip_memtest", "Skip the RAM pattern test"),
                           ("quick_mode", "Quick mode (shorter screen test)"),
                           ("auto_open_report", "Open the report automatically"),
                           ("debug", "Debug (show raw errors)")):
            r = tk.Frame(right, bg=COL["bg"])
            r.pack(fill="x", padx=12, pady=1)
            var = tk.BooleanVar(value=bool(self.settings.get(key)))
            tk.Checkbutton(r, text=label, variable=var, bg=COL["bg"], fg=COL["muted"],
                           selectcolor=COL["panel2"], activebackground=COL["bg"],
                           activeforeground=COL["text"], font=("Segoe UI", 9),
                           anchor="w").pack(side="left")
            self.setting_vars[key] = (var, "bool")
        tk.Button(right, text="Save settings", bg=COL["good"], fg="#06230f", relief="flat",
                  font=("Segoe UI", 10, "bold"), padx=14, pady=6, cursor="hand2",
                  command=self.save_settings).pack(fill="x", padx=12, pady=8)

    # ========================================================= history page
    def _build_history_page(self) -> None:
        top = tk.Frame(self.tab_history, bg=COL["bg"])
        top.pack(fill="x", padx=14, pady=10)
        tk.Label(top, text="Scan history (stored only on this computer)", bg=COL["bg"],
                 fg=COL["text"], font=("Segoe UI", 14, "bold")).pack(side="left")
        tk.Button(top, text="Refresh", bg=COL["panel2"], fg=COL["text"], relief="flat",
                  font=("Segoe UI", 9), padx=12, cursor="hand2",
                  command=self.refresh_history).pack(side="right", padx=4)
        tk.Button(top, text="Open folder", bg=COL["panel2"], fg=COL["text"], relief="flat",
                  font=("Segoe UI", 9), padx=12, cursor="hand2",
                  command=self.open_reports_folder).pack(side="right", padx=4)
        tk.Button(top, text="Export inventory CSV", bg=COL["panel2"], fg=COL["text"], relief="flat",
                  font=("Segoe UI", 9), padx=12, cursor="hand2",
                  command=self.export_csv).pack(side="right", padx=4)

        cols = ("scan_id", "date", "machine", "serial", "grade", "score", "price", "flags", "tech")
        self.tree = ttk.Treeview(self.tab_history, columns=cols, show="headings", height=20)
        widths = (130, 130, 250, 120, 60, 60, 100, 60, 120)
        for c, w in zip(cols, widths):
            self.tree.heading(c, text=c.replace("_", " ").title())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self.tree.bind("<Double-1>", lambda e: self.open_selected_scan())
        bottom = tk.Frame(self.tab_history, bg=COL["bg"])
        bottom.pack(fill="x", padx=14, pady=(0, 10))
        tk.Button(bottom, text="Open this scan", bg=COL["accent"], fg="white", relief="flat",
                  font=("Segoe UI", 10, "bold"), padx=14, pady=6, cursor="hand2",
                  command=self.open_selected_scan).pack(side="left")
        tk.Label(bottom, text="Double-click a row to re-open a finished scan (re-print the report, "
                              "re-check the price).", bg=COL["bg"], fg=COL["muted"],
                 font=("Segoe UI", 9)).pack(side="left", padx=12)

    def refresh_history(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.history_index: Dict[str, Dict[str, Any]] = {}
        for item in list_history():
            tags = ("demo",) if item.get("demo") else ()
            iid = self.tree.insert("", "end", values=(
                item["scan_id"], item["date"], item["machine"], item["serial"],
                item["grade"], item["score"], _inr(item["suggested"]) if item["suggested"] else "",
                item["flags"], item["technician"]), tags=tags)
            self.history_index[iid] = item
        try:
            self.tree.tag_configure("demo", foreground=COL["warn"])
        except Exception:
            pass

    def open_selected_scan(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Select a scan first.")
            return
        item = self.history_index.get(sel[0])
        if not item or not os.path.exists(item["file"]):
            messagebox.showerror(APP_NAME, "That record file is missing.")
            return
        data = load_json(item["file"])
        if not data:
            messagebox.showerror(APP_NAME, "That record could not be read.")
            return
        if not messagebox.askyesno(APP_NAME, "Load scan %s into the app?\nThe current in-progress scan "
                                             "will be replaced." % item["scan_id"]):
            return
        self.scan = Scan.from_dict(data)
        self._sync_rows_from_scan()
        self._refresh_guided()
        self._refresh_result()
        self._refresh_header()
        self.nb.select(self.tab_result)
        if self.scan.report_html and os.path.exists(self.scan.report_html):
            report_mod.open_in_browser(self.scan.report_html)

    # ================================================================= scan
    def start_scan(self) -> None:
        if self.scanning:
            return
        if not IS_WINDOWS:
            if not messagebox.askyesno(APP_NAME,
                                       "This is not Windows, so no real hardware data can be read.\n\n"
                                       "Load the DEMO scan instead to see the full report?"):
                return
            self.load_demo()
            return
        self.scanning = True
        self.bus.cancel.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        for cid, row in self.check_rows.items():
            row["dot"].configure(fg="#39424f")
            row["status"].configure(text="waiting", fg=COL["muted"])
            row["summary"].configure(text="")
            row["time"].configure(text="")
        self.progress.configure(value=0)
        self.prog_lbl.configure(text="0 / %d" % len(AUTO_IDS))
        self.emit_log("", None)
        self.emit_log("=== AUTOMATIC SCAN STARTED %s ===" % time.strftime("%H:%M:%S"), "hd")
        self.phase_lbl.configure(text="Scanning...")
        prevent_sleep(True)
        self.worker = threading.Thread(target=self._scan_worker, daemon=True)
        self.worker.start()

    def stop_scan(self) -> None:
        self.bus.request_cancel()
        self.emit_log("Stop requested - finishing the current check...", "warn")
        self.stop_btn.configure(state="disabled")

    def _scan_worker(self) -> None:
        """Runs on a background thread. All hardware access lives in collectors/."""
        import collectors
        ctx = collectors.build_ctx(bus=self.bus, settings=self.settings.as_dict(),
                                   scan_id=self.scan.id,
                                   emit=lambda m: self.bus.emit("log", text=m))
        ctx["admin"] = is_admin()
        self.scan.ctx = ctx
        ctx["signed_off"] = False
        collectors.full_scan(ctx, self.bus, results=self.scan.results)

    # ================================================================= poll
    def _poll(self) -> None:
        for kind, payload in self.bus.poll():
            if kind == "log":
                self.emit_log(payload.get("text", ""), payload.get("tag"))
            elif kind == "phase":
                self.phase_lbl.configure(text=payload.get("text", ""))
                self.status.configure(text=payload.get("text", ""))
            elif kind == "check_start":
                row = self.check_rows.get(payload["cid"])
                if row:
                    row["dot"].configure(fg=COL["accent"])
                    row["status"].configure(text="RUNNING...", fg=COL["accent"])
                    row["summary"].configure(text="")
                self._t0 = time.time()
            elif kind == "check_done":
                self._on_check_done(payload)
            elif kind == "probe_done":
                self.emit_log("Hardware inventory received: %d data groups." % payload.get("keys", 0), "ok")
            elif kind == "scan_done":
                self._on_scan_done()
            elif kind == "scan_failed":
                self.scanning = False
                self.start_btn.configure(state="normal")
                self.stop_btn.configure(state="disabled")
                messagebox.showerror(APP_NAME, "The scan stopped with an error:\n%s" % payload.get("error"))
        self.root.after(90, self._poll)

    def _on_check_done(self, p: Dict[str, Any]) -> None:
        cid = p["cid"]
        row = self.check_rows.get(cid)
        status = p.get("status", "unknown")
        colour = STATUS_COLOUR.get(status, COL["muted"])
        if row:
            row["dot"].configure(fg=colour)
            row["status"].configure(text=STATUS_TEXT.get(status, status.upper()), fg=colour)
            row["summary"].configure(text=(p.get("summary") or "")[:92],
                                     fg=COL["text"] if status in ("fail", "warn") else COL["muted"])
            row["time"].configure(text="%.1fs" % (p.get("seconds") or 0))
        self.progress.configure(value=100.0 * p.get("index", 0) / max(1, p.get("total", 1)))
        self.prog_lbl.configure(text="%d / %d" % (p.get("index", 0), p.get("total", 1)))
        tag = {"pass": "ok", "warn": "warn", "fail": "bad", "na": None,
               "skipped": "warn"}.get(status)
        text = "%-24s %-11s %s" % (self.scan.results[cid].name[:24] if cid in self.scan.results else cid,
                                   STATUS_TEXT.get(status, status.upper()),
                                   (p.get("summary") or "")[:78])
        self.emit_log(text, tag)
        res = self.scan.results.get(cid)
        if res is not None:
            for flag in res.flags[:3]:
                t = flag[0] if isinstance(flag, (list, tuple)) else str(flag)
                lvl = flag[1] if isinstance(flag, (list, tuple)) else "warn"
                self.emit_log("      ! %s" % t, "bad" if lvl == "fail" else "warn")
            if p.get("error"):
                self.emit_log("      error: %s" % p["error"], "bad")

    def _on_scan_done(self) -> None:
        self.scanning = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.phase_lbl.configure(text="Automatic scan finished - now run the guided tests.")
        self.progress.configure(value=100)
        self.emit_log("=== AUTOMATIC SCAN COMPLETE ===", "hd")
        na = [c for c in AUTO_IDS if self.scan.status_of(c) == "na"]
        if na:
            self.emit_log("Not fitted on this machine (not counted as faults): %s"
                          % ", ".join(self.scan.results[c].name for c in na), "warn")
        fails = [c for c in AUTO_IDS if self.scan.status_of(c) == "fail"]
        if fails:
            self.emit_log("FAILURES: %s" % ", ".join(self.scan.results[c].name for c in fails), "bad")
        self._recompute()
        self._refresh_guided()
        self._refresh_header()
        self.nb.select(self.tab_guided)
        self.status.configure(text="Automatic scan complete. %d checks done - now run the guided "
                                   "tests (page 2)." % len(self.scan.results))

    # =============================================================== guided
    def run_guided(self, cid: str) -> None:
        if self.scanning:
            messagebox.showinfo(APP_NAME, "Wait for the automatic scan to finish first.")
            return
        ctx = self.scan.ctx
        ctx.setdefault("settings", self.settings.as_dict())
        ctx["scan_id"] = self.scan.id
        ctx["emit"] = lambda m: self.emit_log("      [guided] %s" % m)
        res = self.scan.result(cid)
        res.status = "unknown"
        try:
            if cid in ("screen_colours", "screen_pixels", "screen_text"):
                # ONE colour pass fills all three display line items - the
                # technician never sits through the sequence three times.
                from guided.screen import run_screen_test, fan_out_screen, QUICK_SUBSET
                src = self.scan.result("screen_colours")
                subset = QUICK_SUBSET if self.settings.get("quick_mode") else None
                run_screen_test(self.root, src, emit=ctx["emit"], subset=subset)
                fan_out_screen(src, self.scan.result("screen_pixels"),
                               self.scan.result("screen_text"), emit=ctx["emit"])
                res = src
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
                res.skipped("No guided test implemented for %s" % cid)
        except Exception as exc:
            import traceback
            res.status = "skipped"
            res.error = "%s: %s" % (type(exc).__name__, exc)
            res.summary = "The test crashed"
            self.emit_log("Guided test '%s' crashed: %s" % (cid, exc), "bad")
            if self.settings.get("debug"):
                self.emit_log(traceback.format_exc(limit=8), "bad")
            messagebox.showerror(APP_NAME, "That test stopped with an error:\n\n%s\n\n"
                                           "It has been recorded as NOT TESTED so the report "
                                           "stays honest." % exc)
        self._recompute()
        self._refresh_guided()
        self._refresh_result()
        self._refresh_header()
        self.emit_log("Guided '%s' -> %s (%s)" % (cid, STATUS_TEXT.get(res.status, res.status),
                                                  res.summary[:70]),
                      {"pass": "ok", "fail": "bad", "warn": "warn"}.get(res.status))

    def mark_na(self, cid: str) -> None:
        if not messagebox.askyesno(APP_NAME, "Mark '%s' as NOT FITTED on this machine?\n\n"
                                             "Use this only when the component genuinely is not "
                                             "there (for example no camera). It is excluded from "
                                             "the score and recorded in the report."
                                   % GUIDED_LABELS.get(cid, (cid,))[0]):
            return
        res = self.scan.result(cid)
        res.na("Marked as not fitted by the technician")
        res.notes.append("The technician confirmed this component is not present on this machine.")
        self._recompute()
        self._refresh_guided()
        self._refresh_result()

    def _pending_and_flags(self):
        pending = []
        for cid, name, group, weight, tech in CHECKS:
            res = self.scan.results.get(cid)
            if res is None or res.status in ("missing", "skipped", "unknown"):
                pending.append(name)
        flags = []
        for res in self.scan.results.values():
            for f in res.flags:
                flags.append((f[0] if isinstance(f, (list, tuple)) else str(f),
                              f[1] if isinstance(f, (list, tuple)) else "warn"))
        return pending, flags

    # =============================================================== compute
    def _recompute(self) -> None:
        self.scan.grade = grade_all(self.scan.results, self.scan.ctx)
        try:
            self.scan.valuation = estimate(self.scan.results, self.scan.ctx, self.scan.grade,
                                           load_model())
        except Exception as exc:
            self.scan.valuation = {}
            self.emit_log("Valuation failed: %s" % exc, "bad")

    def _sync_rows_from_scan(self) -> None:
        for cid, row in self.check_rows.items():
            res = self.scan.results.get(cid)
            if res is None:
                continue
            colour = STATUS_COLOUR.get(res.status, COL["muted"])
            row["dot"].configure(fg=colour)
            row["status"].configure(text=STATUS_TEXT.get(res.status, res.status.upper()), fg=colour)
            row["summary"].configure(text=(res.summary or "")[:92], fg=COL["muted"])
            row["time"].configure(text="%.1fs" % (res.seconds or 0))
        done = sum(1 for c in AUTO_IDS if c in self.scan.results)
        self.progress.configure(value=100.0 * done / max(1, len(AUTO_IDS)))
        self.prog_lbl.configure(text="%d / %d" % (done, len(AUTO_IDS)))

    # ================================================================= demo
    def load_demo(self, open_report: bool = False) -> None:
        from app.demo import build_demo_scan
        if self.scanning:
            self.stop_scan()
        self.scan = build_demo_scan()
        self._recompute()
        self._sync_rows_from_scan()
        self._refresh_guided()
        self._refresh_result()
        self._refresh_header()
        self.emit_log("=== DEMO SCAN LOADED (simulated data) ===", "warn")
        self.phase_lbl.configure(text="DEMO data loaded - nothing was read from this machine.")
        self.nb.select(self.tab_result)
        if open_report:
            self.export_report(None, silent=True)

    def new_scan(self) -> None:
        if not messagebox.askyesno(APP_NAME, "Start a brand new scan?\nThe current one will be "
                                             "discarded (already-saved reports stay on disk)."):
            return
        if self.scanning:
            self.stop_scan()
        self.scan = Scan()
        self.bus.cancel.clear()
        for cid, row in self.check_rows.items():
            row["dot"].configure(fg="#39424f")
            row["status"].configure(text="waiting", fg=COL["muted"])
            row["summary"].configure(text="")
            row["time"].configure(text="")
        self._refresh_guided()
        self._refresh_result()
        self._refresh_header()
        self.progress.configure(value=0)
        self.prog_lbl.configure(text="0 / %d" % len(AUTO_IDS))
        self.nb.select(self.tab_scan)
        self.emit_log("New scan started: %s" % self.scan.id, "hd")

    # ============================================================== details
    def show_detail(self, cid: str) -> None:
        res = self.scan.results.get(cid)
        if res is None:
            messagebox.showinfo(APP_NAME, "This check has not run yet.")
            return
        top = tk.Toplevel(self.root)
        top.title("%s - %s" % (res.name, STATUS_TEXT.get(res.status, res.status)))
        top.configure(bg=COL["bg"])
        top.geometry("780x640")
        head = tk.Frame(top, bg=COL["panel2"])
        head.pack(fill="x")
        tk.Label(head, text=res.name, bg=COL["panel2"], fg=COL["text"],
                 font=("Segoe UI", 15, "bold"), anchor="w").pack(fill="x", padx=16, pady=(12, 0))
        colour = STATUS_COLOUR.get(res.status, COL["muted"])
        tk.Label(head, text="%s   |   score %s   |   %s   |   %.1fs"
                 % (STATUS_TEXT.get(res.status, res.status), res.score if res.score is not None else "-",
                    res.group, res.seconds or 0), bg=COL["panel2"], fg=colour,
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", padx=16, pady=(0, 10))
        txt = tk.Text(top, bg="#0b0e14", fg="#c9d5e6", relief="flat", font=("Consolas", 10),
                      wrap="word", padx=12, pady=10)
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        lines = [res.summary or ""]
        if res.error:
            lines.append("ERROR: " + res.error)
        for f in res.flags:
            t = f[0] if isinstance(f, (list, tuple)) else str(f)
            l = f[1] if isinstance(f, (list, tuple)) else "warn"
            lines.append("[%s] %s" % (l.upper(), t))
        lines.append("")
        for k, v in res.details.items():
            if v in ("", None):
                continue
            lines.append("%-46s %s" % (k, v))
        if res.marks:
            lines.append("")
            lines.append("--- technician markings ---")
            for m in res.marks:
                if isinstance(m, dict):
                    lines.append("%-40s %-8s %s" % (str(m.get("name", ""))[:40],
                                                    str(m.get("mark", "")).upper(),
                                                    m.get("note", "")))
        if res.notes:
            lines.append("")
            lines.append("--- notes ---")
            lines.extend(res.notes)
        if res.photos:
            lines.append("")
            lines.append("--- photos ---")
            lines.extend(os.path.basename(p) for p in res.photos)
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")
        # show photos if there are any
        if res.photos:
            pf = tk.Frame(top, bg=COL["bg"])
            pf.pack(fill="x", padx=10, pady=(0, 10))
            for p in res.photos[:3]:
                try:
                    img = tk.PhotoImage(file=p)
                    k = max(1, max(img.width() // 220, img.height() // 160))
                    if k > 1:
                        img = img.subsample(k, k)
                    lbl = tk.Label(pf, image=img, bg=COL["bg"])
                    lbl.image = img
                    lbl.pack(side="left", padx=4)
                except Exception:
                    pass

    # ================================================================ export
    def export_report(self, customer: Optional[bool], silent: bool = False) -> None:
        if not self.scan.results:
            messagebox.showinfo(APP_NAME, "Nothing to export yet - run a scan first.")
            return
        self.save_branding(silent=True)
        self._recompute()
        self.scan.finished = time.time()
        try:
            paths = report_mod.write_reports(self.scan, self.branding, folder=REPORTS_DIR)
        except Exception as exc:
            messagebox.showerror(APP_NAME, "The report could not be written:\n%s" % exc)
            return
        self.export_lbl.configure(text="Saved:\n%s\n%s\n%s" % (
            os.path.basename(paths["internal"]), os.path.basename(paths["customer"]),
            os.path.basename(paths["json"])), fg=COL["good"])
        self.emit_log("Report written: %s" % paths["internal"], "ok")
        self.refresh_history()
        if not silent and self.settings.get("auto_open_report"):
            target = paths["customer"] if customer is True else paths["internal"]
            report_mod.open_in_browser(target)
        if not silent:
            messagebox.showinfo(APP_NAME, "Report saved.\n\nInternal: %s\nCustomer: %s\n\n"
                                          "Open it in a browser and press Ctrl+P to save as PDF."
                                % (paths["internal"], paths["customer"]))

    def save_branding(self, silent: bool = False) -> None:
        for key, var in self.brand_fields.items():
            self.branding.set(key, var.get())
        self.branding.set("primary", self.colour_var.get().strip() or "#1b6ef3")
        techs = [t.strip() for t in self.tech_var.get().split(",") if t.strip()]
        self.branding.set("technicians", techs or ["Technician 1"])
        self.branding.save()
        try:
            self.colour_preview.configure(bg=self.branding.get("primary"))
        except Exception:
            pass
        if not silent:
            self.export_lbl.configure(text="Branding saved to %s" % os.path.basename(
                os.path.join(DATA_DIR, "branding.json")), fg=COL["good"])
            messagebox.showinfo(APP_NAME, "Branding saved. Every future report will use it.")

    def save_settings(self) -> None:
        for key, (var, kind) in self.setting_vars.items():
            try:
                if kind == "int":
                    self.settings.set(key, int(float(var.get())))
                elif kind == "bool":
                    self.settings.set(key, bool(var.get()))
                else:
                    self.settings.set(key, var.get())
            except Exception:
                pass
        self.settings.save()
        messagebox.showinfo(APP_NAME, "Settings saved.")

    def pick_logo(self) -> None:
        path = filedialog.askopenfilename(title="Choose a logo image",
                                          filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp"),
                                                     ("All files", "*.*")])
        if not path:
            return
        saved = self.branding.set_logo_from_file(path)
        self.logo_lbl.configure(text=("saved: %s" % os.path.basename(saved)) if saved
                                else "could not copy that file",
                                fg=COL["good"] if saved else COL["bad"])

    def clear_logo(self) -> None:
        self.branding.clear_logo()
        self.logo_lbl.configure(text="logo removed", fg=COL["muted"])

    # ================================================================= misc
    def _elevate(self) -> None:
        if relaunch_as_admin():
            self.emit_log("Relaunching as Administrator...", "warn")
            self.root.after(600, self.root.destroy)
        else:
            messagebox.showerror(APP_NAME, "Could not elevate. Right-click the .exe and choose "
                                           "'Run as administrator'.")

    def open_reports_folder(self) -> None:
        _open_folder(REPORTS_DIR)

    def open_photos_folder(self) -> None:
        _open_folder(PHOTOS_DIR)

    def open_data_folder(self) -> None:
        _open_folder(DATA_DIR)

    def export_csv(self) -> None:
        path = os.path.join(DATA_DIR, "inventory.csv")
        if os.path.exists(path):
            _open_folder(DATA_DIR)
        else:
            messagebox.showinfo(APP_NAME, "No inventory yet - it is created after the first "
                                          "exported report.")

    def emit_log(self, text: str, tag: Optional[str] = None) -> None:
        try:
            stamp = time.strftime("%H:%M:%S")
            self.log.insert("end", "%s  %s\n" % (stamp, text), tag or ())
            self.log.see("end")
            if int(self.log.index("end-1c").split(".")[0]) > 4000:
                self.log.delete("1.0", "500.0")
        except Exception:
            pass


GRADE_HEX = {"A+": "#0f9d58", "A": "#0f9d58", "B": "#3f8cff", "C": "#e08600",
             "D": "#e05a00", "E": "#c62828"}


def _inr(value: Any) -> str:
    from app.core import fmt_inr
    if value in (None, ""):
        return "-"
    return fmt_inr(value)


def score_colour(score: float) -> str:
    if score >= 88:
        return COL["good"]
    if score >= 68:
        return COL["warn"]
    if score >= 45:
        return "#fb923c"
    return COL["bad"]


def _open_folder(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
        if IS_WINDOWS:
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            os.system('xdg-open "%s" >/dev/null 2>&1 &' % path)
    except Exception:
        pass


def main(demo: bool = False) -> int:
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)      # crisp text on HiDPI screens
    except Exception:
        pass
    app = App(root, demo=demo)
    app.refresh_history()
    root.mainloop()
    return 0
