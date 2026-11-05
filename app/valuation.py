"""
valuation.py -- the resale price engine (Indian second-hand market).

It is a transparent, rule-based model: a base value taken from the CPU
generation (the single biggest driver of used-laptop price in India), plus
itemised adjustments for RAM, storage, display, graphics, Windows 11 support
and licence, minus itemised repair deductions for every fault the inspection
actually found, then multiplied by the condition grade.

Every rupee in the answer is explained on one line in the report, so a shop
owner can argue with the model instead of guessing.

The numbers were seeded from real Indian used-market listings in late 2026
(Dell Latitude 5400 i5-8th/8GB/256GB around 20-24k, Latitude 5410 i5-10th
around 24-26k, Latitude 5420 i5-11th around 29k, Latitude 5430 i5-12th around
29-33k, 6th-gen i5 around 13-16k). They are ESTIMATES. The whole model lives
in pricing.json next to the .exe, so every shop can tune it to its own street
prices in two minutes without rebuilding anything.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core import DATA_DIR, CheckResult, fmt_gb, fmt_inr, load_json, write_json

PRICING_FILE = os.path.join(DATA_DIR, "pricing.json")

DEFAULT_MODEL: Dict[str, Any] = {
    "currency": "INR",
    "model_version": "2026.11",
    "market_note": "Seeded from Indian used-laptop listings, late 2026. Tune to your street prices.",
    # what the shop pays the customer, as a fraction of the resale price
    "buy_ratio": 0.62,
    "spread_low": 0.90,
    "spread_high": 1.13,
    "min_price": 2500,
    "max_price": 400000,

    # base resale value for a MID-RANGE laptop of that CPU generation with
    # 8 GB RAM and a 256 GB SSD. Business-class and gaming lines then get a
    # premium on top, consumer lines a discount (series_keywords below).
    "base_intel": {
        "2": 6000, "3": 6500, "4": 7500, "5": 8500, "6": 11000, "7": 14000,
        "8": 20000, "9": 23000, "10": 25500, "11": 29500, "12": 31500,
        "13": 42000, "14": 55000, "15": 68000, "0": 5000,
    },
    "base_amd": {
        "1": 12000, "2": 15000, "3": 18000, "4": 24000, "5": 28000,
        "6": 33000, "7": 42000, "8": 52000, "9": 62000, "0": 7000,
    },
    "base_other": 8000,

    "tier_adjust": {"budget": -5000, "entry": -3000, "mid": 0, "high": 3500,
                    "enthusiast": 9500, "unknown": 0},

    "series_keywords": {
        "business": (["latitude", "thinkpad", "elitebook", "probook", "zbook", "precision",
                      "thinkbook", "expertbook", "vostro", "travelmate", "extensa", "portege",
                      "tecra", "elite", "pro x360"], 1800),
        "gaming": (["g15", "g16", "nitro", "tuf", "rog", "legion", "omen", "victus", "predator",
                    "razer", "alienware", "ideapad gaming", "helios", "katana", "sword"], 3200),
        "consumer": (["inspiron", "ideapad", "pavilion", "aspire", "vivobook", "zenbook",
                      "yoga", "flex", "chromebook", "hp 14", "hp 15", "lenovo v1", "ideapad 1",
                      "ideapad 3"], -900),
        "premium": (["surface", "xps", "spectre", "macbook", "thinkpad x1", "zbook", "razer blade"], 3000),
    },

    "ram_adjust": {"0": -6000, "4": -3000, "8": 0, "12": 900, "16": 2600,
                   "20": 3200, "24": 3800, "32": 5200, "48": 7000, "64": 9000},
    "free_slot_bonus": 400,
    "single_channel_penalty": -700,

    "storage_adjust": {"hdd": -3500, "ssd_128": -1500, "ssd_256": 0, "ssd_512": 2000,
                       "ssd_1024": 4000, "ssd_2048": 7000, "ssd_4096": 11000},

    "display_adjust": {"HD (1366x768)": -2500, "HD+ (1600x900)": -900,
                       "Full HD (1920x1080)": 0, "QHD (2560x1440)": 2600,
                       "4K UHD": 5500},
    "touch_bonus": 1500,
    "size_adjust": {"17": 900, "16": 700, "15": 400, "14": 0, "13": -600, "12": -1300, "11": -1900},

    "gpu_adjust": {"integrated": 0, "dedicated": 3500, "high_end": 8000, "workstation": 7000},

    "os_adjust": {"win11_installed": 1500, "win11_capable_not_installed": -500,
                  "win11_not_capable": -3500, "not_activated": -2500, "oem_key": 500},

    "age_penalty": {"7": -0.08, "10": -0.16, "13": -0.25},

    "grade_multiplier": {"A+": 1.06, "A": 1.00, "B": 0.93, "C": 0.82, "D": 0.66, "E": 0.45},
    "cosmetic_multiplier": {"A": 1.00, "B": 0.97, "C": 0.92, "D": 0.84},

    # flat deductions for faults, in rupees (roughly the repair cost + margin hit)
    "deductions": {
        "battery_dead": 2800, "battery_below_55": 2800, "battery_below_70": 1400,
        "battery_below_85": 500,
        "drive_failing": 4500, "drive_worn": 2200,
        "screen_panel_bad": 5500, "screen_panel_warn": 1500,
        "keyboard_bad": 2800, "keyboard_incomplete": 1200,
        "touchpad_bad": 1200, "speaker_bad": 1500, "speaker_weak": 600,
        "mic_bad": 700, "camera_bad": 1300,
        "wifi_missing": 700, "wifi_bad": 1300, "bt_missing": 300,
        "usb_port_dead": 700, "hdmi_bad": 1100, "jack_bad": 600,
        "charger_missing": 1300, "bitlocker_locked": 2000,
        "liquid_damage": 9000, "battery_swollen": 3000, "hinge_bad": 3500,
        "case_crack": 1500, "whea_errors": 3000, "memory_errors": 2500,
        "throttling": 1200, "hot_cpu": 900, "problem_devices": 600,
        "cosmetic_grade_d": 2500, "cosmetic_grade_c": 1200,
        "unsigned": 800, "missing_tests": 500,
    },
}


def load_model() -> Dict[str, Any]:
    model = json.loads(json.dumps(DEFAULT_MODEL))     # deep copy
    saved = load_json(PRICING_FILE)
    if isinstance(saved, dict):
        for k, v in saved.items():
            if isinstance(v, dict) and isinstance(model.get(k), dict):
                model[k].update(v)
            else:
                model[k] = v
        model["_loaded_from_file"] = True
    else:
        model["_loaded_from_file"] = False
        try:
            write_json(PRICING_FILE, {k: v for k, v in model.items() if not k.startswith("_")})
        except Exception:
            pass
    return model


def save_model(model: Dict[str, Any]) -> bool:
    return write_json(PRICING_FILE, {k: v for k, v in model.items() if not k.startswith("_")})


# --------------------------------------------------------------------------

def _closest(table: Dict[str, Any], key: float) -> int:
    """Pick the entry whose numeric key is nearest (for RAM/size buckets)."""
    if not table:
        return 0
    best = None
    bestd = None
    for k, v in table.items():
        try:
            kk = float(k)
        except Exception:
            continue
        d = abs(kk - key)
        if bestd is None or d < bestd:
            bestd, best = d, v
    return int(best or 0)


def _gen_table_key(gen: Any, table: Dict[str, Any]) -> int:
    try:
        g = int(gen)
    except Exception:
        g = 0
    g = max(0, min(g, 15))
    for step in range(0, 6):
        if str(g - step) in table:
            return int(table[str(g - step)])
        if str(g + step) in table:
            return int(table[str(g + step)])
    return int(table.get("0", 8000))


def estimate(results: Dict[str, CheckResult], ctx: Dict[str, Any],
             grade_info: Dict[str, Any], model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a fully itemised valuation."""
    m = model or load_model()
    rows: List[Dict[str, Any]] = []

    def add(label: str, amount: float, note: str = "", kind: str = "adjust") -> None:
        # percentages and multipliers are fractions - they must NOT be rounded
        # to whole rupees, or a 0.93 grade multiplier becomes 1 and is ignored.
        value = float(amount) if kind in ("pct", "mult") else int(round(amount))
        rows.append({"label": label, "amount": value, "note": note, "kind": kind})

    cpu = ctx.get("cpu") or {}
    gen = cpu.get("gen") or {}
    ident = ctx.get("identity") or {}
    mem = ctx.get("memory") or {}
    sto = ctx.get("storage") or {}
    disp = ctx.get("display") or {}
    gpu = ctx.get("gpu") or {}
    osinfo = ctx.get("os") or {}
    batt = ctx.get("battery") or {}

    # ---- base ------------------------------------------------------------
    brand = (gen.get("brand") or "Unknown").lower()
    if brand == "intel":
        base = _gen_table_key(gen.get("gen", 0), m["base_intel"])
    elif brand == "amd":
        base = _gen_table_key(gen.get("gen", 0), m["base_amd"])
    else:
        base = int(m.get("base_other", 8000))
    add("Base value - %s %s (%s)" % (gen.get("brand", "?"), gen.get("family", "CPU"),
                                     gen.get("gen_label", "generation unknown")),
        base, "The CPU generation drives most of a used laptop's value", "base")

    # ---- tier ------------------------------------------------------------
    tier_adj = int(m["tier_adjust"].get(gen.get("tier", "unknown"), 0))
    if tier_adj:
        add("Processor class: %s" % gen.get("tier", "?").title(), tier_adj, "", "adjust")

    # ---- series / model line ---------------------------------------------
    blob = " ".join(str(x) for x in (ident.get("manufacturer", ""), ident.get("model", ""),
                                     ident.get("family", ""), ident.get("sku", ""))).lower()
    matched = None
    for label, (keywords, amount) in m["series_keywords"].items():
        if any(k in blob for k in keywords):
            matched = (label, amount)
            break
    if matched and matched[1]:
        add("Product line: %s class" % matched[0], matched[1],
            "detected from the model name '%s'" % (ident.get("model") or "?"), "adjust")

    # ---- RAM --------------------------------------------------------------
    ram_gb = float(mem.get("total_gb") or 0)
    add("RAM %s" % (fmt_gb(mem.get("total_bytes") or 0) if mem.get("total_bytes") else "unknown"),
        _closest(m["ram_adjust"], ram_gb), "", "adjust")
    if mem.get("free_slots"):
        add("Free RAM slot (upgradeable)", int(m.get("free_slot_bonus", 0)), "", "adjust")
    if mem.get("single_channel"):
        add("Single-channel memory (one stick)", int(m.get("single_channel_penalty", 0)),
            "Adding a second stick is cheap and makes it feel faster", "adjust")

    # ---- storage -----------------------------------------------------------
    disks = sto.get("disks") or []
    total_tb = (sto.get("total_bytes") or 0) / (1024 ** 4)
    total_gb = (sto.get("total_bytes") or 0) / (1024 ** 3)
    if sto.get("boot_is_ssd") or (sto.get("has_ssd") and not sto.get("has_hdd")):
        bucket = "ssd_128" if total_gb < 200 else ("ssd_256" if total_gb < 400 else
                 ("ssd_512" if total_gb < 800 else ("ssd_1024" if total_gb < 1600 else
                  ("ssd_2048" if total_gb < 3200 else "ssd_4096"))))
        add("Storage %s SSD" % (("%.0f GB" % total_gb) if total_gb else "?"),
            int(m["storage_adjust"].get(bucket, 0)), "", "adjust")
    elif sto.get("has_hdd"):
        add("Storage %s HDD (mechanical)" % (("%.0f GB" % total_gb) if total_gb else "?"),
            int(m["storage_adjust"].get("hdd", -3500)),
            "Buyers expect an SSD; fitting one costs about 1,500-2,500", "adjust")
    else:
        add("Storage %.2f TB (type unknown)" % total_tb if total_tb else "Storage unknown",
            int(m["storage_adjust"].get("ssd_256", 0)), "", "adjust")

    # ---- display ------------------------------------------------------------
    klass = disp.get("klass") or "Full HD (1920x1080)"
    add("Display %s" % klass, int(m["display_adjust"].get(klass, 0)), "", "adjust")
    size = disp.get("size_inch")
    if size:
        add('Screen size %.1f"' % size, _closest(m["size_adjust"], float(size)), "", "adjust")
    if disp.get("touch"):
        add("Touch screen", int(m.get("touch_bonus", 0)), "", "adjust")

    # ---- GPU -----------------------------------------------------------------
    names = " ".join(gpu.get("names") or []).lower()
    if gpu.get("dedicated"):
        if any(k in names for k in ("rtx", "gtx 16", "rx 6", "rx 7", "arc a", "rx 5", "gtx 10")):
            add("Dedicated gaming GPU", int(m["gpu_adjust"]["high_end"]), names[:50], "adjust")
        elif any(k in names for k in ("quadro", "rtx a", "radeon pro", "wx ", "t600", "t1200")):
            add("Workstation GPU", int(m["gpu_adjust"]["workstation"]), names[:50], "adjust")
        else:
            add("Dedicated GPU", int(m["gpu_adjust"]["dedicated"]), names[:50], "adjust")
    else:
        add("Integrated graphics only", 0, "", "adjust")

    # ---- OS / licence ----------------------------------------------------------
    if osinfo.get("win11_installed"):
        add("Windows 11 installed", int(m["os_adjust"]["win11_installed"]), "", "adjust")
    elif osinfo.get("win11_capable"):
        add("Windows 10, but the hardware supports Windows 11",
            int(m["os_adjust"]["win11_capable_not_installed"]),
            "Free upgrade - do it before selling", "adjust")
    else:
        add("Windows 10 with no Windows 11 support (out of security updates)",
            int(m["os_adjust"]["win11_not_capable"]),
            "Windows 10 support ended 14 Oct 2025", "adjust")
    if not osinfo.get("activated", True):
        add("Windows is NOT activated", int(m["os_adjust"]["not_activated"]), "", "deduct")
    elif osinfo.get("oem_key"):
        add("OEM digital licence in firmware (clean re-activation guaranteed)",
            int(m["os_adjust"].get("oem_key", 0)), "", "adjust")

    # ---- age ---------------------------------------------------------------------
    age = None
    for src in (("display", "panel_year"), ("bios_year", None)):
        pass
    bios_date = (ctx.get("bios_year"))
    if disp.get("panel_year"):
        try:
            age = int(time.strftime("%Y")) - int(disp["panel_year"])
        except Exception:
            age = None
    if age is None and ctx.get("os_install_year"):
        age = int(time.strftime("%Y")) - int(ctx["os_install_year"])
    if age and age >= 7:
        pen = 0.0
        for threshold, value in sorted(((int(k), v) for k, v in m["age_penalty"].items())):
            if age >= threshold:
                pen = value
        if pen:
            add("Age penalty (panel/build is ~%d years old)" % age, pen, "percentage", "pct")

    # ---- condition multipliers ------------------------------------------------------
    grade = grade_info.get("grade", "C")
    gmult = float(m["grade_multiplier"].get(grade, 0.85))
    add("Condition grade %s (%.0f/100 health score)" % (grade, grade_info.get("score", 0)),
        gmult, "multiplier", "mult")
    cgrade = ctx.get("cosmetic_grade") or "B"
    cmult = float(m["cosmetic_multiplier"].get(cgrade, 0.95))
    add("Cosmetic grade %s" % cgrade, cmult, "multiplier", "mult")

    # ---- fault deductions -------------------------------------------------------------
    d = m["deductions"]

    def status_of(cid: str) -> str:
        r = results.get(cid)
        return r.status if r is not None else "missing"

    def score_of(cid: str) -> Optional[float]:
        r = results.get(cid)
        return r.score if r is not None else None

    # battery
    health = batt.get("health")
    if not batt.get("present"):
        add("No working battery fitted", -int(d["battery_dead"]), "A replacement is expected", "deduct")
    elif health is not None:
        if health < 55:
            add("Battery health %.0f%% (below 55%%)" % health, -int(d["battery_below_55"]),
                "Replace the battery", "deduct")
        elif health < 70:
            add("Battery health %.0f%%" % health, -int(d["battery_below_70"]), "", "deduct")
        elif health < 85:
            add("Battery health %.0f%%" % health, -int(d["battery_below_85"]), "", "deduct")
    if status_of("charger") == "fail":
        add("Charging circuit / adapter fault", -int(d["charger_missing"]), "", "deduct")

    # storage
    st = results.get("storage")
    if st is not None:
        if st.status == "fail":
            add("Drive failing or unreadable SMART errors", -int(d["drive_failing"]),
                "Must be replaced before resale", "deduct")
        elif (sto.get("max_wear") or 0) >= 70:
            add("SSD wear %.0f%% used" % (sto.get("max_wear") or 0), -int(d["drive_worn"]), "", "deduct")

    # display
    sc = results.get("screen_colours")
    if sc is not None and sc.status in ("fail", "warn"):
        add("Display defects confirmed by eye (%s)" % ("bad panels/bleed" if sc.status == "fail" else "minor"),
            -int(d["screen_panel_bad"] if sc.status == "fail" else d["screen_panel_warn"]),
            "", "deduct")
    if status_of("display") == "fail":
        add("Panel not running at native resolution / EDID fault", -int(d["screen_panel_warn"]), "", "deduct")

    # input
    kb = results.get("keyboard")
    if kb is not None:
        if kb.status == "fail":
            add("Keyboard faulty or the test was tampered with", -int(d["keyboard_bad"]), "", "deduct")
        elif kb.status in ("warn", "skipped"):
            add("Keyboard test incomplete", -int(d["keyboard_incomplete"]), "", "deduct")
    if status_of("touchpad") == "fail":
        add("Touchpad faulty", -int(d["touchpad_bad"]), "", "deduct")

    # audio / camera
    if status_of("speakers") == "fail":
        add("Speaker fault", -int(d["speaker_bad"]), "", "deduct")
    elif status_of("speakers") == "warn":
        add("Speaker quality poor", -int(d["speaker_weak"]), "", "deduct")
    if status_of("microphone") == "fail":
        add("Microphone fault", -int(d["mic_bad"]), "", "deduct")
    if status_of("camera_capture") == "fail":
        add("Camera fault", -int(d["camera_bad"]), "", "deduct")

    # wireless
    if status_of("wifi") == "na":
        add("No Wi-Fi fitted", -int(d["wifi_missing"]), "", "deduct")
    elif status_of("wifi") == "fail":
        add("Wi-Fi fault", -int(d["wifi_bad"]), "", "deduct")
    if status_of("bluetooth") == "na":
        add("No Bluetooth fitted", -int(d["bt_missing"]), "", "deduct")

    # ports
    up = results.get("usb_ports")
    if up is not None:
        dead = sum(1 for mk in (up.marks or []) if mk.get("mark") in ("dead", "no", "bad"))
        if not dead:
            # fall back to what the check recorded in its details table
            for key, val in (up.details or {}).items():
                if "dead" in str(key).lower() or "dead" in str(val).lower():
                    try:
                        dead = max(dead, int(re.search(r"\d+", str(val) or "1").group()))
                    except Exception:
                        dead = max(dead, 1)
        if dead:
            add("%d dead USB port(s)" % dead, -int(d["usb_port_dead"]) * dead, "", "deduct")
    wp = results.get("web_ports")
    if wp is not None:
        for mk in (wp.marks or []):
            if mk.get("mark") == "no":
                key = "hdmi_bad" if mk.get("id") == "hdmi" else "jack_bad"
                add("Faulty %s" % mk.get("name", "port"), -int(d.get(key, 600)), "", "deduct")

    # cosmetic-driven faults
    cos = results.get("cosmetic")
    if cos is not None:
        for mk in (cos.marks or []):
            if mk.get("mark") != "no":
                continue
            mid = mk.get("id")
            note = mk.get("note") or ""
            if mid == "liquid":
                add("LIQUID DAMAGE found", -int(d["liquid_damage"]),
                    "Do not resell as a working machine without a board-level repair", "deduct")
            elif mid == "battery_phys":
                add("Swollen battery (fire risk)", -int(d["battery_swollen"]),
                    "Remove and dispose of safely before selling", "deduct")
            elif mid == "hinge":
                add("Broken / cracked hinge", -int(d["hinge_bad"]), "", "deduct")
            elif mid == "charger":
                add("No charger supplied", -int(d["charger_missing"]), "", "deduct")
            elif mid in ("lid", "base", "bezel", "screen_cos", "palmrest"):
                add("Cracked or damaged %s" % (note or mid), -int(d["case_crack"]), "", "deduct")
        if cgrade == "D":
            add("Overall cosmetic grade D", -int(d["cosmetic_grade_d"]), "", "deduct")
        elif cgrade == "C":
            add("Overall cosmetic grade C", -int(d["cosmetic_grade_c"]), "", "deduct")

    # security / stability
    bl = results.get("bios_tpm")
    if bl is not None:
        for text, level in bl.flags:
            if isinstance(text, str) and "BitLocker" in text:
                add("BitLocker-encrypted drive (must be decrypted or key supplied)",
                    -int(d["bitlocker_locked"]), "", "deduct")
                break
    if status_of("memory_test") == "fail":
        add("Memory errors detected", -int(d["memory_errors"]), "Replace the faulty module", "deduct")
    if status_of("events") == "fail":
        add("Hardware errors logged (WHEA)", -int(d["whea_errors"]), "", "deduct")
    cs = results.get("cpu_stress")
    if cs is not None and cs.status == "fail":
        add("CPU throttles badly under load", -int(d["throttling"]),
            "Clean the fan and replace the thermal paste", "deduct")
    elif cs is not None and any("hot" in str(f[0]).lower() for f in cs.flags):
        add("Runs hot under load", -int(d["hot_cpu"]), "", "deduct")
    ev = ctx.get("events") or {}
    if ev.get("problems"):
        add("%d device(s) in an error state" % ev["problems"],
            -int(d["problem_devices"]) * min(3, ev["problems"]), "", "deduct")

    # integrity
    if not ctx.get("signed_off", False):
        add("Inspection not signed off", -int(d["unsigned"]), "", "deduct")
    if grade_info.get("pending"):
        add("%d required test(s) never completed" % len(grade_info["pending"]),
            -int(d["missing_tests"]) * min(4, len(grade_info["pending"])), "", "deduct")

    # ---- maths -------------------------------------------------------------------
    subtotal = 0
    pct_total = 0.0
    mult_total = 1.0
    deductions_total = 0
    for r in rows:
        if r["kind"] == "pct":
            pct_total += float(r["amount"])
        elif r["kind"] == "mult":
            mult_total *= float(r["amount"])
        elif r["kind"] == "deduct":
            subtotal += r["amount"]                 # amount is already negative
            deductions_total += abs(r["amount"])
        else:
            subtotal += r["amount"]

    after_pct = subtotal * (1.0 + pct_total)
    suggested = after_pct * mult_total
    suggested = max(float(m.get("min_price", 2500)),
                    min(float(m.get("max_price", 400000)), suggested))

    low = suggested * float(m.get("spread_low", 0.90))
    high = suggested * float(m.get("spread_high", 1.13))
    buy = suggested * float(m.get("buy_ratio", 0.62))

    # round to sensible retail numbers
    def rnd(x: float) -> int:
        x = max(0.0, float(x))
        if x < 10000:
            return int(round(x / 100.0) * 100)
        if x < 50000:
            return int(round(x / 500.0) * 500)
        return int(round(x / 1000.0) * 1000)

    out = {
        "currency": m.get("currency", "INR"),
        "model_version": m.get("model_version", "?"),
        "rows": rows,
        "subtotal": rnd(subtotal),
        "condition_multiplier": round(mult_total, 3),
        "age_penalty_pct": round(pct_total * 100, 1),
        "suggested": rnd(suggested),
        "low": rnd(low),
        "high": rnd(high),
        "buy_price": rnd(buy),
        "repair_estimate": rnd(deductions_total),
        "margin": rnd(suggested) - rnd(buy),
        "margin_pct": round(100.0 * (rnd(suggested) - rnd(buy)) / rnd(suggested), 1) if suggested else 0,
        "grade": grade,
        "note": m.get("market_note", ""),
    }
    out["as_is_price"] = rnd(suggested)
    return out
