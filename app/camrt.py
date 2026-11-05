"""Camera capture through Windows Runtime MediaCapture.

This is the SAME capture stack the built-in Windows Camera app uses, so if the
Camera app works on a machine, this works too - unlike DirectShow, which many
modern UVC cameras and privacy layers break.

It runs headless in a child process (powershell.exe) and writes
``frame_000.png, frame_001.png ...`` plus ``status.json`` into the folder the
parent UI polls.  Stdlib-only: the PowerShell script below is the whole
dependency.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Dict

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$folder  = [string]'@FOLDER@'
$seconds = [double]@SECONDS@
$gap     = [int]@GAP@
$index   = [int]@INDEX@

function Wait-Op([object]$op) {
  while ($op.Status -eq [Windows.Foundation.AsyncStatus]::Started) { Start-Sleep -Milliseconds 40 }
  if ($op.Status -ne [Windows.Foundation.AsyncStatus]::Completed) {
    $msg = try { $op.ErrorCode.Message } catch { 'unknown WinRT error' }
    throw ('WinRT async ' + $op.Status + ': ' + $msg)
  }
  return $op.GetResults()
}

[void][Windows.Foundation.AsyncStatus,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Media.Capture.MediaCapture,Windows.Media.Capture,ContentType=WindowsRuntime]
[void][Windows.Media.Capture.MediaCaptureInitializationSettings,Windows.Media.Capture,ContentType=WindowsRuntime]
[void][Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage,ContentType=WindowsRuntime]
[void][Windows.Storage.Streams.DataReader,Windows.Storage,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapEncoder,Windows.Graphics,ContentType=WindowsRuntime]
[void][Windows.Devices.Enumeration.DeviceInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime]

$status = [ordered]@{ ok=$false; frames=0; error=''; device=''; width='?'; height='?'; engine='winrt-mediacapture' }
function Write-Status {
  $status | ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $folder 'status.json') -Encoding UTF8
}

try {
  $set = New-Object Windows.Media.Capture.MediaCaptureInitializationSettings
  $set.StreamingCaptureMode  = [Windows.Media.Capture.StreamingCaptureMode]::Video
  $set.PhotoCaptureSource    = [Windows.Media.Capture.PhotoCaptureSource]::VideoPreview
  if ($index -ge 0) {
    $found = Wait-Op ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync([Windows.Devices.Enumeration.DeviceClass]::VideoCapture))
    if ($found.Count -gt $index) {
      $set.VideoDeviceId = $found[$index].Id
      $status.device = $found[$index].Name
    } elseif ($found.Count -gt 0) {
      $set.VideoDeviceId = $found[0].Id
      $status.device = $found[0].Name
    }
  }
  $mc = New-Object Windows.Media.Capture.MediaCapture
  Wait-Op ($mc.InitializeAsync($set))
  if (-not $status.device) { $status.device = 'default camera' }
  Write-Status

  $i = 0
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $seconds) {
    $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    Wait-Op ($mc.CapturePhotoToStreamAsync($stream, [Windows.Graphics.Imaging.BitmapEncoder]::PngEncoderId))
    $size = [uint32]$stream.Size
    $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
    [void](Wait-Op ($reader.LoadAsync($size)))
    $buf = New-Object byte[] $size
    $reader.ReadBytes($buf)
    $reader.Dispose(); $stream.Dispose()
    $path = Join-Path $folder ('frame_{0:000}.png' -f $i)
    [System.IO.File]::WriteAllBytes($path, $buf)
    if ($i -eq 0 -and $size -gt 24) {
      $status.width  = [int]($buf[16]*16777216 + $buf[17]*65536 + $buf[18]*256 + $buf[19])
      $status.height = [int]($buf[20]*16777216 + $buf[21]*65536 + $buf[22]*256 + $buf[23])
    }
    $i++
    $status.ok = $true; $status.frames = $i
    Write-Status
    Start-Sleep -Milliseconds $gap
  }
  $status.ok = $true; $status.frames = $i
  Write-Status
  exit 0
} catch {
  $status.error = (($_.Exception.Message + ' | ' + $_.ScriptStackTrace) -replace '[\r\n]+', ' ')
  Write-Status
  exit 2
}
"""


def _write_status(folder: str, status: Dict[str, Any]) -> None:
    try:
        with open(os.path.join(folder, "status.json"), "w", encoding="utf-8") as fh:
            json.dump(status, fh)
    except Exception:
        pass


def live_frames(folder: str, seconds: float = 25.0, fps: float = 1.5,
                index: int = -1) -> Dict[str, Any]:
    """Capture PNG frames with WinRT MediaCapture. Returns the status dict."""
    os.makedirs(folder, exist_ok=True)
    status: Dict[str, Any] = {"ok": False, "frames": 0, "error": "",
                              "device": "", "engine": "winrt-mediacapture"}
    if sys.platform != "win32":
        status["error"] = "WinRT capture needs Windows"
        return status
    gap = max(120, int(1000.0 / max(0.4, fps)))
    script = (_PS_SCRIPT.replace("@FOLDER@", folder.replace("'", "''"))
              .replace("@SECONDS@", "%.1f" % seconds)
              .replace("@GAP@", str(gap)).replace("@INDEX@", str(index)))
    ps1 = os.path.join(folder, "camrt.ps1")
    try:
        with open(ps1, "w", encoding="utf-8") as fh:
            fh.write(script)
    except Exception as exc:
        status["error"] = "could not write capture script: %s" % exc
        return status
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", ps1]
    try:
        proc = subprocess.run(cmd, timeout=seconds + 60,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode not in (0, 2) and proc.stderr:
            status["error"] = proc.stderr.decode("utf-8", "replace")[:400]
    except subprocess.TimeoutExpired:
        status["error"] = "capture timed out"
    except Exception as exc:
        status["error"] = "%s: %s" % (type(exc).__name__, exc)
    sp = os.path.join(folder, "status.json")
    if os.path.exists(sp):
        try:
            with open(sp, "r", encoding="utf-8") as fh:
                got = json.load(fh)
            status.update(got)
        except Exception:
            pass
    if not status.get("ok") and not status.get("error"):
        status["error"] = "WinRT capture produced no frames and no error text"
    status["frames"] = len([f for f in os.listdir(folder)
                            if f.startswith("frame_") and f.endswith(".png")])
    status["ok"] = bool(status["frames"])
    _write_status(folder, status)
    return status
