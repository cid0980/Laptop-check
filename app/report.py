"""
report.py -- the deliverable: a single self-contained HTML inspection report.

Why HTML and not PDF?
  * one file, opens in any browser, prints to PDF with Ctrl+P
  * no PDF library, so the .exe stays small and installs nothing
  * photos and the QR code are embedded as base64 / inline SVG, so the file
    can be e-mailed or WhatsApp'd on its own and still looks complete
  * the same engine produces two versions:
        INTERNAL  - everything: faults, buy price, margin, repair estimates
        CUSTOMER  - clean one-pager: specs, what was tested, the grade and the
                    shop's selling price. No buy price, no internal flags.

Also writes:  <scan>.json  (full machine-readable record)
              inventory.csv (one line per laptop, for the shop's stock list)
"""

from __future__ import annotations

import html
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from app import png as pnglib
from app.core import (APP_NAME, APP_VERSION, CHECKS, PHOTOS_DIR, fmt_bytes,
                      fmt_inr, load_json, write_json, write_text)
from app.qr import encode as qr_encode, to_svg as qr_svg
from app.scan import Scan

STATUS_META = {
    "pass":    ("PASS",       "#0f9d58", "#e6f6ec"),
    "warn":    ("WARNING",    "#b26a00", "#fff4e0"),
    "fail":    ("FAIL",       "#c62828", "#fdeaea"),
    "na":      ("NOT FITTED", "#5b6b82", "#eef1f6"),
    "skipped": ("NOT TESTED", "#7a5cc4", "#f1ecfb"),
    "missing": ("NOT RUN",    "#7a5cc4", "#f1ecfb"),
    "unknown": ("UNKNOWN",    "#5b6b82", "#eef1f6"),
}

GRADE_COLOUR = {"A+": "#0f9d58", "A": "#0f9d58", "B": "#3f8cff",
                "C": "#e08600", "D": "#e05a00", "E": "#c62828"}


def esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _pill(status: str) -> str:
    label, fg, bg = STATUS_META.get(status, STATUS_META["unknown"])
    return ('<span class="pill" style="color:%s;background:%s;border:1px solid %s33">%s</span>'
            % (fg, bg, fg, label))


def _bar(score: Optional[float], colour: str = "#3f8cff") -> str:
    if score is None:
        return '<span class="muted">-</span>'
    s = max(0.0, min(100.0, float(score)))
    return ('<div class="bar"><div class="barfill" style="width:%.0f%%;background:%s"></div>'
            '<span class="barlabel">%.0f</span></div>' % (s, colour, s))


def _photo_tag(path: str, caption: str = "") -> str:
    try:
        if not path or not os.path.exists(path):
            return ""
        ext = os.path.splitext(path)[1].lower()
        if ext in (".jpg", ".jpeg"):
            mime = "image/jpeg"
            with open(path, "rb") as fh:
                import base64
                uri = "data:%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode())
        else:
            uri = pnglib.file_to_data_uri(path)
        if not uri:
            return ""
        return ('<figure class="photo"><img src="%s" alt="%s"/><figcaption>%s</figcaption></figure>'
                % (uri, esc(caption), esc(caption or os.path.basename(path))))
    except Exception:
        return ""


# ==========================================================================
# CSS
# ==========================================================================

def css(primary: str, accent: str, danger: str, warn: str) -> str:
    return """
:root{--primary:%(primary)s;--accent:%(accent)s;--danger:%(danger)s;--warn:%(warn)s;
--ink:#0d1117;--ink2:#3d4757;--muted:#6b7688;--line:#e3e8ef;--bg:#f5f7fa;--card:#fff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;font-size:14px;line-height:1.45}
.page{max-width:1040px;margin:0 auto;padding:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:18px 20px;margin-bottom:14px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
h1,h2,h3{margin:0 0 8px}
h2{font-size:16px;letter-spacing:.02em;text-transform:uppercase;color:var(--ink2);
border-bottom:2px solid var(--line);padding-bottom:6px;margin-bottom:12px}
h3{font-size:14px}
.muted{color:var(--muted)}
.small{font-size:12px}
table{width:100%%;border-collapse:collapse}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#f8fafc;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
td.k{width:34%%;color:var(--ink2);font-weight:600}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700;
letter-spacing:.03em;white-space:nowrap}
.bar{position:relative;background:#eef1f6;border-radius:6px;height:16px;min-width:90px;overflow:hidden}
.barfill{height:100%%;border-radius:6px}
.barlabel{position:absolute;right:6px;top:0;font-size:11px;font-weight:700;color:#0d1117;line-height:16px}
.head{display:flex;gap:16px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap}
.brand{display:flex;gap:12px;align-items:center}
.brand img{height:52px;width:auto;border-radius:6px}
.shop{font-size:22px;font-weight:800;color:var(--primary);line-height:1.1}
.tag{color:var(--muted);font-size:12px}
.verdict{display:flex;gap:20px;align-items:center;flex-wrap:wrap}
.gradebox{width:112px;height:112px;border-radius:16px;display:flex;flex-direction:column;
align-items:center;justify-content:center;color:#fff;flex:0 0 auto}
.gradebox .g{font-size:44px;font-weight:900;line-height:1}
.gradebox .s{font-size:12px;opacity:.92;margin-top:2px}
.pricebox{border:2px solid var(--primary);border-radius:12px;padding:10px 16px;min-width:230px}
.pricebox .big{font-size:26px;font-weight:900;color:var(--primary)}
.kv{display:grid;grid-template-columns:1fr 1fr;gap:0 22px}
.kv table{margin-bottom:4px}
.specgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}
.spec{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:#fcfdff}
.spec .t{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.spec .v{font-size:15px;font-weight:700;margin-top:2px}
.spec .d{font-size:11px;color:var(--muted);margin-top:2px}
.flag{border-left:4px solid var(--warn);background:#fffaf0;padding:8px 12px;border-radius:0 8px 8px 0;
margin-bottom:6px}
.flag.fail{border-left-color:var(--danger);background:#fff5f5}
.flag.ok{border-left-color:var(--accent);background:#f2fbf6}
.grouphead{display:flex;justify-content:space-between;align-items:center;
background:#f2f5f9;border-radius:8px;padding:6px 10px;margin:10px 0 4px;font-weight:700}
details{border:1px solid var(--line);border-radius:10px;margin-bottom:8px;background:#fff}
details>summary{cursor:pointer;padding:9px 12px;font-weight:700;list-style:none;
display:flex;justify-content:space-between;gap:10px;align-items:center}
details>summary::-webkit-details-marker{display:none}
details[open]>summary{border-bottom:1px solid var(--line);background:#fafbfd}
.detail{padding:10px 12px}
.notes{margin:6px 0 0;padding-left:18px}
.notes li{margin-bottom:3px}
.photo{margin:6px 8px 6px 0;display:inline-block;text-align:center}
.photo img{max-width:280px;max-height:200px;border:1px solid var(--line);border-radius:8px}
.photo figcaption{font-size:11px;color:var(--muted);margin-top:3px;max-width:280px}
.qr{float:right;margin-left:14px}
.banner{border-radius:10px;padding:10px 14px;font-weight:700;margin-bottom:12px}
.banner.draft{background:#fff4e0;border:1px solid #f0c36d;color:#7a4b00}
.banner.integrity{background:#fff5f5;border:1px solid #f0b6b6;color:#8a1c1c}
.money{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.pos{color:#0f7b3f}.neg{color:#c62828}
.foot{text-align:center;color:var(--muted);font-size:11px;padding:14px 0 24px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media print{
 body{background:#fff;font-size:11.5px}
 .page{max-width:none;padding:0}
 .card{box-shadow:none;border-color:#d8dee7;break-inside:avoid;page-break-inside:avoid;margin-bottom:8px;padding:10px 12px}
 details{break-inside:avoid}
 details:not(.keep)>summary+*{display:block}
 .noprint{display:none}
 h2{font-size:13px}
 .gradebox{width:88px;height:88px}.gradebox .g{font-size:34px}
 a{color:inherit;text-decoration:none}
}
@page{margin:12mm}
""" % {"primary": primary, "accent": accent, "danger": danger, "warn": warn}


# ==========================================================================
# recommendations engine
# ==========================================================================

RULES: List[Tuple[str, str, str, str, str]] = [
    # (needle in a FLAG, priority, action, cost, why)
    ("liquid damage", "SAFETY", "Do not resell as a working unit. Board-level cleaning/repair or sell for parts.",
     "\u20b90 - \u20b98,000", "Liquid damage corrodes over weeks; it always comes back as a warranty claim."),
    ("swollen", "SAFETY", "Remove and safely dispose of the swollen battery before anything else.",
     "\u20b92,000 - \u20b94,500", "A swollen battery is a fire risk and is lifting the chassis."),
    ("fire risk", "SAFETY", "Remove the battery and store the unit away from other stock.",
     "\u20b92,000 - \u20b94,500", "Safety first."),
    ("bitlocker", "MUST", "Decrypt the drive (or obtain the recovery key) and clear any BIOS password.",
     "\u20b90", "A locked machine cannot be resold or re-imaged."),
    ("not activated", "MUST", "Activate Windows with the firmware key, or reinstall Windows 11.",
     "\u20b90 - \u20b91,500", "Buyers see the activation watermark and refuse to pay full price."),
    ("failing", "MUST", "Replace the storage drive and image Windows fresh.",
     "\u20b91,800 - \u20b94,500", "A failing drive is a guaranteed return."),
    ("uncorrected", "MUST", "Replace the drive - uncorrected SMART errors mean data loss.",
     "\u20b91,800 - \u20b94,500", "Unrecoverable read/write errors."),
    ("memory error", "MUST", "Replace the faulty RAM module and re-run the memory test.",
     "\u20b91,200 - \u20b93,000", "Memory errors cause random crashes and blue screens."),
    ("whea", "MUST", "Investigate the hardware errors: reseat RAM, update the BIOS, re-test under load.",
     "\u20b90 - \u20b92,000", "WHEA errors mean the CPU, bus or memory is unstable."),
    ("keyboard", "MUST", "Replace the keyboard (or the whole top case).",
     "\u20b91,500 - \u20b93,500", "A dead key makes the machine unsellable."),
    ("rejected", "MUST", "Re-run the keyboard test on the built-in keyboard only.",
     "\u20b90", "The previous keyboard result came from a device that is not the built-in keyboard."),
    ("injected", "MUST", "Re-run the keyboard test with no remote or macro software running.",
     "\u20b90", "Software-simulated key presses were detected."),
    ("never completed", "MUST", "Complete the missing tests before selling.",
     "\u20b90", "An incomplete inspection cannot be graded honestly."),
    ("not signed off", "MUST", "Have the technician sign off the inspection.",
     "\u20b90", "The report is currently a DRAFT."),
    ("never inspected", "MUST", "Re-run the display colour test and mark every screen.",
     "\u20b90", "Some test screens were never looked at."),
    ("display defect", "SHOULD", "Replace the panel if the defect shows on a white or black screen.",
     "\u20b93,000 - \u20b97,500", "Screen faults are the first thing a buyer sees."),
    ("thermal throttl", "SHOULD", "Clean the fan and heatsink, replace the thermal paste.",
     "\u20b9400 - \u20b9900", "Cheap fix, big improvement in feel and in the stress-test score."),
    ("battery health", "SHOULD", "Replace the battery.",
     "\u20b91,800 - \u20b94,000", "The most-asked question from used-laptop buyers."),
    ("no working battery", "SHOULD", "Fit a new battery.",
     "\u20b91,800 - \u20b94,000", "A laptop without a working battery sells for much less."),
    ("mechanical hdd", "SHOULD", "Upgrade to a SATA or NVMe SSD and clone/reinstall Windows.",
     "\u20b91,500 - \u20b93,000", "The biggest speed upgrade available; buyers expect an SSD."),
    ("too little for windows", "SHOULD", "Upgrade RAM to at least 8 GB (16 GB is the sweet spot).",
     "\u20b91,200 - \u20b93,200", "4 GB cannot run Windows 11 and a browser comfortably."),
    ("windows 10", "SHOULD", "Upgrade to Windows 11 if the hardware allows (free).",
     "\u20b90", "Windows 10 stopped receiving security updates on 14 Oct 2025."),
    ("usb port", "SHOULD", "Inspect and re-solder or replace the USB port/board.",
     "\u20b9500 - \u20b92,000", "A dead port is a common return reason."),
    ("hinge", "SHOULD", "Repair or replace the hinge and any cracked chassis parts.",
     "\u20b91,200 - \u20b93,500", "Hinge damage spreads quickly once it starts."),
    ("charger", "SHOULD", "Source a matching original charger.",
     "\u20b9800 - \u20b91,800", "Selling without a charger costs more than buying one."),
    ("no audio output", "SHOULD", "Reinstall the audio driver; if that fails, the speaker cable or board is at fault.",
     "\u20b90 - \u20b92,000", "No sound output at all."),
    ("camera", "NICE", "Check the privacy switch and driver, otherwise replace the camera module.",
     "\u20b90 - \u20b92,500", "Video calls are expected now."),
    ("wi-fi adapter is present but disabled", "NICE", "Enable the Wi-Fi adapter (Device Manager / BIOS) and re-test.",
     "\u20b90", "Usually a switch, not a fault."),
    ("wlan autoconfig", "NICE", "Start the WLAN AutoConfig service (services.msc) and re-test.",
     "\u20b90", "Windows cannot use Wi-Fi while that service is stopped."),
    ("bluetooth", "NICE", "Reinstall the Bluetooth driver / start the Bluetooth Support service.",
     "\u20b90 - \u20b9800", "Often just a stopped service."),
    ("faulty", "SHOULD", "Repair or replace the faulty item before selling.",
     "\u20b9500 - \u20b92,500", "Found during the guided port test."),
    ("cracked or damaged", "SHOULD", "Replace the damaged chassis part.",
     "\u20b9800 - \u20b93,000", "Cosmetic damage is the first thing a buyer discounts."),
    ("error state", "SHOULD", "Fix the device(s) in an error state: reinstall drivers or check the hardware.",
     "\u20b90 - \u20b91,500", "Device Manager is showing a problem."),
    ("no camera photo", "NICE", "Re-run the camera test and keep a photo as evidence.",
     "\u20b90", "The report has no proof that the camera was tested."),
    ("never answered", "NICE", "Complete the questions that were left unanswered.",
     "\u20b90", "Unanswered questions weaken the report."),
    ("did not tick", "NICE", "Re-run the test and tick the confirmation box.",
     "\u20b90", "The anti-cheat confirmation was not given."),
    ("external/virtual keyboard", "NICE", "Unplug external keyboards before the keyboard test.",
     "\u20b90", "They invalidate the built-in keyboard test."),
]

# notes that are not faults but are worth acting on (exact phrases)
NOTE_RULES: List[Tuple[str, str, str, str, str]] = [
    ("Single-channel memory", "NICE", "Add a second identical RAM stick for dual-channel speed.",
     "\u20b91,200 - \u20b92,600", "Up to 20% faster in real use, and it justifies a higher price."),
    ("Windows 10 reached end of support", "SHOULD", "Upgrade to Windows 11 if the hardware allows (free).",
     "\u20b90", "Windows 10 stopped receiving security updates on 14 Oct 2025."),
    ("Virtualisation is switched off", "NICE", "Enable VT-x / AMD-V in the BIOS.",
     "\u20b90", "Students and developers check for it."),
    ("Secure Boot is disabled", "NICE", "Enable Secure Boot in the BIOS.",
     "\u20b90", "Required for Windows 11 and expected by business buyers."),
    ("Graphics driver is from", "NICE", "Update the graphics and chipset drivers, and the BIOS.",
     "\u20b90", "Improves stability and the buyer's first impression."),
    ("is a mechanical HDD", "SHOULD", "Upgrade to an SSD and clone or reinstall Windows.",
     "\u20b91,500 - \u20b93,000", "The single biggest performance upgrade for an old laptop."),
    ("Low-resolution (1366x768) panel", "NICE", "Check whether an FHD panel is available for this model.",
     "\u20b92,500 - \u20b94,500", "A 1366x768 screen is the most common reason buyers walk away."),
    ("Only %d GB RAM", "SHOULD", "Upgrade the RAM to at least 8 GB.",
     "\u20b91,200 - \u20b93,200", "4 GB is not enough for Windows 11 plus a browser."),
]


def recommendations(scan: Scan) -> List[Dict[str, str]]:
    """Build the 'what to do before selling it' list from flags and key notes."""
    flags: List[str] = []
    for res in scan.results.values():
        for f in res.flags:
            flags.append(f[0] if isinstance(f, (list, tuple)) else str(f))
    notes: List[str] = []
    for res in scan.results.values():
        notes.extend(res.notes)

    out: List[Dict[str, str]] = []
    seen = set()
    order = {"SAFETY": 0, "MUST": 1, "SHOULD": 2, "NICE": 3}

    for text in flags:
        low = text.lower()
        for needle, priority, action, cost, why in RULES:
            if needle.lower() in low and action not in seen:
                seen.add(action)
                out.append({"priority": priority, "action": action, "cost": cost,
                            "why": why, "because": text})
    for text in notes:
        for needle, priority, action, cost, why in NOTE_RULES:
            if needle.lower() in text.lower() and action not in seen:
                seen.add(action)
                out.append({"priority": priority, "action": action, "cost": cost,
                            "why": why, "because": text})
    out.sort(key=lambda r: (order.get(r["priority"], 9), r["action"]))
    if not out:
        out.append({"priority": "NICE",
                    "action": "Detail clean: screen, keyboard and chassis; refresh Windows and drivers.",
                    "cost": "\u20b90 - \u20b9500",
                    "why": "Presentation is what actually sells a used laptop.",
                    "because": "No faults were found."})
    return out


# ==========================================================================
# sections
# ==========================================================================

def _header(scan: Scan, branding: Any, mode: str) -> str:
    ident = scan.ctx.get("identity") or {}
    logo = branding.logo_data_uri()
    qr_target = branding.qr_target(scan.id)
    qr_html = ""
    if qr_target:
        matrix = qr_encode(qr_target, "M")
        if matrix:
            qr_html = ('<div class="qr">%s<div class="small muted" style="text-align:center;max-width:120px">'
                       'Scan to verify</div></div>'
                       % qr_svg(matrix, scale=3, quiet=2))
    contact = " &nbsp;|&nbsp; ".join(x for x in [
        esc(branding.get("address")), esc(branding.get("phone")),
        esc(branding.get("email")), esc(branding.get("website")),
        ("GSTIN " + esc(branding.get("gst"))) if branding.get("gst") else ""] if x)
    title = esc(branding.get("report_title") or "Laptop Inspection Report")
    if mode == "customer":
        title += " &middot; Customer copy"
    return """
<div class="card">
  %(qr)s
  <div class="head">
    <div class="brand">
      %(logo)s
      <div>
        <div class="shop">%(shop)s</div>
        <div class="tag">%(tagline)s</div>
        <div class="small muted" style="margin-top:4px">%(contact)s</div>
      </div>
    </div>
    <div style="text-align:right">
      <h1 style="font-size:19px;color:var(--primary)">%(title)s</h1>
      <div class="small muted">Report ID <b>%(sid)s</b></div>
      <div class="small muted">%(date)s &nbsp;&middot;&nbsp; %(dur)s</div>
      <div class="small muted">Inspected by <b>%(tech)s</b></div>
    </div>
  </div>
</div>
""" % {
        "qr": qr_html,
        "logo": ('<img src="%s" alt="logo"/>' % logo) if logo else "",
        "shop": esc(branding.get("shop_name")),
        "tagline": esc(branding.get("tagline")),
        "contact": contact or "&nbsp;",
        "title": title,
        "sid": esc(scan.id),
        "date": esc(time.strftime("%d %b %Y, %H:%M", time.localtime(scan.started))),
        "dur": "test took %d min %d s" % divmod(int((scan.finished or time.time()) - scan.started), 60),
        "tech": esc(scan.ctx.get("technician") or "not recorded"),
    }


def _banners(scan: Scan) -> str:
    out = []
    integrity = (scan.grade or {}).get("integrity") or []
    pending = (scan.grade or {}).get("pending") or []
    if not scan.ctx.get("signed_off", False):
        out.append('<div class="banner draft">DRAFT &mdash; this inspection was not signed off by a '
                   'technician%s.</div>' % (" and %d test(s) were never completed" % len(pending)
                                            if pending else ""))
    for line in integrity[:6]:
        out.append('<div class="banner integrity">INTEGRITY: %s</div>' % esc(line))
    if scan.demo:
        out.append('<div class="banner draft">DEMO REPORT &mdash; generated with simulated data, '
                   'not from a real machine.</div>')
    return "".join(out)


def _verdict(scan: Scan, mode: str) -> str:
    g = scan.grade or {}
    v = scan.valuation or {}
    grade = g.get("grade", "?")
    colour = GRADE_COLOUR.get(grade, "#5b6b82")
    ident = scan.ctx.get("identity") or {}
    price_html = ""
    if mode == "internal" and v:
        price_html = """
    <div class="pricebox">
      <div class="small muted">Suggested resale price</div>
      <div class="big">%(sugg)s</div>
      <div class="small">realistic range %(low)s &ndash; %(high)s</div>
      <table style="margin-top:6px">
        <tr><td class="k">Buy / trade-in price</td><td class="money"><b>%(buy)s</b></td></tr>
        <tr><td class="k">Your margin</td><td class="money">%(margin)s (%(mpct)s%%)</td></tr>
        <tr><td class="k">Estimated repair cost</td><td class="money">%(rep)s</td></tr>
      </table>
    </div>""" % {"sugg": fmt_inr(v.get("suggested")), "low": fmt_inr(v.get("low")),
                "high": fmt_inr(v.get("high")), "buy": fmt_inr(v.get("buy_price")),
                "margin": fmt_inr(v.get("margin")), "mpct": v.get("margin_pct", 0),
                "rep": fmt_inr(v.get("repair_estimate"))}
    elif mode == "customer" and v:
        price_html = """
    <div class="pricebox">
      <div class="small muted">Our price for this machine</div>
      <div class="big">%(sugg)s</div>
      <div class="small muted">%(warr)s</div>
    </div>""" % {"sugg": fmt_inr(v.get("suggested")),
                 "warr": esc(scan.ctx.get("branding_warranty") or "")}

    counts = {"pass": 0, "warn": 0, "fail": 0, "na": 0, "skipped": 0}
    for r in scan.results.values():
        counts[r.status] = counts.get(r.status, 0) + 1

    return """
<div class="card">
  <div class="verdict">
    <div class="gradebox" style="background:%(colour)s">
      <div class="g">%(grade)s</div>
      <div class="s">%(score)s / 100</div>
    </div>
    <div style="flex:1;min-width:280px">
      <h3 style="font-size:18px">%(machine)s</h3>
      <div class="muted">%(meaning)s</div>
      <div style="margin:8px 0"><b>%(verdict)s</b></div>
      <div class="small">
        %(passn)d passed &nbsp;&middot;&nbsp; %(warnn)d warnings &nbsp;&middot;&nbsp;
        %(failn)d failed &nbsp;&middot;&nbsp; %(nan)d not fitted &nbsp;&middot;&nbsp;
        %(skipn)d not tested
      </div>
      <div class="small muted" style="margin-top:6px">
        Inspection completeness <b>%(comp)s%%</b> &nbsp;&middot;&nbsp;
        %(items)d scored checks &nbsp;&middot;&nbsp;
        %(w11)s
      </div>
    </div>
    %(price)s
  </div>
</div>
""" % {"colour": colour, "grade": esc(grade), "score": g.get("score", 0),
       "machine": esc("%s %s" % (ident.get("manufacturer", ""), ident.get("model", ""))).strip(),
       "meaning": esc(g.get("meaning", "")), "verdict": esc(g.get("verdict", "")),
       "passn": counts["pass"], "warnn": counts["warn"], "failn": counts["fail"],
       "nan": counts["na"], "skipn": counts.get("skipped", 0) + counts.get("missing", 0),
       "comp": (g.get("completeness", 0)), "items": g.get("scored_items", 0),
       "w11": esc(scan.ctx.get("w11_label", "")), "price": price_html}


def _specs(scan: Scan) -> str:
    ctx = scan.ctx
    ident = ctx.get("identity") or {}
    cpu = ctx.get("cpu") or {}
    gen = cpu.get("gen") or {}
    mem = ctx.get("memory") or {}
    sto = ctx.get("storage") or {}
    disp = ctx.get("display") or {}
    batt = ctx.get("battery") or {}
    gpu = ctx.get("gpu") or {}
    osinfo = ctx.get("os") or {}

    def spec(title: str, value: str, detail: str = "") -> str:
        return ('<div class="spec"><div class="t">%s</div><div class="v">%s</div>'
                '<div class="d">%s</div></div>' % (esc(title), esc(value), esc(detail)))

    cards = [
        spec("Processor", cpu.get("name", "-").split(" CPU ")[0][:38],
             "%s cores / %s threads &middot; %s" % (cpu.get("cores", "?"), cpu.get("threads", "?"),
                                                    gen.get("gen_label", ""))),
        spec("Memory", "%d GB" % int(mem.get("total_gb") or 0) if mem.get("total_gb") else "-",
             "%s &middot; %d module(s) &middot; %s" % (mem.get("type", ""), mem.get("modules", 0),
                                                       ("%d free slot(s)" % mem["free_slots"])
                                                       if mem.get("free_slots") else "no free slot")),
        spec("Storage", fmt_bytes(sto.get("total_bytes")) if sto.get("total_bytes") else "-",
             "%s &middot; %d disk(s)%s" % ("SSD" if sto.get("has_ssd") else "HDD",
                                           len(sto.get("disks") or []),
                                           " &middot; wear %d%%" % sto["max_wear"]
                                           if sto.get("max_wear") else "")),
        spec("Display", disp.get("klass", "-"),
             ("%s &middot; " % ('%.1f"' % disp["size_inch"]) if disp.get("size_inch") else "") +
             ("%dx%d" % (disp["cur_w"], disp["cur_h"]) if disp.get("cur_w") else "") +
             (" &middot; touch" if disp.get("touch") else "")),
        spec("Graphics", (gpu.get("names") or ["-"])[0][:34],
             "%d adapter(s)%s" % (gpu.get("count", 0),
                                   " &middot; dedicated GPU" if gpu.get("dedicated") else " &middot; integrated")),
        spec("Battery", ("%.0f%% health" % batt["health"]) if batt.get("health") else
             ("present" if batt.get("present") else "not fitted"),
             ("%d charge cycles" % batt["cycles"]) if batt.get("cycles") is not None else
             ("cycle count not reported" if batt.get("present") else "-")),
        spec("Operating system", (osinfo.get("caption") or "-").replace("Microsoft ", ""),
             "%s &middot; %s" % ("Activated" if osinfo.get("activated") else "NOT activated",
                                 "Windows 11" if osinfo.get("win11_installed")
                                 else ("Windows 11 capable" if osinfo.get("win11_capable")
                                       else "Windows 11 NOT supported"))),
        spec("Model / serial", ident.get("model", "-")[:30],
             "%s &middot; S/N %s" % (ident.get("manufacturer", ""), ident.get("serial", "?"))),
    ]
    return '<div class="card"><h2>What this machine is</h2><div class="specgrid">%s</div></div>' % "".join(cards)


def _scorecard(scan: Scan, mode: str) -> str:
    groups: Dict[str, List[Any]] = {}
    for cid, name, group, weight, tech in CHECKS:
        groups.setdefault(group, []).append(cid)
    g = scan.grade or {}
    gscore = {r["group"]: r for r in (g.get("groups") or [])}

    rows = []
    for group, cids in groups.items():
        info = gscore.get(group, {})
        gs = info.get("score")
        rows.append('<div class="grouphead"><span>%s</span><span>%s</span></div>'
                    % (esc(group), _bar(gs) if gs is not None else '<span class="muted small">no score</span>'))
        rows.append("<table>")
        for cid in cids:
            r = scan.results.get(cid)
            if r is None:
                rows.append("<tr><td class='k'>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                            % (esc(dict((c[0], c[1]) for c in CHECKS)[cid]), _pill("missing"),
                               '<span class="muted">not run</span>', ""))
                continue
            if mode == "customer" and r.status == "skipped":
                continue
            detail = esc(r.summary or "")
            if r.flags and mode == "internal":
                detail += '<div class="small" style="color:#c62828">%s</div>' % esc(
                    "; ".join(f[0] if isinstance(f, (list, tuple)) else str(f) for f in r.flags[:2]))
            techbadge = ('<span class="small muted">guided</span>' if r.tech == "guided"
                         else '<span class="small muted">auto</span>')
            rows.append("<tr><td class='k'>%s %s</td><td>%s</td><td>%s</td><td style='width:110px'>%s</td></tr>"
                        % (esc(r.name), techbadge, _pill(r.status), detail, _bar(r.score)))
        rows.append("</table>")
    return '<div class="card"><h2>Scorecard &mdash; %d checks</h2>%s</div>' % (
        len(CHECKS), "".join(rows))


def _flags(scan: Scan, mode: str) -> str:
    g = scan.grade or {}
    flags = g.get("flags") or []
    if mode == "customer":
        flags = [(t, l) for t, l in flags if l != "fail"][:6]
        if not flags:
            return ""
    if not flags:
        return ('<div class="card"><h2>Problems found</h2><div class="flag ok">No problems were found '
                'during this inspection.</div></div>')
    rows = []
    for text, level in flags:
        cls = "fail" if level == "fail" else ""
        rows.append('<div class="flag %s">%s</div>' % (cls, esc(text)))
    return '<div class="card"><h2>Problems found (%d)</h2>%s</div>' % (len(flags), "".join(rows))


def _details(scan: Scan, mode: str) -> str:
    out = ['<div class="card"><h2>Full test detail</h2>'
           '<div class="small muted" style="margin-bottom:8px">Every number the tool read from the '
           'machine, plus what the technician saw and marked.</div>']
    for cid, name, group, weight, tech in CHECKS:
        r = scan.results.get(cid)
        if r is None:
            continue
        if mode == "customer" and r.status in ("skipped",) and not r.details:
            continue
        photos = "".join(_photo_tag(p, "%s - evidence" % name) for p in r.photos[:4])
        marks_html = ""
        if r.marks:
            items = []
            for m in r.marks:
                if isinstance(m, dict):
                    mk = str(m.get("mark") or "not marked").upper()
                    colour = {"GOOD": "#0f9d58", "OKAY": "#b26a00", "BAD": "#c62828",
                              "SKIP": "#5b6b82", "WEAK": "#b26a00", "NONE": "#c62828",
                              "YES": "#0f9d58", "NO": "#c62828", "NA": "#5b6b82",
                              "AUTO": "#0f9d58", "MANUAL": "#b26a00", "DEAD": "#c62828"}.get(mk, "#5b6b82")
                    note = m.get("note") or ""
                    items.append('<tr><td class="k">%s</td><td><span style="color:%s;font-weight:700">%s</span>%s</td></tr>'
                                 % (esc(m.get("name", "")), colour, esc(mk),
                                    (' <span class="muted small">- %s</span>' % esc(note)) if note else ""))
                else:
                    items.append("<tr><td>%s</td></tr>" % esc(m))
            marks_html = ('<h3 style="margin-top:10px">Technician markings</h3>'
                          '<table>%s</table>' % "".join(items))
        notes_html = ""
        if r.notes:
            notes_html = '<ul class="notes small">%s</ul>' % "".join(
                "<li>%s</li>" % esc(n) for n in r.notes)
        flags_html = ""
        if r.flags:
            flags_html = "".join('<div class="flag %s">%s</div>'
                                 % ("fail" if (f[1] if isinstance(f, (list, tuple)) else "warn") == "fail" else "",
                                    esc(f[0] if isinstance(f, (list, tuple)) else f))
                                 for f in r.flags)
        details_html = ""
        if r.details:
            details_html = "<table>" + "".join(
                "<tr><td class='k'>%s</td><td>%s</td></tr>" % (esc(k), esc(v))
                for k, v in r.details.items() if v not in ("", None)) + "</table>"
        err = ('<div class="small" style="color:#c62828">Read error: %s</div>' % esc(r.error)) if r.error else ""
        out.append("""
<details class="keep">
 <summary><span>%s &nbsp;%s</span><span class="small muted">%s &nbsp;&middot;&nbsp; %.1fs</span></summary>
 <div class="detail">%s%s%s%s%s%s</div>
</details>""" % (esc(name), _pill(r.status),
                 esc(r.summary or ""), r.seconds or 0.0,
                 err, flags_html, details_html, marks_html, notes_html, photos))
    out.append("</div>")
    return "".join(out)


def _notfitted(scan: Scan) -> str:
    items = (scan.grade or {}).get("not_fitted") or []
    if not items:
        return ""
    rows = "".join("<li>%s</li>" % esc(i) for i in items)
    return ('<div class="card"><h2>Components not fitted to this machine</h2>'
            '<div class="small muted" style="margin-bottom:6px">These are not faults. They are simply '
            'absent, so they were excluded from the score and only affect the price where a buyer '
            'would expect them.</div><ul class="notes">%s</ul></div>' % rows)


def _integrity(scan: Scan) -> str:
    rows = []
    kb = scan.results.get("keyboard")
    if kb is not None:
        rows.append("<h3>Keyboard anti-cheat audit</h3><table>")
        for k, v in kb.details.items():
            if any(s in k for s in ("Raw Input", "Low-level", "Device", "Injected", "VIOLATION",
                                    "Rejected", "confirmation", "Required", "Pressed")):
                rows.append("<tr><td class='k'>%s</td><td>%s</td></tr>" % (esc(k), esc(v)))
        rows.append("</table>")
    g = scan.grade or {}
    pending = g.get("pending") or []
    if pending:
        rows.append("<h3>Tests that were never completed</h3><ul class='notes'>%s</ul>"
                    % "".join("<li>%s</li>" % esc(p) for p in pending))
    if not rows:
        return ""
    return ('<div class="card"><h2>Inspection integrity</h2>'
            '<div class="small muted" style="margin-bottom:8px">Recorded so that nobody can pass a '
            'machine without actually testing it.</div>%s</div>' % "".join(rows))


def _valuation(scan: Scan, mode: str) -> str:
    v = scan.valuation or {}
    if not v:
        return ""
    if mode == "customer":
        return ('<div class="card"><h2>Our price</h2><div class="pricebox" style="display:inline-block">'
                '<div class="small muted">Grade %s &mdash; tested on %d points</div>'
                '<div class="big">%s</div><div class="small muted">Negotiable within %s &ndash; %s</div>'
                '</div></div>'
                % (esc(v.get("grade", "?")), len(CHECKS), fmt_inr(v.get("suggested")),
                   fmt_inr(v.get("low")), fmt_inr(v.get("high"))))
    rows = []
    for r in v.get("rows", []):
        kind = r.get("kind")
        amount = r.get("amount", 0)
        if kind == "mult":
            shown = "&times; %.2f" % amount
            cls = ""
        elif kind == "pct":
            shown = "%.0f%%" % (amount * 100)
            cls = "neg" if amount < 0 else "pos"
        else:
            shown = ("+" if amount >= 0 else "") + fmt_inr(amount)
            cls = "neg" if amount < 0 else "pos"
        rows.append("<tr><td>%s%s</td><td class='money %s'>%s</td><td class='small muted'>%s</td></tr>"
                    % (esc(r.get("label")), "", cls, shown, esc(r.get("note", ""))))
    return """
<div class="card">
 <h2>How we arrived at the price</h2>
 <div class="small muted" style="margin-bottom:8px">Valuation model %(mv)s &mdash; seeded from Indian
 used-laptop market prices. Edit <b>pricing.json</b> next to the .exe to match your own street prices;
 nothing has to be rebuilt.</div>
 <table>%(rows)s</table>
 <table style="margin-top:10px">
   <tr><td class="k">Spec value before condition</td><td class="money">%(sub)s</td></tr>
   <tr><td class="k">Condition multiplier (grade %(gr)s &times; cosmetic)</td><td class="money">&times; %(cm)s</td></tr>
   <tr><td class="k"><b>Suggested resale price</b></td><td class="money"><b>%(sugg)s</b></td></tr>
   <tr><td class="k">Realistic selling range</td><td class="money">%(low)s &ndash; %(high)s</td></tr>
   <tr><td class="k">Buy / trade-in price to offer the customer</td><td class="money">%(buy)s</td></tr>
   <tr><td class="k">Your margin at the suggested price</td><td class="money">%(margin)s (%(mpct)s%%)</td></tr>
   <tr><td class="k">Estimated cost of the repairs listed below</td><td class="money">%(rep)s</td></tr>
 </table>
</div>""" % {"mv": esc(v.get("model_version", "?")), "rows": "".join(rows),
             "sub": fmt_inr(v.get("subtotal")), "gr": esc(v.get("grade", "?")),
             "cm": v.get("condition_multiplier", 1), "sugg": fmt_inr(v.get("suggested")),
             "low": fmt_inr(v.get("low")), "high": fmt_inr(v.get("high")),
             "buy": fmt_inr(v.get("buy_price")), "margin": fmt_inr(v.get("margin")),
             "mpct": v.get("margin_pct", 0), "rep": fmt_inr(v.get("repair_estimate"))}


def _recommendations(scan: Scan, mode: str) -> str:
    recs = recommendations(scan)
    if mode == "customer":
        recs = [r for r in recs if r["priority"] in ("NICE",)] or []
        if not recs:
            return ""
    colour = {"SAFETY": "#c62828", "MUST": "#e05a00", "SHOULD": "#b26a00", "NICE": "#0f7b3f"}
    rows = "".join(
        '<tr><td><span class="pill" style="color:%s;background:%s18;border:1px solid %s44">%s</span></td>'
        '<td><b>%s</b><div class="small muted">%s</div></td>'
        '<td class="money">%s</td></tr>'
        % (colour.get(r["priority"], "#5b6b82"), colour.get(r["priority"], "#5b6b82"),
           colour.get(r["priority"], "#5b6b88"), esc(r["priority"]), esc(r["action"]),
           esc(r["why"]), esc(r["cost"]))
        for r in recs)
    return ('<div class="card"><h2>What to do before selling it</h2><table>%s</table></div>' % rows)


def _log(scan: Scan) -> str:
    if not scan.log:
        return ""
    rows = "".join("<tr><td class='k small'>%s</td><td class='small'>%s</td></tr>"
                   % (esc(e.get("t")), esc(e.get("text"))) for e in scan.log[-120:])
    return ('<div class="card noprint"><h2>Live test log</h2>'
            '<div style="max-height:260px;overflow:auto"><table>%s</table></div></div>' % rows)


def _footer(scan: Scan, branding: Any, mode: str) -> str:
    note = branding.get("footer_note") if mode == "internal" else branding.get("customer_report_note")
    warranty = branding.get("warranty_text")
    return """
<div class="card">
  %(warranty)s
  <div class="small muted">%(note)s</div>
  <div class="small muted" style="margin-top:6px">
    Generated by <b>%(app)s v%(ver)s</b> on %(when)s &nbsp;&middot;&nbsp;
    %(checks)d automated checks, %(guided)d technician-verified tests &nbsp;&middot;&nbsp;
    Report ID %(sid)s &nbsp;&middot;&nbsp; All data stayed on this machine.
  </div>
  <div class="small muted" style="margin-top:6px">
    Prices are estimates produced by a rule-based model tuned to the Indian used-laptop market.
    They are a starting point for negotiation, not a guarantee.
  </div>
</div>
<div class="foot">%(shop)s &nbsp;&middot;&nbsp; %(contact)s</div>
""" % {
        "warranty": ('<div class="flag ok" style="margin-bottom:8px"><b>Warranty:</b> %s</div>'
                     % esc(warranty)) if warranty else "",
        "note": esc(note or ""), "app": esc(APP_NAME), "ver": esc(APP_VERSION),
        "when": esc(time.strftime("%Y-%m-%d %H:%M:%S")),
        "checks": sum(1 for r in scan.results.values() if r.tech == "auto"),
        "guided": sum(1 for r in scan.results.values() if r.tech == "guided"),
        "sid": esc(scan.id), "shop": esc(branding.get("shop_name")),
        "contact": esc(" &middot; ".join(x for x in [branding.get("phone"), branding.get("email"),
                                                     branding.get("website")] if x)),
    }


# ==========================================================================
# render
# ==========================================================================

def render(scan: Scan, branding: Any, mode: str = "internal") -> str:
    b = branding.data if hasattr(branding, "data") else branding
    primary = b.get("primary") or "#1b6ef3"
    accent = b.get("accent") or "#12b76a"
    danger = b.get("danger") or "#e5484d"
    warn = b.get("warn") or "#f5a524"
    scan.ctx["branding_warranty"] = b.get("warranty_text", "")
    osinfo = scan.ctx.get("os") or {}
    scan.ctx["w11_label"] = ("Windows 11 installed" if osinfo.get("win11_installed")
                             else ("Windows 11 capable - upgrade recommended"
                                   if osinfo.get("win11_capable") else "Windows 11 not supported"))

    title = "%s - %s %s" % (b.get("shop_name", "Report"),
                            (scan.ctx.get("identity") or {}).get("manufacturer", ""),
                            (scan.ctx.get("identity") or {}).get("model", ""))
    body = [_header(scan, branding, mode), _banners(scan), _verdict(scan, mode), _specs(scan)]
    if mode == "internal":
        body.append(_flags(scan, mode))
        body.append(_valuation(scan, mode))
        body.append(_recommendations(scan, mode))
    else:
        body.append(_recommendations(scan, mode))
    body.append(_scorecard(scan, mode))
    if mode == "internal":
        body.append(_integrity(scan))
    body.append(_notfitted(scan))
    body.append(_details(scan, mode))
    if mode == "internal":
        body.append(_log(scan))
    body.append(_footer(scan, branding, mode))

    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>%(title)s</title>
<style>%(css)s</style>
</head><body><div class="page">
<div class="noprint" style="text-align:right;margin-bottom:8px">
 <button onclick="window.print()" style="background:%(primary)s;color:#fff;border:0;border-radius:8px;
 padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer">Print / Save as PDF</button>
</div>
%(body)s
</div></body></html>""" % {"title": esc(title.strip()), "css": css(primary, accent, danger, warn),
                           "primary": primary, "body": "".join(body)}


def write_reports(scan: Scan, branding: Any, folder: Optional[str] = None) -> Dict[str, str]:
    """Render + save internal and customer HTML, plus the JSON record."""
    folder = folder or os.path.join(PHOTOS_DIR, "..", "reports")
    folder = os.path.abspath(folder)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    internal = os.path.join(folder, "%s_%s_report.html" % (scan.id, stamp))
    customer = os.path.join(folder, "%s_%s_customer.html" % (scan.id, stamp))
    json_path = os.path.join(folder, "%s.json" % scan.id)

    html_int = render(scan, branding, "internal")
    html_cus = render(scan, branding, "customer")
    write_text(internal, html_int)
    write_text(customer, html_cus)
    scan.report_html = internal
    scan.report_customer_html = customer
    scan.finished = scan.finished or time.time()
    write_json(json_path, scan.to_dict())
    scan.append_inventory(json_path)
    return {"internal": internal, "customer": customer, "json": json_path}


def open_in_browser(path: str) -> bool:
    try:
        import webbrowser
        webbrowser.open("file:///" + path.replace("\\", "/").lstrip("/"))
        return True
    except Exception:
        return False
