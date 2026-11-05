"""
screen.py -- the guided DISPLAY test.

Exactly what the shop asked for: the screen shows pure colours one by one
(black, white, red, green, blue, ...) full-screen, the technician looks at the
real panel and marks each one GOOD / OKAY / BAD, with an optional note.

Nothing is guessed by software here - a dead pixel, backlight bleed or a
pressure mark can only be judged by a human eye, so the tool makes that
judgement fast, consistent and recorded.

Interface rules:
  * real full-screen (taskbar covered), so edge bleed can be seen
  * the control panel AUTO-HIDES after a few seconds so it never hides a defect
  * move the mouse or press any key to bring the panel back
  * keyboard: 1/G good, 2/O okay, 3/B bad, 4/S skip, Space next, Back back,
    Esc end the test
  * every screen that was not marked is recorded as "not inspected" so nobody
    can skip the awkward ones silently
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import font as tkfont
from typing import Any, Callable, Dict, List, Optional

from app.core import CheckResult, IS_WINDOWS, prevent_sleep
from app import png

MARK_SCORE = {"good": 100.0, "okay": 72.0, "bad": 12.0}

SCREENS: List[Dict[str, Any]] = [
    {"id": "white",   "name": "PURE WHITE",   "kind": "solid", "rgb": (255, 255, 255),
     "look": "Dark or dead pixels, dirt under the glass, yellow/dark patches, uneven brightness"},
    {"id": "black",   "name": "PURE BLACK",   "kind": "solid", "rgb": (0, 0, 0),
     "look": "Backlight bleed (white glow at the edges/corners), stuck bright pixels, uneven black"},
    {"id": "red",     "name": "PURE RED",     "kind": "solid", "rgb": (255, 0, 0),
     "look": "Dead sub-pixels (black dots), colour tint, patches, scratches visible on red"},
    {"id": "green",   "name": "PURE GREEN",   "kind": "solid", "rgb": (0, 255, 0),
     "look": "Dead sub-pixels, uniformity, any dark spots or lines"},
    {"id": "blue",    "name": "PURE BLUE",    "kind": "solid", "rgb": (0, 0, 255),
     "look": "Dead sub-pixels, uniformity, faint lines or bright spots"},
    {"id": "yellow",  "name": "YELLOW",       "kind": "solid", "rgb": (255, 255, 0),
     "look": "Uniformity and dead pixels (yellow shows pressure marks well)"},
    {"id": "cyan",    "name": "CYAN",         "kind": "solid", "rgb": (0, 255, 255),
     "look": "Uniformity and dead pixels"},
    {"id": "magenta", "name": "MAGENTA",      "kind": "solid", "rgb": (255, 0, 255),
     "look": "Uniformity and dead pixels"},
    {"id": "gray50",  "name": "MID GREY 50%", "kind": "solid", "rgb": (128, 128, 128),
     "look": "Pressure marks, dents, dirt, clouding - all easiest to see on mid grey"},
    {"id": "gray85",  "name": "LIGHT GREY",   "kind": "solid", "rgb": (217, 217, 217),
     "look": "Bright-spot uniformity, dust, coating damage"},
    {"id": "gray15",  "name": "DARK GREY",    "kind": "solid", "rgb": (38, 38, 38),
     "look": "Backlight uniformity without going fully black"},
    {"id": "hgrad",   "name": "HORIZONTAL GRADIENT", "kind": "hgrad",
     "look": "Colour banding (visible stripes), smoothness of the fade"},
    {"id": "vgrad",   "name": "VERTICAL GRADIENT", "kind": "vgrad",
     "look": "Vertical banding, uneven backlight from top to bottom"},
    {"id": "steps",   "name": "GREY STEPS",   "kind": "steps",
     "look": "Can you see every step? Missing steps = bad gamma / banding"},
    {"id": "checker", "name": "CHECKERBOARD", "kind": "checker", "cell": 28,
     "look": "Moiré patterns, uneven squares, panel damage, dead zones"},
    {"id": "px1v",    "name": "1-PIXEL VERTICAL LINES", "kind": "stripes", "vertical": True,
     "look": "Sharpness - lines must be crisp, not blurred or shimmering (scaling problem)"},
    {"id": "px1h",    "name": "1-PIXEL HORIZONTAL LINES", "kind": "stripes", "vertical": False,
     "look": "Sharpness and any horizontal line defects"},
    {"id": "grid",    "name": "GEOMETRY GRID", "kind": "grid",
     "look": "Straight lines must be straight, corners square, centre crosshair centred"},
    {"id": "bleed",   "name": "BACKLIGHT BLEED TEST", "kind": "corners",
     "look": "Look at the four corners: any light leaking through the bezel?"},
    {"id": "text",    "name": "TEXT SHARPNESS", "kind": "text",
     "look": "Text must be crisp at every size - blur means wrong resolution or a failing panel"},
]


def _build_image(spec: Dict[str, Any], w: int, h: int) -> Optional[bytes]:
    kind = spec["kind"]
    try:
        if kind == "hgrad":
            return png.h_gradient(w, h, (0, 0, 0), (255, 255, 255))
        if kind == "vgrad":
            return png.v_gradient(w, h, (0, 0, 0), (255, 255, 255))
        if kind == "steps":
            return png.gray_steps(w, h, 16)
        if kind == "checker":
            return png.checkerboard(w, h, spec.get("cell", 28))
        if kind == "stripes":
            return png.stripes(w, h, 1, (255, 255, 255), (0, 0, 0), spec.get("vertical", True))
        if kind == "grid":
            return png.grid(w, h, max(60, w // 24))
        if kind == "corners":
            return png.corner_boxes(w, h, (0, 0, 0), (38, 38, 38), max(120, w // 12))
    except Exception:
        return None
    return None


class ScreenTest:
    """Runs the whole colour sequence. Call .run() from the Tk main thread."""

    def __init__(self, master: tk.Misc, res: CheckResult,
                 emit: Optional[Callable[[str], None]] = None,
                 subset: Optional[List[str]] = None) -> None:
        self.master = master
        self.res = res
        self.emit = emit
        self.screens = [s for s in SCREENS if not subset or s["id"] in subset]
        self.idx = 0
        self.marks: List[Dict[str, Any]] = []
        self.done = False
        self.top: Optional[tk.Toplevel] = None
        self.canvas: Optional[tk.Canvas] = None
        self.panel: Optional[tk.Frame] = None
        self.photo: Optional[tk.PhotoImage] = None
        self.hide_job: Optional[str] = None
        self.panel_visible = False
        self.text_items: List[int] = []
        self.w = 0
        self.h = 0
        self.started = time.time()

    # ------------------------------------------------------------------ run
    def run(self) -> CheckResult:
        self.master.withdraw()
        prevent_sleep(True)
        self.top = tk.Toplevel(self.master)
        self.top.configure(bg="black", cursor="none")
        # Bullet-proof fullscreen. "-fullscreen" silently does nothing on some
        # graphics drivers, which left the test in a corner window. A borderless
        # window pinned to 0,0 at exactly the screen size always covers it.
        self.top.overrideredirect(True)
        self.top.update_idletasks()
        from app.core import screen_size
        self.w, self.h = screen_size()
        if not self.w:
            self.w = self.top.winfo_screenwidth()
            self.h = self.top.winfo_screenheight()
        self.top.geometry("%dx%d+0+0" % (self.w, self.h))
        try:
            self.top.attributes("-topmost", True)
        except Exception:
            pass
        self.top.update()
        self.canvas = tk.Canvas(self.top, width=self.w, height=self.h,
                                bg="black", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.top.bind("<Configure>", self._on_resize)
        self._build_panel()

        self.top.bind("<Key>", self._on_key)
        self.top.bind("<Motion>", self._show_panel)
        self.top.bind("<Button-1>", self._show_panel)
        self.top.protocol("WM_DELETE_WINDOW", self._finish)
        self.top.focus_force()

        if self.emit:
            self.emit("Display test started: %d screens, %dx%d" % (len(self.screens), self.w, self.h))
        self._show_screen()
        self.master.wait_window(self.top)

        prevent_sleep(False)
        self.master.deiconify()
        self.master.lift()
        self._write_result()
        return self.res

    def _on_resize(self, event: Any) -> None:
        """Re-render the pattern if the window size changes (dpi / monitor)."""
        if event.widget is not self.top or self.done:
            return
        if abs(event.width - self.w) > 2 or abs(event.height - self.h) > 2:
            self.w, self.h = event.width, event.height
            try:
                self.canvas.configure(width=self.w, height=self.h)
                self._show_screen()
            except Exception:
                pass

    # ---------------------------------------------------------------- panel
    def _build_panel(self) -> None:
        self.panel = tk.Frame(self.top, bg="#111318", highlightthickness=1,
                              highlightbackground="#3a4150")
        self.title = tk.Label(self.panel, text="", bg="#111318", fg="#ffffff",
                              font=("Segoe UI", 15, "bold"), anchor="w")
        self.title.pack(fill="x", padx=14, pady=(10, 0))
        self.look = tk.Label(self.panel, text="", bg="#111318", fg="#c9d3e3",
                             font=("Segoe UI", 10), anchor="w", justify="left",
                             wraplength=max(420, self.w - 80))
        self.look.pack(fill="x", padx=14, pady=(2, 6))

        row = tk.Frame(self.panel, bg="#111318")
        row.pack(fill="x", padx=14, pady=(0, 8))
        self.mark_label = tk.Label(row, text="Not marked", bg="#111318", fg="#8b93a5",
                                   font=("Segoe UI", 10, "bold"), width=14, anchor="w")
        self.mark_label.pack(side="left")
        for text, key, colour in (("GOOD  (1)", "good", "#1f9d55"),
                                  ("OKAY  (2)", "okay", "#c9a227"),
                                  ("BAD  (3)", "bad", "#d0453b"),
                                  ("SKIP  (4)", "skip", "#5b6472")):
            b = tk.Button(row, text=text, bg=colour, fg="white", relief="flat",
                          activeforeground="white", activebackground=colour,
                          font=("Segoe UI", 10, "bold"), padx=14, pady=5,
                          cursor="hand2",
                          command=lambda k=key: self._mark(k))
            b.pack(side="left", padx=4)

        row2 = tk.Frame(self.panel, bg="#111318")
        row2.pack(fill="x", padx=14, pady=(0, 10))
        tk.Label(row2, text="Note:", bg="#111318", fg="#8b93a5",
                 font=("Segoe UI", 9)).pack(side="left")
        self.note = tk.Entry(row2, bg="#1c2029", fg="#e8edf5", relief="flat",
                             insertbackground="#e8edf5", font=("Segoe UI", 9), width=52)
        self.note.pack(side="left", padx=6, ipady=3)
        tk.Button(row2, text="Next  (Space)", bg="#2f6feb", fg="white", relief="flat",
                  font=("Segoe UI", 9, "bold"), padx=10, cursor="hand2",
                  command=self._next).pack(side="left", padx=3)
        tk.Button(row2, text="Back", bg="#2a2f3a", fg="#dfe5ef", relief="flat",
                  font=("Segoe UI", 9), padx=8, cursor="hand2",
                  command=self._prev).pack(side="left", padx=3)
        tk.Button(row2, text="End test (Esc)", bg="#2a2f3a", fg="#dfe5ef", relief="flat",
                  font=("Segoe UI", 9), padx=8, cursor="hand2",
                  command=self._finish).pack(side="left", padx=3)
        self.counter = tk.Label(self.panel, text="", bg="#111318", fg="#6f7889",
                                font=("Segoe UI", 8), anchor="e")
        self.counter.pack(fill="x", padx=14, pady=(0, 6))

    def _place_panel(self) -> None:
        if not self.panel:
            return
        self.panel.place(relx=0.5, rely=1.0, anchor="s", y=-16)
        self.panel_visible = True
        if self.hide_job:
            try:
                self.top.after_cancel(self.hide_job)
            except Exception:
                pass
        self.hide_job = self.top.after(6000, self._hide_panel)

    def _hide_panel(self) -> None:
        if self.panel and self.panel_visible:
            self.panel.place_forget()
            self.panel_visible = False
        self.hide_job = None

    def _show_panel(self, event: Any = None) -> None:
        if event is not None:
            # ignore the synthetic motion events that placing the panel causes
            pass
        if not self.panel_visible:
            self._place_panel()
            try:
                self.top.configure(cursor="")
            except Exception:
                pass
        else:
            if self.hide_job:
                try:
                    self.top.after_cancel(self.hide_job)
                except Exception:
                    pass
            self.hide_job = self.top.after(6000, self._hide_panel)

    # --------------------------------------------------------------- screens
    def _clear(self) -> None:
        self.photo = None
        for item in self.text_items:
            try:
                self.canvas.delete(item)
            except Exception:
                pass
        self.text_items = []
        try:
            self.canvas.delete("img")
        except Exception:
            pass

    def _show_screen(self) -> None:
        if not self.screens:
            self._finish()
            return
        self.idx = max(0, min(self.idx, len(self.screens) - 1))
        spec = self.screens[self.idx]
        self._clear()
        kind = spec["kind"]
        if kind == "solid":
            self.canvas.configure(bg="#%02x%02x%02x" % spec["rgb"])
        elif kind == "text":
            self.canvas.configure(bg="white")
            self._draw_text()
        else:
            data = _build_image(spec, self.w, self.h)
            if data is None:
                self.canvas.configure(bg="#%02x%02x%02x" % spec.get("rgb", (128, 128, 128)))
            else:
                try:
                    self.photo = tk.PhotoImage(
                        data=png.png_b64(self.w, self.h, data))
                    self.canvas.create_image(0, 0, image=self.photo, anchor="nw", tags="img")
                except Exception as exc:
                    self.canvas.configure(bg="#404040")
                    self.text_items.append(self.canvas.create_text(
                        self.w // 2, self.h // 2, fill="white",
                        text="Pattern could not be drawn: %s" % exc))
        prev = self._mark_for(spec["id"])
        self.title.configure(text="%d/%d  %s" % (self.idx + 1, len(self.screens), spec["name"]))
        self.look.configure(text="Look for: " + spec["look"])
        self.counter.configure(text="Move the mouse to show this panel again  |  1 good  2 okay  3 bad  4 skip  "
                                    "Space next  Esc finish")
        self._set_mark_label(prev)
        self._place_panel()
        try:
            self.top.lift()
            self.top.focus_force()
        except Exception:
            pass

    def _draw_text(self) -> None:
        w, h = self.w, self.h
        lines = [
            ("Segoe UI", int(h * 0.055), "bold", "Laptop Check Pro - text sharpness test"),
            ("Segoe UI", int(h * 0.030), "normal", "The quick brown fox jumps over the lazy dog. 0123456789"),
            ("Consolas", int(h * 0.022), "normal", "ABCDEFGHIJKLMNOPQRSTUVWXYZ  abcdefghijklmnopqrstuvwxyz"),
            ("Segoe UI", int(h * 0.016), "normal", "1234567890 !@#$%^&*()_+-=[]{};':\",./<>?  |\\  ~`"),
            ("Segoe UI", int(h * 0.012), "normal",
             "Tiny text line - if this is blurry the panel is not running at its native resolution."),
            ("Segoe UI", int(h * 0.010), "normal",
             "Even tinier text - check the very edges of the screen for focus and colour fringing."),
        ]
        y = int(h * 0.10)
        for family, size, weight, text in lines:
            try:
                f = tkfont.Font(family=family, size=max(6, size), weight=weight)
            except Exception:
                f = tkfont.Font(size=max(6, size), weight=weight)
            self.text_items.append(self.canvas.create_text(w // 2, y, text=text, fill="black", font=f))
            y += int(size * 1.9) + 6
        # fine detail block
        for i in range(60):
            x0 = int(w * 0.12) + i * max(2, int(w * 0.006))
            self.text_items.append(self.canvas.create_line(
                x0, int(h * 0.80), x0, int(h * 0.90), fill="black", width=1))
        self.text_items.append(self.canvas.create_text(
            w // 2, int(h * 0.95), fill="#333333",
            text="Vertical hairlines: they must stay separate and evenly spaced, never merge or shimmer"))

    # ---------------------------------------------------------------- marks
    def _mark_for(self, sid: str) -> Optional[Dict[str, Any]]:
        for m in self.marks:
            if m["id"] == sid:
                return m
        return None

    def _set_mark_label(self, prev: Optional[Dict[str, Any]]) -> None:
        if not prev:
            self.mark_label.configure(text="Not marked", fg="#8b93a5")
            return
        colour = {"good": "#4ade80", "okay": "#fbbf24", "bad": "#f87171",
                  "skip": "#94a3b8"}.get(prev["mark"], "#8b93a5")
        self.mark_label.configure(text="Marked: " + prev["mark"].upper(), fg=colour)

    def _mark(self, mark: str) -> None:
        spec = self.screens[self.idx]
        note = ""
        try:
            note = self.note.get().strip()
            self.note.delete(0, "end")
        except Exception:
            pass
        entry = {"id": spec["id"], "name": spec["name"], "mark": mark,
                 "note": note, "ts": time.strftime("%H:%M:%S")}
        found = False
        for i, m in enumerate(self.marks):
            if m["id"] == spec["id"]:
                self.marks[i] = entry
                found = True
                break
        if not found:
            self.marks.append(entry)
        self._set_mark_label(entry)
        if self.emit:
            self.emit("Display '%s' marked %s%s" % (spec["name"], mark.upper(),
                                                    (" - " + note) if note else ""))
        if mark == "skip":
            self.top.after(250, self._next)

    def _next(self) -> None:
        if self.idx >= len(self.screens) - 1:
            self._finish()
            return
        self.idx += 1
        self._show_screen()

    def _prev(self) -> None:
        if self.idx > 0:
            self.idx -= 1
            self._show_screen()

    def _on_key(self, event: Any) -> None:
        # let the note entry take normal typing
        try:
            if self.panel_visible and self.top.focus_get() is self.note:
                if event.keysym == "Escape":
                    self._finish()
                return
        except Exception:
            pass
        k = (event.keysym or "").lower()
        ch = (event.char or "").lower()
        self._show_panel()
        if k in ("escape",):
            self._finish()
        elif k in ("space", "right", "return", "next"):
            self._next()
        elif k in ("left", "backspace", "prior"):
            self._prev()
        elif k in ("g",) or ch == "1":
            self._mark("good")
        elif k in ("o",) or ch == "2":
            self._mark("okay")
        elif k in ("b",) or ch == "3":
            self._mark("bad")
        elif k in ("s",) or ch == "4":
            self._mark("skip")

    def _finish(self) -> None:
        if self.done:
            return
        self.done = True
        try:
            self.top.destroy()
        except Exception:
            pass

    # --------------------------------------------------------------- result
    def _write_result(self) -> None:
        res = self.res
        res.marks = self.marks
        res.tech = "guided"
        seen = {m["id"]: m for m in self.marks}
        good = sum(1 for m in self.marks if m["mark"] == "good")
        okay = sum(1 for m in self.marks if m["mark"] == "okay")
        bad = [m for m in self.marks if m["mark"] == "bad"]
        skipped = sum(1 for m in self.marks if m["mark"] == "skip")
        missing = [s for s in self.screens if s["id"] not in seen]

        res.metric("Screens shown", len(self.screens))
        res.metric("Marked good", good)
        res.metric("Marked okay", okay)
        res.metric("Marked bad", len(bad))
        res.metric("Skipped", skipped)
        res.metric("Not inspected", len(missing))
        res.metric("Screen resolution used", "%dx%d" % (self.w, self.h))
        res.metric("Test duration", "%.0f s" % (time.time() - self.started))

        for m in self.marks:
            res.metric("  %s" % m["name"], "%s%s" % (
                m["mark"].upper(), (" - " + m["note"]) if m.get("note") else ""))
        for s in missing:
            res.metric("  %s" % s["name"], "NOT INSPECTED")

        if missing:
            res.flag("%d display test screen(s) were never inspected (%s)"
                     % (len(missing), ", ".join(s["name"] for s in missing[:6])), "warn")
        for m in bad:
            res.flag("Display defect on %s%s" % (m["name"], (": " + m["note"]) if m.get("note") else ""), "fail")

        scores = [MARK_SCORE[m["mark"]] for m in self.marks if m["mark"] in MARK_SCORE]
        if not scores:
            res.skipped("Display test not completed - no screen was marked")
            return
        score = sum(scores) / len(scores)
        if missing:
            score -= 4 * len(missing)
        if bad:
            score = min(score, 35.0)
        res.set("pass" if score >= 88 else ("warn" if score >= 55 else "fail"),
                "%d good / %d okay / %d bad out of %d screens"
                % (good, okay, len(bad), len(self.screens)), score=score)


def run_screen_test(master: tk.Misc, res: CheckResult,
                    emit: Optional[Callable[[str], None]] = None,
                    subset: Optional[List[str]] = None) -> CheckResult:
    return ScreenTest(master, res, emit=emit, subset=subset).run()


# ---------------------------------------------------------------------------
# One pass, three report items.
#
# The report tracks display quality as three separate line items (colour
# uniformity, dead pixels / backlight bleed, text and geometry sharpness)
# because they fail for different reasons and cost different amounts to fix.
# The technician should NOT have to sit through the colour sequence three
# times, so a single pass is split across all three afterwards.
# ---------------------------------------------------------------------------

PIXEL_IDS = ["white", "black", "red", "green", "blue", "yellow", "cyan", "magenta",
             "gray50", "gray85", "gray15", "hgrad", "vgrad", "steps", "bleed", "checker"]
TEXT_IDS = ["text", "grid", "px1v", "px1h"]
QUICK_SUBSET = ["white", "black", "red", "green", "blue", "gray50", "hgrad",
                "steps", "bleed", "grid", "text"]


def fan_out_screen(src: CheckResult, pixels: CheckResult, text: CheckResult,
                   emit: Optional[Callable[[str], None]] = None) -> None:
    """Derive the pixel and text results from one completed colour pass."""
    names = {s["id"]: s["name"] for s in SCREENS}
    for target, ids, label in ((pixels, PIXEL_IDS, "Dead pixel / backlight bleed"),
                               (text, TEXT_IDS, "Text and geometry sharpness")):
        marks = [m for m in (src.marks or []) if m.get("id") in ids]
        target.tech = "guided"
        target.seconds = round(src.seconds or 0.0, 1)
        target.marks = marks
        target.details["Derived from"] = ("the single display pass (%d screen(s) marked)"
                                          % len(src.marks or []))
        target.notes.append("Marked during the same display pass as the colour test, so the "
                            "technician only looked at the panel once.")
        if not marks:
            target.skipped("Those screens were not part of the display pass that was run")
            if emit:
                emit("%s: no screens covered - run the full display test" % label)
            continue
        bad = [m for m in marks if m.get("mark") == "bad"]
        good = sum(1 for m in marks if m.get("mark") == "good")
        okay = sum(1 for m in marks if m.get("mark") == "okay")
        missing = [i for i in ids if i not in {m.get("id") for m in marks}]
        scores = [MARK_SCORE[m["mark"]] for m in marks if m.get("mark") in MARK_SCORE]
        score = (sum(scores) / len(scores)) if scores else 0.0
        if missing:
            score -= 4 * len(missing)
            target.flag("%d %s screen(s) were never inspected (%s)"
                        % (len(missing), label.lower(),
                           ", ".join(names.get(i, i) for i in missing[:5])), "warn")
        for m in bad:
            target.flag("%s defect on %s%s" % (label, names.get(m["id"], m["id"]),
                                               (": " + m["note"]) if m.get("note") else ""), "fail")
        if bad:
            score = min(score, 35.0)
        target.metric("Screens judged", len(marks))
        target.metric("Good / Okay / Bad", "%d / %d / %d" % (good, okay, len(bad)))
        for m in marks:
            target.metric("  %s" % names.get(m["id"], m["id"]),
                          "%s%s" % (str(m.get("mark", "")).upper(),
                                    (" - " + m["note"]) if m.get("note") else ""))
        target.set("pass" if score >= 88 else ("warn" if score >= 55 else "fail"),
                   "%s: %d good / %d okay / %d bad (%d screen(s))"
                   % (label, good, okay, len(bad), len(marks)), score=score)
        if emit:
            emit("%s -> %s (%.0f/100)" % (label, target.status.upper(), score))
