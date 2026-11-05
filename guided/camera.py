"""
camera.py -- guided CAMERA test with a real live preview.

The preview frames are produced by a CHILD PROCESS (run.py --camera-live) that
talks DirectShow through ctypes. Reasons:

  * a third-party webcam driver that crashes takes the child down, not the app
  * the main window stays responsive while frames are captured
  * nothing extra is installed - DirectShow is part of Windows

If the capture fails (blocked by privacy settings, IR-only camera, broken
driver) the technician gets two honest fallbacks: open the built-in Windows
Camera app, or attach a photo they took themselves. The report then says the
image was attached manually instead of pretending it was automatic.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable, Dict, List, Optional

from app.core import (CheckResult, IS_WINDOWS, IS_FROZEN, PHOTOS_DIR, _popen_kwargs,
                      run_cmd, temp_path)
from guided.widgets import COLOURS, GuidedDialog


def _child_cmd(args: List[str]) -> List[str]:
    """How to re-launch ourselves headless."""
    if IS_FROZEN:
        return [sys.executable] + args
    return [sys.executable, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "run.py")] + args


def open_windows_camera_app() -> bool:
    if not IS_WINDOWS:
        return False
    rc, _, _ = run_cmd(["cmd.exe", "/c", "start", "ms-camera:"], timeout=15)
    return rc == 0


class CameraTest:
    def __init__(self, master: tk.Misc, res: CheckResult, ctx: Dict[str, Any],
                 emit: Optional[Callable[[str], None]] = None) -> None:
        self.master = master
        self.res = res
        self.ctx = ctx
        self.emit = emit
        self.proc: Optional[subprocess.Popen] = None
        self.folder = ""
        self.photo: Optional[tk.PhotoImage] = None
        self.state: Dict[str, Any] = {"running": False, "frames": 0, "chosen": None,
                                      "manual": False, "error": ""}
        self.preview_size = (430, 322)
        self.canvas: Optional[tk.Canvas] = None
        self.dlg: Optional[GuidedDialog] = None

    # ------------------------------------------------------------------ run
    def run(self) -> CheckResult:
        cam = self.ctx.get("camera") or {}
        if not cam.get("present"):
            self.res.na("No camera is fitted to this machine")
            self.res.note("Windows lists no camera device. Confirm physically (some laptops have a "
                          "shutter or a BIOS switch) before writing it off.")
            return self.res

        devices = cam.get("devices") or []
        self.res.metric("Camera devices reported by Windows", len(devices))
        for d in devices:
            self.res.metric("  %s" % d.get("name"), "status %s [%s]" % (d.get("status"), d.get("class")))

        marks = [("good", "IMAGE IS CLEAR", COLOURS["good"]),
                 ("weak", "POOR / DARK / STRIPED", COLOURS["okay"]),
                 ("bad", "NO IMAGE / BROKEN", COLOURS["bad"]),
                 ("na", "NO CAMERA FITTED", COLOURS["skip"])]

        self.dlg = GuidedDialog(
            self.master, "Camera test", "Live capture through Windows DirectShow",
            ["Press START CAMERA. A live picture from the webcam should appear below within ~3 seconds.",
             "Look at the picture: focus, colour, brightness, dead lines, flicker, IR-cut problems.",
             "Press KEEP THIS PHOTO to store the frame as evidence in the report.",
             "! No picture? Windows may be blocking camera access: Settings > Privacy & security > "
             "Camera > let desktop apps access your camera. Then press START CAMERA again.",
             "If the driver is broken, use OPEN WINDOWS CAMERA APP or ATTACH A PHOTO, and mark honestly."],
            marks=marks, body_builder=self._build, width=820)
        out = self.dlg.run()
        self.stop()

        frames = self.state.get("frames", 0)
        self.res.metric("Live frames captured", frames)
        if self.state.get("device"):
            self.res.metric("Capture device used", self.state["device"])
        if self.state.get("error"):
            self.res.metric("Capture problem", self.state["error"][:160])
            self.res.note("Automatic capture failed: %s" % self.state["error"])
        if self.state.get("chosen"):
            self.res.photos.append(self.state["chosen"])
            self.res.metric("Photo stored in report",
                            os.path.basename(self.state["chosen"]) +
                            (" (attached manually)" if self.state.get("manual") else ""))
        else:
            self.res.metric("Photo stored in report", "None")
            self.res.flag("No camera photo was stored as evidence", "warn")
        if frames == 0 and not self.state.get("manual"):
            self.res.flag("The camera produced no frames at all during the test", "warn")

        if out.get("cancelled"):
            self.res.skipped("Camera test was cancelled by the technician")
            return self.res

        mark = out.get("mark")
        self.res.marks = [{"id": "camera", "name": "Camera image quality", "mark": mark,
                           "note": out.get("note", "")}]
        if out.get("note"):
            self.res.note("Technician note: " + out["note"])
        score = {"good": 100.0, "weak": 58.0, "bad": 6.0, "na": None}.get(mark)
        if score is None:
            self.res.na("Technician confirmed no camera is fitted / usable")
            return self.res
        if mark == "bad":
            self.res.flag("Camera does not produce a usable image", "fail")
        elif mark == "weak":
            self.res.flag("Camera image quality is poor", "warn")
        if mark == "good" and frames == 0:
            score = min(score, 60.0)
            self.res.note("Marked good but no frame was captured automatically - the verdict is based on "
                          "the Windows Camera app or an attached photo.")
        self.res.set("pass" if score >= 88 else ("warn" if score >= 50 else "fail"),
                     {"good": "Camera works, image is clear",
                      "weak": "Camera works, image quality is poor",
                      "bad": "Camera does not work"}.get(mark, "Camera"), score=score)
        return self.res

    # ------------------------------------------------------------------- ui
    def _build(self, dlg: GuidedDialog, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=COLOURS["bg"])
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, width=self.preview_size[0], height=self.preview_size[1],
                                bg="#080a0e", highlightthickness=1,
                                highlightbackground=COLOURS["line"])
        self.canvas.pack(side="left", padx=(0, 10))
        self.canvas.create_text(self.preview_size[0] // 2, self.preview_size[1] // 2,
                                text="camera preview\n(press START CAMERA)", fill="#3f4a5c",
                                font=("Segoe UI", 11), justify="center", tags="placeholder")
        side = tk.Frame(wrap, bg=COLOURS["bg"])
        side.pack(side="left", fill="both", expand=True)
        dlg.add_action("\u25cf  START CAMERA", self.start, COLOURS["accent"])
        dlg.add_action("KEEP THIS PHOTO", self.keep_frame, COLOURS["good"])
        dlg.add_action("STOP", self.stop, COLOURS["panel2"])
        row2 = tk.Frame(side, bg=COLOURS["bg"])
        row2.pack(fill="x", pady=(8, 0))
        tk.Button(row2, text="Open Windows Camera app", bg=COLOURS["panel2"], fg=COLOURS["text"],
                  relief="flat", font=("Segoe UI", 9), padx=8, pady=4, cursor="hand2",
                  command=self._open_app).pack(side="left", padx=(0, 6))
        tk.Button(row2, text="Attach a photo...", bg=COLOURS["panel2"], fg=COLOURS["text"],
                  relief="flat", font=("Segoe UI", 9), padx=8, pady=4, cursor="hand2",
                  command=self._attach).pack(side="left")
        self.info = tk.Label(side, text="Idle.", bg=COLOURS["bg"], fg=COLOURS["muted"],
                             font=("Segoe UI", 9), anchor="w", justify="left", wraplength=300)
        self.info.pack(fill="x", pady=(10, 0))

    def _set_info(self, text: str, colour: Optional[str] = None) -> None:
        if getattr(self, "info", None) is not None:
            self.info.configure(text=text, fg=colour or COLOURS["muted"])

    def _open_app(self) -> None:
        if open_windows_camera_app():
            self._set_info("Windows Camera app opened. Close it before pressing START CAMERA here "
                           "(only one app can hold the webcam at a time).", "#ffd479")
        else:
            self._set_info("Could not open the Windows Camera app.", "#ff9d9d")

    def _attach(self) -> None:
        path = filedialog.askopenfilename(
            title="Attach a camera photo",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
        if not path:
            return
        scan = self.ctx.get("scan_id", "scan")
        dest = os.path.join(PHOTOS_DIR, "%s_camera_manual.png" % scan)
        try:
            shutil.copyfile(path, dest)
            self.state["chosen"] = dest
            self.state["manual"] = True
            self._set_info("Attached %s" % os.path.basename(path), COLOURS["good"])
            self.res.metric("Photo attached from file", path)
        except Exception as exc:
            self._set_info("Could not copy that file: %s" % exc, "#ff9d9d")

    # --------------------------------------------------------------- capture
    def start(self) -> None:
        if self.state["running"]:
            self.stop()
        if not IS_WINDOWS:
            self._set_info("Camera capture only runs on Windows.", "#ff9d9d")
            self.state["error"] = "Not Windows"
            return
        self.folder = temp_path("lcp-cam-", "")
        os.makedirs(self.folder, exist_ok=True)
        seconds = int(self.ctx.get("settings", {}).get("camera_seconds", 45) or 45)
        index = int(self.ctx.get("settings", {}).get("camera_index", 0) or 0)
        cmd = _child_cmd(["--camera-live", self.folder, "--seconds", str(seconds),
                          "--fps", "3", "--index", str(index)])
        try:
            self.proc = subprocess.Popen(cmd, **_popen_kwargs())
        except Exception as exc:
            self.state["error"] = "Could not start the capture helper: %s" % exc
            self._set_info(self.state["error"], "#ff9d9d")
            return
        self.state["running"] = True
        self.state["frames"] = 0
        self._set_info("Starting the camera, please wait...", COLOURS["accent"])
        if self.emit:
            self.emit("Camera capture child process started")
        self.master.after(350, self._poll)

    def _poll(self) -> None:
        if not self.state["running"]:
            return
        alive = self.proc is not None and self.proc.poll() is None
        status: Dict[str, Any] = {}
        sp = os.path.join(self.folder, "status.json")
        if os.path.exists(sp):
            try:
                with open(sp, "r", encoding="utf-8") as fh:
                    status = json.load(fh)
            except Exception:
                status = {}
        files = sorted(glob.glob(os.path.join(self.folder, "frame_*.png")))
        if status.get("device"):
            self.state["device"] = status["device"]
        if status.get("error") and not files:
            self.state["error"] = status["error"]
        if files:
            newest = files[-1]
            self.state["frames"] = len(files) + status.get("frames", 0) - len(files)
            self.state["frames"] = max(self.state["frames"], len(files))
            self._show(newest)
            self._set_info("LIVE  -  %s  %sx%s  -  %d frame(s) captured. "
                           "Press KEEP THIS PHOTO to store one in the report."
                           % (status.get("device", "camera"), status.get("width", "?"),
                              status.get("height", "?"), len(files)), COLOURS["good"])
            # keep the folder small: only the newest few frames
            for old in files[:-6]:
                try:
                    os.remove(old)
                except Exception:
                    pass
        elif status.get("error"):
            self._set_info("Camera capture failed: %s" % status["error"][:220], "#ff9d9d")
        elif not alive:
            self.state["running"] = False
            if not status.get("error"):
                self.state["error"] = self.state["error"] or "The capture helper stopped unexpectedly"
            self._set_info("Capture stopped. %s" % (status.get("error") or ""), "#ff9d9d")
            return
        if alive or files:
            try:
                self.master.after(380, self._poll)
            except Exception:
                pass
        else:
            self.state["running"] = False

    def _show(self, path: str) -> None:
        try:
            img = tk.PhotoImage(file=path)
        except Exception:
            return
        w, h = img.width(), img.height()
        k = max(1, max(w // self.preview_size[0], h // self.preview_size[1]))
        if k > 1:
            img = img.subsample(k, k)
        self.photo = img
        if self.canvas is not None:
            try:
                self.canvas.delete("all")
                self.canvas.create_image(self.preview_size[0] // 2, self.preview_size[1] // 2,
                                         image=self.photo)
            except Exception:
                pass

    def keep_frame(self) -> None:
        files = sorted(glob.glob(os.path.join(self.folder or "", "frame_*.png"))) if self.folder else []
        if not files:
            self._set_info("Nothing to keep yet - wait for a live frame first.", "#ffd479")
            return
        scan = self.ctx.get("scan_id", "scan")
        dest = os.path.join(PHOTOS_DIR, "%s_camera_%s.png" % (scan, time.strftime("%H%M%S")))
        try:
            shutil.copyfile(files[-1], dest)
            self.state["chosen"] = dest
            self.state["manual"] = False
            self._set_info("Saved %s" % os.path.basename(dest), COLOURS["good"])
            if self.emit:
                self.emit("Camera photo saved: %s" % os.path.basename(dest))
        except Exception as exc:
            self._set_info("Could not save the photo: %s" % exc, "#ff9d9d")

    def stop(self) -> None:
        self.state["running"] = False
        if self.proc is not None and self.proc.poll() is None:
            for how in ("terminate", "kill"):
                try:
                    getattr(self.proc, how)()
                    break
                except Exception:
                    continue
        self.proc = None
        # clean the temporary frames
        if self.folder and os.path.isdir(self.folder):
            keep = self.state.get("chosen")
            try:
                for f in glob.glob(os.path.join(self.folder, "*")):
                    if keep and os.path.abspath(f) == os.path.abspath(keep):
                        continue
                    os.remove(f)
                os.rmdir(self.folder)
            except Exception:
                pass


def run_camera_test(master: tk.Misc, res: CheckResult, ctx: Dict[str, Any],
                    emit: Optional[Callable[[str], None]] = None) -> CheckResult:
    return CameraTest(master, res, ctx, emit).run()
