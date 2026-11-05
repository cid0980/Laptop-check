"""
widgets.py -- one reusable dialog for every guided (human-judged) test.

Every guided test looks the same to the technician:
   a big title, plain-language instructions, optional live feedback
   (a mic level meter, a camera preview, a port list), then
   GOOD / OKAY / BAD / SKIP plus a free-text note.

Consistency matters: the report can then compare one laptop with another
because everybody answered the same question the same way.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

COLOURS = {
    "bg": "#0f1218", "panel": "#171b23", "panel2": "#1e2430",
    "text": "#e8edf5", "muted": "#93a0b4", "line": "#2b3240",
    "good": "#1f9d55", "okay": "#c9a227", "bad": "#d0453b",
    "skip": "#4b5563", "accent": "#2f6feb",
}

MARKS: List[Tuple[str, str, str]] = [
    ("good", "GOOD", COLOURS["good"]),
    ("okay", "OKAY / MINOR ISSUE", COLOURS["okay"]),
    ("bad", "BAD / NOT WORKING", COLOURS["bad"]),
    ("skip", "SKIP (could not test)", COLOURS["skip"]),
]


_STEP = {"n": 0, "total": 0}


def set_step(n: int, total: int) -> None:
    """Called by the wizard so every guided screen can show GUIDED n/total."""
    _STEP["n"], _STEP["total"] = n, total


def _step_text() -> str:
    if _STEP["total"]:
        return "GUIDED TEST %d OF %d" % (_STEP["n"], _STEP["total"])
    return ""


def brand_bar(top: tk.Misc, title: str, on_close: Optional[Callable[[], None]] = None) -> tk.Frame:
    """The thin app-chrome bar every guided surface shares, so the tests look
    like screens of one application instead of foreign pop-ups."""
    bar = tk.Frame(top, bg="#0b0e13")
    bar.pack(fill="x", side="top")
    tk.Label(bar, text="LAPTOP CHECK PRO", bg="#0b0e13", fg="#6b7688",
             font=("Segoe UI", 9, "bold")).pack(side="left", padx=18, pady=(7, 0))
    st = _step_text()
    if st:
        tk.Label(bar, text=st, bg="#0b0e13", fg="#2f6feb",
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=(10, 0), pady=(7, 0))
    tk.Label(bar, text=title, bg="#0b0e13", fg="#93a0b4",
             font=("Segoe UI", 9)).pack(side="left", padx=(14, 0), pady=(7, 0))
    if on_close is not None:
        tk.Button(bar, text="\u2715", bg="#0b0e13", fg="#93a0b4", relief="flat",
                  activebackground="#1e2430", activeforeground="#e8edf5",
                  font=("Segoe UI", 10, "bold"), cursor="hand2",
                  command=on_close).pack(side="right", padx=10, pady=2)
    return bar


class GuidedDialog:
    """Modal dialog. result = {'mark','note','cancelled','extra'}"""

    def __init__(self, master: tk.Misc, title: str, subtitle: str = "",
                 instructions: Optional[List[str]] = None,
                 marks: Optional[List[Tuple[str, str, str]]] = None,
                 body_builder: Optional[Callable[["GuidedDialog", tk.Frame], None]] = None,
                 width: int = 760, allow_skip: bool = True) -> None:
        self.master = master
        self.title_text = title
        self.result: Dict[str, Any] = {"mark": None, "note": "", "cancelled": True, "extra": {}}
        self.marks = marks if marks is not None else [m for m in MARKS if allow_skip or m[0] != "skip"]
        self.body_builder = body_builder

        self.top = tk.Toplevel(master)
        self.top.configure(bg=COLOURS["bg"])
        # one application, one look: every guided test is a full-screen
        # borderless screen of the app, never a foreign pop-up window
        self.top.overrideredirect(True)
        self.top.update_idletasks()
        from app.core import screen_size
        sw, sh = screen_size()
        if not sw:
            sw = self.top.winfo_screenwidth()
            sh = self.top.winfo_screenheight()
        self.top.geometry("%dx%d+0+0" % (sw, sh))
        try:
            self.top.attributes("-topmost", True)
        except Exception:
            pass
        w = min(width, sw - 120)
        brand_bar(self.top, title, on_close=self._cancel)
        host = tk.Frame(self.top, bg=COLOURS["bg"])
        host.pack(fill="both", expand=True,
                  padx=max(24, (sw - min(1180, sw - 60)) // 2), pady=(10, 18))

        head = tk.Frame(host, bg=COLOURS["panel2"])
        head.pack(fill="x")
        tk.Label(head, text=title, bg=COLOURS["panel2"], fg=COLOURS["text"],
                 font=("Segoe UI", 16, "bold"), anchor="w").pack(fill="x", padx=18, pady=(14, 0))
        if subtitle:
            tk.Label(head, text=subtitle, bg=COLOURS["panel2"], fg=COLOURS["accent"],
                     font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", padx=18)

        body = tk.Frame(host, bg=COLOURS["bg"])
        body.pack(fill="both", expand=True, padx=18, pady=12)

        if instructions:
            box = tk.Frame(body, bg=COLOURS["panel"], highlightthickness=1,
                           highlightbackground=COLOURS["line"])
            box.pack(fill="x", pady=(0, 10))
            for i, line in enumerate(instructions):
                tk.Label(box, text=("%d.  " % (i + 1)) + line, bg=COLOURS["panel"],
                         fg=COLOURS["text"] if not line.startswith("!") else "#ffd479",
                         font=("Segoe UI", 10), anchor="w", justify="left",
                         wraplength=w - 80).pack(fill="x", padx=12, pady=3)

        self.action_frame = tk.Frame(body, bg=COLOURS["bg"])
        self.action_frame.pack(fill="x", pady=(0, 10))
        if body_builder:
            try:
                body_builder(self, body)
            except Exception as exc:
                tk.Label(body, text="Panel error: %s" % exc, bg=COLOURS["bg"],
                         fg="#ff9d9d", font=("Segoe UI", 9)).pack(fill="x")

        self.status = tk.Label(body, text="", bg=COLOURS["bg"], fg=COLOURS["muted"],
                               font=("Segoe UI", 10), anchor="w", justify="left", wraplength=w - 80)
        self.status.pack(fill="x", pady=(4, 4))

        # ---- mark row ----------------------------------------------------
        markrow = tk.Frame(host, bg=COLOURS["panel"])
        markrow.pack(fill="x", side="bottom")
        inner = tk.Frame(markrow, bg=COLOURS["panel"])
        inner.pack(fill="x", padx=14, pady=10)

        left = tk.Frame(inner, bg=COLOURS["panel"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text="Note for the report (optional):", bg=COLOURS["panel"],
                 fg=COLOURS["muted"], font=("Segoe UI", 8)).pack(anchor="w")
        self.note = tk.Entry(left, bg=COLOURS["panel2"], fg=COLOURS["text"],
                             insertbackground=COLOURS["text"], relief="flat",
                             font=("Segoe UI", 10))
        self.note.pack(fill="x", ipady=5, pady=(2, 0))

        right = tk.Frame(inner, bg=COLOURS["panel"])
        right.pack(side="right", padx=(10, 0))
        self.mark_buttons = {}
        for key, label, colour in self.marks:
            b = tk.Button(right, text=label, bg=colour, fg="white", relief="flat",
                          activeforeground="white", activebackground=colour,
                          font=("Segoe UI", 10, "bold"), padx=12, pady=8, cursor="hand2",
                          command=lambda k=key: self._choose(k))
            b.pack(side="left", padx=3)
            self.mark_buttons[key] = b

        self.top.bind("<Key>", self._on_key)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.top.after(60, self._centre)

    # ------------------------------------------------------------------ ui
    def _centre(self) -> None:
        # full-screen surface: nothing to centre, just take the keyboard
        try:
            self.top.focus_force()
        except Exception:
            pass

    def say(self, text: str, colour: Optional[str] = None) -> None:
        self.status.configure(text=text, fg=colour or COLOURS["muted"])
        try:
            self.top.update_idletasks()
        except Exception:
            pass

    def add_action(self, text: str, command: Callable[[], None],
                   colour: str = COLOURS["accent"]) -> tk.Button:
        b = tk.Button(self.action_frame, text=text, bg=colour, fg="white", relief="flat",
                      activeforeground="white", font=("Segoe UI", 10, "bold"),
                      padx=12, pady=6, cursor="hand2", command=command)
        b.pack(side="left", padx=(0, 6), pady=2)
        return b

    def _on_key(self, event: Any) -> None:
        try:
            if self.top.focus_get() is self.note:
                if event.keysym == "Escape":
                    self._cancel()
                return
        except Exception:
            pass
        ch = (event.char or "")
        keys = {m[0]: i + 1 for i, m in enumerate(self.marks)}
        mapping = {"1": "good", "2": "okay", "3": "bad", "4": "skip",
                   "g": "good", "o": "okay", "b": "bad", "s": "skip"}
        k = ch.lower() or (event.keysym or "").lower()[:1]
        if k in mapping and mapping[k] in keys:
            self._choose(mapping[k])
        elif event.keysym == "Escape":
            self._cancel()

    def _choose(self, mark: str) -> None:
        self.result["mark"] = mark
        self.result["note"] = self.note.get().strip()
        self.result["cancelled"] = False
        self._close()

    def _cancel(self) -> None:
        self.result["cancelled"] = True
        self._close()

    def _close(self) -> None:
        try:
            self.top.destroy()
        except Exception:
            pass

    # ----------------------------------------------------------------- run
    def run(self) -> Dict[str, Any]:
        try:
            self.top.grab_set()
        except Exception:
            pass
        self.master.wait_window(self.top)
        return self.result


def mark_dialog(master: tk.Misc, title: str, subtitle: str = "",
                instructions: Optional[List[str]] = None,
                body_builder: Optional[Callable[[GuidedDialog, tk.Frame], None]] = None,
                width: int = 760, marks: Optional[List[Tuple[str, str, str]]] = None,
                allow_skip: bool = True) -> Dict[str, Any]:
    return GuidedDialog(master, title, subtitle, instructions, marks=marks,
                        body_builder=body_builder, width=width,
                        allow_skip=allow_skip).run()


def checklist_dialog(master: tk.Misc, title: str, subtitle: str,
                     items: List[Dict[str, Any]], width: int = 820) -> Dict[str, Any]:
    """A list of yes/no/na questions (used for ports and cosmetic checks).

    items: [{'id','label','hint','options':[(key,label,colour),...]}]
    Returns {'answers': {id: key}, 'note': str, 'cancelled': bool}
    """
    answers: Dict[str, Any] = {}
    result: Dict[str, Any] = {"answers": answers, "note": "", "cancelled": True, "mark": None}

    top = tk.Toplevel(master)
    top.configure(bg=COLOURS["bg"])
    top.overrideredirect(True)
    top.update_idletasks()
    from app.core import screen_size
    sw, sh = screen_size()
    if not sw:
        sw = top.winfo_screenwidth()
        sh = top.winfo_screenheight()
    top.geometry("%dx%d+0+0" % (sw, sh))
    try:
        top.attributes("-topmost", True)
    except Exception:
        pass
    brand_bar(top, title, on_close=lambda: top.destroy())
    host = tk.Frame(top, bg=COLOURS["bg"])
    host.pack(fill="both", expand=True,
              padx=max(24, (sw - min(1180, sw - 60)) // 2), pady=(10, 18))

    head = tk.Frame(host, bg=COLOURS["panel2"])
    head.pack(fill="x")
    tk.Label(head, text=title, bg=COLOURS["panel2"], fg=COLOURS["text"],
             font=("Segoe UI", 16, "bold"), anchor="w").pack(fill="x", padx=18, pady=(14, 0))
    tk.Label(head, text=subtitle, bg=COLOURS["panel2"], fg=COLOURS["accent"],
             font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", padx=18, pady=(0, 10))

    wrap = tk.Frame(host, bg=COLOURS["bg"])
    wrap.pack(fill="both", expand=True, padx=10, pady=10)
    canvas = tk.Canvas(wrap, bg=COLOURS["bg"], highlightthickness=0)
    sb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=COLOURS["bg"])
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _stretch(event: Any) -> None:
        try:
            canvas.itemconfigure(win_id, width=event.width)
        except Exception:
            pass
    canvas.bind("<Configure>", _stretch)
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    def _wheel(event: Any) -> None:
        try:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass
    canvas.bind_all("<MouseWheel>", _wheel)

    row_frames = []
    note_entries: Dict[str, Any] = {}
    for it in items:
        opts = it.get("options") or [("yes", "OK", COLOURS["good"]),
                                     ("weak", "WEAK", COLOURS["okay"]),
                                     ("no", "FAULTY", COLOURS["bad"]),
                                     ("na", "NOT FITTED", COLOURS["skip"])]
        box = tk.Frame(inner, bg=COLOURS["panel"], highlightthickness=1,
                       highlightbackground=COLOURS["line"])
        box.pack(fill="x", pady=4, padx=6)
        txt = tk.Frame(box, bg=COLOURS["panel"])
        txt.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(txt, text=it["label"], bg=COLOURS["panel"], fg=COLOURS["text"],
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x")
        if it.get("hint"):
            tk.Label(txt, text=it["hint"], bg=COLOURS["panel"], fg=COLOURS["muted"],
                     font=("Segoe UI", 9), anchor="w", justify="left",
                     wraplength=width - 120).pack(fill="x")
        if it.get("actions"):
            arow = tk.Frame(box, bg=COLOURS["panel"])
            arow.pack(fill="x", padx=10, pady=(2, 4))
            for a in it["actions"]:
                if len(a) >= 3:
                    alabel, acmd, acolour = a[0], a[1], a[2]
                else:
                    alabel, acmd, acolour = a[0], a[1], None
                tk.Button(arow, text=alabel, bg=acolour or COLOURS["accent"], fg="white",
                          relief="flat", activeforeground="white",
                          font=("Segoe UI", 9, "bold"), padx=10, pady=4, cursor="hand2",
                          command=acmd).pack(side="left", padx=(0, 6))
        brow = tk.Frame(box, bg=COLOURS["panel"])
        brow.pack(fill="x", padx=10, pady=(2, 10))
        lbl = tk.Label(brow, text="not answered", bg=COLOURS["panel"], fg=COLOURS["muted"],
                       font=("Segoe UI", 9, "bold"), width=16, anchor="w")
        lbl.pack(side="left")

        def make(iid: str, key: str, text: str, colour: str, lab: tk.Label) -> Callable[[], None]:
            def handler() -> None:
                answers[iid] = key
                lab.configure(text=text, fg=colour)
            return handler

        for key, text, colour in opts:
            b = tk.Button(brow, text=text, bg=COLOURS["panel2"], fg=colour, relief="flat",
                          activebackground=colour, activeforeground="white",
                          font=("Segoe UI", 9, "bold"), padx=10, pady=4, cursor="hand2",
                          command=make(it["id"], key, text, colour, lbl))
            b.pack(side="left", padx=3)
        if it.get("note"):
            ent = tk.Entry(brow, bg=COLOURS["panel2"], fg=COLOURS["text"], relief="flat",
                           insertbackground=COLOURS["text"], font=("Segoe UI", 9), width=30)
            ent.pack(side="left", padx=6, ipady=3)
            note_entries[it["id"]] = ent
        row_frames.append(box)

    foot = tk.Frame(top, bg=COLOURS["panel"])
    foot.pack(fill="x", side="bottom")
    fin = tk.Frame(foot, bg=COLOURS["panel"])
    fin.pack(fill="x", padx=14, pady=10)
    tk.Label(fin, text="Overall note:", bg=COLOURS["panel"], fg=COLOURS["muted"],
             font=("Segoe UI", 8)).pack(side="left")
    note_e = tk.Entry(fin, bg=COLOURS["panel2"], fg=COLOURS["text"], relief="flat",
                      insertbackground=COLOURS["text"], font=("Segoe UI", 10))
    note_e.pack(side="left", fill="x", expand=True, padx=8, ipady=5)

    counter = tk.Label(fin, text="0 / %d answered" % len(items), bg=COLOURS["panel"],
                       fg=COLOURS["muted"], font=("Segoe UI", 9, "bold"))
    counter.pack(side="left", padx=6)

    def done(mark: str) -> None:
        for iid, ent in note_entries.items():
            try:
                v = ent.get().strip()
                if v:
                    answers[iid + "_note"] = v
            except Exception:
                pass
        result["note"] = note_e.get().strip()
        result["mark"] = mark
        result["cancelled"] = mark == "cancel"
        try:
            canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        top.destroy()

    def update_counter() -> None:
        n = len([k for k in answers if not k.endswith("_note")])
        counter.configure(text="%d / %d answered" % (n, len(items)))
        top.after(400, update_counter)
    update_counter()

    tk.Button(fin, text="FINISH", bg=COLOURS["accent"], fg="white", relief="flat",
              font=("Segoe UI", 10, "bold"), padx=14, pady=6, cursor="hand2",
              command=lambda: done("done")).pack(side="right", padx=3)
    tk.Button(fin, text="Cancel", bg=COLOURS["panel2"], fg=COLOURS["muted"], relief="flat",
              font=("Segoe UI", 9), padx=10, cursor="hand2",
              command=lambda: done("cancel")).pack(side="right", padx=3)

    top.protocol("WM_DELETE_WINDOW", lambda: done("cancel"))
    try:
        top.grab_set()
    except Exception:
        pass
    master.wait_window(top)
    return result
