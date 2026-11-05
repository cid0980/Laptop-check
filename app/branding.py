"""
branding.py -- shop branding + application settings, stored next to the .exe.

Both are plain JSON files, so a shop can brand the tool in two minutes and can
even hand-edit the files. On first run the defaults are written out so the
owner can see every knob that exists.
"""

from __future__ import annotations

import base64
import os
import shutil
from typing import Any, Dict, List, Optional

from app.core import DATA_DIR, load_json, write_json

BRANDING_FILE = os.path.join(DATA_DIR, "branding.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
LOGO_FILE = os.path.join(DATA_DIR, "logo.png")

DEFAULT_BRANDING: Dict[str, Any] = {
    "shop_name": "Your Laptop Store",
    "tagline": "Certified pre-owned laptops - every unit tested on 60+ points",
    "address": "",
    "phone": "",
    "email": "",
    "website": "",
    "gst": "",
    "report_title": "Laptop Inspection Report",
    "footer_note": ("This report was generated automatically by Laptop Check Pro. "
                    "It records the condition of this specific machine at the time of testing."),
    "warranty_text": "",
    "technicians": ["Technician 1"],
    "primary": "#1b6ef3",
    "accent": "#12b76a",
    "danger": "#e5484d",
    "warn": "#f5a524",
    "qr_url_template": "",          # e.g. https://yourshop.example/verify/{scan_id}
    "customer_report_note": ("Every laptop we sell is tested point by point with our own "
                             "diagnostic tool and this report is yours to keep."),
}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "stress_seconds": 20,
    "stress_cores": 0,               # 0 = all logical processors
    "skip_stress": False,
    "memtest_mb": 256,
    "memtest_passes": 2,
    "skip_memtest": False,
    "camera_seconds": 45,
    "camera_index": 0,
    "required_guided": [
        "screen_colours", "speakers", "microphone", "keyboard",
        "touchpad", "usb_ports", "cosmetic", "final_check",
    ],
    "quick_mode": False,             # shorter screen sequence + no stress test
    "debug": False,
    "keep_reports_days": 365,
    "default_technician": "",
    "auto_open_report": True,
    "currency": "INR",
}


def _merge(defaults: Dict[str, Any], saved: Any) -> Dict[str, Any]:
    out = dict(defaults)
    if isinstance(saved, dict):
        for k, v in saved.items():
            out[k] = v
    return out


class Branding:
    def __init__(self) -> None:
        self.data: Dict[str, Any] = _merge(DEFAULT_BRANDING, load_json(BRANDING_FILE))

    def get(self, key: str, default: Any = "") -> Any:
        v = self.data.get(key, default)
        return default if v in (None, "") else v

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save(self) -> bool:
        return write_json(BRANDING_FILE, self.data)

    def reload(self) -> None:
        self.data = _merge(DEFAULT_BRANDING, load_json(BRANDING_FILE))

    # ---- logo ---------------------------------------------------------
    def set_logo_from_file(self, path: str) -> Optional[str]:
        if not path or not os.path.exists(path):
            return None
        try:
            shutil.copyfile(path, LOGO_FILE)
            return LOGO_FILE
        except Exception:
            return None

    def clear_logo(self) -> None:
        try:
            if os.path.exists(LOGO_FILE):
                os.remove(LOGO_FILE)
        except Exception:
            pass

    def logo_data_uri(self, max_bytes: int = 400_000) -> str:
        try:
            if not os.path.exists(LOGO_FILE):
                return ""
            if os.path.getsize(LOGO_FILE) > max_bytes:
                return ""
            ext = os.path.splitext(LOGO_FILE)[1].lower().lstrip(".") or "png"
            mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif",
                    "bmp": "bmp", "webp": "webp"}.get(ext, "png")
            with open(LOGO_FILE, "rb") as fh:
                return "data:image/%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode())
        except Exception:
            return ""

    def qr_target(self, scan_id: str) -> str:
        tpl = (self.data.get("qr_url_template") or "").strip()
        if not tpl:
            return ""
        try:
            return tpl.format(scan_id=scan_id, id=scan_id)
        except Exception:
            return tpl.replace("{scan_id}", scan_id)


class Settings:
    def __init__(self) -> None:
        self.data: Dict[str, Any] = _merge(DEFAULT_SETTINGS, load_json(SETTINGS_FILE))

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default if default is not None else DEFAULT_SETTINGS.get(key))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save(self) -> bool:
        return write_json(SETTINGS_FILE, self.data)

    def reload(self) -> None:
        self.data = _merge(DEFAULT_SETTINGS, load_json(SETTINGS_FILE))

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.data)
