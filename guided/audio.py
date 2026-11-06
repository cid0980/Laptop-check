"""
audio.py -- SPEAKER and MICROPHONE guided tests.

Everything is generated at run time from the standard library:
  * the test tones are synthesised in Python and written to a small WAV file
    (no audio files shipped, no downloads, no codecs to install)
  * playback uses winsound (built into Windows)
  * recording uses the waveIn API through ctypes (built into Windows)

Speaker test: left only, right only, both, then a 100 Hz - 8 kHz sweep that
makes a rattling or torn speaker cone obvious. The technician marks each one.

Microphone test: records a few seconds while the technician speaks or taps,
draws a live level meter, and reports both the measured signal level and the
human verdict.
"""

from __future__ import annotations

import array
import ctypes
import math
import os
import threading
import time
import tkinter as tk
import wave
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core import CheckResult, DATA_DIR, IS_WINDOWS, temp_path
from guided.widgets import COLOURS, GuidedDialog, checklist_dialog

if IS_WINDOWS:
    try:
        import winsound
    except Exception:                      # pragma: no cover
        winsound = None
else:
    winsound = None

CACHE = os.path.join(DATA_DIR, "audio")
try:
    os.makedirs(CACHE, exist_ok=True)
except Exception:
    pass


# ==========================================================================
# tone generation
# ==========================================================================

def make_tone(path: str, seconds: float = 3.0, freq: float = 440.0,
              pan: str = "both", sweep: Optional[Tuple[float, float]] = None,
              sr: int = 44100, volume: float = 0.55) -> bool:
    """Synthesise a 16-bit stereo WAV. pan: both | left | right."""
    n = int(sr * seconds)
    buf = array.array("h")
    phase = 0.0
    f0, f1 = sweep if sweep else (freq, freq)
    for i in range(n):
        t = i / sr
        if sweep:
            # exponential sweep
            f = f0 * ((f1 / f0) ** (t / seconds))
        else:
            f = freq
        phase += 2 * math.pi * f / sr
        v = math.sin(phase)
        # gentle fade in/out so there is no click
        env = 1.0
        fade = int(sr * 0.02)
        if i < fade:
            env = i / fade
        elif i > n - fade:
            env = max(0.0, (n - i) / fade)
        s = int(32767 * volume * env * v)
        l = s if pan in ("both", "left") else 0
        r = s if pan in ("both", "right") else 0
        buf.append(l)
        buf.append(r)
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(buf.tobytes())
        return True
    except Exception:
        return False


def ensure_tones() -> Dict[str, str]:
    """Create (or reuse) the four test WAV files."""
    specs = {
        "left":  dict(seconds=2.5, freq=440.0, pan="left"),
        "right": dict(seconds=2.5, freq=440.0, pan="right"),
        "both":  dict(seconds=2.5, freq=1000.0, pan="both"),
        "sweep": dict(seconds=6.0, pan="both", sweep=(100.0, 8000.0)),
    }
    out: Dict[str, str] = {}
    for key, kw in specs.items():
        path = os.path.join(CACHE, "tone_%s.wav" % key)
        if not (os.path.exists(path) and os.path.getsize(path) > 1000):
            make_tone(path, **kw)
        if os.path.exists(path):
            out[key] = path
    return out


_player: Dict[str, Any] = {"thread": None, "stop": False}


def play_wav(path: str, on_done: Optional[Callable[[], None]] = None) -> bool:
    if not IS_WINDOWS or winsound is None or not os.path.exists(path):
        return False
    stop_sound()

    def worker() -> None:
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME)   # synchronous
        except Exception:
            pass
        finally:
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass

    _player["thread"] = threading.Thread(target=worker, daemon=True)
    _player["thread"].start()
    return True


def stop_sound() -> None:
    if IS_WINDOWS and winsound is not None:
        try:
            winsound.PlaySound(None, 0)
        except Exception:
            pass


def max_out_volume() -> bool:
    """Push the waveOut stream volume to 100% so a turned-down stream can never
    fake a dead speaker. (System-wide mute is handled by the fixes module.)"""
    if not IS_WINDOWS:
        return False
    try:
        w = ctypes.windll.winmm
        w.waveOutSetVolume.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        w.waveOutSetVolume.restype = ctypes.c_uint
        return w.waveOutSetVolume(None, 0xFFFFFFFF) == 0
    except Exception:
        return False


def beep(freq: int = 880, ms: int = 180) -> None:
    if IS_WINDOWS and winsound is not None:
        try:
            threading.Thread(target=lambda: winsound.Beep(freq, ms), daemon=True).start()
        except Exception:
            pass


# ==========================================================================
# microphone capture (waveIn via ctypes)
# ==========================================================================

class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", ctypes.c_ushort),
        ("nChannels", ctypes.c_ushort),
        ("nSamplesPerSec", ctypes.c_ulong),
        ("nAvgBytesPerSec", ctypes.c_ulong),
        ("nBlockAlign", ctypes.c_ushort),
        ("wBitsPerSample", ctypes.c_ushort),
        ("cbSize", ctypes.c_ushort),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", ctypes.c_ulong),
        ("dwBytesRecorded", ctypes.c_ulong),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", ctypes.c_ulong),
        ("dwLoops", ctypes.c_ulong),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
    ]


WHDR_DONE = 0x0001
WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_NULL = 0x00000000
WAVE_FORMAT_PCM = 1


class MicRecorder:
    """Non-blocking multi-buffer waveIn recorder with level polling."""

    def __init__(self, seconds: float = 6.0, sr: int = 22050, chunk_ms: int = 400) -> None:
        self.seconds = seconds
        self.sr = sr
        self.chunk_ms = chunk_ms
        self.channels = 1
        self.bits = 16
        self.error = ""
        self.levels: List[Dict[str, Any]] = []
        self._hwi = ctypes.c_void_p(0)
        self._hdrs: List[Tuple[WAVEHDR, ctypes.Array, int]] = []
        self._winmm = None
        self._running = False
        self._peak = 0
        self._n_chunks = max(2, int(seconds * 1000 / chunk_ms))

    # ------------------------------------------------------------------ open
    def start(self) -> bool:
        if not IS_WINDOWS:
            self.error = "Microphone capture needs Windows"
            return False
        try:
            self._winmm = ctypes.windll.winmm
        except Exception as exc:
            self.error = "winmm unavailable: %s" % exc
            return False
        try:
            w = self._winmm
            # 64-bit-safe prototypes: dwCallback / dwInstance are pointer-sized.
            # Without these, waveInOpen overflows on the callback argument and
            # the microphone never opens.
            _U = ctypes.c_uint
            w.waveInGetNumDevs.restype = _U
            w.waveInOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), _U,
                                     ctypes.POINTER(WAVEFORMATEX), ctypes.c_size_t,
                                     ctypes.c_size_t, _U]
            w.waveInOpen.restype = _U
            for _fn in ("waveInClose", "waveInStart", "waveInReset", "waveInStop"):
                getattr(w, _fn).argtypes = [ctypes.c_void_p]
                getattr(w, _fn).restype = _U
            for _fn in ("waveInPrepareHeader", "waveInUnprepareHeader", "waveInAddBuffer"):
                getattr(w, _fn).argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), _U]
                getattr(w, _fn).restype = _U
            w.waveInGetErrorTextW.argtypes = [_U, ctypes.c_wchar_p, _U]
            w.waveInGetErrorTextW.restype = _U
            ndev = int(self._winmm.waveInGetNumDevs())
        except Exception:
            ndev = 0
        if ndev <= 0:
            self.error = "Windows reports no recording device at all"
            return False

        block_align = self.channels * self.bits // 8
        wfx = WAVEFORMATEX()
        wfx.wFormatTag = WAVE_FORMAT_PCM
        wfx.nChannels = self.channels
        wfx.nSamplesPerSec = self.sr
        wfx.wBitsPerSample = self.bits
        wfx.nBlockAlign = block_align
        wfx.nAvgBytesPerSec = self.sr * block_align
        wfx.cbSize = 0
        self._wfx = wfx

        rc = self._winmm.waveInOpen(ctypes.byref(self._hwi), WAVE_MAPPER,
                                    ctypes.byref(wfx), 0, 0, CALLBACK_NULL)
        if rc != 0:
            self.error = "waveInOpen failed with code %d (%s)" % (rc, self._errtext(rc))
            return False

        buf_bytes = int(self.sr * block_align * self.chunk_ms / 1000)
        for _ in range(self._n_chunks):
            data = (ctypes.c_char * buf_bytes)()
            hdr = WAVEHDR()
            hdr.lpData = ctypes.cast(data, ctypes.c_void_p)
            hdr.dwBufferLength = buf_bytes
            hdr.dwBytesRecorded = 0
            hdr.dwFlags = 0
            hdr.dwLoops = 0
            r = self._winmm.waveInPrepareHeader(self._hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if r != 0:
                self.error = "waveInPrepareHeader failed (%d)" % r
                self.close()
                return False
            r = self._winmm.waveInAddBuffer(self._hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if r != 0:
                self.error = "waveInAddBuffer failed (%d)" % r
                self.close()
                return False
            self._hdrs.append((hdr, data, buf_bytes))

        r = self._winmm.waveInStart(self._hwi)
        if r != 0:
            self.error = "waveInStart failed (%d) - the microphone may be blocked by Windows privacy settings" % r
            self.close()
            return False
        self._running = True
        self._t0 = time.time()
        return True

    def _errtext(self, rc: int) -> str:
        try:
            buf = ctypes.create_unicode_buffer(256)
            self._winmm.waveInGetErrorTextW(rc, buf, 256)
            return buf.value
        except Exception:
            return "unknown"

    # ------------------------------------------------------------------ poll
    def poll(self) -> List[Dict[str, Any]]:
        """Return the levels for every chunk that finished since the last poll."""
        new: List[Dict[str, Any]] = []
        if not self._running:
            return new
        for hdr, data, buflen in self._hdrs:
            if hdr.dwFlags & WHDR_DONE and not getattr(hdr, "_taken", False):
                n = int(hdr.dwBytesRecorded or 0)
                if n < 2:
                    n = buflen
                raw = ctypes.string_at(data, min(n, buflen))
                samples = array.array("h")
                samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
                peak = max((abs(s) for s in samples), default=0)
                if samples:
                    rms = math.sqrt(sum(s * s for s in samples) / float(len(samples)))
                else:
                    rms = 0.0
                entry = {
                    "peak": peak, "rms": rms,
                    "peak_db": 20 * math.log10(peak / 32768.0) if peak else -120.0,
                    "rms_db": 20 * math.log10(rms / 32768.0) if rms else -120.0,
                    "t": round(time.time() - self._t0, 2),
                }
                self._peak = max(self._peak, peak)
                self.levels.append(entry)
                new.append(entry)
                hdr._taken = True
                # re-queue so a long test keeps producing data
                if self._running and time.time() - self._t0 < self.seconds:
                    hdr.dwFlags = 0
                    hdr.dwBytesRecorded = 0
                    hdr._taken = False
                    try:
                        self._winmm.waveInAddBuffer(self._hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
                    except Exception:
                        pass
        return new

    def elapsed(self) -> float:
        return time.time() - getattr(self, "_t0", time.time())

    def done(self) -> bool:
        return (not self._running) or (self.elapsed() >= self.seconds)

    def stop(self) -> None:
        if self._running and self._winmm is not None:
            try:
                self._winmm.waveInStop(self._hwi)
            except Exception:
                pass
        self._running = False

    def close(self) -> None:
        self.stop()
        if self._winmm is None or not self._hwi:
            return
        for hdr, data, _ in self._hdrs:
            try:
                self._winmm.waveInUnprepareHeader(self._hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
            except Exception:
                pass
        try:
            self._winmm.waveInClose(self._hwi)
        except Exception:
            pass
        self._hwi = ctypes.c_void_p(0)
        self._hdrs = []


# ==========================================================================
# SPEAKER TEST
# ==========================================================================

def run_speaker_test(master: tk.Misc, res: CheckResult, ctx: Dict[str, Any],
                     emit: Optional[Callable[[str], None]] = None) -> CheckResult:
    # NOTE: we do NOT gate this test on Windows' device enumeration. Shops
    # don't care whether Windows lists a "media player" - they care whether
    # the speakers make sound. So we always play the tones (winmm, part of
    # Windows itself, no other app involved) and let the human ear decide.
    audio = ctx.get("audio") or {}
    if not IS_WINDOWS:
        res.skipped("Speaker test needs Windows")
        return res

    from app import fixes
    fix_note = fixes.ensure_audio()
    if fix_note:
        res.note(fix_note)
    if max_out_volume():
        res.note("The tool set the playback stream volume to 100% for this test.")
    try:
        from app import audiovol
        vol_note = audiovol.ensure_audible()
        if vol_note:
            res.note(vol_note)
    except Exception:
        pass
    tones = ensure_tones()
    if not tones:
        res.skipped("Could not generate the test tones")
        return res

    names = ", ".join(audio.get("speaker_names") or [])[:90]
    res.metric("Output devices listed by Windows", audio.get("speakers", 0) or "none listed (ignored)")
    if names:
        res.metric("Device names", names)
    elif not audio.get("speakers"):
        res.note("Windows listed no playback device, but the tones are played through the "
                 "system audio stack anyway - trust your ears, not the device list.")

    state: Dict[str, Any] = {"played": set()}

    def play(key: str) -> Callable[[], None]:
        def go() -> None:
            path = tones.get(key)
            if not path:
                return
            state["played"].add(key)
            if emit:
                emit("Playing %s test tone..." % key)
            play_wav(path)
        return go

    opts = [("good", "CLEAR", COLOURS["good"]),
            ("weak", "WEAK / DISTORTED", COLOURS["okay"]),
            ("none", "NO SOUND", COLOURS["bad"]),
            ("na", "N/A", COLOURS["skip"])]

    items = [
        {"id": "left", "label": "LEFT speaker - 440 Hz tone",
         "hint": "Sound must come from the LEFT side only. If it comes from both sides the "
                 "channels are swapped or the speaker wiring is wrong.",
         "actions": [("\u25b6  PLAY LEFT", play("left"))], "options": opts, "note": True},
        {"id": "right", "label": "RIGHT speaker - 440 Hz tone",
         "hint": "Sound must come from the RIGHT side only.",
         "actions": [("\u25b6  PLAY RIGHT", play("right"))], "options": opts, "note": True},
        {"id": "both", "label": "BOTH speakers - 1 kHz tone",
         "hint": "Both sides at the same loudness. One side quieter means a damaged speaker.",
         "actions": [("\u25b6  PLAY BOTH", play("both"))], "options": opts, "note": True},
        {"id": "sweep", "label": "FREQUENCY SWEEP - 100 Hz to 8 kHz",
         "hint": "Listen for rattling, buzzing or crackling as the pitch rises - that is a torn "
                 "cone or a loose speaker grille. 6 seconds long.",
         "actions": [("\u25b6  PLAY SWEEP", play("sweep"))], "options": opts, "note": True},
        {"id": "maxvol", "label": "LOUDNESS at 100% volume",
         "hint": "! Turn Windows volume to 100% and play the sweep again. Is it loud enough for a "
                 "normal room? Very quiet output usually means a dying amplifier.",
         "actions": [("\u25b6  PLAY SWEEP AGAIN", play("sweep"))], "options": opts, "note": True},
        {"id": "jack", "label": "3.5 mm HEADPHONE jack",
         "hint": "Plug headphones in: sound must move to the headphones and the speakers must go "
                 "silent. Unplug: speakers must come back. Windows shows a headphone endpoint.",
         "actions": [("\u25b6  PLAY BOTH", play("both"))], "options": opts, "note": True},
    ]

    out = checklist_dialog(master, "Speaker and headphone test",
                           "Play each tone, listen, then mark it. Do not guess - actually listen.",
                           items)
    stop_sound()

    if out.get("cancelled"):
        res.skipped("Speaker test was cancelled")
        return res

    answers: Dict[str, Any] = out.get("answers", {})
    score_map = {"good": 100.0, "weak": 55.0, "none": 5.0, "na": None}
    scores: List[float] = []
    for it in items:
        key = answers.get(it["id"])
        note = answers.get(it["id"] + "_note", "")
        label = {o[0]: o[1] for o in opts}.get(key, "not answered")
        res.metric(it["label"].split(" - ")[0], label + ((" - " + note) if note else ""))
        if key is None:
            res.flag("'%s' was never answered" % it["label"], "warn")
            scores.append(55.0)   # unanswered = uncertain, never silently "good"
            continue
        sc = score_map.get(key)
        if sc is not None:
            scores.append(sc)
        if key == "none":
            res.flag("%s: NO SOUND" % it["label"].split(" - ")[0], "fail")
        elif key == "weak":
            res.flag("%s: weak or distorted" % it["label"].split(" - ")[0], "warn")

    res.metric("Tones actually played", len(state["played"]))
    if not state["played"]:
        res.flag("No tone was ever played - the speaker marks cannot be trusted", "fail")
        scores.append(0.0)

    if not scores:
        res.skipped("Speaker test not completed")
        return res
    score = sum(scores) / len(scores)
    res.marks = [{"id": it["id"], "name": it["label"], "mark": answers.get(it["id"]),
                  "note": answers.get(it["id"] + "_note", "")} for it in items]
    if out.get("note"):
        res.note("Technician note: " + out["note"])
    res.set("pass" if score >= 88 else ("warn" if score >= 50 else "fail"),
            "Speakers and headphone jack scored %.0f/100" % score, score=score)
    return res


# ==========================================================================
# MICROPHONE TEST
# ==========================================================================

def run_mic_test(master: tk.Misc, res: CheckResult, ctx: Dict[str, Any],
                 emit: Optional[Callable[[str], None]] = None) -> CheckResult:
    # As with the speakers: try the hardware first (winmm waveIn - part of
    # Windows, no other app). Windows' device list is only a note, never a
    # reason to skip: on many shop PCs the list is simply wrong.
    audio = ctx.get("audio") or {}
    if not IS_WINDOWS:
        res.skipped("Microphone test needs Windows")
        return res

    from app import fixes
    fix_note = fixes.allow_privacy("microphone")
    if fix_note:
        res.note(fix_note)
    from app import audiovol as _av
    vol_note = _av.ensure_audible(0.65, flow=1)
    if vol_note:
        res.note(vol_note)
    res.metric("Recording devices listed by Windows", audio.get("mics", 0) or "none listed (ignored)")
    if audio.get("mic_names"):
        res.metric("Device names", ", ".join(audio.get("mic_names"))[:90])

    seconds = 6.0
    rec = MicRecorder(seconds=seconds, sr=22050, chunk_ms=300)
    state: Dict[str, Any] = {"started": False, "bars": []}

    def build(dlg: Any, parent: tk.Frame) -> None:
        box = tk.Frame(parent, bg=COLOURS["panel"], highlightthickness=1,
                       highlightbackground=COLOURS["line"])
        box.pack(fill="x", pady=(0, 8))
        tk.Label(box, text="Speak normally, or tap the lid near the microphone, for 6 seconds.",
                 bg=COLOURS["panel"], fg=COLOURS["text"], font=("Segoe UI", 11, "bold"),
                 anchor="w").pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(box, text="The meter below shows what Windows actually received from the microphone.",
                 bg=COLOURS["panel"], fg=COLOURS["muted"], font=("Segoe UI", 9),
                 anchor="w").pack(fill="x", padx=12, pady=(0, 6))
        cv = tk.Canvas(box, width=680, height=90, bg="#0f172a", highlightthickness=0)
        cv.pack(fill="x", padx=12, pady=(0, 6))
        state["canvas"] = cv
        state["dlg"] = dlg
        dlg.add_action("\u25cf  START RECORDING", start_rec, COLOURS["accent"])
        dlg.add_action("\u25b6  SPEAKER\u2192MIC LOOPBACK", loopback, COLOURS["panel2"])
        dlg.add_action("Stop", stop_rec, COLOURS["panel2"])
        _draw_meter(cv, [])
        dlg.say("Press START RECORDING, then speak.")

    def _draw_meter(cv: tk.Canvas, levels: List[Dict[str, Any]]) -> None:
        try:
            cv.delete("all")
        except Exception:
            return
        try:
            w = int(cv["width"]) or 680
            h = int(cv["height"]) or 90
        except Exception:
            w, h = 680, 90
        # dB scale: -60 .. 0
        for db in (-60, -45, -30, -15):
            x = int(w * (db + 60) / 60.0)
            cv.create_line(x, 0, x, h, fill="#1c2430")
            cv.create_text(x + 2, 8, text="%d dB" % db, anchor="nw", fill="#4d5a6d",
                           font=("Segoe UI", 7))
        bw = max(4, w // max(1, len(levels) + 6))
        for i, lv in enumerate(levels):
            db = max(-60.0, min(0.0, lv["rms_db"]))
            bh = int((h - 24) * (db + 60) / 60.0)
            x0 = 6 + i * (bw + 2)
            colour = COLOURS["bad"] if db < -50 else (COLOURS["okay"] if db < -35 else COLOURS["good"])
            cv.create_rectangle(x0, h - 16 - bh, x0 + bw, h - 16, fill=colour, outline="")
        if levels:
            best = max(lv["peak_db"] for lv in levels)
            cv.create_text(w - 8, h - 8, anchor="se", fill="#e2e8f0", font=("Segoe UI", 9, "bold"),
                           text="peak %.1f dB" % best)

    def tick() -> None:
        if not state["started"]:
            return
        new = rec.poll()
        if new:
            state["bars"].extend(new)
            _draw_meter(state.get("canvas"), state["bars"][-40:])
            d = state.get("dlg")
            if d:
                last = new[-1]
                d.say("Recording... level %.1f dB (peak %.1f dB)" % (last["rms_db"], last["peak_db"]),
                      COLOURS["good"] if last["peak_db"] > -40 else COLOURS["muted"])
        if rec.done() or rec.elapsed() > seconds + 1.5:
            finish_rec()
            return
        try:
            master.after(120, tick)
        except Exception:
            pass

    def start_rec() -> None:
        d = state.get("dlg")
        if state["started"]:
            return
        if not rec.start():
            if d:
                d.say("Microphone could not be opened: %s" % rec.error, "#fca5a5")
            state["open_error"] = rec.error
            return
        state["started"] = True
        state["bars"] = []
        if d:
            d.say("Recording - speak now!", COLOURS["good"])
        if emit:
            emit("Microphone recording started (%.0f s)..." % seconds)
        tick()

    def stop_rec() -> None:
        rec.stop()

    def loopback() -> None:
        """Objective proof: play 1 kHz through the speakers WHILE the mic
        records. If the mic meter jumps, speaker chain and mic chain both work."""
        d = state.get("dlg")
        if not state["started"]:
            if d:
                d.say("Press START RECORDING first, then press LOOPBACK.", "#fcd34d")
            return
        path = (ensure_tones() or {}).get("both")
        if not path:
            return
        max_out_volume()
        state["lb_t0"] = rec.elapsed()
        play_wav(path)
        if d:
            d.say("Playing 1 kHz through the SPEAKERS while the MIC records - watch the meter jump.",
                  COLOURS["accent"])

    def finish_rec() -> None:
        if not state["started"]:
            return
        state["started"] = False
        rec.stop()
        d = state.get("dlg")
        if d and state["bars"]:
            best = max(b["peak_db"] for b in state["bars"])
            d.say("Finished. Peak level %.1f dB. Now mark the result." % best,
                  COLOURS["good"] if best > -40 else COLOURS["okay"])
        lb = state.get("lb_t0")
        if lb is not None and state["bars"]:
            base = [b for b in state["bars"] if b["t"] < lb - 0.2]
            during = [b for b in state["bars"] if lb + 0.3 <= b["t"] <= lb + 3.2]
            if during:
                b_db = max([b["peak_db"] for b in base], default=-60.0)
                d_db = max(b["peak_db"] for b in during)
                res.metric("Speaker-to-mic loopback",
                           "%+.1f dB over baseline (%.1f -> %.1f dB)" % (d_db - b_db, b_db, d_db))
                if d_db - b_db > 6:
                    res.note("Loopback proof: the microphone clearly heard the speaker tone - "
                             "speakers AND microphone both work.")
                else:
                    res.note("Loopback: the microphone did not clearly hear the speaker tone "
                             "(mic muted, speakers very quiet, or mic far from speakers).")

    marks = [("good", "MIC WORKS", COLOURS["good"]),
             ("weak", "VERY QUIET", COLOURS["okay"]),
             ("none", "NO INPUT", COLOURS["bad"]),
             ("na", "NO MIC FITTED", COLOURS["skip"])]

    out = None
    try:
        dlg = GuidedDialog(master, "Microphone test",
                           "Measured with the Windows waveIn API - no extra software",
                           ["Press START RECORDING and speak normally for 6 seconds.",
                            "Watch the meter: it should move while you speak and fall silent when you stop.",
                            "! If nothing moves at all: the tool already set the Windows microphone "
                            "privacy consent to Allow. Check the microphone mute key on the keyboard."
                            "is set to Allow, then test again before condemning the microphone.",
                            "Then mark the result."],
                           marks=marks, body_builder=build, width=780)
        out = dlg.run()
    finally:
        try:
            rec.close()
        except Exception:
            pass

    levels = state["bars"] or rec.levels
    if levels:
        peak_db = max(l["peak_db"] for l in levels)
        rms_db = max(l["rms_db"] for l in levels)
        res.metric("Chunks captured", len(levels))
        res.metric("Peak level", "%.1f dBFS" % peak_db)
        res.metric("Average level", "%.1f dBFS" % rms_db)
        auto = "clear signal" if peak_db > -35 else ("faint signal" if peak_db > -50 else "no usable signal")
        res.metric("Automatic verdict", auto)
    else:
        peak_db = None
        res.metric("Chunks captured", 0)
        res.metric("Automatic verdict", "recording never started" + (
            (" - " + state.get("open_error", "")) if state.get("open_error") else ""))

    if out is None or out.get("cancelled"):
        res.skipped("Microphone test was cancelled")
        if peak_db is not None:
            res.note("Measured peak %.1f dBFS before the test was cancelled." % peak_db)
        return res

    mark = out.get("mark")
    res.marks = [{"id": "mic", "name": "Microphone", "mark": mark, "note": out.get("note", "")}]
    if out.get("note"):
        res.note("Technician note: " + out["note"])

    score = {"good": 100.0, "weak": 55.0, "none": 8.0, "na": None}.get(mark)
    if peak_db is not None:
        if peak_db > -35 and mark == "none":
            res.note("WARNING: the technician marked 'no input' but the meter measured %.1f dBFS - "
                     "re-check the marking." % peak_db)
        if peak_db < -50 and mark == "good":
            res.note("WARNING: marked 'works' but the measured peak was only %.1f dBFS (very quiet)." % peak_db)
            score = 70.0
    if state.get("open_error") and mark in (None, "none"):
        res.flag("Microphone could not be opened: %s" % state["open_error"], "warn")
    if score is None:
        res.na("Technician confirmed no microphone is fitted")
        return res
    if mark == "none":
        res.flag("Microphone does not pick up sound", "fail")
    elif mark == "weak":
        res.flag("Microphone input is very quiet", "warn")
    res.set("pass" if score >= 88 else ("warn" if score >= 50 else "fail"),
            {"good": "Microphone works", "weak": "Microphone is very quiet",
             "none": "Microphone does not work"}.get(mark, "Microphone test"), score=score)
    return res
