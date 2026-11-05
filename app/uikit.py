"""Small professional UI kit: open-source icons (Feather ISC / Tabler MIT,
pre-rasterised to PNG by tools/make_icons.py), a real progress bar, status
cards and a busy-overlay so the user always sees the tool working.

Tkinter can only load PNG/GIF, and the app must stay standard-library at
runtime, so the icons ship as committed PNGs inside the exe.
"""
from __future__ import annotations

import os
import tkinter as tk
from typing import Any, Dict, Optional

_CACHE: Dict[str, "tk.PhotoImage"] = {}

_ICON_DIR_CANDIDATES = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "icons"),
    os.path.join(getattr(__import__("sys"), "_MEIPASS", ""), "assets", "icons"),
)


def icon_dir() -> str:
    for c in _ICON_DIR_CANDIDATES:
        if c and os.path.isdir(c):
            return c
    return _ICON_DIR_CANDIDATES[0]


def icon(name: str, colour: str = "white") -> Optional["tk.PhotoImage"]:
    """48 px PhotoImage for an icon name ('check-circle', 'wifi', ...)."""
    key = "%s|%s" % (name, colour)
    if key in _CACHE:
        return _CACHE[key]
    path = os.path.join(icon_dir(), "%s_%s.png" % (name, colour))
    if not os.path.exists(path):
        path = os.path.join(icon_dir(), "%s_white.png" % name)
    if not os.path.exists(path):
        return None
    try:
        img = tk.PhotoImage(file=path)
    except Exception:
        return None
    _CACHE[key] = img
    return img


def icon_label(parent: tk.Misc, name: str, colour: str = "white",
               bg: Optional[str] = None, size: int = 48) -> tk.Label:
    """A label showing one icon; keeps the PhotoImage reference alive."""
    img = icon(name, colour)
    lab = tk.Label(parent, bg=bg or (parent["bg"] if "bg" in parent.keys() else "#0f1218"),
                   image=img if img else "", text="" if img else "",
                   width=0 if img else 0, height=0 if img else 0)
    lab.image = img          # type: ignore[attr-defined]
    if img and size != 48:
        lab.configure(image=img)
    return lab


class ProgressBar(tk.Canvas):
    """Determinate professional progress bar: track, smooth fill, caption."""

    def __init__(self, parent: tk.Misc, width: int = 900, height: int = 26,
                 fg: str = "#2f6feb", track: str = "#1e2430",
                 bg: str = "#0f1218", caption: str = "") -> None:
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self._w, self._h = width, height
        self.fg, self.track = fg, track
        self._frac = 0.0
        self._caption = caption
        self._draw()

    def set(self, frac: float, caption: Optional[str] = None) -> None:
        self._frac = max(0.0, min(1.0, frac))
        if caption is not None:
            self._caption = caption
        self._draw()

    def _draw(self) -> None:
        try:
            self.delete("all")
            h = self._h
            self.create_rectangle(0, 0, self._w, h, fill=self.track, outline="")
            fw = int(self._w * self._frac)
            if fw > 2:
                self.create_rectangle(0, 0, fw, h, fill=self.fg, outline="")
            if self._caption:
                self.create_text(8, h // 2, anchor="w", fill="#e8edf5",
                                 text=self._caption, font=("Segoe UI", 10, "bold"))
            self.create_text(self._w - 8, h // 2, anchor="e", fill="#c9d3e3",
                             text="%d%%" % round(self._frac * 100),
                             font=("Segoe UI", 10, "bold"))
        except Exception:
            pass


class BusyOverlay:
    """Full-screen 'something is happening' overlay with icon + progress bar.

    Used for every operation that takes more than a second: probe, report,
    PDF printing, debug bundle. The user is never left staring at a frozen
    window again.
    """

    def __init__(self, master: tk.Misc, title: str, detail: str = "",
                 icon_name: str = "activity") -> None:
        self.top = tk.Toplevel(master)
        self.top.configure(bg="#0b0e13")
        self.top.overrideredirect(True)
        self.top.update_idletasks()
        try:
            w = self.top.winfo_screenwidth()
            h = self.top.winfo_screenheight()
            self.top.geometry("%dx%d+0+0" % (w, h))
            self.top.attributes("-topmost", True)
        except Exception:
            pass
        mid = tk.Frame(self.top, bg="#0b0e13")
        mid.pack(expand=True)
        self._img = icon(icon_name, "accent")
        if self._img:
            tk.Label(mid, bg="#0b0e13", image=self._img).pack(pady=(0, 14))
        self.title_lab = tk.Label(mid, text=title, bg="#0b0e13", fg="#e8edf5",
                                  font=("Segoe UI", 22, "bold"))
        self.title_lab.pack()
        self.detail_lab = tk.Label(mid, text=detail, bg="#0b0e13", fg="#93a0b4",
                                   font=("Segoe UI", 12), justify="center")
        self.detail_lab.pack(pady=(6, 18))
        self.bar = ProgressBar(mid, width=760, height=24)
        self.bar.pack()
        self._ind = 0
        self._job: Optional[str] = None
        self._pulse()

    def _pulse(self) -> None:
        """Indeterminate shimmer while fraction is unknown."""
        try:
            if self._frac_known():
                return
            self._ind = (self._ind + 2) % 100
            self.bar.set(self._ind / 100.0)
            self._job = self.top.after(60, self._pulse)
        except Exception:
            pass

    def _frac_known(self) -> bool:
        return getattr(self.bar, "_manual", False)

    def set(self, frac: Optional[float], detail: Optional[str] = None,
            title: Optional[str] = None) -> None:
        try:
            if frac is not None:
                self.bar._manual = True          # type: ignore[attr-defined]
                if self._job:
                    self.top.after_cancel(self._job)
                    self._job = None
                self.bar.set(frac)
            if detail is not None:
                self.detail_lab.configure(text=detail)
            if title is not None:
                self.title_lab.configure(text=title)
            self.top.update_idletasks()
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._job:
                self.top.after_cancel(self._job)
            self.top.destroy()
        except Exception:
            pass


def state_card(parent: tk.Misc, icon_name: str, colour: str, title: str,
               text: str = "", action_text: str = "",
               action_cmd: Optional[Any] = None, bg: str = "#0f1218") -> tk.Frame:
    """The one consistent 'state of this page' block: icon + headline +
    plain-language explanation + a single obvious action."""
    card = tk.Frame(parent, bg="#171b23", highlightthickness=1,
                    highlightbackground="#2b3240")
    card.pack(fill="x", pady=8, padx=4)
    inner = tk.Frame(card, bg="#171b23")
    inner.pack(fill="x", padx=18, pady=14)
    left = tk.Frame(inner, bg="#171b23")
    left.pack(side="left")
    img = icon(icon_name, colour)
    if img:
        tk.Label(left, image=img, bg="#171b23").pack(side="left", padx=(0, 14))
        left.image = img          # type: ignore[attr-defined]
    right = tk.Frame(inner, bg="#171b23")
    right.pack(side="left", fill="x", expand=True)
    tk.Label(right, text=title, bg="#171b23", fg="#e8edf5",
             font=("Segoe UI", 15, "bold"), anchor="w",
             wraplength=820, justify="left").pack(fill="x")
    if text:
        tk.Label(right, text=text, bg="#171b23", fg="#93a0b4",
                 font=("Segoe UI", 11), anchor="w", wraplength=820,
                 justify="left").pack(fill="x", pady=(3, 0))
    if action_text and action_cmd is not None:
        tk.Button(right, text=action_text, bg="#2f6feb", fg="white",
                  relief="flat", font=("Segoe UI", 11, "bold"), padx=16,
                  pady=6, cursor="hand2", activebackground="#2f6feb",
                  activeforeground="white", command=action_cmd).pack(
            anchor="w", pady=(10, 0))
    return card
