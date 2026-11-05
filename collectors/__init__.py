"""
collectors -- everything that reads the machine automatically.

Three modules, one entry point:

    collectors.system      one big PowerShell/CIM probe -> ctx["raw"]
    collectors.components  18 evaluators that turn raw data into verdicts
    collectors.stress      the two long tests (CPU burn, RAM pattern test)

Call `full_scan(ctx, bus)` and it does the whole automatic phase, emitting
progress on the Bus so the GUI can show it live:

    bus.emit("phase", text="...")
    bus.emit("check_start", cid=..., index=..., total=...)
    bus.emit("check_done",  cid=..., status=..., summary=..., score=..., seconds=...)
    bus.emit("log", text="...", tag="ok"|"warn"|"bad")
    bus.emit("scan_done")

The same function is used by the GUI and by `run.py --scan-headless`, so a
server or a batch job can inspect a machine with no window at all.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional

from app.core import (AUTO_IDS, Bus, CheckResult, DATA_DIR, get_power_status,
                      is_admin, make_result, run_shell, write_json)

from collectors import components as C
from collectors import stress as ST
from collectors import system as S

REGISTRY: Dict[str, Any] = {}
REGISTRY.update(C.REGISTRY)
REGISTRY.update(ST.REGISTRY)

PHASE1 = "Reading the hardware inventory (one system probe)..."
PHASE2 = "Reading Wi-Fi, the battery report and privacy settings..."
PHASE3 = "Evaluating every component..."


def build_ctx(bus: Optional[Bus] = None, settings: Optional[Dict[str, Any]] = None,
              scan_id: str = "", emit: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Create the context dict every collector shares."""
    bus = bus or Bus()
    return {
        "bus": bus,
        "settings": dict(settings or {}),
        "debug": bool((settings or {}).get("debug")),
        "scan_id": scan_id,
        "admin": is_admin(),
        "emit": emit or (lambda m: bus.emit("log", text=m)),
        "raw": {},
        "started": time.time(),
    }


def probe_all(ctx: Dict[str, Any], bus: Optional[Bus] = None) -> Dict[str, Any]:
    """Phase 1 + 2: gather every piece of raw hardware data once."""
    bus = bus or ctx.get("bus") or Bus()
    emit = ctx.get("emit") or (lambda m: None)

    bus.emit("phase", text=PHASE1)
    bus.emit("log", text="Phase 1/3 - %s" % PHASE1, tag="hd")
    probe = S.probe(progress=lambda m: bus.emit("log", text=m))
    ctx["raw"] = probe.get("raw") or {}
    ctx["probe_ok"] = bool(probe.get("ok", False))
    if probe.get("error"):
        bus.emit("log", text="Probe note: %s" % probe["error"], tag="warn")
    bus.emit("probe_done", keys=len(ctx["raw"]))
    emit("Hardware inventory received: %d data groups." % len(ctx["raw"]))
    try:
        write_json(os.path.join(DATA_DIR, "last_probe_raw.json"), ctx["raw"])
    except Exception:
        pass

    # fix what Windows left broken BEFORE judging the hardware
    from app import fixes
    notes = fixes.run_all_pre_fixes()
    ctx["auto_fixes"] = notes
    for note in notes:
        bus.emit("log", text="Auto-fix: %s" % note, tag="warn")
        emit("Auto-fix: %s" % note)

    bus.emit("phase", text=PHASE2)
    bus.emit("log", text="Phase 2/3 - %s" % PHASE2, tag="hd")
    _rc, out, _err = run_shell("netsh wlan show interfaces", timeout=30)
    ctx["wlan_interface"] = S.parse_wlan_interfaces(out)
    _rc, out, _err = run_shell("netsh wlan show drivers", timeout=30)
    ctx["wlan_driver"] = S.parse_wlan_drivers(out)
    ctx["battery_report"] = C.collect_battery_report(ctx)
    ctx["power_status"] = get_power_status()
    br = ctx.get("battery_report") or {}
    if br.get("design_wh"):
        emit("Battery report: design %.1f Wh, full charge %.1f Wh, %s cycle(s)"
             % (br["design_wh"], br.get("full_wh") or 0.0, br.get("cycles") or "?"))
    return ctx


def run_auto_checks(ctx: Dict[str, Any], bus: Optional[Bus] = None,
                    ids: Optional[List[str]] = None,
                    results: Optional[Dict[str, CheckResult]] = None) -> Dict[str, CheckResult]:
    """Phase 3: evaluate every automatic check, emitting progress as it goes."""
    bus = bus or ctx.get("bus") or Bus()
    results = results if results is not None else {}
    todo = list(ids or AUTO_IDS)
    bus.emit("phase", text=PHASE3)
    bus.emit("log", text="Phase 3/3 - evaluating %d automatic checks..." % len(todo), tag="hd")
    for i, cid in enumerate(todo, 1):
        if bus.cancel.is_set():
            bus.emit("log", text="Scan stopped by the user.", tag="warn")
            break
        res = make_result(cid)
        bus.emit("check_start", cid=cid, index=i, total=len(todo))
        started = time.time()
        fn = REGISTRY.get(cid)
        if fn is None:
            res.skipped("No collector implemented for this check")
        else:
            try:
                fn(res, ctx)
            except Exception as exc:                      # one bad check never kills the scan
                res.status = "skipped"
                res.error = "%s: %s" % (type(exc).__name__, exc)
                res.summary = "This check crashed and was skipped"
                bus.emit("log", text="  ! %s crashed: %s" % (cid, exc), tag="bad")
                if ctx.get("debug"):
                    import traceback
                    bus.emit("log", text=traceback.format_exc(limit=6), tag="bad")
        if res.seconds is None or res.seconds <= 0:
            res.seconds = round(time.time() - started, 2)
        results[res.cid] = res
        bus.emit("check_done", cid=cid, index=i, total=len(todo), status=res.status,
                 summary=res.summary, score=res.score, seconds=res.seconds,
                 flags=len(res.flags), details=len(res.details), error=res.error)
    # record what the tool fixed itself, on the checks it concerns
    for note in (ctx.get("auto_fixes") or []):
        low = note.lower()
        targets = []
        if "audio service" in low:
            targets = ["audio_dev", "speakers"]
        elif "wlan" in low or "wi-fi adapter" in low:
            targets = ["wifi"]
        for cid in targets:
            r = results.get(cid)
            if r is not None:
                r.note(note)
                r.flag(note, "info")
    return results


def full_scan(ctx: Dict[str, Any], bus: Optional[Bus] = None,
              ids: Optional[List[str]] = None,
              results: Optional[Dict[str, CheckResult]] = None) -> Dict[str, CheckResult]:
    """Probe + evaluate. This is what the START FULL SCAN button runs."""
    bus = bus or ctx.get("bus") or Bus()
    try:
        probe_all(ctx, bus)
        results = run_auto_checks(ctx, bus, ids=ids, results=results)
        bus.emit("scan_done")
    except Exception as exc:
        import traceback
        bus.emit("log", text="FATAL: %s: %s" % (type(exc).__name__, exc), tag="bad")
        bus.emit("log", text=traceback.format_exc(limit=8), tag="bad")
        bus.emit("scan_failed", error=str(exc))
    return results if results is not None else {}


__all__ = ["REGISTRY", "build_ctx", "probe_all", "run_auto_checks", "full_scan",
           "components", "stress", "system"]
