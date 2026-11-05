"""
demo.py -- a realistic simulated scan.

Three uses:
  1. Let the shop see exactly what the report looks like BEFORE testing a real
     laptop (Start > "Load demo scan").
  2. Let the developer build and check the report engine on any OS - this is
     how the whole report/valuation/grading stack is verified without Windows.
  3. `run.py --selftest` uses it to prove the exe still works end to end.

Every value here is clearly marked as simulated, and the generated report
carries a red "DEMO REPORT" banner so it can never be mistaken for a real
inspection or handed to a customer by accident.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from app.core import CHECKS, CheckResult, make_result
from app.scan import Scan

DEMO_IDENTITY = {
    "manufacturer": "Dell Inc.", "model": "Latitude 5400", "family": "Latitude",
    "sku": "LAT5400-8365U", "serial": "DEMO7X4Q2", "chassis": "Notebook", "is_laptop": True,
}


def _r(cid: str, status: str, summary: str, score: float,
       details: Dict[str, Any], notes: List[str] = (), flags: List[Any] = (),
       marks: List[Dict[str, Any]] = (), photos: List[str] = (),
       seconds: float = 0.4) -> CheckResult:
    res = make_result(cid)
    res.status = status
    res.summary = summary
    res.score = score
    res.details = dict(details)
    res.notes = list(notes)
    res.flags = [tuple(f) if isinstance(f, (list, tuple)) else (f, "warn") for f in flags]
    res.marks = list(marks)
    res.photos = list(photos)
    res.seconds = seconds
    return res


def build_demo_scan(scan_id: str = "LCP-DEMO-0001") -> Scan:
    scan = Scan(scan_id=scan_id)
    scan.demo = True
    scan.started = time.time() - 412
    scan.finished = time.time()
    scan.ctx.update({
        "identity": DEMO_IDENTITY,
        "technician": "Demo Technician",
        "signed_off": True,
        "admin": True,
        "cosmetic_grade": "B",
        "cpu": {"name": "Intel(R) Core(TM) i5-8365U CPU @ 1.60GHz", "cores": 4, "threads": 8,
                "max_mhz": 1896,
                "gen": {"brand": "Intel", "family": "Core i5", "gen": 8, "gen_label": "8th Gen",
                        "win11_ok": True, "tier": "mid", "raw": "i5-8365U"}},
        "memory": {"total_bytes": 8 * 1024 ** 3, "total_gb": 8.0, "modules": 1, "slots": 2,
                   "free_slots": 1, "speed": 2400, "type": "DDR4", "single_channel": True},
        "storage": {"total_bytes": 256 * 1024 ** 3, "has_ssd": True, "has_hdd": False,
                    "boot_is_ssd": True, "max_wear": 6.0, "problems": [],
                    "disks": [{"name": "SK hynix SC311 SATA 256GB", "size": 256 * 1024 ** 3,
                               "media": "SSD", "bus": "SATA", "health": "Healthy",
                               "wear": 6.0, "poh": 9412, "serial": "DEMO1234567", "is_boot": True}]},
        "display": {"native_w": 1920, "native_h": 1080, "cur_w": 1920, "cur_h": 1080, "hz": 60,
                    "size_inch": 14.0, "klass": "Full HD (1920x1080)", "panel_maker": "AU Optronics",
                    "panel_name": "B140HAN04.0", "panel_year": 2019, "touch": False, "brightness": 70},
        "gpu": {"names": ["Intel(R) UHD Graphics 620"], "dedicated": False, "count": 1},
        "battery": {"present": True, "design_mwh": 42000, "full_mwh": 30240, "health": 72.0,
                    "wear": 28.0, "cycles": 486},
        "os": {"caption": "Microsoft Windows 11 Pro", "build": "26100", "major": 26100,
               "arch": "64-bit", "activated": True, "activation_label": "Licensed (activated)",
               "oem_key": True, "win11_installed": True, "win11_capable": True,
               "tpm_spec": "2.0", "install_date": "2024-08-11"},
        "wifi": {"present": True}, "bluetooth": {"present": True, "devices": 3},
        "ethernet": {"present": True},
        "audio": {"speakers": 2, "mics": 2,
                  "speaker_names": ["Speakers (Realtek High Definition Audio)",
                                    "Headphones (Realtek Audio)"],
                  "mic_names": ["Microphone Array (Realtek Audio)"]},
        "camera": {"present": True, "devices": [{"name": "Integrated Webcam", "status": "OK",
                                                 "class": "Camera", "id": "USB\\VID_0BDA", "ir": False}]},
        "ports": {"usb_controllers": 3, "usb3": True, "hubs": 4, "optical": False,
                  "card_reader": True, "fingerprint": False, "external_video": 1},
        "events": {"whea": 0, "disk": 0, "crash": 0, "problems": 0},
        "input": {"internal_keyboards": ["ACPI\\PNP0303"], "all_keyboards": ["ACPI\\PNP0303"],
                  "external": [], "touchpads": ["DELL0873 Precision Touchpad"], "ext_mice": []},
    })

    R = scan.results
    R["identity"] = _r("identity", "pass", "Dell Inc. Latitude 5400 (S/N DEMO7X4Q2)", 100, {
        "Manufacturer": "Dell Inc.", "Model": "Latitude 5400", "Family / Series": "Latitude",
        "Serial number": "DEMO7X4Q2", "Chassis type": "Notebook", "Form factor": "Laptop / Notebook",
        "Motherboard": "Dell Inc. 0DEMO"}, seconds=1.2)
    R["os_license"] = _r("os_license", "pass", "Microsoft Windows 11 Pro - Licensed (activated)", 100, {
        "Operating system": "Microsoft Windows 11 Pro", "Build": "26100", "Architecture": "64-bit",
        "Installed on": "2024-08-11", "Windows activation": "Licensed (activated)",
        "Licence edition": "Windows(R) Operating System, OEM_DM channel",
        "Firmware (OEM) key present": "Yes - 4KQ7X*****",
        "Windows 11 installed": "Yes", "TPM": "2.0"},
        notes=["Windows is activated - buyer gets a genuine, licensed machine.",
               "A digital licence key is embedded in the motherboard, so Windows can be re-installed "
               "and will re-activate automatically."], seconds=3.4)
    R["bios_tpm"] = _r("bios_tpm", "pass", "BIOS 1.31.0 (2024-02-19), TPM 2.0", 95, {
        "BIOS vendor": "Dell Inc.", "BIOS version": "1.31.0", "BIOS date": "2024-02-19",
        "SMBIOS": "3.2", "TPM version": "2.0", "TPM enabled": "Yes", "TPM activated": "Yes",
        "Secure Boot": "Enabled", "BitLocker": "Not available (Windows Home) or not encrypted",
        "BIOS age": "2 year(s)"}, seconds=2.1)
    R["cpu"] = _r("cpu", "pass", "Intel(R) Core(TM) i5-8365U CPU @ 1.60GHz", 100, {
        "Processor": "Intel(R) Core(TM) i5-8365U CPU @ 1.60GHz", "Cores / Threads": "4 cores / 8 threads",
        "Max clock": "1.89 GHz", "Current clock": "1.89 GHz", "Cache": "L2 1024 KB / L3 6.0 MB",
        "Brand / family": "Intel Core i5", "Generation": "8th Gen", "Performance tier": "Mid",
        "Virtualisation (VT-x/AMD-V)": "Enabled", "SLAT (needed for Hyper-V)": "Supported"}, seconds=2.6)
    R["cpu_stress"] = _r("cpu_stress", "pass", "Loaded 8 thread(s) for 20s, peak 78 C", 92, {
        "Threads used": 8, "Test length": "20 seconds", "Idle temperature": "54 C (ACPI\\ThermalZone)",
        "Peak temperature": "78 C", "Temperature curve": "58 -> 66 -> 72 -> 76 -> 78 -> 78",
        "Clock during load": "2896 MHz (max reported 1896 MHz)", "CPU utilisation reached": "100%",
        "Sustained throughput vs. start": "96%", "Work units / second": "start 184220 -> end 176851",
        "New WHEA errors during the test": 0},
        notes=["The CPU held its speed for the whole test (96% sustained) - cooling is healthy.",
               "Peak temperature 78 C - normal."], seconds=24.8)
    R["memory"] = _r("memory", "warn", "8 GB DDR4 in 1 module", 88, {
        "Module 1 (DIMM A)": "8 GB DDR4 DELL 0DEMO @ 2400 MHz", "Total RAM installed": "8 GB",
        "Memory type": "DDR4", "Running speed": "2400 MHz", "Modules fitted": "1",
        "Slots available": "1 of 2", "Maximum supported": "32 GB", "Seen by Windows": "8 GB"},
        notes=["Single-channel memory: only one module fitted. Adding a second identical module gives "
               "a real speed boost and raises the resale price.",
               "8 GB RAM is the practical minimum today."],
        flags=[["Only one memory module fitted - single channel", "warn"]], seconds=1.8)
    R["memory_test"] = _r("memory_test", "pass", "256 MB verified across 2 passes, no errors", 100, {
        "Memory exercised": "256 MB", "Passes": 2,
        "Patterns used": "0x00, 0xFF, 0x5A, 0xA5, 0x33, 0xCC, 0x0F, 0xF0, random",
        "Data written + verified": "4.5 GB", "Errors found": 0, "Throughput": "2180 MB/s",
        "Test duration": "2.1 s", "Last Windows Memory Diagnostic": "Never run on this machine"},
        notes=["This is a fast in-Windows integrity check. For a definitive verdict run the Windows "
               "Memory Diagnostic (mdsched.exe, reboots the PC) or MemTest86 from a USB stick."],
        seconds=2.1)
    R["storage"] = _r("storage", "pass", "256 GB across 1 disk(s), SSD", 94, {
        "Disk 1": "SK hynix SC311 SATA 256GB (SATA SSD) - 256 GB",
        "  Health (SMART)": "Healthy / OK", "  Serial": "DEMO1234567", "  Wear used": "6%",
        "  Power-on time": "9,412 hours (~1.1 years)", "  Temperature": "38 C",
        "  SMART errors": "read 0 (uncorrected 0) / write 0 (uncorrected 0)",
        "Total storage": "256 GB", "Drive C:": "148 GB free of 237 GB (NTFS) - Healthy"},
        notes=["Windows boots from an SSD - good."], seconds=6.2)
    R["gpu"] = _r("gpu", "pass", "Intel(R) UHD Graphics 620", 96, {
        "GPU 1": "Intel(R) UHD Graphics 620", "  Driver": "31.0.101.2121 (2024-06-04)",
        "  Video memory": "1.0 GB", "  Output mode": "1920x1080 @ 60 Hz, 32-bit colour",
        "  Status": "OK", "Dedicated graphics": "No (integrated only)"}, seconds=1.9)
    R["display"] = _r("display", "pass", '14.0" panel, Full HD (1920x1080)', 94, {
        "Panel manufacturer": "AU Optronics", "Panel model": "B140HAN04.0",
        "Panel native resolution": "1920x1080", "Panel diagonal": '14.0"',
        "Panel manufactured": "2019 (week 21)", "EDID checksum": "Valid",
        "Current resolution": "1920x1080 @ 60 Hz", "Displays connected": 1,
        "Resolution class": "Full HD (1920x1080)", "Output LVDS (internal panel)": "LVDS (internal panel)",
        "Backlight brightness": "70% (11 levels reported)", "Touch screen": "No"},
        notes=["The panel itself is about 7 years old."], seconds=2.4)
    R["battery"] = _r("battery", "warn", "Health 72% (486 cycles)", 82, {
        "Battery present": "Yes", "Device name": "DELL DEMO", "Chemistry": "Lithium-ion",
        "Design capacity": "42000 mWh (42.0 Wh)", "Full charge capacity": "30240 mWh (30.2 Wh)",
        "Battery health": "72.0%", "Wear": "28.0%", "Charge cycles": 486,
        "Charge right now": "84% (AC connected)", "Charging state": "Charging",
        "Estimated runtime left": "1h 42m"},
        flags=[["Battery health 72% - runtime will be short; buyers usually want a replacement", "warn"]],
        seconds=7.6)
    R["charger"] = _r("charger", "pass", "Adapter connected and the battery IS charging", 100, {
        "Power source": "AC connected", "Charging flag": "Yes", "Charge level": "84%",
        "Charge rate": "42.0 W", "Power online": True}, seconds=0.6)
    R["wifi"] = _r("wifi", "pass", "Wi-Fi adapter connected to 'SHOP-5G'", 100, {
        "Adapter": "Intel(R) Wireless-AC 9560", "Status": "Up", "MAC": "DE:MO:12:34:56:78",
        "Link speed": "866 Mbps", "Driver": "Netwtw08.sys 22.240.0.3", "Driver date": "2024-04-09",
        "Radio standards": "802.11a 802.11b 802.11g 802.11n 802.11ac",
        "Interface state": "connected", "Connected network": "SHOP-5G", "Signal strength": "92%",
        "Rx / Tx rate": "866.7 / 780.0 Mbps", "WLAN service": "Running"},
        notes=["Wi-Fi 5 (802.11ac) - fine for most buyers."], seconds=4.2)
    R["bluetooth"] = _r("bluetooth", "pass", "Bluetooth present (3 entries)", 100, {
        "BT device 1": "Intel(R) Wireless Bluetooth(R) - OK",
        "Bluetooth service": "Running (Automatic)", "Radios detected": 1},
        notes=["Pairing test: in the guided phase, pair a phone or a mouse to confirm the radio works."],
        seconds=1.4)
    R["ethernet"] = _r("ethernet", "pass", "Ethernet adapter present (Up)", 100, {
        "LAN adapter": "Realtek PCIe GbE Family Controller", "Status": "Up",
        "Link speed": "1 Gbps", "MAC": "DE:MO:AA:BB:CC:DD"}, seconds=1.1)
    R["audio_dev"] = _r("audio_dev", "pass", "2 output and 1 input device(s) registered", 100, {
        "Audio controllers": 1, "Controller 1": "Realtek High Definition Audio - OK",
        "Playback endpoints (speakers/headphones)": 2,
        "  Output": "Speakers (Realtek(R) Audio)", "Recording endpoints (microphones)": 1,
        "  Input": "Microphone Array (Realtek(R) Audio)",
        "Windows Audio service": "Running", "Audio Endpoint Builder": "Running"}, seconds=1.6)
    R["camera_dev"] = _r("camera_dev", "pass", "1 camera device(s) detected", 100, {
        "Camera 1": "Integrated Webcam [Camera] - OK", "Instance": "USB\\VID_0BDA&PID_5510",
        "Windows camera privacy setting": "Allow", "Windows Camera service": "Running"}, seconds=1.3)
    R["ports"] = _r("ports", "pass", "3 USB controller(s), card reader: yes, optical: no", 100, {
        "USB host controllers": 3, "USB 3.x support": "Yes (2 controller(s))", "USB hubs": 4,
        "Serial / COM / LPT ports": 0, "Optical drive (DVD/CD)": "No",
        "SD / card reader": "Yes - Realtek PCIE CardReader", "Fingerprint reader": "No",
        "Video output": "HDMI"}, seconds=2.0)
    R["privacy_leftovers"] = _r("privacy_leftovers", "pass", "Only system profiles present - disk looks clean", 100, {
        "User profiles on disk": "none real"})
    R["touch"] = _r("touch", "pass", "9/9 tap targets hit with the finger", 100, {
        "Tap targets hit": "9 / 9", "Stray touches": 0})
    R["biometric"] = _r("biometric", "pass", "1 biometric device(s) present and healthy", 100, {
        "Biometric device 1": "Synaptics WBDI - SGX - OK"})
    R["input_dev"] = _r("input_dev", "pass", "1 keyboard device(s), 1 pointing device(s)", 100, {
        "Keyboard": "Standard PS/2 Keyboard [internal] OK", "Instance": "ACPI\\PNP0303\\4&DEMO",
        "Built-in keyboard detected": "Yes", "Touchpad": "DELL0873 Precision Touchpad",
        "External keyboards connected": 0,
        "Pointing device": "DELL0873 Precision Touchpad (HID\\DELL0873)"}, seconds=1.5)
    R["events"] = _r("events", "pass", "No hardware errors logged - clean event history", 100, {
        "WHEA hardware errors (45 days)": 0, "Disk / storage errors (45 days)": 0,
        "Unexpected shutdowns (Kernel-Power 41 / 6008)": 0, "Devices in error state right now": 0},
        seconds=5.1)

    # ---- guided ---------------------------------------------------------
    screens = [("PURE WHITE", "good", ""), ("PURE BLACK", "okay", "small clouding bottom-left corner"),
               ("PURE RED", "good", ""), ("PURE GREEN", "good", ""), ("PURE BLUE", "good", ""),
               ("YELLOW", "good", ""), ("CYAN", "good", ""), ("MAGENTA", "good", ""),
               ("MID GREY 50%", "good", ""), ("LIGHT GREY", "good", ""), ("DARK GREY", "good", ""),
               ("HORIZONTAL GRADIENT", "good", ""), ("VERTICAL GRADIENT", "good", ""),
               ("GREY STEPS", "good", ""), ("CHECKERBOARD", "good", ""),
               ("1-PIXEL VERTICAL LINES", "good", ""), ("1-PIXEL HORIZONTAL LINES", "good", ""),
               ("GEOMETRY GRID", "good", ""), ("BACKLIGHT BLEED TEST", "okay", "faint glow top-right"),
               ("TEXT SHARPNESS", "good", "")]
    R["screen_colours"] = _r("screen_colours", "warn", "18 good / 2 okay / 0 bad out of 20 screens", 90, {
        "Screens shown": 20, "Marked good": 18, "Marked okay": 2, "Marked bad": 0, "Skipped": 0,
        "Not inspected": 0, "Screen resolution used": "1920x1080", "Test duration": "148 s"},
        marks=[{"id": "s%d" % i, "name": n, "mark": m, "note": nt}
               for i, (n, m, nt) in enumerate(screens)],
        flags=[["Display defect on PURE BLACK: small clouding bottom-left corner", "warn"]],
        seconds=148.0)
    R["screen_pixels"] = _r("screen_pixels", "pass", "No dead pixels found", 100, {
        "Method": "Full-screen white / black / red / green / blue inspection",
        "Dead pixels": 0, "Stuck pixels": 0, "Scratches": "none visible"},
        marks=[{"id": "pixels", "name": "Dead / stuck pixels", "mark": "good", "note": ""}], seconds=32.0)
    R["screen_text"] = _r("screen_text", "pass", "Text is crisp at native resolution", 100, {
        "Sharpness": "Crisp", "Colour fringing": "None"},
        marks=[{"id": "text", "name": "Text sharpness", "mark": "good", "note": ""}], seconds=9.0)
    R["speakers"] = _r("speakers", "pass", "Speakers and headphone jack scored 100/100", 100, {
        "LEFT speaker": "CLEAR", "RIGHT speaker": "CLEAR", "BOTH speakers": "CLEAR",
        "FREQUENCY SWEEP": "CLEAR", "LOUDNESS at 100% volume": "CLEAR", "3.5 mm HEADPHONE jack": "CLEAR",
        "Tones actually played": 4},
        marks=[{"id": "left", "name": "LEFT speaker", "mark": "good", "note": ""},
               {"id": "right", "name": "RIGHT speaker", "mark": "good", "note": ""},
               {"id": "both", "name": "BOTH speakers", "mark": "good", "note": ""},
               {"id": "sweep", "name": "FREQUENCY SWEEP", "mark": "good", "note": ""},
               {"id": "jack", "name": "3.5 mm HEADPHONE jack", "mark": "good", "note": ""}], seconds=41.0)
    R["microphone"] = _r("microphone", "pass", "Microphone works", 100, {
        "Input devices found": 1, "Chunks captured": 20, "Peak level": "-14.2 dBFS",
        "Average level": "-27.6 dBFS", "Automatic verdict": "clear signal"},
        marks=[{"id": "mic", "name": "Microphone", "mark": "good", "note": ""}], seconds=9.0)
    R["camera_capture"] = _r("camera_capture", "pass", "Camera works, image is clear", 100, {
        "Camera devices reported by Windows": 1, "Live frames captured": 68,
        "Capture device used": "Integrated Webcam", "Photo stored in report": "DEMO_camera.png"},
        marks=[{"id": "camera", "name": "Camera image quality", "mark": "good", "note": ""}], seconds=24.0)
    kb_details = {
        "Raw Input devices found": 1, "Raw Input capture": "Working",
        "Low-level hook capture": "Working",
        "  Device [ALLOWED]": "\\??\\ACPI#PNP0303#4&DEMO - legacy PS/2-style ACPI keyboard (PNP03xx)",
        "Required keys": 61, "Pressed on the built-in keyboard": 61,
        "Optional keys also tested": 5, "Keys marked as not present": 2, "Keys never pressed": 0,
        "Keys rejected (wrong device)": 0, "Total violations recorded": 0,
        "Injected key presses detected": 0, "Technician confirmation ticked": "Yes",
        "Test duration": "74 s", "Not-present keys": "Ins, PgUp",
    }
    R["keyboard"] = _r("keyboard", "pass",
                       "All 61 required keys verified on the built-in keyboard, no violations", 100,
                       kb_details,
                       notes=["Anti-cheat: keys were validated against the Raw Input device handle of the "
                              "built-in keyboard, plus a low-level hook that flags injected key presses."],
                       marks=[{"id": "keyboard", "name": "Built-in keyboard", "mark": "good",
                               "note": "61/61 required keys verified on the built-in keyboard, 0 violation(s)"}],
                       seconds=74.0)
    R["touchpad"] = _r("touchpad", "pass", "Touchpad scored 100/100 across 8 checks", 100, {
        "Pointer MOVES smoothly": "WORKS", "LEFT click (press and tap-to-click)": "WORKS",
        "RIGHT click": "WORKS", "TWO-FINGER SCROLL": "WORKS",
        "Precision gestures (3-finger swipe / pinch zoom)": "WORKS",
        "No cursor DRIFT when untouched": "WORKS",
        "Pad surface and buttons are physically sound": "WORKS",
        "TrackPoint / pointing stick (if fitted)": "NOT FITTED"}, seconds=38.0)
    R["usb_ports"] = _r("usb_ports", "warn", "3/4 USB port(s) verified working, 1 dead", 72, {
        "Ports declared by technician": 4, "Verified automatically (device appeared)": 3,
        "Confirmed manually": 0, "Dead / loose": 1, "Not tested": 0,
        "  Port 1": "auto", "  Port 2": "auto", "  Port 3": "auto", "  Port 4": "dead",
        "USB host controllers seen": 3, "USB 3.x present": "Yes"},
        flags=[["1 USB port(s) are dead or loose", "warn"]], seconds=126.0)
    R["web_ports"] = _r("web_ports", "pass", "3 of 3 external port groups verified, 0 faulty", 100, {
        "HDMI / DisplayPort": "WORKS - detected automatically",
        "Headphone jack": "WORKS - detected automatically",
        "Card reader": "WORKS - detected automatically",
        "Card reader present (from hardware scan)": "Yes"}, seconds=96.0)
    R["cosmetic"] = _r("cosmetic", "warn", "Cosmetic grade B (86/100), 1 photo(s)", 86, {
        "Lid / top cover": "WORKS", "Screen bezel and hinges area": "WORKS",
        "HINGE - open and close 5 times": "WORKS", "Bottom case": "UNRELIABLE - two shallow scratches",
        "Palm rest and touchpad area": "WORKS", "Keycaps - wear and shine": "UNRELIABLE - WASD shine",
        "Port openings - damage": "WORKS", "Screws all present and undamaged": "WORKS",
        "NO signs of liquid damage": "WORKS", "No burning / chemical smell": "WORKS",
        "Screen glass/panel surface": "WORKS", "Charger / adapter supplied": "WORKS",
        "Battery not swollen": "WORKS", "Box / bill / accessories": "FAULTY - no box or bill",
        "Cosmetic grade": "B (86% of items clean)", "Damage photos attached": 0},
        flags=[["Cosmetic issue: Box / bill / accessories", "warn"]], seconds=163.0)
    R["final_check"] = _r("final_check", "pass", "Signed off by Demo Technician", 100, {
        "Technician": "Demo Technician", "Tests left incomplete": 0,
        "Problem flags at sign-off": 4}, seconds=12.0)
    return scan
