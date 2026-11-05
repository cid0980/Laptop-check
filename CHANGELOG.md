# Changelog

All notable changes to **Laptop Check Pro** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The running version lives in `app/core.py` (`APP_VERSION`) and is stamped on
every report; released executables are named after these versions.

## [1.3.0]
### Guided tests now look and behave like one application
- Every guided test is a full-screen borderless screen of the app with the
  shared brand bar ("LAPTOP CHECK PRO - GUIDED TEST n OF 12"), no foreign
  pop-up windows with title bars anymore.
- Display test: the control panel now ALWAYS auto-hides after ~3 s (it used to
  stay parked over the bottom of the screen once the mouse moved, hiding the
  very strip you inspect for dead pixels). Move the mouse to bring it back.
### Camera that works where the Windows Camera app works
- Capture child now tries WinRT MediaCapture first (the exact stack the
  Windows Camera app uses), DirectShow second; both errors are recorded into
  status.json and diagnostics so the next debug bundle pinpoints any blocker.
### The tool turns hardware ON instead of skipping it
- Disabled Bluetooth radios are re-enabled by the tool (Enable-PnpDevice),
  Bluetooth Support service started, before Bluetooth is judged.
- Speaker test sets the playback stream volume to 100% itself first.
### Stronger proofs
- Touchpad: a 12 s measured pass records real pointer travel and left/right
  press counts before the checklist; low travel or missing clicks are flagged.
- Microphone: new SPEAKER->MIC LOOPBACK button plays 1 kHz through the
  speakers while recording; a >6 dB jump proves speakers AND mic in one go.
### New check
- Biometric sensor (fingerprint reader / IR face camera): present + healthy.
  Automatic checks are now 21, total 33.
### Looks genuine
- Exe carries full Windows file properties (product, company, copyright,
  version) via a generated version resource; icon + UAC shield retained.
- Optional CI code-signing: add SIGN_PFX_BASE64 / SIGN_PFX_PASS secrets and
  every release exe is signed (see docs/signing.md for the SmartScreen story).

## [1.2.1]
### Fixed - from the shop PC's own probe file (first real debug iteration)
- Service verdicts: Get-Service returns enum INTEGERS in JSON (4 = Running); the
  checks compared words, so EVERY machine was told "Audio/WLAN service not
  running". Decoded both forms everywhere.
- Wi-Fi: cellular WWAN modules ("Wireless WAN") were counted as Wi-Fi adapters
  and flagged DISABLED; excluded. Wi-Fi now judged from the real 802.11 adapter.
- Checks now re-query services/adapters AFTER the auto-fixes, so a service the
  tool started itself is never reported stopped.
- Bluetooth: a radio with a missing driver (Device Manager Code 28, e.g. Intel
  VID_8087) is reported as "hardware present, driver missing", not "not fitted".
- Debug bundle zip name contained colons -> [Errno 22] on Windows; sanitised.
- Keyboard: when a security product swallows WM_INPUT entirely (keys pressed but
  0 counted), the test falls back to verified hook counting if exactly one
  built-in keyboard exists, and says so on screen and in the report.
- The Windows key / Ctrl+Esc are swallowed during the guided phase so the Start
  menu can never cover a test window again.
- Ethernet "Not Present" (dock-only RJ45) is a note, not a fault.
- No advice line sends anyone to Settings/services.msc/Device Manager anymore.
- Bluetooth Support service is started by the pre-fix pass as well.

## [1.2.0] - 2026-11-06

The "send me one file and I fix your machine" release.

### Added
- **Built-in debugger and support bundle.** Every log line, every exception
  with full traceback, the raw hardware probe exactly as Windows returned it,
  service states, scaling/DPI, security software, camera child-process
  answer - packed into one zip: `LCP_Data\\LCP_debug_<stamp>.zip`.
  * button "Create debug file for support" on the first and last screen
  * written automatically when a test window crashes or the scan fails
  * headless: `LaptopCheckPro.exe --debugbundle` (works even if the GUI dies)
- **The tool now fixes the machine instead of sending the user to Windows
  Settings:** starts stopped Audio / WLAN AutoConfig services, enables a
  disabled Wi-Fi adapter, and sets camera/microphone privacy consent to
  Allow - each change recorded as a note in the report.
- CPU stress test fallback: if security software kills the PowerShell load
  worker, a built-in thread worker runs the load test anyway.

### Fixed
- **Keyboard test counted 0 keys**: Win32 calls had no ctypes prototypes, so
  64-bit handles overflowed 32-bit argument slots
  ("argument 11: int too long to convert") and the Raw Input window died.
  Same class of bug fixed for the microphone (waveIn) prototypes.
- **Touchpad and cosmetic test crashed** with `name 'buttons' is not defined`
  (leftover line from an older fix).
- **Automatic checks flashed past unreadably**: results are now paced - each
  result stays on screen ~1.7 s before the next check appears, and the flow
  waits for the last result before moving on.

## [1.1.2] - 2026-11-06

### Added
- If Windows refuses the hardware probe (security software, stripped-down
  builds), the tool now says so in plain words on the results screen and
  points at `LCP_Data\diagnostics.txt`, instead of silently showing
  NOT TESTED everywhere.

## [1.1.1] - 2026-11-06

### Fixed
- **True full screen on scaled displays.** The exe now declares per-monitor
  DPI awareness *before* the first window is created and takes the screen
  size straight from Windows in physical pixels. On laptops with 125-150%
  display scaling the previous build computed "full screen" in logical
  pixels, so the tool covered only part of the display and the wallpaper
  stayed visible beside it.
- The main window stays on top while full-screen, so other windows (setup
  wizards, chat apps) cannot slide over an inspection in progress.

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
