"""
system.py -- ONE big hardware probe.

Instead of launching PowerShell 30 times (slow, flickery), we build a single
script that collects everything and returns one JSON blob. Each snippet is
individually wrapped in try/catch, so a machine that lacks a class (no
Bluetooth, no TPM, Windows Home without BitLocker...) returns an empty list
for that key instead of breaking the whole scan.

THIS IS HOW "NO LOOPHOLES / NO BLANK SCREENS" IS ACHIEVED:
  * key present but empty  -> component is NOT FITTED  (status "na")
  * key present with data  -> normal evaluation
  * whole probe failed     -> every check reports "skipped" with a reason
"""

from __future__ import annotations

import base64
import re
import struct
from typing import Any, Dict, List, Optional

from app.core import IS_WINDOWS, batch_ps, cim, get, parse_json_loose, run_ps, run_shell, strip_nulls

# --------------------------------------------------------------------------
# The probe
# --------------------------------------------------------------------------

PS = {
    # ---- identity -------------------------------------------------------
    "cs": "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,SystemFamily,SystemSKUNumber,TotalPhysicalMemory,NumberOfLogicalProcessors,PCSystemType,PCSystemTypeEx,UserName,Domain",
    "csp": "Get-CimInstance Win32_ComputerSystemProduct | Select-Object Name,Vendor,Version,IdentifyingNumber,UUID",
    "bios": "Get-CimInstance Win32_BIOS | Select-Object Manufacturer,SMBIOSBIOSVersion,SMBIOSMajorVersion,SMBIOSMinorVersion,ReleaseDate,SerialNumber,Name,Version",
    "board": "Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product,SerialNumber,Version",
    "enclosure": "Get-CimInstance Win32_SystemEnclosure | Select-Object ChassisTypes,SerialNumber,Manufacturer,Version",
    # ---- OS + licence ---------------------------------------------------
    "os": "Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture,InstallDate,LastBootUpTime,TotalVisibleMemorySize,FreePhysicalMemory,ProductType,RegisteredUser,Organization,Locale",
    "license": "Get-CimInstance SoftwareLicensingProduct -Filter \"ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL\" | Select-Object Name,LicenseStatus,Description,PartialProductKey,ApplicationID",
    "slmgr": "$s = Get-CimInstance SoftwareLicensingService; $s | Select-Object OA3xOriginalProductKey,OA3xOriginalProductKeyReserved,ClientMachineID,RemainingWindowsReArmCount",
    # ---- CPU ------------------------------------------------------------
    "cpu": "Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,CurrentClockSpeed,L2CacheSize,L3CacheSize,Manufacturer,ProcessorId,VirtualizationFirmwareEnabled,SecondLevelAddressTranslationExtensions,LoadPercentage,Description,Family",
    "thermal": "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue | Select-Object InstanceName,CurrentTemperature",
    # ---- memory ---------------------------------------------------------
    "ram": "Get-CimInstance Win32_PhysicalMemory | Select-Object BankLabel,DeviceLocator,Capacity,Speed,ConfiguredClockSpeed,ConfiguredVoltage,Manufacturer,PartNumber,SerialNumber,SMBIOSMemoryType,FormFactor,DataWidth,TotalWidth",
    "ramarray": "Get-CimInstance Win32_PhysicalMemoryArray | Select-Object MemoryDevices,MaxCapacity,MaxCapacityEx,Use",
    # ---- storage --------------------------------------------------------
    "disks": "Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,MediaType,BusType,HealthStatus,OperationalStatus,Size,SpindleSpeed,SerialNumber,Model,AdapterRate,IsBoot,IsSystem,DeviceType,UniqueId",
    "reliability": "Get-PhysicalDisk | ForEach-Object { $c = $_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; if ($c) { [pscustomobject]@{ DeviceId=$_.DeviceId; FriendlyName=$_.FriendlyName; Wear=$c.Wear; Temperature=$c.Temperature; TemperatureMax=$c.TemperatureMax; PowerOnHours=$c.PowerOnHours; StartStopCycleCount=$c.StartStopCycleCount; ReadErrorsTotal=$c.ReadErrorsTotal; ReadErrorsUncorrected=$c.ReadErrorsUncorrected; WriteErrorsTotal=$c.WriteErrorsTotal; WriteErrorsUncorrected=$c.WriteErrorsUncorrected; ManufactureDate=$c.ManufactureDate } } }",
    "volumes": "Get-Volume | Select-Object DriveLetter,FileSystemLabel,FileSystemType,Size,SizeRemaining,HealthStatus,OperationalStatus,DriveType",
    "partition": "Get-Partition | Select-Object DiskNumber,PartitionNumber,DriveLetter,Size,Type,IsBoot,IsSystem",
    "trim": "fsutil behavior query DisableDeleteNotify",
    # ---- graphics + display ---------------------------------------------
    "gpu": "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,DriverDate,VideoProcessor,CurrentHorizontalResolution,CurrentVerticalResolution,CurrentRefreshRate,CurrentBitsPerPixel,VideoModeDescription,Status,PNPDeviceID,AdapterCompatibility,MinRefreshRate,MaxRefreshRate,InfFilename",
    "desktopmonitor": "Get-CimInstance Win32_DesktopMonitor | Select-Object Name,ScreenWidth,ScreenHeight,Availability,Status,MonitorManufacturer,MonitorType,PNPDeviceID,ConfigManagerErrorCode",
    "monitorid": "Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorID -ErrorAction SilentlyContinue | Select-Object InstanceName,ManufacturerName,ProductCodeID,SerialNumberID,UserFriendlyName,YearOfManufacture,WeekOfManufacture",
    "monitorbasic": "Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBasicParams -ErrorAction SilentlyContinue | Select-Object InstanceName,VideoInputType,MaxHorizontalImageSize,MaxVerticalImageSize",
    "monitorconn": "Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorConnectionParams -ErrorAction SilentlyContinue | Select-Object InstanceName,VideoOutputTechnology",
    "brightness": "Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness -ErrorAction SilentlyContinue | Select-Object InstanceName,CurrentBrightness,Levels",
    "edid": "Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Enum\\DISPLAY' -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Property -contains 'EDID' } | ForEach-Object { $e = (Get-ItemProperty -Path $_.PSPath -Name EDID -ErrorAction SilentlyContinue).EDID; if ($e) { [pscustomobject]@{ Key=$_.PSChildName; Path=$_.Name; EDID=[Convert]::ToBase64String([byte[]]$e) } } }",
    "videores": "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Screen]::AllScreens | ForEach-Object { [pscustomobject]@{ Device=$_.DeviceName; Primary=$_.Primary; W=$_.Bounds.Width; H=$_.Bounds.Height; BitsPerPixel=$_.BitsPerPixel } }",
    # ---- battery --------------------------------------------------------
    "battery": "Get-CimInstance Win32_Battery | Select-Object BatteryStatus,EstimatedChargeRemaining,DesignVoltage,Name,DeviceID,Chemistry,ExpectedLife,FullChargeCapacity,SmartBatteryVersion,Status,ConfigManagerErrorCode",
    "batterystatus": "Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue | Select-Object InstanceName,Charging,Discharging,PowerOnline,RemainingCapacity,Voltage,ChargeRate,DischargeRate,EstimatedTime",
    "batteryfull": "Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity -ErrorAction SilentlyContinue | Select-Object InstanceName,FullChargedCapacity",
    "batterystatic": "Get-CimInstance -Namespace root/wmi -ClassName BatteryStaticData -ErrorAction SilentlyContinue | Select-Object InstanceName,DesignedCapacity,Chemistry,DeviceName,ManufactureDate,SerialNumber,UniqueID",
    "batterycycle": "Get-CimInstance -Namespace root/wmi -ClassName BatteryCycleCount -ErrorAction SilentlyContinue | Select-Object InstanceName,CycleCount",
    # ---- wireless / network ---------------------------------------------
    "netadapter": "Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Select-Object Name,InterfaceDescription,Status,LinkSpeed,MacAddress,PhysicalMediaType,FullDuplex,InterfaceIndex,DriverInformation,MtuSize,AdminStatus,Virtual",
    "netadaptercfg": "Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=TRUE' | Select-Object Description,MACAddress,DHCPEnabled,IPAddress,DefaultIPGateway,DNSDomain",
    "wlanif": "netsh wlan show interfaces",
    "wlandrv": "netsh wlan show drivers",
    "bluetooth": "Get-PnpDevice -Class Bluetooth -PresentOnly -ErrorAction SilentlyContinue | Select-Object FriendlyName,Status,InstanceId,Manufacturer,Problem,Class",
    "btservice": "Get-Service bthserv -ErrorAction SilentlyContinue | Select-Object Name,Status,StartType,DisplayName",
    "btcount": "(Get-PnpDevice -Class Bluetooth -PresentOnly -ErrorAction SilentlyContinue | Measure-Object).Count",
    # ---- audio / camera / input ------------------------------------------
    "sounddev": "Get-CimInstance Win32_SoundDevice | Select-Object Name,Manufacturer,Status,StatusInfo,ConfigManagerErrorCode,PNPDeviceID,DeviceID",
    "audioendpoint": "Get-PnpDevice -Class AudioEndpoint -PresentOnly -ErrorAction SilentlyContinue | Select-Object FriendlyName,Status,InstanceId",
    "audiodev": "Get-PnpDevice -Class Media -PresentOnly -ErrorAction SilentlyContinue | Select-Object FriendlyName,Status,InstanceId,Manufacturer",
    "camera": "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.Class -in @('Camera','Image') -or $_.FriendlyName -match 'camera|webcam|cam |AVerMedia|Integrated Camera|IR Camera' } | Select-Object FriendlyName,Class,Status,InstanceId,Manufacturer,Problem",
    "keyboard": "Get-PnpDevice -Class Keyboard -PresentOnly -ErrorAction SilentlyContinue | Select-Object FriendlyName,Status,InstanceId,Manufacturer,Problem",
    # Keyboard detail + PARENT device: this is what makes the anti-cheat
    # keyboard test possible (we must tell a built-in keyboard from a USB one).
    "kbdetail": "Get-PnpDevice -Class Keyboard -PresentOnly -ErrorAction SilentlyContinue | ForEach-Object { $p = ($_ | Get-PnpDeviceProperty -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data; $b = ($_ | Get-PnpDeviceProperty -KeyName 'DEVPKEY_Device_BusReportedDeviceDesc' -ErrorAction SilentlyContinue).Data; $h = ($_ | Get-PnpDeviceProperty -KeyName 'DEVPKEY_Device_Children' -ErrorAction SilentlyContinue).Data; [pscustomobject]@{ FriendlyName=$_.FriendlyName; InstanceId=$_.InstanceId; Status=$_.Status; Problem=$_.Problem; Parent=$p; BusDesc=$b; Children=($h -join ';') } }",
    "rawkbd": "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -match 'PNP0303|PNP030|VEN_.*&DEV_' -and $_.Class -in @('Keyboard','HIDClass','System') } | Select-Object FriendlyName,Class,InstanceId | Select-Object -First 25",
    "pnp_touch": "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match 'Touch' } | Select-Object FriendlyName,Class,Status,InstanceId | Select-Object -First 12",
    "camconsent": "@( (Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\webcam' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Value -ErrorAction SilentlyContinue), (Get-ItemProperty 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\webcam' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Value -ErrorAction SilentlyContinue) ) | Where-Object { $_ } | ForEach-Object { [pscustomobject]@{ Value = [string]$_ } }",
    "micconsent": "@( (Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Value -ErrorAction SilentlyContinue), (Get-ItemProperty 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Value -ErrorAction SilentlyContinue) ) | Where-Object { $_ } | ForEach-Object { [pscustomobject]@{ Value = [string]$_ } }",
    "mouse": "Get-PnpDevice -Class Mouse -PresentOnly -ErrorAction SilentlyContinue | Select-Object FriendlyName,Status,InstanceId,Manufacturer,Problem",
    "hid": "Get-PnpDevice -Class HIDClass -PresentOnly -ErrorAction SilentlyContinue | Select-Object FriendlyName,Status,InstanceId | Select-Object -First 60",
    "usbctl": "Get-CimInstance Win32_USBController | Select-Object Name,Status,ProtocolSupported,Manufacturer,DeviceID",
    "usbhub": "Get-PnpDevice -Class USB -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match 'Hub|host|Root|xHCI|eXtensible' } | Select-Object FriendlyName,Status,InstanceId | Select-Object -First 40",
    "ports": "Get-PnpDevice -Class Ports -PresentOnly -ErrorAction SilentlyContinue | Select-Object FriendlyName,Status,InstanceId",
    "cdrom": "Get-CimInstance Win32_CDROMDrive | Select-Object Name,Drive,Status,MediaLoaded,CapabilityDescriptions",
    "sdcard": "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match 'Card Reader|SD Host|Memory Stick|MMC|Realtek.*Reader' } | Select-Object FriendlyName,Class,Status,InstanceId",
    "fingerprint": "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match 'Fingerprint|Biometric' -or $_.Class -eq 'Biometric' } | Select-Object FriendlyName,Class,Status,InstanceId",
    # ---- problem devices --------------------------------------------------
    "problems": "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.Status -ne 'OK' } | Select-Object FriendlyName,Class,Status,Problem,InstanceId | Select-Object -First 40",
    "pnpall": "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Select-Object FriendlyName,Class,Status | Group-Object Class | ForEach-Object { [pscustomobject]@{ Class=$_.Name; Count=$_.Count } }",
    # ---- security ----------------------------------------------------------
    "tpm": "Get-CimInstance -Namespace root/cimv2/security/microsofttpm -ClassName Win32_Tpm -ErrorAction SilentlyContinue | Select-Object SpecVersion,IsEnabled_InitialValue,IsActivated_InitialValue,IsOwned_InitialValue,ManufacturerVersion,PhysicalPresenceVersionInfo",
    "secureboot": "try { [pscustomobject]@{ SecureBoot = (Confirm-SecureBootUEFI) } } catch { [pscustomobject]@{ SecureBoot = 'UnsupportedOrLegacy' } }",
    "bitlocker": "Get-BitLockerVolume -ErrorAction SilentlyContinue | Select-Object MountPoint,VolumeStatus,ProtectionStatus,EncryptionMethod,LockStatus",
    "uefi": "$env:firmware_type; bcdedit /enum '{current}' | Select-String -Pattern 'path' -SimpleMatch",
    # ---- event log (hardware errors) ---------------------------------------
    "whea": "Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=(Get-Date).AddDays(-45)} -MaxEvents 15 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id,LevelDisplayName,@{n='Msg';e={ ($_.Message -split \"`n\")[0].Substring(0,[Math]::Min(220,($_.Message -split \"`n\")[0].Length)) }}",
    "diskevents": "Get-WinEvent -FilterHashtable @{LogName='System'; Id=7,9,11,15,51,52,98,129,153; StartTime=(Get-Date).AddDays(-45)} -MaxEvents 25 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id,ProviderName,LevelDisplayName",
    "crashevents": "Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,6008,1001; StartTime=(Get-Date).AddDays(-45)} -MaxEvents 15 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id,ProviderName,LevelDisplayName",
    "blueevents": "Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=41; StartTime=(Get-Date).AddDays(-90)} -MaxEvents 10 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id",
    "winlog": "Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Windows Error Reporting'; StartTime=(Get-Date).AddDays(-30)} -MaxEvents 10 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id",
    # ---- misc ---------------------------------------------------------------
    "services": "Get-Service -Name Audiosrv,AudioEndpointBuilder,BTHPORT,bthserv,WlanSvc,Netman,CamSvc,SysMain -ErrorAction SilentlyContinue | Select-Object Name,Status,StartType,DisplayName",
    "uptime": "[pscustomobject]@{ UpSeconds = [int]((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalSeconds }",
    "memdiag": "Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-MemoryDiagnostics-Results'} -MaxEvents 5 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id,LevelDisplayName,@{n='Msg';e={ ($_.Message -split \"`n\")[0] }}",
    "memdiagsched": "Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-MemoryDiagnostics-Schedule'} -MaxEvents 3 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id",
    "win11cpu": "$c = (Get-CimInstance Win32_Processor).Name; $t = (Get-CimInstance -Namespace root/cimv2/security/microsofttpm -ClassName Win32_Tpm -ErrorAction SilentlyContinue).SpecVersion; [pscustomobject]@{ Cpu=$c; Tpm=$t }",
}


def probe(progress: Optional[Any] = None, timeout: float = 240.0) -> Dict[str, Any]:
    """Run the whole probe. Returns {'raw': {...}, 'ok': bool, 'error': str}."""
    if not IS_WINDOWS:
        return {"raw": {}, "ok": False, "error": "Not running on Windows (demo mode)"}
    if progress:
        progress("Collecting hardware data with one system probe (this takes 10-40 seconds)...")
    raw = batch_ps(PS, timeout=timeout)
    if not raw:
        # Fall back to a few direct CIM calls so the tool still says something.
        if progress:
            progress("Probe returned nothing - retrying with smaller queries...")
        raw = {}
        for key in ("cs", "bios", "os", "cpu", "ram", "disks", "gpu", "battery", "netadapter"):
            cls = {
                "cs": "Win32_ComputerSystem", "bios": "Win32_BIOS",
                "os": "Win32_OperatingSystem", "cpu": "Win32_Processor",
                "ram": "Win32_PhysicalMemory", "disks": "Win32_DiskDrive",
                "gpu": "Win32_VideoController", "battery": "Win32_Battery",
                "netadapter": "Win32_NetworkAdapter",
            }[key]
            raw[key] = cim(cls)
        return {"raw": raw, "ok": bool(raw.get("cs")), "error": "" if raw.get("cs") else "WMI probe failed"}
    return {"raw": raw, "ok": True, "error": ""}


# --------------------------------------------------------------------------
# netsh text parsing (Wi-Fi)
# --------------------------------------------------------------------------

def parse_wlan_interfaces(text: str) -> Dict[str, Any]:
    """Parse `netsh wlan show interfaces` (English + a few localisations)."""
    out: Dict[str, Any] = {"present": False}
    if not text:
        return out
    if re.search(r"not running|auto ?configuration|AutoConfig|is not running", text, re.I):
        # "The Wireless AutoConfiguration Service (wlansvc) is not running"
        if re.search(r"wlansvc\)?\s+is not running|service.*not running", text, re.I):
            out["service_stopped"] = True
    lines = [l.strip() for l in text.splitlines() if ":" in l]
    kv = {}
    for line in lines:
        k, _, v = line.partition(":")
        kv[k.strip().lower()] = v.strip()
    def pick(*keys: str) -> str:
        for k in keys:
            for kk, vv in kv.items():
                if k in kk:
                    return vv
        return ""
    name = pick("name", "profile")
    if name or pick("state", "status"):
        out["present"] = True
    out["interface"] = pick("name")
    out["description"] = pick("description")
    out["guid"] = pick("guid")
    out["state"] = pick("state", "status")
    out["ssid"] = pick("ssid") if "bssid" not in pick("ssid").lower() else ""
    out["bssid"] = pick("bssid")
    out["network_type"] = pick("network type")
    out["radio_type"] = pick("radio type")
    out["auth"] = pick("authentication")
    out["cipher"] = pick("cipher")
    out["channel"] = pick("channel")
    out["rx_mbps"] = pick("receive rate (mbps)", "receive rate")
    out["tx_mbps"] = pick("transmit rate (mbps)", "transmit rate")
    sig = pick("signal")
    m = re.search(r"(\d+)", sig)
    out["signal_percent"] = int(m.group(1)) if m else None
    return out


def parse_wlan_drivers(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not text:
        return out
    kv = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.strip().partition(":")
            kv[k.strip().lower()] = v.strip()
    def pick(*keys: str) -> str:
        for k in keys:
            for kk, vv in kv.items():
                if k in kk:
                    return vv
        return ""
    out["driver"] = pick("driver name")
    out["vendor"] = pick("driver provider")
    out["driver_date"] = pick("driver date")
    out["driver_version"] = pick("driver version")
    radios = pick("radio types supported", "radio type")
    out["radio_types"] = [r.strip() for r in re.split(r"[,\s]+", radios) if r.strip()]
    hosted = pick("hosted network supported")
    out["hosted_network"] = hosted
    out["has_wifi_driver"] = bool(out["driver"])
    return out


# --------------------------------------------------------------------------
# EDID parsing (panel identity, size, manufacture date, serial)
# --------------------------------------------------------------------------

EDID_MANUFACTURERS = {
    "AUO": "AU Optronics", "BOE": "BOE", "CMN": "Chimei Innolux", "CMI": "Chimei Innolux",
    "IVO": "InfoVision", "LG ": "LG Display", "LGD": "LG Display", "SDC": "Samsung Display",
    "SEC": "Samsung", "SHP": "Sharp", "DEL": "Dell", "LEN": "Lenovo", "HPN": "HP",
    "ACR": "Acer", "ASU": "ASUS", "MSI": "MSI", "SAM": "Samsung", "PHL": "Philips",
    "VSC": "ViewSonic", "GSM": "LG (Goldstar)", "NEC": "NEC", "PNR": "Planar",
    "APP": "Apple", "SII": "Silicon Image", "CRT": "Generic",
}


def parse_edid(data: bytes) -> Dict[str, Any]:
    """Parse a 128-byte (or 256-byte EDID 1.3/1.4) block."""
    info: Dict[str, Any] = {}
    if not data or len(data) < 128:
        return info
    if data[:8] != bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00]):
        return info
    try:
        mfg_id = struct.unpack(">H", data[8:10])[0]
        c1 = chr(((mfg_id >> 10) & 0x1F) + 64)
        c2 = chr(((mfg_id >> 5) & 0x1F) + 64)
        c3 = chr((mfg_id & 0x1F) + 64)
        code = c1 + c2 + c3
        info["manufacturer_code"] = code
        info["manufacturer"] = EDID_MANUFACTURERS.get(code, EDID_MANUFACTURERS.get(code.strip(), code))
        info["product_code"] = hex(struct.unpack("<H", data[10:12])[0])
        serial_num = struct.unpack("<I", data[12:16])[0]
        info["week"] = data[16]
        info["year"] = 1990 + data[17]
        info["edid_version"] = "%d.%d" % (data[18], data[19])
        # basic display parameters
        video_input = data[20]
        info["digital"] = bool(video_input & 0x80)
        max_h, max_v = data[21], data[22]
        if max_h and max_v:
            diag_cm = (max_h ** 2 + max_v ** 2) ** 0.5
            info["diagonal_cm"] = round(diag_cm, 1)
            info["diagonal_inch"] = round(diag_cm / 2.54, 1)
        # descriptor blocks: monitor name + serial string
        for off in range(54, 126, 18):
            block = data[off : off + 18]
            if block[0] == 0 and block[1] == 0:
                tag = block[3]
                txt = block[5:18].decode("ascii", "replace").strip().rstrip("\n")
                if tag == 0xFC:
                    info["monitor_name"] = txt
                elif tag == 0xFF:
                    info["serial_string"] = txt
                elif tag == 0xFD:
                    if len(block) >= 10:
                        info["min_v_rate"] = block[5]
                        info["max_v_rate"] = block[6]
                        info["min_h_rate_khz"] = block[7] * 10
                        info["max_h_rate_khz"] = block[8] * 10
                        info["max_pixel_clock_mhz"] = block[9] * 10
                elif tag == 0xFB:
                    info["has_detailed_color"] = True
            else:
                # detailed timing descriptor -> native resolution
                try:
                    pixel_clock = struct.unpack("<H", block[0:2])[0] * 10  # kHz
                    h_active = ((block[4] & 0xF0) << 4) | block[2]
                    v_active = ((block[7] & 0xF0) << 4) | block[5]
                    if pixel_clock and h_active and v_active:
                        info["native_width"] = h_active
                        info["native_height"] = v_active
                        info["native_pixel_clock_khz"] = pixel_clock
                        refresh = round(pixel_clock * 1000.0 / (h_active * v_active), 1) if h_active * v_active else None
                        info["native_refresh_hz"] = refresh
                except Exception:
                    pass
        if serial_num and serial_num != 0xFFFFFFFF:
            info["serial_number"] = str(serial_num)
        # EDID 1.4 basic display: screen size may be in cm at bytes 21/22 (already)
        info["extensions"] = data[126]
        # checksum
        info["checksum_ok"] = (sum(data[:128]) % 256) == 0
    except Exception as exc:
        info["parse_error"] = str(exc)
    return info


def decode_edid_list(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in rows or []:
        b64 = get(row, "EDID") or get(row, "edid")
        if not b64:
            continue
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue
        info = parse_edid(raw)
        if info:
            info["registry_key"] = get(row, "Key") or get(row, "Path")
            info["edid_bytes"] = len(raw)
            out.append(info)
    return out


# --------------------------------------------------------------------------
# CPU generation detection (matters a LOT for resale value + Windows 11)
# --------------------------------------------------------------------------

def cpu_generation(cpu_name: str) -> Dict[str, Any]:
    """Return {'brand','family','gen','gen_label','win11_ok','tier'}."""
    res = {"brand": "Unknown", "family": "", "gen": 0, "gen_label": "?",
           "win11_ok": False, "tier": "unknown", "raw": cpu_name or ""}
    if not cpu_name:
        return res
    n = cpu_name.lower()

    tier = "unknown"
    if "core ultra 9" in n or "i9" in n or "ryzen 9" in n:
        tier = "enthusiast"
    elif "core ultra 7" in n or "i7" in n or "ryzen 7" in n:
        tier = "high"
    elif "core ultra 5" in n or "i5" in n or "ryzen 5" in n:
        tier = "mid"
    elif "i3" in n or "ryzen 3" in n:
        tier = "entry"
    elif "pentium" in n or "celeron" in n or "athlon" in n:
        tier = "budget"
    res["tier"] = tier

    # Intel Core Ultra (Meteor Lake / Lunar Lake)  e.g. "Core Ultra 7 155U"
    m = re.search(r"core ultra ([3579])\s+(\d{1})(\d{2})", n)
    if m:
        res.update(brand="Intel", family="Core Ultra", gen=int(m.group(2)) + 12,
                   gen_label="Core Ultra series %s" % m.group(2), win11_ok=True)
        return res
    # Intel Core i-series: i5-8250U / i7-10750H / i5-1235U
    m = re.search(r"i([3579])[- ](\d{1,2})(\d{2,3})", n)
    if m:
        gen = int(m.group(2))
        res.update(brand="Intel", family="Core i%s" % m.group(1), gen=gen,
                   gen_label="%dth Gen" % gen, win11_ok=gen >= 8)
        return res
    # Older Intel: "CPU 2.40GHz" without i-series, or Core2
    if "core2" in n or "core 2" in n:
        res.update(brand="Intel", family="Core 2", gen=2, gen_label="Core 2 era", win11_ok=False)
        return res
    # AMD Ryzen: Ryzen 5 4500U / Ryzen 7 5800H / Ryzen 5 PRO 6650U
    m = re.search(r"ryzen ([3579])\s+(?:pro\s+)?(\d{1})(\d{3})", n)
    if m:
        gen = int(m.group(2))
        res.update(brand="AMD", family="Ryzen %s" % m.group(1), gen=gen,
                   gen_label="Ryzen %s000 series" % gen, win11_ok=gen >= 2)
        return res
    if "ryzen" in n:
        res.update(brand="AMD", family="Ryzen", gen=1, gen_label="Ryzen (early)", win11_ok=False)
        return res
    if "athlon" in n:
        res.update(brand="AMD", family="Athlon", gen=0, gen_label="Athlon", win11_ok=False)
        return res
    if "intel" in n:
        res.update(brand="Intel", family="Intel (other)", gen=0,
                   gen_label="Legacy Intel", win11_ok=False)
        return res
    if "amd" in n:
        res.update(brand="AMD", family="AMD (other)", gen=0, gen_label="Legacy AMD", win11_ok=False)
    return res


RAM_TYPES = {
    0: "Unknown", 1: "Other", 2: "DRAM", 3: "Synchronous DRAM", 4: "Cache DRAM",
    5: "EDO", 6: "EDRAM", 7: "VRAM", 8: "SRAM", 9: "RAM", 10: "ROM", 11: "Flash",
    12: "EEPROM", 13: "FEPROM", 14: "EPROM", 15: "CDRAM", 16: "3DRAM",
    17: "SDRAM", 18: "SGRAM", 19: "RDRAM", 20: "DDR", 21: "DDR2", 22: "DDR2 FB-DIMM",
    24: "DDR3", 26: "DDR4", 34: "DDR5",
}


def ram_type_label(code: Any) -> str:
    try:
        return RAM_TYPES.get(int(code), "Type %s" % code)
    except Exception:
        return "Unknown"


CHASSIS_TYPES = {
    1: "Other", 2: "Unknown", 3: "Desktop", 4: "Low Profile Desktop",
    5: "Pizza Box", 6: "Mini Tower", 7: "Tower", 8: "Portable", 9: "Laptop",
    10: "Notebook", 11: "Hand Held", 12: "Docking Station", 13: "All in One",
    14: "Sub Notebook", 15: "Space Saving", 16: "Lunch Box", 17: "Main System Chassis",
    18: "Expansion Chassis", 19: "Sub Chassis", 20: "Bus Expansion Chassis",
    21: "Peripheral Chassis", 22: "RAID Chassis", 23: "Rack Mount Chassis",
    24: "Sealed-Case PC", 30: "Tablet", 31: "Convertible", 32: "Detachable",
}


def chassis_label(types: Any) -> str:
    if not types:
        return "Unknown"
    if isinstance(types, (list, tuple)):
        labels = [CHASSIS_TYPES.get(int(t), "Type %s" % t) for t in types]
        return " / ".join(labels)
    return CHASSIS_TYPES.get(int(types), "Type %s" % types)


VIDEO_OUTPUT_TECH = {
    0: "VGA", 2: "S-Video", 3: "Composite", 4: "Component", 5: "DVI",
    6: "HDMI", 7: "LVDS (internal panel)", 8: "D-Jpn", 9: "SDI",
    10: "DisplayPort external", 11: "DisplayPort embedded",
    12: "UDI external", 13: "UDI embedded", 14: "SDTVDONGLE",
    15: "Miracast", 0x80000000: "Internal display", -1: "Internal display",
    0xFFFFFFFF: "Internal display",
}


def video_output_label(code: Any) -> str:
    try:
        c = int(code)
    except Exception:
        return "Unknown"
    if c < 0:
        c = c & 0xFFFFFFFF
    return VIDEO_OUTPUT_TECH.get(c, "Other (%s)" % c)


BATTERY_STATUS = {
    0: "Empty", 1: "AC connected", 2: "Fully charged", 3: "Low",
    4: "Critical", 5: "Charging", 6: "Charging + high", 7: "Charging + low",
    8: "Charging + critical", 9: "Undefined", 10: "Partially charged",
}


def battery_status_label(code: Any) -> str:
    try:
        return BATTERY_STATUS.get(int(code), "Status %s" % code)
    except Exception:
        return "Unknown"


BATTERY_CHEMISTRY = {1: "Other", 2: "Unknown", 3: "Lead Acid", 4: "Nickel Cadmium",
                     5: "Nickel Metal Hydride", 6: "Lithium-ion", 7: "Zinc air",
                     8: "Lithium Polymer"}


def battery_chem_label(code: Any) -> str:
    try:
        return BATTERY_CHEMISTRY.get(int(code), "Type %s" % code)
    except Exception:
        return "Unknown"
