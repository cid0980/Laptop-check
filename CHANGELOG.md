# Changelog

All notable changes to **Laptop Check Pro** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The running version lives in `app/core.py` (`APP_VERSION`) and is stamped on
every report; released executables are named after these versions.

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
