"""
components.py -- evaluates every AUTOMATIC check from the single system probe.

Rules:
  * A component that does not exist on this machine -> status "na"
    ("Not fitted"). It is NEVER counted as a failure and NEVER drags the
    score down. A laptop without Bluetooth is not a broken laptop.
  * Every number we show the user is stored in result.details so the report
    and the on-screen panel have something to display. No blank screens.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core import (
    CheckResult, get, kelvin_to_c, pct, read_text, run_cmd, run_shell,
    safe_check, temp_path, IS_WINDOWS, fmt_bytes, fmt_gb, fmt_hours, fmt_secs,
)
from collectors import system as S


# ==========================================================================
# helpers
# ==========================================================================

def _rows(ctx: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    raw = ctx.get("raw") or {}
    val = raw.get(key)
    if isinstance(val, list):
        return [r for r in val if isinstance(r, dict)]
    if isinstance(val, dict):
        return [val]
    return []


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        if isinstance(v, str):
            v = v.replace(",", "").strip()
            if not v:
                return None
        return float(v)
    except Exception:
        return None


def _int(v: Any) -> Optional[int]:
    n = _num(v)
    return int(n) if n is not None else None


def _s(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("none", "null") else s


# Get-Service / Get-CimInstance serialize enum properties as INTEGERS in JSON
# (Status 4 == Running, StartType 2 == Automatic). Comparing those against the
# word "Running" made every real machine look broken - decode both forms.
_SVC_STATES = {1: "Stopped", 2: "StartPending", 3: "StopPending", 4: "Running",
               5: "ContinuePending", 6: "PausePending", 7: "Paused"}
_SVC_STARTS = {0: "Boot", 1: "System", 2: "Automatic", 3: "Manual", 4: "Disabled"}


def _svc_state(v: Any) -> str:
    """Normalise a Windows service Status value to its word form."""
    if isinstance(v, bool):
        return _s(v)
    if isinstance(v, int):
        return _SVC_STATES.get(v, "Unknown(%s)" % v)
    s = _s(v)
    if s.isdigit():
        return _SVC_STATES.get(int(s), "Unknown(%s)" % s)
    return s


def _svc_start(v: Any) -> str:
    if isinstance(v, int):
        return _SVC_STARTS.get(v, "Unknown(%s)" % v)
    s = _s(v)
    if s.isdigit():
        return _SVC_STARTS.get(int(s), "Unknown(%s)" % s)
    return s


def _cim_date(v: Any) -> str:
    """CIM DateTime looks like 20190314091200.000000+330."""
    s = _s(v)
    if len(s) >= 14 and s[:14].isdigit():
        try:
            return "%s-%s-%s" % (s[0:4], s[4:6], s[6:8])
        except Exception:
            return s
    if not s:
        return ""
    # JSON-serialised DateTime object
    m = re.search(r"/Date\((-?\d+)\)/", s)
    if m:
        try:
            import datetime as _dt
            return _dt.datetime.utcfromtimestamp(int(m.group(1)) / 1000.0).strftime("%Y-%m-%d")
        except Exception:
            return s
    return s[:19].replace("T", " ")


# ==========================================================================
# IDENTITY
# ==========================================================================

@safe_check
def check_identity(res: CheckResult, ctx: Dict[str, Any]) -> None:
    cs = _rows(ctx, "cs")[:1]
    csp = _rows(ctx, "csp")[:1]
    bios = _rows(ctx, "bios")[:1]
    board = _rows(ctx, "board")[:1]
    enc = _rows(ctx, "enclosure")[:1]
    cs = cs[0] if cs else {}
    csp = csp[0] if csp else {}
    bios = bios[0] if bios else {}
    board = board[0] if board else {}
    enc = enc[0] if enc else {}

    manufacturer = _s(get(cs, "Manufacturer")) or _s(get(csp, "Vendor"))
    model = _s(get(cs, "Model")) or _s(get(csp, "Name"))
    family = _s(get(cs, "SystemFamily"))
    sku = _s(get(cs, "SystemSKUNumber"))
    serial = _s(get(bios, "SerialNumber")) or _s(get(csp, "IdentifyingNumber")) or _s(get(board, "SerialNumber"))
    chassis = S.chassis_label(get(enc, "ChassisTypes"))
    pc_type = _int(get(cs, "PCSystemTypeEx")) or _int(get(cs, "PCSystemType"))
    is_laptop = pc_type == 2 or chassis.lower() in (
        "laptop", "notebook", "portable", "sub notebook", "convertible", "detachable", "tablet"
    ) or bool(_rows(ctx, "battery"))

    res.metric("Manufacturer", manufacturer or "Unknown")
    res.metric("Model", model or "Unknown")
    if family:
        res.metric("Family / Series", family)
    if sku:
        res.metric("Product (SKU)", sku)
    res.metric("Serial number", serial or "Not readable")
    res.metric("Chassis type", chassis)
    res.metric("Form factor", "Laptop / Notebook" if is_laptop else "Desktop or other")
    res.metric("Motherboard", " ".join(x for x in [_s(get(board, "Manufacturer")), _s(get(board, "Product"))] if x) or "-")

    ctx["identity"] = {
        "manufacturer": manufacturer, "model": model, "family": family, "sku": sku,
        "serial": serial, "chassis": chassis, "is_laptop": is_laptop,
    }

    problems = []
    if not serial or serial.lower() in ("to be filled by o.e.m.", "default", "system serial number", "none", "0"):
        problems.append("Serial number is blank or a factory placeholder")
    if not model or model.lower() in ("to be filled by o.e.m.", "system product name", "unknown"):
        problems.append("Model name not reported by the BIOS")
    if not is_laptop:
        res.note("This machine does not report itself as a laptop - battery/portable checks may not apply.")

    if problems:
        res.warn("; ".join(problems), score=55.0)
        for p in problems:
            res.flag(p, "warn")
    else:
        res.ok("%s %s (S/N %s)" % (manufacturer or "?", model or "?", serial or "?"), score=100.0)


# ==========================================================================
# OS + LICENCE
# ==========================================================================

@safe_check
def check_os_license(res: CheckResult, ctx: Dict[str, Any]) -> None:
    osr = _rows(ctx, "os")[:1]
    osr = osr[0] if osr else {}
    lic = _rows(ctx, "license")
    slm = _rows(ctx, "slmgr")[:1]
    slm = slm[0] if slm else {}

    caption = _s(get(osr, "Caption"))
    build = _s(get(osr, "BuildNumber"))
    arch = _s(get(osr, "OSArchitecture"))
    install = _cim_date(get(osr, "InstallDate"))
    lastboot = _cim_date(get(osr, "LastBootUpTime"))
    up = _rows(ctx, "uptime")
    up_secs = _int(get(up[0], "UpSeconds")) if up else None

    res.metric("Operating system", caption or "Unknown")
    res.metric("Build", build or "?")
    res.metric("Architecture", arch or "?")
    res.metric("Installed on", install or "?")
    res.metric("Last boot", lastboot or "?")
    if up_secs:
        res.metric("Uptime", fmt_secs(up_secs))

    # ---- activation -------------------------------------------------
    status_map = {
        0: ("Unlicensed", "fail"), 1: ("Licensed (activated)", "pass"),
        2: ("Out-of-box grace", "warn"), 3: ("Out-of-tolerance grace", "warn"),
        4: ("Non-genuine grace", "fail"), 5: ("Notification mode (NOT activated)", "fail"),
        6: ("Extended grace", "warn"),
    }
    chosen = None
    for row in lic:
        ls = _int(get(row, "LicenseStatus"))
        name = _s(get(row, "Name"))
        if ls is None:
            continue
        label, sev = status_map.get(ls, ("Status %s" % ls, "warn"))
        if chosen is None or ls == 1:
            chosen = (label, sev, name, _s(get(row, "PartialProductKey")))
        if ls == 1:
            break
    if chosen:
        label, sev, name, ppk = chosen
        res.metric("Windows activation", label)
        res.metric("Licence edition", name or "-")
        if ppk:
            res.metric("Product key (last 5)", "*****-" + ppk[-5:] if len(ppk) >= 5 else ppk)
    else:
        label, sev = "Could not read licence state", "warn"
        res.metric("Windows activation", label)

    # OEM key baked into the firmware -> can always be re-activated cleanly
    oem_key = _s(get(slm, "OA3xOriginalProductKey"))
    if oem_key and oem_key.lower() not in ("none", ""):
        res.metric("Firmware (OEM) key present", "Yes - %s*****" % oem_key[:5])
        res.note("A digital licence key is embedded in the motherboard, so Windows can be re-installed and will re-activate automatically.")
    else:
        res.metric("Firmware (OEM) key present", "Not found")

    # ---- Windows 10 support ended 14 Oct 2025 ------------------------
    major = 0
    try:
        major = int(build)
    except Exception:
        major = 0
    win11_cpu = _rows(ctx, "win11cpu")
    cpu_gen = ctx.get("cpu_gen") or {}
    tpm_rows = _rows(ctx, "tpm")
    tpm_spec = _s(get(tpm_rows[0], "SpecVersion")) if tpm_rows else ""
    tpm2 = tpm_spec.startswith("2")
    sb = _rows(ctx, "secureboot")
    secureboot = str(get(sb[0], "SecureBoot")).lower() if sb else ""

    win11_ready = major >= 22000
    win11_capable = bool(cpu_gen.get("win11_ok")) and tpm2 and secureboot in ("true", "1")
    res.metric("Windows 11 installed", "Yes" if win11_ready else "No")
    res.metric("Windows 11 capable (CPU+TPM2+SecureBoot)",
               "Yes" if win11_capable else "No")
    res.metric("TPM", tpm_spec or "Not detected")
    ctx["os"] = {
        "caption": caption, "build": build, "major": major, "arch": arch,
        "activated": sev == "pass", "activation_label": label,
        "oem_key": bool(oem_key), "win11_installed": win11_ready,
        "win11_capable": win11_capable, "tpm_spec": tpm_spec,
        "install_date": install,
    }

    score = 100.0
    if sev == "pass":
        res.note("Windows is activated - buyer gets a genuine, licensed machine.")
    elif sev == "warn":
        score -= 25
        res.flag("Windows is in a grace period - activate before selling", "warn")
    else:
        score -= 45
        res.flag("Windows is NOT activated - buyer will see a watermark", "fail")

    if major and 10240 <= major < 22000:
        # Windows 10
        res.note("Windows 10 reached end of support on 14 Oct 2025. Free security updates have stopped "
                 "(paid ESU is available). This lowers resale value unless Windows 11 is installed.")
        if not win11_capable:
            score -= 15
            res.flag("Windows 10 with no Windows 11 support - out of security updates", "warn")
        else:
            score -= 5
            res.flag("Runs Windows 10 (out of support) but the hardware supports Windows 11 - upgrade recommended", "warn")

    res.set(sev if sev != "pass" else ("pass" if score >= 85 else "warn"),
            "%s - %s" % (caption or "Windows", label), score=score)


# ==========================================================================
# BIOS / TPM / SECURITY
# ==========================================================================

@safe_check
def check_bios_tpm(res: CheckResult, ctx: Dict[str, Any]) -> None:
    bios = _rows(ctx, "bios")[:1]
    bios = bios[0] if bios else {}
    maker = _s(get(bios, "Manufacturer"))
    ver = _s(get(bios, "SMBIOSBIOSVersion")) or _s(get(bios, "Version"))
    rel = _cim_date(get(bios, "ReleaseDate"))
    smbios = "%s.%s" % (_s(get(bios, "SMBIOSMajorVersion")), _s(get(bios, "SMBIOSMinorVersion")))

    tpm_rows = _rows(ctx, "tpm")
    tpm = tpm_rows[0] if tpm_rows else {}
    tpm_spec = _s(get(tpm, "SpecVersion")) or "-"
    tpm_enabled = get(tpm, "IsEnabled_InitialValue")
    tpm_active = get(tpm, "IsActivated_InitialValue")

    sb = _rows(ctx, "secureboot")
    secureboot = str(get(sb[0], "SecureBoot")) if sb else "?"
    if secureboot.lower() in ("true", "1"):
        secureboot = "Enabled"
    elif secureboot.lower() in ("false", "0"):
        secureboot = "Disabled"
    else:
        secureboot = "Not supported (Legacy BIOS)"

    bl = _rows(ctx, "bitlocker")

    res.metric("BIOS vendor", maker or "-")
    res.metric("BIOS version", ver or "-")
    res.metric("BIOS date", rel or "-")
    res.metric("SMBIOS", smbios if smbios != "." else "-")
    res.metric("TPM version", tpm_spec)
    res.metric("TPM enabled", {True: "Yes", False: "No"}.get(tpm_enabled, "Unknown"))
    res.metric("TPM activated", {True: "Yes", False: "No"}.get(tpm_active, "Unknown"))
    res.metric("Secure Boot", secureboot)

    score = 100.0
    if tpm_spec == "-":
        res.note("No TPM chip detected.")
    elif not tpm_spec.startswith("2"):
        score -= 10
        res.flag("TPM %s (Windows 11 needs TPM 2.0)" % tpm_spec, "warn")
    if secureboot == "Disabled":
        score -= 5
        res.note("Secure Boot is disabled - enable it in BIOS before selling (Windows 11 requires it).")
    elif secureboot.startswith("Not supported"):
        score -= 10
        res.flag("Legacy BIOS (no UEFI/Secure Boot) - cannot run Windows 11", "warn")

    # BitLocker: an encrypted, locked drive is a show-stopper for resale
    if bl:
        for row in bl:
            mp = _s(get(row, "MountPoint"))
            vs = _s(get(row, "VolumeStatus"))
            ps = _s(get(row, "ProtectionStatus"))
            res.metric("BitLocker %s" % mp, "%s / protection %s" % (vs or "?", ps or "?"))
            if vs and vs.lower() not in ("fullydecrypted", "fully decrypted"):
                score -= 25
                res.flag("Drive %s is BitLocker-encrypted (%s) - MUST be decrypted or the recovery key handed over"
                         % (mp, vs), "fail")
    else:
        res.metric("BitLocker", "Not available (Windows Home) or not encrypted")

    # BIOS age
    if rel and len(rel) >= 4:
        try:
            age = int(time.strftime("%Y")) - int(rel[:4])
            res.metric("BIOS age", "%d year(s)" % age)
            if age >= 6:
                res.note("BIOS is %d years old - check the manufacturer site for an update." % age)
        except Exception:
            pass

    res.set("pass" if score >= 85 else ("warn" if score >= 55 else "fail"),
            "BIOS %s (%s), TPM %s" % (ver or "?", rel or "?", tpm_spec), score=score)


# ==========================================================================
# CPU
# ==========================================================================

@safe_check
def check_cpu(res: CheckResult, ctx: Dict[str, Any]) -> None:
    rows = _rows(ctx, "cpu")
    if not rows:
        res.skipped("Processor information could not be read")
        return
    cpu = rows[0]
    name = _s(get(cpu, "Name"))
    cores = _int(get(cpu, "NumberOfCores")) or 0
    threads = _int(get(cpu, "NumberOfLogicalProcessors")) or 0
    maxclk = _num(get(cpu, "MaxClockSpeed"))
    curclk = _num(get(cpu, "CurrentClockSpeed"))
    l3 = _num(get(cpu, "L3CacheSize"))
    l2 = _num(get(cpu, "L2CacheSize"))
    virt = get(cpu, "VirtualizationFirmwareEnabled")
    slaat = get(cpu, "SecondLevelAddressTranslationExtensions")

    gen = S.cpu_generation(name)
    ctx["cpu_gen"] = gen

    res.metric("Processor", name or "Unknown")
    res.metric("Cores / Threads", "%d cores / %d threads" % (cores, threads))
    res.metric("Max clock", "%.2f GHz" % (maxclk / 1000.0) if maxclk else "-")
    res.metric("Current clock", "%.2f GHz" % (curclk / 1000.0) if curclk else "-")
    res.metric("Cache", "L2 %s / L3 %s" % (
        ("%d KB" % l2) if l2 else "-", ("%d MB" % (l3 / 1024.0)) if l3 else "-"))
    res.metric("Brand / family", "%s %s" % (gen["brand"], gen["family"]))
    res.metric("Generation", gen["gen_label"])
    res.metric("Performance tier", gen["tier"].title())
    res.metric("Virtualisation (VT-x/AMD-V)", {True: "Enabled", False: "Disabled in BIOS"}.get(virt, "Unknown"))
    res.metric("SLAT (needed for Hyper-V)", {True: "Supported", False: "Not supported"}.get(slaat, "Unknown"))

    score = 100.0
    if cores and cores < 2:
        score -= 25
        res.flag("Single-core CPU - very slow for modern use", "warn")
    if maxclk and maxclk < 1600:
        score -= 8
        res.note("Low maximum clock speed (%.1f GHz)." % (maxclk / 1000.0))
    if gen["gen"] and not gen["win11_ok"]:
        res.note("This CPU generation is not on Microsoft's Windows 11 support list.")
    if virt is False:
        res.note("Virtualisation is switched off in the BIOS - most buyers want it on.")
    if len(rows) > 1:
        res.metric("Physical CPUs", len(rows))

    ctx["cpu"] = {"name": name, "cores": cores, "threads": threads,
                  "max_mhz": maxclk, "gen": gen}
    res.set("pass" if score >= 85 else "warn", name or "Processor detected", score=score)


# ==========================================================================
# MEMORY
# ==========================================================================

@safe_check
def check_memory(res: CheckResult, ctx: Dict[str, Any]) -> None:
    mods = _rows(ctx, "ram")
    cs = _rows(ctx, "cs")[:1]
    cs = cs[0] if cs else {}
    total_wmi = _num(get(cs, "TotalPhysicalMemory"))

    arr = _rows(ctx, "ramarray")[:1]
    arr = arr[0] if arr else {}
    slots_total = _int(get(arr, "MemoryDevices"))
    maxcap = _num(get(arr, "MaxCapacityEx")) or (_num(get(arr, "MaxCapacity")) or 0) * 1024  # KB -> bytes

    if not mods:
        res.skipped("No memory module information reported by the BIOS")
        return

    total = 0
    speeds = []
    lines = []
    makers = set()
    types = set()
    for i, m in enumerate(mods, 1):
        cap = _num(get(m, "Capacity")) or 0
        total += cap
        spd = _int(get(m, "ConfiguredClockSpeed")) or _int(get(m, "Speed")) or 0
        if spd:
            speeds.append(spd)
        mk = _s(get(m, "Manufacturer"))
        pn = _s(get(m, "PartNumber"))
        loc = _s(get(m, "DeviceLocator")) or _s(get(m, "BankLabel")) or ("Slot %d" % i)
        mtype = S.ram_type_label(get(m, "SMBIOSMemoryType"))
        if mk:
            makers.add(mk)
        types.add(mtype)
        res.metric("Module %d (%s)" % (i, loc),
                   "%s %s %s @ %s MHz" % (fmt_gb(cap), mtype, (mk + " " + pn).strip(), spd or "?"))
        lines.append((loc, cap, spd, mtype))

    res.metric("Total RAM installed", fmt_gb(total))
    res.metric("Memory type", ", ".join(sorted(t for t in types if t and t != "Unknown")) or "Unknown")
    res.metric("Running speed", ("%d MHz" % max(speeds)) if speeds else "-")
    res.metric("Modules fitted", "%d" % len(mods))
    res.metric("Slots available", "%d of %s" % (
        (slots_total - len(mods)) if slots_total else "?", slots_total or "?"))
    if maxcap:
        res.metric("Maximum supported", fmt_gb(maxcap))

    score = 100.0
    if total_wmi and total_wmi > 0:
        os_total = _num((_rows(ctx, "os") or [{}])[0].get("TotalVisibleMemorySize"))
        if os_total:
            res.metric("Seen by Windows", fmt_gb(os_total * 1024))
            if os_total * 1024 < total * 0.93:
                score -= 20
                res.flag("Windows sees less RAM than is physically fitted (%s vs %s) - a module may be faulty or mis-seated"
                         % (fmt_gb(os_total * 1024), fmt_gb(total)), "fail")

    if len(set(speeds)) > 1:
        score -= 5
        res.flag("Mixed memory speeds (%s MHz) - all modules run at the slowest one"
                 % "/".join(str(s) for s in sorted(set(speeds))), "warn")
    if len(makers) > 1:
        res.note("Memory modules are from different manufacturers (%s) - usually fine, but a sign of a repair."
                 % ", ".join(sorted(makers)))
    if len(mods) == 1 and slots_total and slots_total > 1:
        res.note("Single-channel memory: only one module fitted. Adding a second identical module gives a real speed boost "
                 "and raises the resale price.")
    total_gb = total / (1024 ** 3)
    if total_gb <= 4:
        score -= 12
        res.flag("Only %d GB RAM - too little for Windows 11 / modern browsers, an upgrade is expected" % int(total_gb), "warn")
    elif total_gb <= 8:
        res.note("8 GB RAM is the practical minimum today.")

    ctx["memory"] = {"total_bytes": total, "total_gb": total_gb, "modules": len(mods),
                     "slots": slots_total, "free_slots": (slots_total - len(mods)) if slots_total else 0,
                     "speed": max(speeds) if speeds else 0,
                     "type": ", ".join(sorted(t for t in types if t)), "single_channel": len(mods) == 1}
    res.set("pass" if score >= 85 else ("warn" if score >= 55 else "fail"),
            "%s fitted (%s)" % (fmt_gb(total), res.metric and "" or ""), score=score)
    res.summary = "%s %s in %d module(s)" % (
        fmt_gb(total), ctx["memory"]["type"] or "RAM", len(mods))


# ==========================================================================
# STORAGE
# ==========================================================================

@safe_check
def check_storage(res: CheckResult, ctx: Dict[str, Any]) -> None:
    disks = _rows(ctx, "disks")
    rel_rows = _rows(ctx, "reliability")
    vols = _rows(ctx, "volumes")

    if not disks:
        # fall back to Win32_DiskDrive
        from app.core import cim
        disks = cim("Win32_DiskDrive", ["Model", "Size", "MediaType", "InterfaceType", "Status", "SerialNumber", "Partitions"])
        if not disks:
            res.skipped("No storage controller information available")
            return
        for d in disks:
            d["_fallback"] = True

    rel_by_id: Dict[str, Dict[str, Any]] = {}
    for r in rel_rows:
        rel_by_id[str(get(r, "DeviceId"))] = r

    score = 100.0
    ctx["storage"] = {"disks": [], "total_bytes": 0, "has_hdd": False, "has_ssd": False,
                      "boot_is_ssd": False, "max_wear": 0.0, "problems": []}
    total_bytes = 0

    for i, d in enumerate(disks, 1):
        name = _s(get(d, "FriendlyName")) or _s(get(d, "Model")) or "Disk %d" % i
        size = _num(get(d, "Size")) or 0
        total_bytes += size
        media = _s(get(d, "MediaType")) or "Unspecified"
        bus = _s(get(d, "BusType")) or _s(get(d, "InterfaceType")) or "?"
        health = _s(get(d, "HealthStatus")) or "?"
        opstat = _s(get(d, "OperationalStatus"))
        serial = _s(get(d, "SerialNumber"))
        did = str(get(d, "DeviceId", i - 1))
        r = rel_by_id.get(did, {})

        label = "%s (%s %s)" % (name, bus, media)
        res.metric("Disk %d" % i, "%s - %s" % (label, fmt_bytes(size)))
        res.metric("  Health (SMART)", "%s / %s" % (health, opstat or "-"))
        res.metric("  Serial", serial or "-")

        wear = _num(get(r, "Wear"))
        poh = _num(get(r, "PowerOnHours"))
        temp = _num(get(r, "Temperature"))
        read_err = _num(get(r, "ReadErrorsTotal"))
        read_unc = _num(get(r, "ReadErrorsUncorrected"))
        write_err = _num(get(r, "WriteErrorsTotal"))
        write_unc = _num(get(r, "WriteErrorsUncorrected"))
        ssc = _num(get(r, "StartStopCycleCount"))

        if wear is not None:
            res.metric("  Wear used", "%d%%" % wear)
        if poh:
            res.metric("  Power-on time", fmt_hours(poh))
        if temp:
            tc = temp - 273.0 if temp > 150 else temp
            res.metric("  Temperature", "%.0f C" % tc)
        if read_err is not None or write_err is not None:
            res.metric("  SMART errors", "read %s (uncorrected %s) / write %s (uncorrected %s)" % (
                _int(read_err), _int(read_unc), _int(write_err), _int(write_unc)))

        entry = {"name": name, "size": size, "media": media, "bus": bus,
                 "health": health, "wear": wear, "poh": poh, "serial": serial,
                 "is_boot": bool(get(d, "IsBoot")) or bool(get(d, "IsSystem"))}
        ctx["storage"]["disks"].append(entry)

        # ---- evaluate -------------------------------------------------
        dl = health.lower()
        if dl in ("unhealthy", "bad", "failed"):
            score -= 45
            res.flag("Disk %d SMART health = %s - DRIVE IS FAILING, replace before selling" % (i, health), "fail")
            ctx["storage"]["problems"].append("failing drive")
        elif dl in ("warning", "degraded"):
            score -= 25
            res.flag("Disk %d SMART health = Warning - drive is degrading" % i, "warn")
            ctx["storage"]["problems"].append("degrading drive")

        if wear is not None and wear > 0:
            ctx["storage"]["max_wear"] = max(ctx["storage"]["max_wear"], wear)
            if wear >= 90:
                score -= 35
                res.flag("SSD %d has used %d%% of its life - replace soon" % (i, wear), "fail")
            elif wear >= 70:
                score -= 18
                res.flag("SSD %d has used %d%% of its life" % (i, wear), "warn")
            elif wear >= 40:
                score -= 8
                res.note("SSD %d wear indicator: %d%% used." % (i, wear))
        if (read_unc or 0) > 0 or (write_unc or 0) > 0:
            score -= 30
            res.flag("Disk %d logged UNCORRECTED read/write errors - data at risk" % i, "fail")
        elif (read_err or 0) > 0 or (write_err or 0) > 0:
            score -= 8
            res.note("Disk %d logged %s read / %s write errors (corrected)." % (i, _int(read_err), _int(write_err)))
        if poh and poh > 40000:
            score -= 6
            res.note("Disk %d has %s of power-on time - heavily used." % (i, fmt_hours(poh)))

        ml = media.lower()
        if "ssd" in ml or "solid" in ml or "nvme" in bus.lower():
            ctx["storage"]["has_ssd"] = True
            if entry["is_boot"]:
                ctx["storage"]["boot_is_ssd"] = True
        elif "hdd" in ml or "hard" in ml:
            ctx["storage"]["has_hdd"] = True
            score -= 12
            res.flag("Disk %d is a mechanical HDD (%s) - buyers expect an SSD; upgrading adds value" % (i, fmt_gb(size)), "warn")
            if ssc and ssc > 20000:
                res.note("Start/stop cycles: %d (high for a laptop HDD)." % ssc)
        else:
            # MediaType unknown: infer from bus / spindle speed
            spindle = _int(get(d, "SpindleSpeed"))
            if spindle and spindle > 0:
                ctx["storage"]["has_hdd"] = True
                entry["media"] = "HDD"
                score -= 12
                res.flag("Disk %d is a mechanical HDD (%d RPM)" % (i, spindle), "warn")
            else:
                ctx["storage"]["has_ssd"] = True
                entry["media"] = "SSD"

    ctx["storage"]["total_bytes"] = total_bytes
    res.metric("Total storage", fmt_bytes(total_bytes))

    # ---- volumes / free space -----------------------------------------
    for v in vols:
        letter = _s(get(v, "DriveLetter"))
        if not letter:
            continue
        size = _num(get(v, "Size")) or 0
        free = _num(get(v, "SizeRemaining")) or 0
        fs = _s(get(v, "FileSystemType"))
        vh = _s(get(v, "HealthStatus"))
        if size:
            res.metric("Drive %s:" % letter, "%s free of %s (%s) - %s" % (
                fmt_bytes(free), fmt_bytes(size), fs or "?", vh or "?"))
            if vh and vh.lower() not in ("healthy",):
                score -= 10
                res.flag("Volume %s: reports %s" % (letter, vh), "warn")
            if size > 0 and free / size < 0.05:
                res.note("Drive %s: has only %.0f%% free space." % (letter, 100.0 * free / size))

    trim = _rows(ctx, "trim")
    if not trim:
        # fsutil returns text, not an object
        raw_trim = (ctx.get("raw") or {}).get("trim")
        if isinstance(raw_trim, str) and raw_trim.strip():
            res.metric("TRIM (SSD optimisation)", raw_trim.strip().splitlines()[-1][:70])

    if ctx["storage"]["has_ssd"] and ctx["storage"]["boot_is_ssd"]:
        res.note("Windows boots from an SSD - good.")
    elif ctx["storage"]["has_ssd"]:
        res.note("An SSD is fitted but Windows may not be booting from it - check.")

    ctx["storage"]["score_penalty"] = 100.0 - score
    res.set("pass" if score >= 85 else ("warn" if score >= 50 else "fail"),
            "%s across %d disk(s)%s" % (fmt_bytes(total_bytes), len(disks),
                                        ", SSD" if ctx["storage"]["has_ssd"] else ", HDD"),
            score=score)


# ==========================================================================
# GRAPHICS
# ==========================================================================

@safe_check
def check_gpu(res: CheckResult, ctx: Dict[str, Any]) -> None:
    gpus = _rows(ctx, "gpu")
    if not gpus:
        res.skipped("No graphics adapter information")
        return
    score = 100.0
    names = []
    dedicated = False
    for i, g in enumerate(gpus, 1):
        name = _s(get(g, "Name"))
        drv = _s(get(g, "DriverVersion"))
        drvdate = _cim_date(get(g, "DriverDate"))
        vram = _num(get(g, "AdapterRAM"))
        hres = _int(get(g, "CurrentHorizontalResolution"))
        vres = _int(get(g, "CurrentVerticalResolution"))
        hz = _int(get(g, "CurrentRefreshRate"))
        bpp = _int(get(g, "CurrentBitsPerPixel"))
        status = _s(get(g, "Status"))
        names.append(name)
        res.metric("GPU %d" % i, name or "Unknown")
        res.metric("  Driver", "%s (%s)" % (drv or "?", drvdate or "?"))
        if vram:
            # WMI reports AdapterRAM as a 32-bit value: caps out at 4 GB
            shown = fmt_bytes(vram) + (" (WMI caps this field at 4 GB)" if vram >= 4294967295 else "")
            res.metric("  Video memory", shown)
        if hres and vres:
            res.metric("  Output mode", "%dx%d @ %s Hz, %s-bit colour" % (hres, vres, hz or "?", bpp or "?"))
        res.metric("  Status", status or "?")

        nl = (name or "").lower()
        if any(k in nl for k in ("nvidia", "geforce", "radeon rx", "rtx", "gtx", "arc a", "quadro")):
            dedicated = True
        if status and status.lower() != "ok":
            score -= 30
            res.flag("Graphics adapter '%s' reports status %s" % (name, status), "fail")
        if drvdate and len(drvdate) >= 4:
            try:
                if int(time.strftime("%Y")) - int(drvdate[:4]) >= 6:
                    score -= 4
                    res.note("Graphics driver is from %s - a newer driver is almost certainly available." % drvdate[:4])
            except Exception:
                pass

    res.metric("Dedicated graphics", "Yes" if dedicated else "No (integrated only)")
    ctx["gpu"] = {"names": names, "dedicated": dedicated, "count": len(gpus)}
    res.set("pass" if score >= 85 else ("warn" if score >= 55 else "fail"),
            ", ".join(n for n in names if n) or "Graphics detected", score=score)


# ==========================================================================
# DISPLAY + PANEL
# ==========================================================================

@safe_check
def check_display(res: CheckResult, ctx: Dict[str, Any]) -> None:
    gpus = _rows(ctx, "gpu")
    mons = _rows(ctx, "desktopmonitor")
    mid = _rows(ctx, "monitorid")
    mbp = _rows(ctx, "monitorbasic")
    mconn = _rows(ctx, "monitorconn")
    bright = _rows(ctx, "brightness")
    screens = _rows(ctx, "videores")
    edids = S.decode_edid_list(_rows(ctx, "edid"))

    score = 100.0
    native_w = native_h = None
    panel_size = None
    panel_maker = None
    panel_name = None
    panel_year = None

    if edids:
        # prefer the internal panel (LVDS/eDP) if we can tell
        e = edids[0]
        for cand in edids:
            if cand.get("native_width"):
                e = cand
                break
        native_w = e.get("native_width")
        native_h = e.get("native_height")
        panel_maker = e.get("manufacturer")
        panel_name = e.get("monitor_name")
        panel_year = e.get("year")
        panel_size = e.get("diagonal_inch")
        res.metric("Panel manufacturer", panel_maker or "-")
        res.metric("Panel model", panel_name or "-")
        res.metric("Panel native resolution",
                   ("%dx%d" % (native_w, native_h)) if native_w else "-")
        if panel_size:
            res.metric("Panel diagonal", '%.1f"' % panel_size)
        if panel_year and 1995 < panel_year < 2100:
            res.metric("Panel manufactured", "%s (week %s)" % (panel_year, e.get("week")))
            age = int(time.strftime("%Y")) - int(panel_year)
            if age >= 7:
                res.note("The panel itself is about %d years old." % age)
        if e.get("serial_string"):
            res.metric("Panel serial", e["serial_string"])
        if e.get("max_v_rate"):
            res.metric("Panel refresh range", "%d-%d Hz" % (e.get("min_v_rate", 0), e.get("max_v_rate", 0)))
        res.metric("EDID checksum", "Valid" if e.get("checksum_ok") else "INVALID")
        if not e.get("checksum_ok"):
            score -= 5
            res.note("EDID checksum invalid - the panel or cable may have a fault.")
    else:
        res.metric("Panel EDID", "Not readable (needs Administrator rights on some machines)")
        res.note("Run as Administrator to read panel EDID details.")

    # current mode
    cur_w = cur_h = hz = None
    for g in gpus:
        w = _int(get(g, "CurrentHorizontalResolution"))
        h = _int(get(g, "CurrentVerticalResolution"))
        if w and h:
            cur_w, cur_h = w, h
            hz = _int(get(g, "CurrentRefreshRate"))
            break
    if not cur_w and screens:
        cur_w = _int(get(screens[0], "W"))
        cur_h = _int(get(screens[0], "H"))
    if cur_w:
        res.metric("Current resolution", "%dx%d%s" % (cur_w, cur_h, (" @ %d Hz" % hz) if hz else ""))
    res.metric("Displays connected", len(screens) or len(mons) or 1)

    if native_w and cur_w:
        if cur_w < native_w:
            score -= 10
            res.flag("Screen is running at %dx%d but the panel is %dx%d - not using full resolution"
                     % (cur_w, cur_h, native_w, native_h), "warn")

    # basic classification for pricing
    klass = "HD (1366x768)"
    px = (cur_w or native_w or 0) * (cur_h or native_h or 0)
    if px >= 3840 * 2160 * 0.8:
        klass = "4K UHD"
    elif px >= 2560 * 1440 * 0.8:
        klass = "QHD (2560x1440)"
    elif px >= 1920 * 1080 * 0.8:
        klass = "Full HD (1920x1080)"
    elif px >= 1600 * 900 * 0.8:
        klass = "HD+ (1600x900)"
    res.metric("Resolution class", klass)
    if klass.startswith("HD "):
        score -= 6
        res.note("Low-resolution (1366x768) panel - this reduces resale value.")

    # connection type (internal vs external)
    for c in mconn:
        res.metric("Output %s" % (_s(get(c, "InstanceName"))[:18] or "?"),
                   S.video_output_label(get(c, "VideoOutputTechnology")))

    # brightness control
    if bright:
        lvl = get(bright[0], "Levels")
        cur = _int(get(bright[0], "CurrentBrightness"))
        n = len(lvl) if isinstance(lvl, list) else 0
        res.metric("Backlight brightness", "%s%% (%d levels reported)" % (cur if cur is not None else "?", n))
        if n and n < 5:
            score -= 5
            res.note("Only %d brightness levels reported - the display driver may be the generic Microsoft one." % n)
    else:
        res.metric("Backlight brightness", "Not controllable from Windows (external monitor or driver issue)")

    # touch screen?
    touch = False
    for row in _rows(ctx, "hid") + _rows(ctx, "pnp_touch"):
        nm = (_s(get(row, "FriendlyName"))).lower()
        if "touch" in nm:
            touch = True
            break
    res.metric("Touch screen", "Yes" if touch else "No")
    ctx["display"] = {"native_w": native_w, "native_h": native_h, "cur_w": cur_w, "cur_h": cur_h,
                      "hz": hz, "size_inch": panel_size, "klass": klass, "panel_maker": panel_maker,
                      "panel_name": panel_name, "panel_year": panel_year, "touch": touch,
                      "brightness": cur if bright else None}
    res.set("pass" if score >= 88 else ("warn" if score >= 60 else "fail"),
            "%s panel, %s" % ('%.1f"' % panel_size if panel_size else "Internal", klass), score=score)


# ==========================================================================
# BATTERY
# ==========================================================================

def collect_battery_report(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Run powercfg /batteryreport and pull design/full/cycle numbers out of it."""
    out: Dict[str, Any] = {}
    if not IS_WINDOWS:
        return out
    path = temp_path("lcp-battery", ".html")
    rc, so, se = run_cmd(["powercfg", "/batteryreport", "/output", path], timeout=90)
    text = ""
    if os.path.exists(path):
        text = read_text(path)
        try:
            os.remove(path)
        except Exception:
            pass
    if not text:
        out["error"] = (se or "powercfg produced no report").strip()[:200]
        return out
    flat = re.sub(r"<[^>]+>", " ", text)
    flat = re.sub(r"&nbsp;|&#160;", " ", flat)
    flat = re.sub(r"\s+", " ", flat)

    def cap(label: str) -> Optional[float]:
        m = re.search(label + r"\s*[:\-]?\s*([\d,]+(?:\.\d+)?)\s*(mWh|Wh)", flat, re.I)
        if not m:
            return None
        v = float(m.group(1).replace(",", ""))
        return v / 1000.0 if m.group(2).lower() == "mwh" else v

    out["design_wh"] = cap(r"DESIGN(?:ED)?\s+CAPACITY")
    out["full_wh"] = cap(r"FULL\s+CHARGE\s+CAPACITY")
    m = re.search(r"CYCLE\s+COUNT\s*[:\-]?\s*([\d,]+)", flat, re.I)
    if m:
        out["cycles"] = int(m.group(1).replace(",", ""))
    for lbl, key in ((r"MANUFACTURER\s*[:\-]?\s*([A-Za-z0-9 \-_\.]+?)\s+(?:CHEMISTRY|SERIAL|DESIGN)", "manufacturer"),
                     (r"CHEMISTRY\s*[:\-]?\s*([A-Za-z]+)", "chemistry"),
                     (r"SERIAL\s+NUMBER\s*[:\-]?\s*([A-Za-z0-9\-]+)", "serial"),
                     (r"DESIGN\s+CAPACITY\s*[:\-]?\s*([\d,]+)\s*m?Wh", "_x")):
        mm = re.search(lbl, flat, re.I)
        if mm and key != "_x":
            out[key] = mm.group(1).strip()
    return out


@safe_check
def check_battery(res: CheckResult, ctx: Dict[str, Any]) -> None:
    batts = _rows(ctx, "battery")
    static = _rows(ctx, "batterystatic")
    fullc = _rows(ctx, "batteryfull")
    cycles = _rows(ctx, "batterycycle")
    status_rows = _rows(ctx, "batterystatus")
    report = ctx.get("battery_report") or {}
    ps_status = ctx.get("power_status") or {}

    has_battery = bool(batts) or bool(static) or bool(fullc) or ps_status.get("percent") is not None
    if not has_battery or ps_status.get("no_battery"):
        res.na("No battery detected in this machine")
        res.note("Either this is a desktop/all-in-one, or the battery is completely dead / removed. "
                 "For a laptop this must be priced as 'needs a new battery'.")
        ctx["battery"] = {"present": False}
        return

    design_mwh = _num(get(static[0], "DesignedCapacity")) if static else None
    full_mwh = _num(get(fullc[0], "FullChargedCapacity")) if fullc else None
    cyc = _int(get(cycles[0], "CycleCount")) if cycles else None
    chem = None
    dev_name = None
    if static:
        chem = _int(get(static[0], "Chemistry"))
        dev_name = _s(get(static[0], "DeviceName"))
    if report.get("design_wh"):
        design_mwh = report["design_wh"] * 1000.0
    if report.get("full_wh"):
        full_mwh = report["full_wh"] * 1000.0
    if report.get("cycles") is not None:
        cyc = report["cycles"]
    if not chem and batts:
        chem = _int(get(batts[0], "Chemistry"))

    health = pct(full_mwh, design_mwh) if (design_mwh and full_mwh) else None
    wear = round(100.0 - health, 1) if health is not None else None

    res.metric("Battery present", "Yes")
    res.metric("Device name", dev_name or _s(get(batts[0], "Name")) or "-")
    res.metric("Chemistry", S.battery_chem_label(chem) if chem else (report.get("chemistry") or "-"))
    if design_mwh:
        res.metric("Design capacity", "%s mWh (%.1f Wh)" % (int(design_mwh), design_mwh / 1000.0))
    if full_mwh:
        res.metric("Full charge capacity", "%s mWh (%.1f Wh)" % (int(full_mwh), full_mwh / 1000.0))
    if health is not None:
        res.metric("Battery health", "%.1f%%" % health)
        res.metric("Wear", "%.1f%%" % wear)
    else:
        res.metric("Battery health", "Not reported by this machine")
    if cyc is not None:
        res.metric("Charge cycles", cyc)
    if ps_status.get("percent") is not None:
        res.metric("Charge right now", "%d%% (%s)" % (ps_status["percent"], ps_status.get("ac", "?")))
    if batts:
        res.metric("Charging state", S.battery_status_label(get(batts[0], "BatteryStatus")))
    if ps_status.get("remaining_seconds"):
        res.metric("Estimated runtime left", fmt_secs(ps_status["remaining_seconds"]))
    if status_rows:
        est = _num(get(status_rows[0], "EstimatedTime"))
        if est and est < 65535:
            res.metric("Windows runtime estimate", fmt_secs(est * 60))
        rc_mw = _num(get(status_rows[0], "DischargeRate"))
        if rc_mw:
            res.metric("Current draw", "%.1f W" % (rc_mw / 1000.0))
    if report.get("error"):
        res.note("powercfg battery report failed: %s" % report["error"])

    score = 100.0
    ctx["battery"] = {"present": True, "design_mwh": design_mwh, "full_mwh": full_mwh,
                      "health": health, "wear": wear, "cycles": cyc}

    if health is not None:
        if health >= 85:
            res.note("Battery is in good shape (%.0f%% of original capacity)." % health)
            score = 100.0
        elif health >= 70:
            score = 82.0
            res.flag("Battery health %.0f%% - acceptable but noticeably used" % health, "warn")
        elif health >= 55:
            score = 60.0
            res.flag("Battery health %.0f%% - runtime will be short; buyers usually want a replacement" % health, "warn")
        else:
            score = 30.0
            res.flag("Battery health %.0f%% - effectively dead, must be replaced" % health, "fail")
    else:
        score = 70.0
        res.flag("Battery health could not be measured - test runtime manually before pricing", "warn")

    if cyc is not None:
        if cyc > 800:
            score -= 12
            res.flag("%d charge cycles - the battery is well past its design life (typically 500-1000)" % cyc, "warn")
        elif cyc > 500:
            score -= 6
            res.note("%d charge cycles recorded." % cyc)

    if not ps_status.get("ac") == "AC connected" and ps_status.get("percent") is not None:
        if ps_status["percent"] < 15 and not ps_status.get("charging"):
            res.note("Battery is low and not charging - plug in the adapter for the charging test.")
    res.set("pass" if score >= 85 else ("warn" if score >= 50 else "fail"),
            ("Health %.0f%% (%d cycles)" % (health, cyc)) if health is not None and cyc is not None
            else ("Health %.0f%%" % health if health is not None else "Battery present, health unknown"),
            score=score)


@safe_check
def check_charger(res: CheckResult, ctx: Dict[str, Any]) -> None:
    ps = ctx.get("power_status") or {}
    batt = ctx.get("battery") or {}
    if not batt.get("present"):
        res.na("No battery - AC adapter test not applicable")
        return
    ac = ps.get("ac", "Unknown")
    charging = ps.get("charging")
    percent = ps.get("percent")
    res.metric("Power source", ac)
    res.metric("Charging flag", {True: "Yes", False: "No"}.get(charging, "Unknown"))
    res.metric("Charge level", ("%d%%" % percent) if percent is not None else "Unknown")
    if status_rows := _rows(ctx, "batterystatus"):
        res.metric("Charge rate", "%.1f W" % ((_num(get(status_rows[0], "ChargeRate")) or 0) / 1000.0))
        res.metric("Power online", get(status_rows[0], "PowerOnline"))

    if ac == "AC connected":
        if percent is not None and percent >= 99:
            res.ok("Adapter connected, battery full", score=100.0)
        elif charging:
            res.ok("Adapter connected and the battery IS charging", score=100.0)
        else:
            res.warn("Adapter connected but the battery is NOT charging - suspect charger, charging port or battery", score=45.0)
            res.flag("Plugged in but not charging - test with a known-good adapter", "fail")
        res.note("Tip: watch the charge %% for 2-3 minutes. If it does not move, the charging circuit or the "
                 "DC jack needs attention.")
    else:
        res.warn("Running on battery - plug the adapter in to complete the charging test", score=70.0)
        res.flag("Charging test incomplete: adapter was not connected during the scan", "warn")


# ==========================================================================
# WIRELESS: WI-FI / BLUETOOTH / ETHERNET
# ==========================================================================

def _net_adapters(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = _rows(ctx, "netadapter")
    if rows:
        return rows
    from app.core import cim
    return cim("Win32_NetworkAdapter", ["Name", "NetConnectionID", "NetEnabled", "PhysicalAdapter",
                                        "Speed", "MACAddress", "NetConnectionStatus", "PNPDeviceID"])


@safe_check
def check_wifi(res: CheckResult, ctx: Dict[str, Any]) -> None:
    adapters = _net_adapters(ctx)
    wifi = []
    for a in adapters:
        blob = " ".join(_s(get(a, k)) for k in ("InterfaceDescription", "Name", "PhysicalMediaType", "Description")).lower()
        # WWAN / cellular modules say "Wireless WAN" but are NOT Wi-Fi radios
        if any(k in blob for k in ("wwan", "cellular", "sierra", "quectel", "fibocom",
                                   "wireless wan", "lte-a", "snapdragon x")):
            continue
        if any(k in blob for k in ("wireless", "wi-fi", "wifi", "802.11", "wlan", "native 802.11")):
            wifi.append(a)
    drv = ctx.get("wlan_driver") or {}
    iface = ctx.get("wlan_interface") or {}

    if not wifi and not drv.get("has_wifi_driver"):
        res.na("No Wi-Fi adapter fitted")
        res.note("This machine has no wireless card (or it is disabled in the BIOS). "
                 "Price it accordingly, or fit a USB/M.2 Wi-Fi adapter.")
        ctx["wifi"] = {"present": False}
        return

    ctx["wifi"] = {"present": True}
    score = 100.0
    for i, a in enumerate(wifi, 1):
        name = _s(get(a, "InterfaceDescription")) or _s(get(a, "Name")) or _s(get(a, "Description"))
        status = _s(get(a, "Status")) or ("Connected" if get(a, "NetEnabled") else "Disabled")
        res.metric("Adapter", name)
        res.metric("Status", status)
        res.metric("MAC", _s(get(a, "MacAddress")) or "-")
        ls = _s(get(a, "LinkSpeed"))
        if ls:
            res.metric("Link speed", ls)
        if status.lower() in ("disabled", "not present"):
            score -= 25
            res.flag("Wi-Fi adapter is present but DISABLED", "warn")

    if drv:
        res.metric("Driver", "%s %s" % (drv.get("driver") or "?", drv.get("driver_version") or ""))
        res.metric("Driver date", drv.get("driver_date") or "-")
        if drv.get("radio_types"):
            res.metric("Radio standards", ", ".join(drv["radio_types"]))
            best = max([r for r in drv["radio_types"] if r.lower().startswith("802.11")] or ["802.11b"],
                       key=lambda r: r.lower())
            if best.lower() in ("802.11b", "802.11g", "802.11a"):
                score -= 10
                res.flag("Wi-Fi card only supports %s - very old, 2.4 GHz only" % best, "warn")
            elif "802.11ac" in best.lower():
                res.note("Wi-Fi 5 (802.11ac) - fine for most buyers.")
            elif "802.11ax" in best.lower() or "802.11be" in best.lower():
                res.note("Wi-Fi 6/6E - a selling point.")
    if iface:
        res.metric("Interface state", iface.get("state") or "-")
        if iface.get("ssid"):
            res.metric("Connected network", iface.get("ssid"))
        if iface.get("signal_percent") is not None:
            res.metric("Signal strength", "%d%%" % iface["signal_percent"])
            if iface["signal_percent"] < 40:
                res.note("Weak signal at the time of the test (%d%%) - move closer to the router and re-test "
                         "before concluding the card is faulty." % iface["signal_percent"])
        if iface.get("radio_type"):
            res.metric("Current radio", iface.get("radio_type"))
        if iface.get("rx_mbps"):
            res.metric("Rx / Tx rate", "%s / %s Mbps" % (iface.get("rx_mbps"), iface.get("tx_mbps")))
    svc = [r for r in _rows(ctx, "services") if _s(get(r, "Name")).lower() == "wlansvc"]
    if svc:
        st = _svc_state(get(svc[0], "Status"))
        res.metric("WLAN service", st)
        if st.lower() != "running":
            score -= 20
            res.flag("The Windows WLAN AutoConfig service is not running - Wi-Fi cannot work until it is started", "fail")

    if iface.get("service_stopped"):
        score -= 20
        res.flag("WLAN AutoConfig service is stopped", "fail")
    if not wifi:
        res.note("A Wi-Fi driver was found but no active network adapter - the card may be disabled in BIOS.")
        score -= 20

    state = (iface.get("state") or "").lower()
    res.set("pass" if score >= 85 else ("warn" if score >= 50 else "fail"),
            "Wi-Fi adapter %s" % (
                ("connected to '%s'" % iface.get("ssid")) if state == "connected" and iface.get("ssid")
                else ("connected" if state == "connected" else "present")),
            score=score)


@safe_check
def check_bluetooth(res: CheckResult, ctx: Dict[str, Any]) -> None:
    bt = _rows(ctx, "bluetooth")
    svc = [r for r in _rows(ctx, "services") if _s(get(r, "Name")).lower() == "bthserv"]
    radios = [b for b in bt if "radio" in _s(get(b, "FriendlyName")).lower()
              or _s(get(b, "InstanceId")).upper().startswith(("USB\\", "BTH\\MS_BTHBRB"))]
    if not bt:
        # A Bluetooth radio can be present but driver-less (Device Manager Code 28,
        # e.g. Intel radios show as USB\VID_8087). That is a fixable fault, not
        # "no hardware" - say which one it is.
        missing = [p for p in _rows(ctx, "problems")
                   if "VID_8087" in _s(get(p, "InstanceId")).upper()
                   or _s(get(p, "Class")).lower() == "bluetooth"]
        if missing:
            ctx["bluetooth"] = {"present": True, "driver_missing": True}
            res.metric("Bluetooth radio", "present but WITHOUT driver (Device Manager Code %s)"
                       % (_s(get(missing[0], "Problem")) or "28"))
            res.flag("Bluetooth radio is fitted but its driver is not installed - "
                     "install the Bluetooth driver (usually bundled with the chipset/Wi-Fi driver) "
                     "and re-test. The tool cannot install drivers itself.", "warn")
            res.set("warn", "Bluetooth hardware present, driver missing", score=55.0)
            return
        res.na("No Bluetooth hardware fitted")
        res.note("No Bluetooth radio was found. Common on budget and some business models. "
                 "A USB Bluetooth dongle costs very little if a buyer needs it.")
        ctx["bluetooth"] = {"present": False}
        return
    ctx["bluetooth"] = {"present": True, "devices": len(bt)}
    score = 100.0
    for i, b in enumerate(bt[:8], 1):
        nm = _s(get(b, "FriendlyName"))
        st = _s(get(b, "Status"))
        res.metric("BT device %d" % i, "%s - %s" % (nm, st))
        if st and st.lower() != "ok":
            score -= 20
            res.flag("Bluetooth device '%s' has a problem (status %s)" % (nm, st), "warn")
    if len(bt) > 8:
        res.note("%d Bluetooth-related entries in total." % len(bt))
    if svc:
        st = _svc_state(get(svc[0], "Status"))
        res.metric("Bluetooth service", "%s (%s)" % (st, _svc_start(get(svc[0], "StartType"))))
        if st.lower() != "running":
            score -= 10
            res.note("The Bluetooth Support Service is not running - start it before testing pairing.")
    res.metric("Radios detected", len(radios) or "see list")
    res.set("pass" if score >= 85 else "warn",
            "Bluetooth present (%d entr%s)" % (len(bt), "y" if len(bt) == 1 else "ies"), score=score)
    res.note("Pairing test: in the guided phase, pair a phone or a mouse to confirm the radio really works.")


@safe_check
def check_ethernet(res: CheckResult, ctx: Dict[str, Any]) -> None:
    adapters = _net_adapters(ctx)
    eth = []
    for a in adapters:
        blob = " ".join(_s(get(a, k)) for k in ("InterfaceDescription", "Name", "PhysicalMediaType", "Description")).lower()
        if any(k in blob for k in ("ethernet", "802.3", "realtek pcie gbe", "gigabit", "lan ", "family controller")) \
           and "virtual" not in blob and "bluetooth" not in blob and "wi-fi" not in blob and "wireless" not in blob:
            eth.append(a)
    if not eth:
        res.na("No built-in Ethernet port")
        ctx["ethernet"] = {"present": False}
        return
    ctx["ethernet"] = {"present": True}
    score = 100.0
    for i, a in enumerate(eth, 1):
        nm = _s(get(a, "InterfaceDescription")) or _s(get(a, "Name")) or _s(get(a, "Description"))
        st = _s(get(a, "Status")) or "Unknown"
        res.metric("LAN adapter", nm)
        res.metric("Status", st)
        res.metric("Link speed", _s(get(a, "LinkSpeed")) or "-")
        res.metric("MAC", _s(get(a, "MacAddress")) or "-")
        if st.lower() == "disconnected":
            res.note("No network cable was plugged in during the test - plug one in during the guided port test "
                     "to confirm the port works.")
        elif st.lower() == "not present":
            res.note("The LAN chip answers but reports 'not present' - on thin-and-light models the RJ45 "
                     "lives on a dongle or extension connector that was not attached during the scan.")
        elif st.lower() not in ("up", "connected", "ok"):
            score -= 15
            res.flag("Ethernet adapter status: %s" % st, "warn")
    res.set("pass" if score >= 85 else "warn",
            "Ethernet adapter present (%s)" % (_s(get(eth[0], "Status")) or "status unknown"), score=score)


# ==========================================================================
# AUDIO DEVICES
# ==========================================================================

@safe_check
def check_audio_dev(res: CheckResult, ctx: Dict[str, Any]) -> None:
    snd = _rows(ctx, "sounddev")
    endpoints = _rows(ctx, "audioendpoint")
    media = _rows(ctx, "audiodev")
    render = [e for e in endpoints if "{0.0.0." in _s(get(e, "InstanceId")).lower()]
    capture = [e for e in endpoints if "{0.0.1." in _s(get(e, "InstanceId")).lower()]

    score = 100.0
    res.metric("Audio controllers", len(snd))
    for i, s in enumerate(snd[:4], 1):
        nm = _s(get(s, "Name"))
        st = _s(get(s, "Status"))
        err = _int(get(s, "ConfigManagerErrorCode"))
        res.metric("Controller %d" % i, "%s - %s" % (nm, st))
        if st.lower() != "ok" or err:
            score -= 25
            res.flag("Audio controller '%s' reports a problem (status %s, code %s)" % (nm, st, err), "fail")
    res.metric("Playback endpoints (speakers/headphones)", len(render))
    for e in render[:6]:
        res.metric("  Output", "%s (%s)" % (_s(get(e, "FriendlyName")), _s(get(e, "Status"))))
    res.metric("Recording endpoints (microphones)", len(capture))
    for e in capture[:6]:
        res.metric("  Input", "%s (%s)" % (_s(get(e, "FriendlyName")), _s(get(e, "Status"))))

    svc = {(_s(get(r, "Name"))).lower(): _svc_state(get(r, "Status")) for r in _rows(ctx, "services")}
    res.metric("Windows Audio service", svc.get("audiosrv", "?"))
    res.metric("Audio Endpoint Builder", svc.get("audioendpointbuilder", "?"))
    if svc.get("audiosrv", "").lower() != "running":
        score -= 30
        res.flag("The Windows Audio service is not running - no sound will work", "fail")

    ctx["audio"] = {"speakers": len(render), "mics": len(capture),
                    "speaker_names": [_s(get(e, "FriendlyName")) for e in render],
                    "mic_names": [_s(get(e, "FriendlyName")) for e in capture],
                    "controllers": len(snd)}
    if not render:
        res.na("No playback device (speakers/headphones) is registered in Windows")
        res.flag("No audio output device found - driver missing or speakers disconnected", "fail")
        ctx["audio"]["speakers"] = 0
        return
    if not capture:
        res.note("No microphone endpoint found - this machine may have no built-in mic.")
        ctx["audio"]["no_mic"] = True
    res.set("pass" if score >= 88 else ("warn" if score >= 55 else "fail"),
            "%d output and %d input device(s) registered" % (len(render), len(capture)), score=score)


# ==========================================================================
# CAMERA
# ==========================================================================

@safe_check
def check_camera_dev(res: CheckResult, ctx: Dict[str, Any]) -> None:
    cams = _rows(ctx, "camera")
    consent = _rows(ctx, "camconsent")
    if not cams:
        res.na("No camera fitted")
        res.note("No camera device is registered in Windows. Many business laptops and all desktops ship "
                 "without one. Confirm physically before writing it off - a camera can also be disabled in the BIOS.")
        ctx["camera"] = {"present": False}
        return
    ctx["camera"] = {"present": True, "devices": []}
    score = 100.0
    for i, c in enumerate(cams, 1):
        nm = _s(get(c, "FriendlyName"))
        st = _s(get(c, "Status"))
        cls = _s(get(c, "Class"))
        iid = _s(get(c, "InstanceId"))
        prob = _s(get(c, "Problem"))
        ir = " (IR / Windows Hello)" if re.search(r"\bir\b|infrared", nm, re.I) else ""
        res.metric("Camera %d%s" % (i, ir), "%s [%s] - %s" % (nm, cls or "?", st))
        if iid:
            res.metric("  Instance", iid[:60])
        ctx["camera"]["devices"].append({"name": nm, "status": st, "class": cls, "id": iid, "ir": bool(ir)})
        if st.lower() != "ok":
            score -= 30
            res.flag("Camera '%s' has a device problem: %s (status %s)" % (nm, prob or "unknown", st), "fail")
    if len(cams) > 1:
        res.note("More than one camera device (a normal colour camera plus an IR camera for Windows Hello is common).")

    allowed = None
    for row in consent:
        v = _s(get(row, "Value"))
        if v:
            allowed = v
            break
    if allowed:
        res.metric("Windows camera privacy setting", allowed)
        if allowed.lower() == "deny":
            score -= 20
            res.flag("Windows privacy settings still BLOCK camera access. The tool already tried to "
                     "allow it and could not - a group policy or security product is enforcing the "
                     "block on this machine.", "warn")
    svc = [r for r in _rows(ctx, "services") if _s(get(r, "Name")).lower() == "camsvc"]
    if svc:
        res.metric("Windows Camera service", _svc_state(get(svc[0], "Status")))

    res.set("pass" if score >= 88 else ("warn" if score >= 55 else "fail"),
            "%d camera device(s) detected" % len(cams), score=score)


# ==========================================================================
# PORTS + EXPANSION
# ==========================================================================

@safe_check
def check_ports(res: CheckResult, ctx: Dict[str, Any]) -> None:
    usbctl = _rows(ctx, "usbctl")
    hubs = _rows(ctx, "usbhub")
    ports = _rows(ctx, "ports")
    cdrom = _rows(ctx, "cdrom")
    sd = _rows(ctx, "sdcard")
    fp = _rows(ctx, "fingerprint")
    gpus = _rows(ctx, "gpu")
    mconn = _rows(ctx, "monitorconn")

    score = 100.0
    res.metric("USB host controllers", len(usbctl))
    for i, u in enumerate(usbctl[:6], 1):
        res.metric("  Controller %d" % i, "%s (%s)" % (_s(get(u, "Name")), _s(get(u, "Status"))))
    xhci = [u for u in usbctl if "xhci" in _s(get(u, "Name")).lower() or "usb 3" in _s(get(u, "Name")).lower()
            or "extensible" in _s(get(u, "Name")).lower()]
    res.metric("USB 3.x support", "Yes (%d controller(s))" % len(xhci) if xhci else "Not detected")
    res.metric("USB hubs", len(hubs))
    res.metric("Serial / COM / LPT ports", len(ports))
    for p in ports[:6]:
        res.metric("  Port", _s(get(p, "FriendlyName")))
    res.metric("Optical drive (DVD/CD)", "Yes - %s" % _s(get(cdrom[0], "Name")) if cdrom else "No")
    res.metric("SD / card reader", ("Yes - %s" % _s(get(sd[0], "FriendlyName"))) if sd else "No")
    res.metric("Fingerprint reader", ("Yes - %s" % _s(get(fp[0], "FriendlyName"))) if fp else "No")

    # video outputs: internal panel vs external connectors
    external = 0
    for c in mconn:
        lbl = S.video_output_label(get(c, "VideoOutputTechnology"))
        res.metric("Video output", lbl)
        if "internal" not in lbl.lower():
            external += 1
    if not mconn:
        res.metric("Video output", "Could not enumerate (check HDMI/DP physically in the guided test)")

    ctx["ports"] = {"usb_controllers": len(usbctl), "usb3": bool(xhci), "hubs": len(hubs),
                    "optical": bool(cdrom), "card_reader": bool(sd), "fingerprint": bool(fp),
                    "external_video": external}

    if not usbctl:
        score -= 30
        res.flag("No USB host controller detected - USB ports will not work", "fail")
    for u in usbctl:
        if _s(get(u, "Status")).lower() not in ("ok", ""):
            score -= 15
            res.flag("USB controller '%s' status: %s" % (_s(get(u, "Name")), _s(get(u, "Status"))), "warn")
    if not xhci:
        res.note("No USB 3.x controller found - this machine may only have USB 2.0 (slower transfers).")
    res.note("Physical port testing (plugging a pen drive into every port) happens in the guided phase - "
             "Windows can only tell you what is electrically present, not whether a socket is loose.")
    res.set("pass" if score >= 88 else ("warn" if score >= 55 else "fail"),
            "%d USB controller(s), card reader: %s, optical: %s" % (
                len(usbctl), "yes" if sd else "no", "yes" if cdrom else "no"), score=score)


# ==========================================================================
# INPUT DEVICES (hardware presence + external-device audit)
# ==========================================================================

def classify_keyboard(iid: str, parent: str, name: str) -> str:
    """Return 'internal' | 'external-usb' | 'external-bt' | 'virtual' | 'unknown'."""
    iid = (iid or "").upper()
    parent = (parent or "").upper()
    name = (name or "").lower()
    if any(k in name for k in ("virtual", "remote desktop", "teamviewer", "anydesk", "vmware", "vbox", "logitech bolt receiver")):
        return "virtual"
    if iid.startswith("BTHENUM") or parent.startswith("BTHENUM") or "bluetooth" in name:
        return "external-bt"
    if iid.startswith("ACPI\\") or parent.startswith("ACPI\\"):
        return "internal"
    if iid.startswith("USB\\") or parent.startswith("USB\\"):
        return "external-usb"
    if iid.startswith("HID\\") or parent.startswith("HID\\"):
        # HID can wrap either; look one level up
        if "PNP03" in iid or "PNP03" in parent:
            return "internal"
        return "unknown"
    if "PNP03" in iid:
        return "internal"
    return "unknown"


@safe_check
def check_input_dev(res: CheckResult, ctx: Dict[str, Any]) -> None:
    kb = _rows(ctx, "kbdetail") or _rows(ctx, "keyboard")
    mice = _rows(ctx, "mouse")
    score = 100.0
    internal = []
    external = []
    for k in kb:
        nm = _s(get(k, "FriendlyName"))
        iid = _s(get(k, "InstanceId"))
        parent = _s(get(k, "Parent"))
        st = _s(get(k, "Status"))
        kind = classify_keyboard(iid, parent, nm)
        res.metric("Keyboard", "%s [%s] %s" % (nm, kind, st))
        if iid:
            res.metric("  Instance", iid[:70])
        if kind.startswith("internal"):
            internal.append(k)
        elif kind.startswith("external") or kind == "virtual":
            external.append({"name": nm, "id": iid, "kind": kind})
        if st.lower() not in ("ok", ""):
            score -= 25
            res.flag("Keyboard device '%s' has a problem (status %s)" % (nm, st), "fail")

    touchpads = []
    ext_mice = []
    for m in mice:
        nm = _s(get(m, "FriendlyName"))
        iid = _s(get(m, "InstanceId")).upper()
        low = nm.lower()
        if any(k in low for k in ("touchpad", "pointing stick", "precision", "synaptics", "elan", "alps", "i2c hid", "cipa")):
            touchpads.append(nm)
        elif iid.startswith(("USB\\", "HID\\VID")) and not iid.startswith("ACPI"):
            ext_mice.append(nm)
        else:
            touchpads.append(nm)
        res.metric("Pointing device", "%s (%s)" % (nm, iid[:48]))
        if _s(get(m, "Status")).lower() not in ("ok", ""):
            score -= 15
            res.flag("Pointing device '%s' has a problem" % nm, "warn")

    res.metric("Built-in keyboard detected", "Yes" if internal else ("No - only external/virtual" if kb else "None"))
    res.metric("Touchpad", ", ".join(dict.fromkeys(touchpads)) or "Not detected")
    res.metric("External keyboards connected", len(external))
    for e in external:
        res.metric("  External", "%s (%s)" % (e["name"], e["kind"]))

    ctx["input"] = {"internal_keyboards": [ _s(get(k, "InstanceId")) for k in internal ],
                    "all_keyboards": [_s(get(k, "InstanceId")) for k in kb],
                    "external": external, "touchpads": touchpads, "ext_mice": ext_mice}

    if not internal:
        res.note("No clearly internal keyboard was identified. The guided keyboard test will require every key "
                 "to come from a non-USB / non-Bluetooth device handle, so an external keyboard cannot be used "
                 "to pass it.")
    if external:
        score -= 5
        res.flag("%d external/virtual keyboard(s) connected - UNPLUG them before the guided keyboard test" % len(external), "warn")
    if not touchpads:
        res.note("No touchpad detected - check whether this model has one (some do not) or whether the driver is missing.")
    res.set("pass" if score >= 88 else ("warn" if score >= 55 else "fail"),
            "%d keyboard device(s), %d pointing device(s)" % (len(kb), len(mice)), score=score)


# ==========================================================================
# EVENT LOG / HARDWARE ERRORS
# ==========================================================================

@safe_check
def check_events(res: CheckResult, ctx: Dict[str, Any]) -> None:
    whea = _rows(ctx, "whea")
    diskev = _rows(ctx, "diskevents")
    crash = _rows(ctx, "crashevents")
    blue = _rows(ctx, "blueevents")
    problems = _rows(ctx, "problems")

    score = 100.0
    res.metric("WHEA hardware errors (45 days)", len(whea))
    for w in whea[:5]:
        res.metric("  %s" % _cim_date(get(w, "TimeCreated")),
                   "ID %s [%s] %s" % (_s(get(w, "Id")), _s(get(w, "LevelDisplayName")), _s(get(w, "Msg"))[:90]))
    res.metric("Disk / storage errors (45 days)", len(diskev))
    for d in diskev[:6]:
        res.metric("  %s" % _cim_date(get(d, "TimeCreated")),
                   "ID %s from %s" % (_s(get(d, "Id")), _s(get(d, "ProviderName"))))
    res.metric("Unexpected shutdowns (Kernel-Power 41 / 6008)", len(blue) or len(crash))
    res.metric("Devices in error state right now", len(problems))
    for p in problems[:12]:
        res.metric("  Problem device", "%s [%s] status=%s problem=%s" % (
            _s(get(p, "FriendlyName")), _s(get(p, "Class")), _s(get(p, "Status")), _s(get(p, "Problem"))))

    if whea:
        fatal = [w for w in whea if "error" in _s(get(w, "LevelDisplayName")).lower()
                 or _int(get(w, "Id")) in (17, 18, 19, 20, 47)]
        score -= 40 if fatal else 15
        res.flag("%d WHEA hardware error(s) logged in the last 45 days%s - CPU/RAM/bus instability"
                 % (len(whea), ", including fatal ones" if fatal else ""), "fail" if fatal else "warn")
    if diskev:
        score -= min(25, 5 * len(diskev))
        res.flag("%d disk/storage error event(s) in the last 45 days" % len(diskev), "warn")
    if blue:
        score -= min(20, 5 * len(blue))
        res.flag("%d unexpected shutdown / crash event(s) in the last 90 days" % len(blue), "warn")
    if problems:
        bad = [p for p in problems if _s(get(p, "Class")).lower() not in ("softwarecomponent", "softwaredevice", "")]
        if bad:
            score -= min(20, 4 * len(bad))
            res.flag("%d device(s) currently in an error state (driver or hardware)" % len(bad), "warn")

    ctx["events"] = {"whea": len(whea), "disk": len(diskev), "crash": len(blue), "problems": len(problems)}
    if score >= 90:
        res.ok("No hardware errors logged - clean event history", score=score)
    else:
        res.set("warn" if score >= 55 else "fail",
                "%d WHEA / %d disk / %d crash event(s), %d problem device(s)"
                % (len(whea), len(diskev), len(blue), len(problems)), score=score)


# ==========================================================================
# registry
# ==========================================================================

@safe_check
def check_biometric(res: CheckResult, ctx: Dict[str, Any]) -> None:
    """Fingerprint reader / IR face camera: present, and healthy?"""
    bio = _rows(ctx, "fingerprint")
    face = [c for c in _rows(ctx, "camera")
            if "ir" in _s(get(c, "FriendlyName")).lower()
            or "face" in _s(get(c, "FriendlyName")).lower()
            or "ir" in _s(get(c, "InstanceId")).lower()]
    if not bio and not face:
        res.na("No fingerprint reader or IR face camera fitted")
        return
    score = 100.0
    for i, b in enumerate((bio + face)[:6], 1):
        nm = _s(get(b, "FriendlyName")) or _s(get(b, "Name"))
        st = _s(get(b, "Status")) or "OK"
        res.metric("Biometric device %d" % i, "%s - %s" % (nm, st))
        if st.lower() not in ("ok", ""):
            score -= 30
            res.flag("Biometric device '%s' reports a problem (%s)" % (nm, st), "warn")
    res.set("pass" if score >= 85 else "warn",
            "%d biometric device(s) present and healthy" % min(6, len(bio) + len(face)),
            score=score)
    res.note("Windows Hello sign-in is a selling point: enrol a fingerprint before the "
             "buyer sees the machine, or wipe the old enrolments.")


REGISTRY: Dict[str, Any] = {
    "identity": check_identity,
    "os_license": check_os_license,
    "bios_tpm": check_bios_tpm,
    "cpu": check_cpu,
    "memory": check_memory,
    "storage": check_storage,
    "gpu": check_gpu,
    "display": check_display,
    "battery": check_battery,
    "charger": check_charger,
    "wifi": check_wifi,
    "bluetooth": check_bluetooth,
    "ethernet": check_ethernet,
    "audio_dev": check_audio_dev,
    "camera_dev": check_camera_dev,
    "ports": check_ports,
    "input_dev": check_input_dev,
    "biometric": check_biometric,
    "events": check_events,
}
