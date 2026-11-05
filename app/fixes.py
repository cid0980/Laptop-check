"""
fixes.py -- the tool fixes the machine instead of telling the user to.

A shop bench laptop often arrives with the Windows Audio service stopped,
WLAN AutoConfig disabled, the Wi-Fi adapter switched off or the camera/mic
privacy consent set to deny. Telling a technician to "go to Settings and..."
is exactly the wrong answer at a counter. We run as Administrator, so we fix
these ourselves, record what we changed as a flag in the report, and only
talk about Windows settings when a fix genuinely needs a human.

Every function returns a short human sentence ("" when nothing was needed)
and never raises.
"""

from __future__ import annotations

from typing import List, Optional

from app.core import IS_WINDOWS, diag, run_ps, run_shell

AUDIO_SERVICES = ("AudioEndpointBuilder", "Audiosrv")
WLAN_SERVICES = ("WlanSvc",)
BT_SERVICES = ("bthserv",)


def _state(name: str) -> str:
    out = run_ps("(Get-Service -Name '%s' -ErrorAction SilentlyContinue).Status" % name,
                 timeout=20)
    return (out or "").strip() or "Missing"


def _start(name: str) -> bool:
    rc, out, err = run_shell('net start "%s"' % name, timeout=45)
    ok = rc == 0
    diag("fixes: net start %s rc=%s %s" % (name, rc, (out or err or "")[:120]))
    return ok


def ensure_audio() -> str:
    """Start the audio services if stopped - otherwise no speaker test works."""
    if not IS_WINDOWS:
        return ""
    done = []
    for svc in AUDIO_SERVICES:
        st = _state(svc)
        if st.lower() not in ("running",):
            if _start(svc):
                done.append(svc)
            else:
                diag("fixes: could not start %s" % svc)
    if done:
        return ("The Windows Audio service was stopped (no sound is possible while it "
                "is). The tool started %s so the speakers could really be tested."
                % " and ".join(done))
    return ""


def ensure_wlan() -> str:
    if not IS_WINDOWS:
        return ""
    done = []
    for svc in WLAN_SERVICES:
        st = _state(svc)
        if st.lower() in ("stopped", "paused"):
            if _start(svc):
                done.append(svc)
    if done:
        return ("The WLAN AutoConfig service was not running (Wi-Fi cannot work while "
                "it is stopped). The tool started it before testing Wi-Fi.")
    return ""


def enable_wifi_adapter() -> str:
    """If the Wi-Fi interface exists but is disabled, enable it."""
    if not IS_WINDOWS:
        return ""
    rc, out, _err = run_shell("netsh interface show interface", timeout=25)
    if rc != 0 or not out:
        return ""
    for line in out.splitlines():
        low = line.lower()
        if ("wi-fi" in low or "wireless" in low) and "disabled" in low:
            parts = line.split()
            if len(parts) < 4:
                continue
            name = " ".join(parts[3:])
            rc2, out2, err2 = run_shell('netsh interface set interface "%s" enabled' % name,
                                        timeout=25)
            diag("fixes: enable %s rc=%s" % (name, rc2))
            if rc2 == 0:
                return ("The Wi-Fi adapter was switched OFF in Windows. The tool switched "
                        "it on to test it (interface '%s')." % name)
            return ("The Wi-Fi adapter is switched off and Windows refused to switch it "
                    "on automatically - check the Wi-Fi button/fn-key on this laptop.")
    return ""


def allow_privacy(kind: str) -> str:
    """Set the Windows privacy consent for webcam / microphone to Allow.

    Desktop apps honour CapabilityAccessManager ConsentStore values; a shop
    machine that was 'hardened' often has them on Deny, which makes every
    camera and microphone look broken.
    """
    if not IS_WINDOWS:
        return ""
    base = ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
            "CapabilityAccessManager\\ConsentStore\\%s" % kind)
    script = (
        "$changed = 0; "
        "$keys = @('%s'); "
        "$keys += (Get-ChildItem '%s' -Recurse -ErrorAction SilentlyContinue).PSPath; "
        "foreach ($k in $keys) { "
        "  $cur = (Get-ItemProperty -Path $k -Name Value -ErrorAction SilentlyContinue).Value; "
        "  if ($cur -ne $null -and $cur -ne 2) { "
        "    Set-ItemProperty -Path $k -Name Value -Value 2 -ErrorAction SilentlyContinue; "
        "    $changed++ } }; "
        "$changed" % (base, base))
    out = run_ps(script, timeout=30)
    try:
        n = int((out or "0").strip().splitlines()[-1])
    except Exception:
        n = 0
    if n:
        diag("fixes: privacy consent %s changed on %d key(s)" % (kind, n))
        label = "camera" if kind == "webcam" else "microphone"
        return ("Windows privacy settings were blocking the %s. The tool set the consent "
                "to Allow (%d setting(s) changed) so the real hardware could be tested."
                % (label, n))
    return ""


def ensure_bt() -> str:
    if not IS_WINDOWS:
        return ""
    done = []
    for svc in BT_SERVICES:
        st = _state(svc)
        if st.lower() in ("stopped", "paused"):
            if _start(svc):
                done.append(svc)
    if done:
        return ("The Bluetooth Support service was stopped. The tool started it so "
                "pairing can be tested.")
    return ""


def run_all_pre_fixes() -> List[str]:
    """Everything sensible before the automatic checks evaluate hardware."""
    notes: List[str] = []
    for fn in (ensure_audio, ensure_wlan, enable_wifi_adapter, ensure_bt):
        try:
            note = fn()
            if note:
                notes.append(note)
        except Exception as exc:
            diag("fixes: %s failed: %s" % (fn.__name__, exc))
    return notes
