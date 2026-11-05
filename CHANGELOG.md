# Changelog

All notable changes to **Laptop Check Pro** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The running version lives in `app/core.py` (`APP_VERSION`) and is stamped on
every report; released executables are named after these versions.

## [1.1.0] - 2026-11-05

Shop-usable release, rebuilt from real counter feedback.

### Changed
- **New full-screen guided interface.** One flow instead of tabs:
  WELCOME (one START button) -> automatic checks shown one by one in huge
  type with plain-English results (GOOD / NEEDS ATTENTION / PROBLEM /
  NOT FITTED) -> the 12 hands-on tests open themselves in sequence ->
  FINISH screen with the grade and the price in huge type.
- **Reports and PDFs are generated automatically** at the end, using the
  Edge/Chrome already installed on the machine (headless print-to-pdf).
  No more manual export steps; manual export remains as a fallback.
- Display test now uses a borderless window pinned to the exact screen size
  (the old `-fullscreen` flag silently failed on some graphics drivers and
  left the test in a corner), and re-renders on resize / dpi changes.
- Speaker and microphone tests no longer trust Windows' device list: the
  tones are played through the system audio stack (winmm - part of Windows,
  no media player or other app involved) and the microphone is opened
  directly; the human ear/eye is the judge, the device list is only a note.
- PowerShell probing now writes small temporary .ps1 scripts instead of one
  giant `-EncodedCommand` blob (security software on shop PCs routinely
  kills those, which made every check fail at once), splits the probe into
  medium chunks, retries failed chunks key-by-key, and logs everything to
  `LCP_Data/diagnostics.txt` for remote support.

### Added
- `app/pdf.py` headless PDF printing; F2 technician log drawer; F11
  window/full-screen toggle; "See every check" list on the finish screen.

## [1.0.0] - 2026-11-05

First public release. Everything below shipped together as the initial
product for used-laptop resale shops.

### Added
- **Single-file Windows executable** (`LaptopCheckPro.exe`): pure Python
  standard library packaged with PyInstaller (`--onefile`, UAC-admin,
  no console window, custom icon). No installs, no dependencies, no internet.
- **20 automatic hardware checks** via PowerShell/CIM, WMI, `powercfg`,
  `netsh` and direct registry reads: identity & serial, Windows edition +
  activation + firmware OEM licence, BIOS/TPM/Secure Boot/Win11 readiness,
  CPU + generation detection, CPU burn test with live thermals, RAM layout,
  RAM pattern test, storage SMART health/wear/power-on hours, GPU, panel EDID,
  battery health & cycles, charger state, Wi-Fi/Bluetooth/Ethernet, audio
  endpoints, camera device + privacy consent, USB topology, input devices,
  event-log hardware errors.
- **12 guided tests** with full-screen evidence windows: 20-pattern display
  colour/defect/geometry suite with auto-hiding control panel, speakers,
  microphone with live meter, live camera preview with photo capture
  (ctypes DirectShow in an isolated child process), keyboard with
  **anti-cheat device classification** (external/remote/virtual keyboards are
  rejected and recorded), touchpad, USB plug detection, HDMI/jack/card-reader
  plug detection, 14-point cosmetic & safety inspection with photos,
  technician sign-off.
- **Missing hardware handling**: components that are not fitted are reported
  as NOT FITTED and excluded from the score instead of failing the machine.
- **Live process view**: no blank screens - every check shows its status,
  findings, duration and a running log while the scan works.
- **Condition grading A+ … E** with weighted scoring, completeness tracking
  and hard caps for critical faults (failing drive, tampered keyboard test,
  liquid damage, unsigned inspection).
- **Resale valuation in INR** calibrated on Indian used-market listings:
  itemised base/adjustments/multipliers/deductions, suggested price + range,
  buy/trade-in offer, margin, repair estimate. Fully editable in
  `LCP_Data/pricing.json`.
- **Branded dual reports** (internal + customer) as self-contained HTML with
  embedded photos and QR code, printable to PDF; JSON scan record;
  `inventory.csv` stock ledger; scan history with re-open/re-print.
- **Shop branding editor**: name, logo, colours, address, GSTIN, warranty
  text, technicians, QR verification URL - stored once, reused forever.
- Headless modes (`--selftest`, `--demo`, `--report-from`, `--camera-list`)
  and a GitHub Actions pipeline that builds, self-tests and publishes the exe.

### Notes
- Windows 10/11 x64. macOS is planned for v2.0.
- Zero third-party runtime dependencies by design.
