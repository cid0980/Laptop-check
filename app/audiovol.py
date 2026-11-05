"""System audio endpoint control through the Core Audio COM API.

Why: a muted or near-silent Windows master volume makes a perfectly good
speaker look dead. The tool unmutes and sets a sane level itself before any
sound test instead of telling a human to go and find the volume mixer.
Stdlib-only: raw COM vtable calls, the same technique as app/dshow.py.
"""
from __future__ import annotations

import ctypes
from typing import Optional

from app.core import IS_WINDOWS

if IS_WINDOWS:
    from app import dshow
    from app.dshow import GUID, com_call, co_create, make_guid

    _CLSID_ENUM = make_guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    _IID_ENUM = make_guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _IID_VOL = make_guid("{5CDF2C82-841E-4546-9722-0CF74078229A}")

_STATE = {"vol": 0, "note": ""}


def _endpoint_volume() -> Optional[int]:
    if _STATE["vol"]:
        return _STATE["vol"]
    dshow.ole32.CoInitialize(None)
    enum = co_create(_CLSID_ENUM, _IID_ENUM)
    dev = ctypes.c_void_p(0)
    # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender=0, eMultimedia=1)
    hr = com_call(enum, 3, (ctypes.c_int, ctypes.c_int,
                            ctypes.POINTER(ctypes.c_void_p)),
                  0, 1, ctypes.byref(dev))
    if hr < 0 or not dev.value:
        return None
    vol = ctypes.c_void_p(0)
    hr = com_call(dev.value, 3, (ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                 ctypes.POINTER(ctypes.c_void_p)),
                  ctypes.addressof(_IID_VOL), 0x1 | 0x4 | 0x10, None,
                  ctypes.byref(vol))          # CLSCTX_INPROC_SERVER|... standard 0x17
    if hr < 0 or not vol.value:
        return None
    _STATE["vol"] = vol.value
    return vol.value


def get_state() -> dict:
    """{'muted': bool|None, 'level': float|None} for the default output."""
    out = {"muted": None, "level": None}
    if not IS_WINDOWS:
        return out
    try:
        vol = _endpoint_volume()
        if not vol:
            return out
        out["muted"] = bool(com_call(vol, 15, (), restype=ctypes.c_int))
        out["level"] = round(com_call(vol, 9, (), restype=ctypes.c_float), 2)
    except Exception:
        pass
    return out


def ensure_audible(level: float = 0.65) -> str:
    """Unmute the default output and give it a sane level. Human-readable note."""
    if not IS_WINDOWS:
        return ""
    try:
        vol = _endpoint_volume()
        if not vol:
            return ""
        before = get_state()
        changed = []
        if before["muted"]:
            com_call(vol, 14, (ctypes.c_int, ctypes.c_void_p), 0, None)
            changed.append("unmuted the Windows output")
        cur = before["level"]
        if cur is not None and cur < level - 0.05:
            com_call(vol, 7, (ctypes.c_float, ctypes.c_void_p), level, None)
            changed.append("raised the master volume from %d%% to %d%%"
                           % (round(cur * 100), round(level * 100)))
        if changed:
            note = "The tool %s so the speaker test is meaningful." % " and ".join(changed)
            _STATE["note"] = note
            return note
    except Exception:
        pass
    return ""
