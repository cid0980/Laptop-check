#!/usr/bin/env python3
"""
run.py -- the single entry point of Laptop Check Pro.

Normal use:  double-click the .exe. That is all.

The same executable also answers a few command-line switches, which is how the
camera capture is isolated from the GUI (a crashing webcam driver takes down a
throw-away child process instead of the app) and how the developer can
self-test a build:

    LaptopCheckPro.exe                        start the GUI
    LaptopCheckPro.exe --demo                 GUI with a simulated scan loaded
    LaptopCheckPro.exe --selftest             build a demo report, no window
    LaptopCheckPro.exe --report-from FILE.json  re-render a saved scan
    LaptopCheckPro.exe --camera-list [--out FILE]
    LaptopCheckPro.exe --camera-capture OUT.png [--camera-index N]
    LaptopCheckPro.exe --camera-live FOLDER --seconds N --fps F [--index N]
    LaptopCheckPro.exe --version

The shipped .exe is built with --windowed, so sys.stdout can be None. Every
print therefore goes through safe_print(), and every headless mode ALSO writes
its result to a file, which is what the GUI actually reads.
"""

from __future__ import annotations

import argparse
from typing import Any
import json
import os
import sys
import time

# make sure the project root is importable when running from source
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def safe_print(*args: Any, **kwargs: Any) -> None:  # noqa: F821
    try:
        print(*args, **kwargs)
    except Exception:
        pass


def _write(path: str, text: str) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except Exception:
        pass


def cmd_version() -> int:
    from app.core import APP_NAME, APP_VERSION, IS_FROZEN
    safe_print("%s %s (%s, Python %s)" % (APP_NAME, APP_VERSION,
                                          "frozen exe" if IS_FROZEN else "source",
                                          sys.version.split()[0]))
    return 0


def cmd_camera_list(out: str = "") -> int:
    from app.dshow import list_json
    text = list_json()
    if out:
        _write(out, text)
    safe_print(text)
    return 0


def cmd_camera_capture(path: str, index: int, seconds: float) -> int:
    from app.dshow import capture_png
    info = capture_png(path, index=index, warmup=seconds)
    sidecar = os.path.splitext(path)[0] + ".json"
    _write(sidecar, json.dumps(info, indent=2))
    safe_print(json.dumps(info))
    return 0 if info.get("ok") else 2


def cmd_camera_live(folder: str, seconds: float, fps: float, index: int) -> int:
    from app.dshow import live_frames
    status = live_frames(folder, seconds=seconds, fps=fps, index=index)
    return 0 if status.get("ok") else 2


def cmd_selftest(outdir: str = "") -> int:
    """Build a demo scan, grade it, price it and render both reports. No GUI."""
    started = time.time()
    problems = []
    try:
        from app.core import APP_NAME, APP_VERSION, CHECKS, REPORTS_DIR
        from app.demo import build_demo_scan
        from app.grading import grade_all
        from app.valuation import estimate, load_model
        from app.branding import Branding
        from app import report as report_mod
        from app import qr as qr_mod

        # 1. QR encoder
        matrix = qr_mod.encode("https://example.com/verify/LCP-SELFTEST", "M")
        if not matrix or len(matrix) < 21:
            problems.append("QR encoder produced no matrix")
        else:
            svg = qr_mod.to_svg(matrix)
            if "<svg" not in svg:
                problems.append("QR SVG render failed")

        # 2. PNG encoder
        from app import png as png_mod
        data = png_mod.encode_png(8, 8, png_mod.solid(8, 8, (200, 30, 30)))
        if not data.startswith(b"\x89PNG"):
            problems.append("PNG encoder produced no valid header")

        # 3. demo scan -> grade -> price -> report
        scan = build_demo_scan("LCP-SELFTEST")
        grade = grade_all(scan.results, scan.ctx)
        scan.grade = grade
        scan.valuation = estimate(scan.results, scan.ctx, grade, load_model())
        if not grade.get("grade"):
            problems.append("grading produced no grade")
        if not scan.valuation.get("suggested"):
            problems.append("valuation produced no price")
        if len(scan.results) != len(CHECKS):
            problems.append("demo scan has %d/%d checks" % (len(scan.results), len(CHECKS)))

        branding = Branding()
        branding.set("shop_name", "Self Test Store")
        folder = outdir or os.path.join(REPORTS_DIR, "selftest")
        paths = report_mod.write_reports(scan, branding, folder=folder)
        for key in ("internal", "customer", "json"):
            p = paths.get(key)
            if not p or not os.path.exists(p) or os.path.getsize(p) < 4000:
                problems.append("report file missing or too small: %s" % key)

        # 4. collectors import cleanly
        from collectors import components, stress, system
        if len(components.REGISTRY) < 15:
            problems.append("collector registry looks incomplete")
        if not system.PS:
            problems.append("PowerShell probe table is empty")

        # 5. guided modules import cleanly (no Tk window is created)
        import guided.screen, guided.audio, guided.camera, guided.keyboard, guided.misc
        import guided.widgets

        result = {
            "ok": not problems,
            "app": "%s %s" % (APP_NAME, APP_VERSION),
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "checks": len(scan.results),
            "grade": grade.get("grade"),
            "score": grade.get("score"),
            "suggested_price": scan.valuation.get("suggested"),
            "seconds": round(time.time() - started, 2),
            "reports": paths,
            "problems": problems,
        }
    except Exception as exc:
        import traceback
        result = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                  "trace": traceback.format_exc(limit=12)}
    text = json.dumps(result, indent=2, default=str)
    safe_print(text)
    _write(os.path.join(HERE, "selftest.json"), text)
    return 0 if result.get("ok") else 1


def cmd_report_from(path: str) -> int:
    from app.core import load_json
    from app.scan import Scan
    from app.branding import Branding
    from app import report as report_mod
    data = load_json(path)
    if not data:
        safe_print("could not read %s" % path)
        return 2
    scan = Scan.from_dict(data)
    paths = report_mod.write_reports(scan, Branding())
    safe_print(json.dumps(paths, indent=2))
    return 0


def cmd_gui(demo: bool) -> int:
    from app.gui import main
    return main(demo=demo)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="LaptopCheckPro", add_help=True,
                                     description="Laptop Check Pro - used-laptop inspection tool")
    parser.add_argument("--demo", action="store_true", help="start with a simulated scan loaded")
    parser.add_argument("--selftest", action="store_true", help="run a headless self-test")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument("--report-from", metavar="FILE", help="re-render a saved scan JSON")
    parser.add_argument("--camera-list", action="store_true", help="list cameras (headless)")
    parser.add_argument("--camera-capture", metavar="OUT.png", help="capture one frame (headless)")
    parser.add_argument("--camera-live", metavar="FOLDER", help="write live frames (headless)")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--index", type=int, default=None)
    parser.add_argument("--seconds", type=float, default=25.0)
    parser.add_argument("--fps", type=float, default=3.0)
    parser.add_argument("--out", metavar="FILE", default="")
    parser.add_argument("--outdir", metavar="DIR", default="")
    args = parser.parse_args(argv)

    if args.version:
        return cmd_version()
    if args.selftest:
        return cmd_selftest(args.outdir)
    if args.report_from:
        return cmd_report_from(args.report_from)
    if args.camera_list:
        return cmd_camera_list(args.out)
    if args.camera_capture:
        idx = args.index if args.index is not None else args.camera_index
        return cmd_camera_capture(args.camera_capture, idx, min(4.0, max(0.6, args.seconds / 8.0)))
    if args.camera_live:
        idx = args.index if args.index is not None else args.camera_index
        return cmd_camera_live(args.camera_live, args.seconds, args.fps, idx)
    return cmd_gui(args.demo)


if __name__ == "__main__":
    # PyInstaller onefile: make sure any child process we spawn behaves
    try:
        import multiprocessing
        multiprocessing.freeze_support()
    except Exception:
        pass
    sys.exit(main())
