"""
dshow.py -- webcam capture through DirectShow, using ONLY ctypes.

Why not OpenCV / Pillow / pygrabber?
    Because the deliverable must be ONE .exe with nothing to install. OpenCV
    alone would add ~90 MB and a numpy dependency. DirectShow is built into
    every Windows since XP, so we talk to it directly through COM.

Why a child process?
    Camera drivers are third-party code and a bad one can crash the process.
    The GUI therefore never calls this module in-process: run.py is re-launched
    with --camera-live / --camera-capture and writes PNG frames to a folder
    that the GUI polls. If the child dies, the GUI simply reports
    "capture failed" and offers the manual fallback. No crash, no hang.

COM is called with a tiny generic vtable invoker (com_call) instead of
hand-written interface classes - far less code, same result.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from app import png

if sys.platform.startswith("win"):
    ole32 = ctypes.windll.ole32
    oleaut32 = ctypes.windll.oleaut32
else:  # pragma: no cover - import guard so the module loads anywhere
    ole32 = None
    oleaut32 = None

S_OK = 0
S_FALSE = 1
CLSCTX_INPROC_SERVER = 0x1
PINDIR_INPUT = 0
PINDIR_OUTPUT = 1
VT_BSTR = 8


# --------------------------------------------------------------------------
# GUID + COM plumbing
# --------------------------------------------------------------------------

class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def make_guid(s: str) -> GUID:
    g = GUID()
    if ole32 is not None:
        ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(GUID)]
        ole32.CLSIDFromString.restype = ctypes.c_long
        if ole32.CLSIDFromString(s, ctypes.byref(g)) != 0:
            raise OSError("bad GUID: %s" % s)
    return g


def com_call(this: int, index: int, argtypes: Tuple[Any, ...], *args: Any,
             restype: Any = ctypes.c_long) -> Any:
    """Call method #index of the COM object pointed to by `this`."""
    vtbl = ctypes.cast(this, ctypes.POINTER(ctypes.c_void_p))[0]
    addr = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[index]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(addr)(this, *args)


def com_release(this: int) -> None:
    if this:
        try:
            com_call(this, 2, (), )
        except Exception:
            pass


def com_query(this: int, iid: GUID) -> int:
    out = ctypes.c_void_p(0)
    hr = com_call(this, 0, (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                  ctypes.addressof(iid), ctypes.byref(out))
    return out.value if hr >= 0 else 0


def co_create(clsid: GUID, iid: GUID) -> int:
    out = ctypes.c_void_p(0)
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p,
                                       ctypes.c_ulong, ctypes.POINTER(GUID),
                                       ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = ctypes.c_long
    hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
                                ctypes.byref(iid), ctypes.byref(out))
    if hr < 0 or not out.value:
        raise OSError("CoCreateInstance failed, HRESULT 0x%08X" % (hr & 0xFFFFFFFF))
    return out.value


# well-known identifiers
CLSID_FilterGraph = make_guid("{E436EBB3-524F-11CE-9F53-0020AF0BA770}")
CLSID_SystemDeviceEnum = make_guid("{62BE5D10-60EB-11d0-BD3B-00A0C911CE86}")
CLSID_SampleGrabber = make_guid("{C1F400A0-3F08-11d3-9F0B-006008039E37}")
CLSID_VideoInputDeviceCategory = make_guid("{860BB310-5D01-11d0-BD3B-00A0C911CE86}")

IID_IGraphBuilder = make_guid("{56A868A9-0AD4-11CE-B03A-0020AF0BA770}")
IID_IBaseFilter = make_guid("{56A86895-0AD4-11CE-B03A-0020AF0BA770}")
IID_ISampleGrabber = make_guid("{6B652FFF-11FE-4fce-92AD-0266B5D7C78F}")
IID_IAMStreamConfig = make_guid("{C6E13340-30AC-11d0-A18C-00A0C9118956}")
IID_IMediaControl = make_guid("{56A868B1-0AD4-11CE-B03A-0020AF0BA770}")
IID_ICreateDevEnum = make_guid("{29840822-5B84-11D0-BD3B-00A0C911CE86}")
IID_IPropertyBag = make_guid("{55272A00-42CB-11CE-8135-00AA004BB851}")
IID_IMediaEventEx = make_guid("{56A868C0-0AD4-11CE-B03A-0020AF0BA770}")

MEDIATYPE_Video = make_guid("{73646976-0000-0010-8000-00AA00389B71}")
MEDIASUBTYPE_RGB24 = make_guid("{e436eb7d-524f-11ce-9f53-0020af0ba770}")
FORMAT_VideoInfo = make_guid("{05589f80-c356-11ce-bf01-00aa0055595a}")

# vtable indexes
IG_CONNECT, IG_ADDFILTER, IG_RENDER = 11, 3, 12
IBF_ENUMPINS = 9
IMC_RUN, IMC_STOP, IMC_PAUSE = 7, 9, 8
ISG_SETONESHOT, ISG_SETMEDIATYPE, ISG_SETBUFFERSAMPLES = 3, 4, 6
ISG_GETCURRENTBUFFER, ISG_GETCONNECTEDMEDIATYPE = 7, 5
ISC_SETFORMAT, ISC_GETFORMAT = 3, 4
IPIN_CONNECTIONMEDIATYPE, IPIN_QUERYDIRECTION = 7, 9
IENUM_NEXT = 3
IDEV_CREATECLASSENUM = 3
IMON_BINDTOOBJECT, IMON_BINDTOSTORAGE = 9, 10
IPB_READ = 4


# --------------------------------------------------------------------------
# media structures
# --------------------------------------------------------------------------

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_ulong), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_ulong),
                ("biSizeImage", ctypes.c_ulong), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_ulong),
                ("biClrImportant", ctypes.c_ulong)]


class VIDEOINFOHEADER(ctypes.Structure):
    _fields_ = [("rcSource", RECT), ("rcTarget", RECT),
                ("dwBitRate", ctypes.c_ulong), ("dwBitErrorRate", ctypes.c_ulong),
                ("AvgTimePerFrame", ctypes.c_longlong),
                ("bmiHeader", BITMAPINFOHEADER)]


class AM_MEDIA_TYPE(ctypes.Structure):
    _fields_ = [("majortype", GUID), ("subtype", GUID),
                ("bFixedSizeSamples", ctypes.c_int), ("bTemporalCompression", ctypes.c_int),
                ("lSampleSize", ctypes.c_ulong), ("formattype", GUID),
                ("pUnk", ctypes.c_void_p), ("cbFormat", ctypes.c_ulong),
                ("pbFormat", ctypes.c_void_p)]


class VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("wReserved1", ctypes.c_ushort),
                ("wReserved2", ctypes.c_ushort), ("wReserved3", ctypes.c_ushort),
                ("data", ctypes.c_ulonglong * 2)]


# --------------------------------------------------------------------------
# device enumeration
# --------------------------------------------------------------------------

def list_devices() -> List[Dict[str, Any]]:
    """Return [{'index','name','moniker'}] for every DirectShow video input."""
    if ole32 is None:
        return []
    ole32.CoInitialize(None)
    devices: List[Dict[str, Any]] = []
    devenum = enum_ptr = 0
    monikers: List[int] = []
    try:
        devenum = co_create(CLSID_SystemDeviceEnum, IID_ICreateDevEnum)
        out = ctypes.c_void_p(0)
        hr = com_call(devenum, IDEV_CREATECLASSENUM,
                      (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong),
                      ctypes.addressof(CLSID_VideoInputDeviceCategory), ctypes.byref(out), 0)
        if hr < 0 or not out.value:
            return []
        enum_ptr = out.value
        while True:
            m = ctypes.c_void_p(0)
            fetched = ctypes.c_ulong(0)
            hr = com_call(enum_ptr, IENUM_NEXT,
                          (ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_ulong)),
                          1, ctypes.byref(m), ctypes.byref(fetched))
            if hr != S_OK or not m.value or fetched.value == 0:
                break
            monikers.append(m.value)
        for i, mon in enumerate(monikers):
            name = ""
            bag = ctypes.c_void_p(0)
            hr = com_call(mon, IMON_BINDTOSTORAGE,
                          (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_void_p)),
                          0, 0, ctypes.addressof(IID_IPropertyBag), ctypes.byref(bag))
            if hr >= 0 and bag.value:
                try:
                    oleaut32.VariantInit.argtypes = [ctypes.POINTER(VARIANT)]
                    v = VARIANT()
                    oleaut32.VariantInit(ctypes.byref(v))
                    hr2 = com_call(bag.value, IPB_READ,
                                   (ctypes.c_wchar_p, ctypes.POINTER(VARIANT), ctypes.c_void_p),
                                   "FriendlyName", ctypes.byref(v), 0)
                    if hr2 >= 0 and v.vt == VT_BSTR:
                        bstr = ctypes.cast(ctypes.c_void_p(v.data[0]), ctypes.c_wchar_p)
                        name = bstr.value or ""
                    try:
                        oleaut32.VariantClear.argtypes = [ctypes.POINTER(VARIANT)]
                        oleaut32.VariantClear(ctypes.byref(v))
                    except Exception:
                        pass
                except Exception:
                    pass
                com_release(bag.value)
            devices.append({"index": i, "name": name or ("Camera %d" % (i + 1)),
                            "moniker": mon})
    except Exception:
        return devices
    return devices


# --------------------------------------------------------------------------
# capture session
# --------------------------------------------------------------------------

class Camera:
    """One open capture session. Call frame() as often as you like."""

    def __init__(self, index: int = 0, want_w: int = 0, want_h: int = 0) -> None:
        self.index = index
        self.want_w = want_w
        self.want_h = want_h
        self.width = 0
        self.height = 0
        self.bitcount = 24
        self.device_name = ""
        self.error = ""
        self.graph = self.capfilt = self.grab = self.grabfilt = 0
        self.control = 0
        self.pins: List[int] = []
        self.monikers: List[int] = []
        self.devenum = self.enum_ptr = 0
        self.opened = False
        self._buf: Optional[ctypes.Array] = None
        self._bufsize = ctypes.c_long(0)

    # ------------------------------------------------------------------ open
    def open(self, warmup: float = 1.6) -> bool:
        if ole32 is None:
            self.error = "DirectShow is only available on Windows"
            return False
        ole32.CoInitialize(None)
        try:
            devs = list_devices()
            if not devs:
                self.error = ("No camera device is exposed through DirectShow. Either the machine has "
                              "no camera, the camera is disabled in the BIOS, or Windows privacy "
                              "settings block desktop apps from using it.")
                return False
            idx = self.index if 0 <= self.index < len(devs) else 0
            self.device_name = devs[idx]["name"]
            mon = devs[idx]["moniker"]
            self.monikers = [d["moniker"] for d in devs]

            cap = ctypes.c_void_p(0)
            hr = com_call(mon, IMON_BINDTOOBJECT,
                          (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_void_p)),
                          0, 0, ctypes.addressof(IID_IBaseFilter), ctypes.byref(cap))
            if hr < 0 or not cap.value:
                self.error = "Could not bind the camera filter (HRESULT 0x%08X)" % (hr & 0xFFFFFFFF)
                return False
            self.capfilt = cap.value

            self.graph = co_create(CLSID_FilterGraph, IID_IGraphBuilder)
            hr = com_call(self.graph, IG_ADDFILTER,
                          (ctypes.c_void_p, ctypes.c_wchar_p), self.capfilt, "Capture")
            if hr < 0:
                self.error = "AddFilter(capture) failed 0x%08X" % (hr & 0xFFFFFFFF)
                return False

            self.grab = co_create(CLSID_SampleGrabber, IID_ISampleGrabber)
            self.grabfilt = com_query(self.grab, IID_IBaseFilter)
            hr = com_call(self.graph, IG_ADDFILTER,
                          (ctypes.c_void_p, ctypes.c_wchar_p), self.grabfilt, "Grabber")
            if hr < 0:
                self.error = "AddFilter(grabber) failed 0x%08X" % (hr & 0xFFFFFFFF)
                return False

            # ask for 24-bit RGB so we get a plain bitmap, no YUV conversion code
            mt = AM_MEDIA_TYPE()
            ctypes.memset(ctypes.byref(mt), 0, ctypes.sizeof(mt))
            mt.majortype = MEDIATYPE_Video
            mt.subtype = MEDIASUBTYPE_RGB24
            mt.formattype = FORMAT_VideoInfo
            com_call(self.grab, ISG_SETMEDIATYPE, (ctypes.POINTER(AM_MEDIA_TYPE),), ctypes.byref(mt))
            com_call(self.grab, ISG_SETBUFFERSAMPLES, (ctypes.c_int,), 1)
            com_call(self.grab, ISG_SETONESHOT, (ctypes.c_int,), 0)

            # find the capture filter's output pin and its native format
            outpin = self._find_output_pin()
            if not outpin:
                self.error = "The camera filter exposes no output pin"
                return False
            inpin = self._grabber_input_pin()
            if not inpin:
                self.error = "The sample grabber exposes no input pin"
                return False

            hr = com_call(self.graph, IG_CONNECT,
                          (ctypes.c_void_p, ctypes.c_void_p), outpin, inpin)
            if hr < 0:
                self.error = ("Could not connect the camera to the grabber "
                              "(HRESULT 0x%08X). The camera may be in use by another app."
                              % (hr & 0xFFFFFFFF))
                return False

            # what did we actually negotiate?
            cmt = AM_MEDIA_TYPE()
            ctypes.memset(ctypes.byref(cmt), 0, ctypes.sizeof(cmt))
            hr = com_call(self.grab, ISG_GETCONNECTEDMEDIATYPE,
                          (ctypes.POINTER(AM_MEDIA_TYPE),), ctypes.byref(cmt))
            if hr >= 0 and cmt.pbFormat and cmt.formattype == FORMAT_VideoInfo:
                vih = ctypes.cast(cmt.pbFormat, ctypes.POINTER(VIDEOINFOHEADER)).contents
                self.width = int(vih.bmiHeader.biWidth)
                self.height = abs(int(vih.bmiHeader.biHeight))
                self.bitcount = int(vih.bmiHeader.biBitCount)
                self.bottom_up = int(vih.bmiHeader.biHeight) > 0
            if not self.width or not self.height:
                self.error = "Negotiated video format could not be read"
                return False
            if self.bitcount != 24:
                self.error = "Grabber negotiated %d-bit frames, only 24-bit RGB is supported" % self.bitcount
                return False

            self.control = com_query(self.graph, IID_IMediaControl)
            if not self.control:
                self.error = "IMediaControl unavailable"
                return False
            hr = com_call(self.control, IMC_RUN, (), )
            if hr < 0:
                self.error = "Could not start the camera stream (0x%08X)" % (hr & 0xFFFFFFFF)
                return False
            time.sleep(warmup)          # let auto-exposure / white balance settle
            self.opened = True
            return True
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
            return False

    def _find_output_pin(self) -> int:
        enum = ctypes.c_void_p(0)
        hr = com_call(self.capfilt, IBF_ENUMPINS,
                      (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(enum))
        if hr < 0 or not enum.value:
            return 0
        found = 0
        while True:
            pin = ctypes.c_void_p(0)
            fetched = ctypes.c_ulong(0)
            hr = com_call(enum.value, IENUM_NEXT,
                          (ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p),
                           ctypes.POINTER(ctypes.c_ulong)),
                          1, ctypes.byref(pin), ctypes.byref(fetched))
            if hr != S_OK or not pin.value:
                break
            self.pins.append(pin.value)
            direction = ctypes.c_int(-1)
            com_call(pin.value, IPIN_QUERYDIRECTION, (ctypes.POINTER(ctypes.c_int),),
                     ctypes.byref(direction))
            if direction.value == PINDIR_OUTPUT:
                found = pin.value
                break
        com_release(enum.value)
        return found

    def _grabber_input_pin(self) -> int:
        enum = ctypes.c_void_p(0)
        hr = com_call(self.grabfilt, IBF_ENUMPINS,
                      (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(enum))
        if hr < 0 or not enum.value:
            return 0
        found = 0
        while True:
            pin = ctypes.c_void_p(0)
            fetched = ctypes.c_ulong(0)
            hr = com_call(enum.value, IENUM_NEXT,
                          (ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p),
                           ctypes.POINTER(ctypes.c_ulong)),
                          1, ctypes.byref(pin), ctypes.byref(fetched))
            if hr != S_OK or not pin.value:
                break
            self.pins.append(pin.value)
            direction = ctypes.c_int(-1)
            com_call(pin.value, IPIN_QUERYDIRECTION, (ctypes.POINTER(ctypes.c_int),),
                     ctypes.byref(direction))
            if direction.value == PINDIR_INPUT:
                found = pin.value
                break
        com_release(enum.value)
        return found

    # ----------------------------------------------------------------- frame
    def frame(self) -> Optional[bytes]:
        """Return one RGB frame (top-down) or None."""
        if not self.opened:
            return None
        try:
            size = ctypes.c_long(0)
            hr = com_call(self.grab, ISG_GETCURRENTBUFFER,
                          (ctypes.POINTER(ctypes.c_long), ctypes.c_void_p),
                          ctypes.byref(size), None)
            if hr < 0 or size.value <= 0:
                return None
            if self._buf is None or self._bufsize.value != size.value:
                self._buf = (ctypes.c_char * size.value)()
                self._bufsize = size
            hr = com_call(self.grab, ISG_GETCURRENTBUFFER,
                          (ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_char)),
                          ctypes.byref(self._bufsize), self._buf)
            if hr < 0:
                return None
            raw = ctypes.string_at(self._buf, size.value)
        except Exception:
            return None
        need = self.width * self.height * 3
        if len(raw) < need:
            return None
        if getattr(self, "bottom_up", True):
            return png.bgr_bottom_up_to_rgb(raw[:need], self.width, self.height)
        # top-down: still BGR per pixel
        out = bytearray(raw[:need])
        out[0::3], out[2::3] = out[2::3], out[0::3]
        return bytes(out)

    # ----------------------------------------------------------------- close
    def close(self) -> None:
        try:
            if self.control:
                com_call(self.control, IMC_STOP, (), )
        except Exception:
            pass
        for p in self.pins + [self.control, self.grabfilt, self.grab, self.capfilt,
                              self.graph, self.enum_ptr, self.devenum] + self.monikers:
            if p:
                com_release(p)
        self.pins = []
        self.opened = False
        try:
            ole32.CoUninitialize()
        except Exception:
            pass


# --------------------------------------------------------------------------
# entry points used by run.py in the CHILD process
# --------------------------------------------------------------------------

def capture_png(path: str, index: int = 0, warmup: float = 1.6,
                frames: int = 3) -> Dict[str, Any]:
    cam = Camera(index=index)
    info: Dict[str, Any] = {"ok": False, "path": path}
    if not cam.open(warmup=warmup):
        info["error"] = cam.error
        return info
    try:
        got = None
        for _ in range(max(1, frames)):
            f = cam.frame()
            if f:
                got = f
            time.sleep(0.12)
        if not got:
            info["error"] = "Camera stream produced no frame"
            return info
        png.save_png(path, cam.width, cam.height, got)
        info.update(ok=True, width=cam.width, height=cam.height,
                    device=cam.device_name, bytes=len(got))
    finally:
        cam.close()
    return info


def live_frames(folder: str, seconds: float = 25.0, fps: float = 3.0,
                index: int = 0, warmup: float = 1.4) -> Dict[str, Any]:
    """Write frame_0001.png, frame_0002.png ... into `folder` until `seconds`."""
    os.makedirs(folder, exist_ok=True)
    cam = Camera(index=index)
    status = {"ok": False, "folder": folder, "frames": 0}
    if not cam.open(warmup=warmup):
        status["error"] = cam.error
        _write_status(folder, status)
        return status
    status.update(ok=True, width=cam.width, height=cam.height, device=cam.device_name)
    _write_status(folder, status)
    interval = 1.0 / max(0.5, fps)
    n = 0
    t_end = time.time() + seconds
    try:
        while time.time() < t_end:
            f = cam.frame()
            if f:
                n += 1
                png.save_png(os.path.join(folder, "frame_%05d.png" % n), cam.width, cam.height, f)
                status["frames"] = n
                if n % 5 == 0:
                    _write_status(folder, status)
            time.sleep(interval)
    except Exception as exc:
        status["error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        status["frames"] = n
        _write_status(folder, status)
        cam.close()
    return status


def _write_status(folder: str, status: Dict[str, Any]) -> None:
    try:
        tmp = os.path.join(folder, "status.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(status, fh)
        os.replace(tmp, os.path.join(folder, "status.json"))
    except Exception:
        pass


def list_json() -> str:
    try:
        ole32.CoInitialize(None)
        devs = list_devices()
        out = [{"index": d["index"], "name": d["name"]} for d in devs]
    except Exception as exc:
        out = {"error": str(exc)}
    return json.dumps(out)
