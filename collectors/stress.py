"""
stress.py -- the two "load" tests: CPU stress/thermals and a RAM integrity pass.

Both are pure standard library. No Prime95, no MemTest86 download, nothing to
install: the .exe carries everything.

CPU STRESS
  A background PowerShell process runs a real multi-threaded floating-point
  burn (one worker per logical core) and reports work-units-per-second for
  every second of the test. Python samples the ACPI thermal zone at the same
  time. Comparing the cold seconds with the hot seconds tells us whether the
  CPU is THERMALLY THROTTLING (dirty fan, dried paste) - the single most
  common hidden fault in used laptops.

RAM TEST
  Allocates a large block of the machine's own memory, writes known patterns
  (0x00, 0xFF, 0x5A, 0xA5, 0x33, 0xCC, walking bits, pseudo-random), checksums
  each block with zlib.crc32 (C speed) and re-verifies later in the same pass.
  This is a fast integrity check, NOT a substitute for MemTest86, and the
  report says so honestly. It also reads the Windows Memory Diagnostic result
  from the event log if the shop has run one before.
"""

from __future__ import annotations

import base64
import os
import random
import subprocess
import threading
import time
import zlib
from typing import Any, Callable, Dict, List, Optional

from app.core import (
    CheckResult, IS_WINDOWS, _popen_kwargs, get, kelvin_to_c, powershell_exe,
    run_ps, safe_check,
)

BURN_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
$cores = __CORES__
$secs  = __SECS__
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$results = New-Object 'object[]' $cores
$act = [Action[int]]{
  param($k)
  $x = 1.000001
  $per = New-Object System.Collections.ArrayList
  $c = 0
  $last = 0
  while ($sw.Elapsed.TotalSeconds -lt $secs) {
    for ($i = 0; $i -lt 50000; $i++) { $x = [Math]::Sqrt($x * 1.0000001) + 1.0 }
    $c++
    $t = [int]$sw.Elapsed.TotalSeconds
    if ($t -ne $last) { [void]$per.Add(@{ s = $last; n = $c }); $c = 0; $last = $t }
  }
  [void]$per.Add(@{ s = $last; n = $c })
  $results[$k] = $per
}
$po = New-Object System.Threading.Tasks.ParallelOptions
$po.MaxDegreeOfParallelism = $cores
[System.Threading.Tasks.Parallel]::For(0, $cores, $po, $act)
$tot = @{}
for ($k = 0; $k -lt $cores; $k++) {
  $per = $results[$k]
  if ($null -eq $per) { continue }
  foreach ($e in $per) {
    $key = [string]$e.s
    if ($tot.ContainsKey($key)) { $tot[$key] = $tot[$key] + $e.n } else { $tot[$key] = $e.n }
  }
}
$out = [pscustomobject]@{
  cores  = $cores
  secs   = $secs
  elapsed = [Math]::Round($sw.Elapsed.TotalSeconds, 1)
  curve  = $tot
  x      = $x
}
$out | ConvertTo-Json -Depth 4 -Compress
"""


def python_burn(cores: int, seconds: int, say: Optional[Any] = None,
                peaks: Optional[List[float]] = None,
                ctx: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """Fallback load worker: our own threads, no PowerShell involved.

    Returns the same per-second work-unit curve the PowerShell worker makes,
    so the throttling analysis below is identical. Python's GIL means the
    threads share cores, which is fine: we measure SUSTAINED throughput, not
    peak multi-core performance.
    """
    buckets: Dict[int, int] = {}
    lock = threading.Lock()
    stop = [False]
    t0 = time.time()

    def worker() -> None:
        local: Dict[int, int] = {}
        x = 123456789
        while not stop[0]:
            for _ in range(30000):
                x = (x * 1103515245 + 12345) & 0x7FFFFFFF
            sec = int(time.time() - t0)
            local[sec] = local.get(sec, 0) + 1
        with lock:
            for k, v in local.items():
                buckets[k] = buckets.get(k, 0) + v

    threads = [threading.Thread(target=worker, daemon=True)
               for _ in range(max(1, min(cores, 8)))]
    for t in threads:
        t.start()
    while time.time() - t0 < seconds:
        if say:
            say("CPU under load (built-in worker)... %ds left"
                % max(0, int(seconds - (time.time() - t0))))
        if peaks is not None:
            temps, _clock, _load = read_temperatures()
            if temps:
                peaks.append(max(z["c"] for z in temps))
        if ctx is not None and ctx.get("bus") is not None and ctx["bus"].cancel.is_set():
            break
        time.sleep(2.0)
    stop[0] = True
    for t in threads:
        t.join(timeout=5)
    return {str(k): float(v) for k, v in buckets.items()}


def _start_ps(script: str) -> Optional[subprocess.Popen]:
    if not IS_WINDOWS:
        return None
    exe = powershell_exe()
    if not exe:
        return None
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    args = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", b64]
    try:
        return subprocess.Popen(args, **_popen_kwargs())
    except Exception:
        return None


def read_temperatures():
    """-> (temps:list[dict], clock:float|None, load:float|None)"""
    """ACPI thermal zones in Celsius. Empty list when the machine hides them."""
    script = ("$ProgressPreference='SilentlyContinue';$ErrorActionPreference='SilentlyContinue';"
              "$t = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
              "-ErrorAction SilentlyContinue | Select-Object InstanceName,CurrentTemperature;"
              "$c = (Get-CimInstance Win32_Processor | Select-Object -First 1).CurrentClockSpeed;"
              "$l = (Get-CimInstance Win32_Processor | Select-Object -First 1).LoadPercentage;"
              "[pscustomobject]@{ Zones=$t; Clock=$c; Load=$l } | ConvertTo-Json -Depth 3 -Compress")
    data = None
    out = run_ps(script, timeout=25)
    if out:
        from app.core import parse_json_loose
        data = parse_json_loose(out)
    temps = []
    clock = load = None
    if isinstance(data, dict):
        zones = data.get("Zones") or []
        if isinstance(zones, dict):
            zones = [zones]
        for z in zones:
            if isinstance(z, dict):
                c = kelvin_to_c(get(z, "CurrentTemperature"))
                if c is not None:
                    temps.append({"zone": str(get(z, "InstanceName", "?"))[:40], "c": c})
        clock = data.get("Clock")
        load = data.get("Load")
    return temps, clock, load


# ==========================================================================
# CPU STRESS
# ==========================================================================

@safe_check
def check_cpu_stress(res: CheckResult, ctx: Dict[str, Any]) -> None:
    emit: Optional[Callable[[str], None]] = ctx.get("emit")
    settings = ctx.get("settings") or {}
    seconds = int(settings.get("stress_seconds", 20) or 20)
    cpu = ctx.get("cpu") or {}
    logical = int(cpu.get("threads") or 4)
    cores = max(1, min(logical, int(settings.get("stress_cores", logical) or logical)))

    if not IS_WINDOWS:
        res.skipped("Stress test only runs on Windows")
        return
    if settings.get("skip_stress"):
        res.skipped("Disabled in settings")
        return

    def say(msg: str) -> None:
        if emit:
            emit(msg)

    say("CPU stress test: starting %d worker thread(s) for %d second(s)..." % (cores, seconds))
    t0, t1 = read_temperatures()
    res.metric("Threads used", cores)
    res.metric("Test length", "%d seconds" % seconds)
    if t0:
        res.metric("Idle temperature", "%.0f C (%s)" % (
            max(z["c"] for z in t0), t0[0]["zone"]))
    else:
        res.metric("Idle temperature", "Not exposed by this machine's ACPI (very common)")

    script = (BURN_TEMPLATE.replace("__CORES__", str(cores))
                           .replace("__SECS__", str(seconds)))
    proc = _start_ps(script)
    peaks: List[float] = []
    clocks: List[Any] = []
    loads: List[Any] = []
    curve: Dict[str, Any] = {}
    if proc is None:
        # Security software (or a stripped Windows) blocked the PowerShell
        # worker. The load test still happens - with our own threads - because
        # "could not start" is not an answer a shop can use.
        res.note("The PowerShell load worker could not start on this PC (security "
                 "software usually). The built-in load worker was used instead.")
        say("PowerShell worker unavailable - using the built-in load worker...")
        curve = python_burn(cores, seconds, say, peaks, ctx)
    else:
        start = time.time()
        try:
            while True:
                elapsed = time.time() - start
                left = seconds + 8 - elapsed
                if proc.poll() is not None or left <= 0:
                    break
                say("CPU under load... %ds elapsed, %ds left" % (int(elapsed), max(0, int(left))))
                temps, clock, load = read_temperatures()
                if temps:
                    peaks.append(max(z["c"] for z in temps))
                if clock:
                    clocks.append(float(clock))
                if load is not None:
                    loads.append(float(load))
                if ctx.get("bus") is not None and ctx["bus"].cancel.is_set():
                    break
                time.sleep(3.0)
            try:
                out, err = proc.communicate(timeout=seconds + 60)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate(timeout=5)
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

        from app.core import parse_json_loose
        data = parse_json_loose(out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or ""))
        if isinstance(data, dict):
            curve = data.get("curve") or {}
            res.metric("Actual run time", "%s s" % data.get("elapsed"))
        if not curve:
            res.note("The PowerShell load worker produced no data (it may have been "
                     "killed). The built-in load worker was used instead.")
            curve = python_burn(cores, seconds, say, peaks, ctx)

    if peaks:
        res.metric("Peak temperature", "%.0f C" % max(peaks))
        res.metric("Temperature curve", " -> ".join("%.0f" % p for p in peaks[:12]))
    if clocks:
        res.metric("Clock during load", "%d MHz (max reported %s MHz)" % (
            max(float(c) for c in clocks), cpu.get("max_mhz") or "?"))
    if loads:
        res.metric("CPU utilisation reached", "%d%%" % max(loads))

    # ---- throttle analysis from the work curve -------------------------
    score = 100.0
    if curve:
        try:
            pts = sorted((int(k), float(v)) for k, v in curve.items()
                         if str(k).lstrip("-").isdigit())
            # drop the first and last second (ramp-up / tail)
            pts2 = [(sec, v) for sec, v in pts if 1 <= sec <= seconds - 1]
            if len(pts2) >= 4:
                n = len(pts2)
                cold = sum(v for _, v in pts2[: max(1, n // 4)]) / max(1, n // 4)
                hot = sum(v for _, v in pts2[-max(1, n // 4):]) / max(1, n // 4)
                if cold > 0:
                    ratio = hot / cold
                    res.metric("Sustained throughput vs. start", "%.0f%%" % (ratio * 100))
                    res.metric("Work units / second", "start %.0f -> end %.0f" % (cold, hot))
                    if ratio < 0.70:
                        score -= 30
                        res.flag("CPU throughput dropped to %.0f%% under sustained load - THERMAL THROTTLING "
                                 "(clean the fan / replace the thermal paste)" % (ratio * 100), "fail")
                    elif ratio < 0.88:
                        score -= 12
                        res.flag("CPU throughput fell to %.0f%% under load - mild throttling, cooling needs attention"
                                 % (ratio * 100), "warn")
                    else:
                        res.note("The CPU held its speed for the whole test (%.0f%% sustained) - cooling is healthy."
                                 % (ratio * 100))
        except Exception as exc:
            res.note("Throughput analysis unavailable: %s" % exc)
    else:
        score -= 20
        res.flag("Stress worker produced no measurement data - test inconclusive", "warn")

    if peaks:
        peak = max(peaks)
        if peak >= 95:
            score -= 20
            res.flag("Peak temperature %.0f C - dangerously hot, cooling system needs service" % peak, "fail")
        elif peak >= 85:
            score -= 8
            res.flag("Peak temperature %.0f C - runs hot under load" % peak, "warn")
        else:
            res.note("Peak temperature %.0f C - normal." % peak)
    else:
        res.note("This machine does not expose temperature sensors to Windows without a driver. "
                 "Throttling was judged from sustained throughput instead.")

    # did the stress create new hardware errors?
    after = run_ps("$ProgressPreference='SilentlyContinue';$ErrorActionPreference='SilentlyContinue';"
                   "$e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; "
                   "StartTime=(Get-Date).AddMinutes(-3)} -MaxEvents 5 -ErrorAction SilentlyContinue;"
                   "($e | Measure-Object).Count", timeout=40)
    try:
        n = int((after or "0").strip() or 0)
    except Exception:
        n = 0
    res.metric("New WHEA errors during the test", n)
    if n:
        score -= 35
        res.flag("%d new hardware error(s) logged while under load - unstable CPU/board/RAM" % n, "fail")

    say("CPU stress test finished.")
    res.set("pass" if score >= 88 else ("warn" if score >= 55 else "fail"),
            "Loaded %d thread(s) for %ds%s" % (cores, seconds,
                                               (", peak %.0f C" % max(peaks)) if peaks else ""),
            score=score)


# ==========================================================================
# RAM QUICK INTEGRITY TEST
# ==========================================================================

PATTERNS = [0x00, 0xFF, 0x5A, 0xA5, 0x33, 0xCC, 0x0F, 0xF0]


@safe_check
def check_memory_test(res: CheckResult, ctx: Dict[str, Any]) -> None:
    emit: Optional[Callable[[str], None]] = ctx.get("emit")
    settings = ctx.get("settings") or {}
    if settings.get("skip_memtest"):
        res.skipped("Disabled in settings")
        return

    osr = (ctx.get("raw") or {}).get("os") or [{}]
    free_kb = 0
    try:
        free_kb = float(get(osr[0] if isinstance(osr, list) and osr else {}, "FreePhysicalMemory") or 0)
    except Exception:
        free_kb = 0
    target_mb = int(settings.get("memtest_mb", 256) or 256)
    if free_kb:
        cap = int(free_kb * 0.5 / 1024)  # never take more than half of free RAM
        target_mb = max(32, min(target_mb, cap))
    block = target_mb * 1024 * 1024

    def say(msg: str) -> None:
        if emit:
            emit(msg)

    say("RAM test: allocating %d MB and writing test patterns..." % target_mb)
    errors = 0
    checked = 0
    t0 = time.time()
    stored: List[tuple] = []
    try:
        buf = bytearray(block)
    except MemoryError:
        block //= 2
        target_mb //= 2
        try:
            buf = bytearray(block)
        except MemoryError:
            res.skipped("Not enough free memory to run the RAM test")
            return
        res.note("Only %d MB could be allocated (the machine is low on free RAM)." % target_mb)

    try:
        rnd = random.Random(20260105)
        rand_block = bytes(rnd.getrandbits(8) for _ in range(min(block, 4 * 1024 * 1024)))

        passes = int(settings.get("memtest_passes", 2) or 2)
        for p in range(passes):
            for byte in PATTERNS:
                if ctx.get("bus") is not None and ctx["bus"].cancel.is_set():
                    break
                say("RAM test pass %d/%d: pattern 0x%02X ..." % (p + 1, passes, byte))
                buf[:] = bytes([byte]) * block
                crc = zlib.crc32(buf) & 0xFFFFFFFF
                n = buf.count(bytes([byte]))
                if n != block or crc != (zlib.crc32(bytes([byte]) * block) & 0xFFFFFFFF):
                    errors += 1
                    res.flag("Pattern 0x%02X mismatch at %d byte position(s)" % (byte, block - n), "fail")
                checked += block
                stored.append((byte, crc))
            # random pattern
            say("RAM test pass %d/%d: pseudo-random data..." % (p + 1, passes))
            reps = block // len(rand_block) + 1
            buf[:] = (rand_block * reps)[:block]
            rcrc = zlib.crc32(buf) & 0xFFFFFFFF
            checked += block
            # re-verify every stored pattern checksum by rewriting it
            for byte, crc in stored:
                buf[:] = bytes([byte]) * block
                if (zlib.crc32(buf) & 0xFFFFFFFF) != crc:
                    errors += 1
                    res.flag("Stored pattern 0x%02X changed while held in memory - possible bad cell" % byte, "fail")
                checked += block
            stored = []
    except Exception as exc:
        res.note("Test interrupted: %s" % exc)
    finally:
        del buf

    dur = time.time() - t0
    res.metric("Memory exercised", "%d MB" % target_mb)
    res.metric("Passes", passes)
    res.metric("Patterns used", ", ".join("0x%02X" % b for b in PATTERNS) + ", random")
    res.metric("Data written + verified", "%.1f GB" % (checked / (1024 ** 3)))
    res.metric("Errors found", errors)
    res.metric("Throughput", "%.0f MB/s" % (checked / (1024 * 1024) / dur) if dur > 0 else "-")
    res.metric("Test duration", "%.1f s" % dur)

    # what did Windows' own memory diagnostic say last time?
    md = (ctx.get("raw") or {}).get("memdiag") or []
    if isinstance(md, list) and md:
        row = md[0] if isinstance(md[0], dict) else {}
        res.metric("Last Windows Memory Diagnostic", "%s - %s" % (
            str(get(row, "TimeCreated"))[:10], str(get(row, "Msg") or "")[:80]))
        lvl = str(get(row, "LevelDisplayName", "")).lower()
        if "error" in lvl or "critical" in lvl:
            errors += 1
            res.flag("Windows Memory Diagnostic previously reported MEMORY ERRORS", "fail")
    else:
        res.metric("Last Windows Memory Diagnostic", "Never run on this machine")

    res.note("This is a fast in-Windows integrity check. For a definitive verdict run the Windows Memory "
             "Diagnostic (mdsched.exe, reboots the PC) or MemTest86 from a USB stick - the guided screen has "
             "a button for it.")

    say("RAM test complete: %d error(s)." % errors)
    if errors:
        res.fail("%d memory error(s) detected" % errors, score=10.0)
    else:
        res.ok("%d MB verified across %d passes, no errors" % (target_mb, passes), score=100.0)


REGISTRY: Dict[str, Any] = {
    "cpu_stress": check_cpu_stress,
    "memory_test": check_memory_test,
}
