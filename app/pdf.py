"""
pdf.py -- turn a report HTML into a PDF with ZERO extra software.

Every Windows 10/11 machine ships Microsoft Edge, and most also have Chrome.
Both contain a headless PDF printer, so we ask the browser the shop already
has to print our self-contained HTML straight to a PDF file:

    msedge.exe --headless --print-to-pdf="out.pdf" "file:///.../report.html"

No installs, no libraries, no "please press Ctrl+P". If no browser can be
found (locked-down kiosk machines exist) the caller falls back to opening the
HTML so the technician can print it manually.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional

from app.core import IS_WINDOWS, diag, run_cmd

_CANDIDATES: List[str] = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
]


def find_browser() -> Optional[str]:
    """Locate a Chromium browser that can print headless PDFs."""
    if not IS_WINDOWS:
        return None
    found = []
    for c in _CANDIDATES:
        if os.path.exists(c):
            found.append(c)
    if not found:
        # ask the registry where Edge/Chrome really live (per-user installs)
        from app.core import run_ps
        out = run_ps(
            "$p = @(); "
            "foreach ($k in 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
            "App Paths\\msedge.exe','HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
            "App Paths\\msedge.exe','HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
            "App Paths\\chrome.exe','HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
            "App Paths\\chrome.exe') { "
            "  $v = (Get-ItemProperty -Path $k -ErrorAction SilentlyContinue).'(default)'; "
            "  if ($v) { $p += $v } }; $p -join '|'", timeout=20)
        for part in (out or "").split("|"):
            part = part.strip().strip('"')
            if part and os.path.exists(part):
                found.append(part)
    return found[0] if found else None


def html_to_pdf(html_path: str, pdf_path: str, timeout: float = 90.0) -> bool:
    """Print `html_path` to `pdf_path`. True on success."""
    browser = find_browser()
    if not browser:
        diag("pdf: no Chromium browser found for headless printing")
        return False
    html_path = os.path.abspath(html_path)
    pdf_path = os.path.abspath(pdf_path)
    url = "file:///" + html_path.replace("\\", "/")
    for headless in ("--headless=new", "--headless"):
        args = [browser, headless, "--disable-gpu", "--disable-extensions",
                "--no-first-run", "--no-pdf-header-footer",
                "--print-to-pdf=%s" % pdf_path, url]
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except Exception:
            pass
        try:
            run_cmd(args, timeout=timeout)
        except Exception as exc:
            diag("pdf: %s failed: %s" % (headless, exc))
            continue
        # wait for the file to appear and finish growing
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                size = os.path.getsize(pdf_path)
                if size > 2000:
                    time.sleep(0.4)
                    if os.path.getsize(pdf_path) == size:
                        diag("pdf: wrote %s (%d bytes) via %s"
                             % (pdf_path, size, os.path.basename(browser)))
                        return True
            except OSError:
                pass
            time.sleep(0.5)
        diag("pdf: %s produced no file for %s" % (headless, pdf_path))
    return False
