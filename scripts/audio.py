"""Listens to whatever the PC is playing (WASAPI loopback) and analyses it.

    an = Analyzer(); an.start()
    s = an.snapshot()      # s.bands (0..1, bass first), s.level, s.bass, s.beat_age, s.beat_count, s.bpm

Needs numpy and pyaudiowpatch (see requirements.txt). Windows only.
"""
import collections
import threading
import time
from types import SimpleNamespace

try:
    import numpy as np
    import pyaudiowpatch as pa
except ImportError:
    raise SystemExit("numpy / pyaudiowpatch missing: run  py -m pip install -r scripts/requirements.txt")

BANDS = 16
FMIN, FMAX = 40.0, 14000.0
FFT = 2048                       # analysis window (samples) and capture block: normal mode
BLOCK = 1024
LOW_FFT, LOW_BLOCK = 1024, 256   # --low-latency: ~21 ms window, 5 ms blocks (coarser bass)
MIN_BEAT_GAP = 0.22              # s: no two beats closer than this (caps at ~210 bpm)


def find_loopback(p, device=None):
    if device is not None:
        return p.get_device_info_by_index(device)
    try:
        return p.get_default_wasapi_loopback()
    except Exception:
        for lb in p.get_loopback_device_info_generator():
            return lb
    raise SystemExit("no WASAPI loopback device found")


def list_devices():
    p = pa.PyAudio()
    for lb in p.get_loopback_device_info_generator():
        print(f"{lb['index']:3d}  {lb['name']}  {int(lb['defaultSampleRate'])} Hz")
    p.terminate()


class Analyzer:
    def __init__(self, device=None, gain=1.0, low_latency=False):
        self.device, self.gain = device, gain
        self.fft, self.block = (LOW_FFT, LOW_BLOCK) if low_latency else (FFT, BLOCK)
        k = self.block / BLOCK                              # all per-update decays below were tuned for 1024-sample blocks
        self._d_peak, self._d_rms, self._d_bass = 0.9985 ** k, 0.995 ** k, 0.998 ** k
        self._d_fall, self._d_level = 0.82 ** k, 0.9 ** k
        self.bands = [0.0] * BANDS
        self.level = self.bass = 0.0
        self.beat_count = 0
        self.last_beat = 0.0
        self.bpm = 120.0
        self._last_data = 0.0
        self._ring = np.zeros(self.fft, dtype=np.float32)
        self._bass_peak = 1e-6
        self._rms_peak = 1e-4
        self._bpeak = np.full(BANDS, 1e-4)
        self._peak = 1e-4                                  # running max for auto-gain
        self._bass_hist = collections.deque(maxlen=max(8, int(1.0 * 48000 / self.block)))     # ~1 s of bass energy
        self._beats = collections.deque(maxlen=10)
        self._lock = threading.Lock()

    # ---- capture ----
    def start(self):
        self._pa = pa.PyAudio()
        info = find_loopback(self._pa, self.device)
        self.rate, channels = int(info["defaultSampleRate"]), int(info["maxInputChannels"])
        self.name = info["name"]
        self.setup(self.rate, channels)
        self._stream = self._pa.open(format=pa.paFloat32, channels=channels, rate=self.rate, input=True,
                                     input_device_index=info["index"], frames_per_buffer=self.block,
                                     stream_callback=self._callback)
        return self

    def setup(self, rate, channels):
        self.rate, self._channels = rate, channels
        edges = np.geomspace(FMIN, FMAX, BANDS + 1)
        freqs = np.fft.rfftfreq(self.fft, 1 / self.rate)
        self._edges = [np.where((freqs >= edges[k]) & (freqs < edges[k + 1]))[0] for k in range(BANDS)]
        for k, idx in enumerate(self._edges):              # make sure every band has at least one bin
            if len(idx) == 0:
                self._edges[k] = np.array([int(np.searchsorted(freqs, edges[k]))])
        self._win = np.hanning(self.fft).astype(np.float32)
        self._bass_bins = np.where((freqs >= 40) & (freqs < 220))[0]

    def stop(self):
        self._stream.stop_stream()
        self._stream.close()
        self._pa.terminate()

    def _callback(self, data, frames, info, status):
        mono = np.frombuffer(data, dtype=np.float32).reshape(-1, self._channels).mean(axis=1)
        self.process(mono, time.monotonic())
        return None, pa.paContinue

    def process(self, mono, now):
        """Analyse one block of mono samples; `now` is its time in seconds (testable offline)."""
        n = len(mono)
        self._ring = np.concatenate((self._ring[n:], mono)) if n < self.fft else mono[-self.fft:]
        self._last_data = now
        mag = np.abs(np.fft.rfft(self._ring * self._win)) / self.fft * self.gain
        raw = np.array([mag[idx].max() for idx in self._edges])
        self._peak = max(self._peak * self._d_peak, float(raw.max()), 1e-4)          # loudest band, forgets in ~15 s
        # each band against its own recent maximum (music is much louder in the low-mids than the treble),
        # but never below a small fraction of the overall peak so silence doesn't blow up
        self._bpeak = np.maximum(np.maximum(self._bpeak * self._d_peak, raw), self._peak * 0.03)
        norm = np.clip(raw / self._bpeak, 0, 1) ** 1.5
        rms = float(np.sqrt((mono ** 2).mean()))
        self._rms_peak = max(self._rms_peak * self._d_rms, rms, 1e-4)                  # ~4 s memory
        with self._lock:
            self.bands = [max(float(v), old * self._d_fall) for v, old in zip(norm, self.bands)]   # fast attack, slow fall
            self.level = max(min(1.0, (rms / self._rms_peak) ** 2), self.level * self._d_level)
            bass = float(mag[self._bass_bins].mean())
            self._bass_peak = max(self._bass_peak * self._d_bass, bass, 1e-6)           # the bass's own loudness scale
            self.bass = min(1.0, bass / self._bass_peak)
            avg = sum(self._bass_hist) / len(self._bass_hist) if self._bass_hist else 0
            look = max(1, round(0.021 * self.rate / self.block))       # compare with ~21 ms ago, whatever the block size
            prev = self._bass_hist[-look] if len(self._bass_hist) >= look else 0
            self._bass_hist.append(bass)
            # a kick: well above the last second's average, still rising, and loud enough to matter
            if (bass > avg * 1.25 and bass > prev * 1.03 and bass > self._bass_peak * 0.30
                    and now - self.last_beat > MIN_BEAT_GAP):
                self.last_beat = now
                self.beat_count += 1
                self._beats.append(now)
                self._update_bpm()

    def _update_bpm(self):
        beats = list(self._beats)
        if len(beats) < 5:
            return
        votes = []                                          # beat period candidates, missed beats divided out
        for i in range(len(beats)):
            for j in range(i + 1, len(beats)):
                for k in (1, 2, 3, 4):
                    period = (beats[j] - beats[i]) / k
                    if 0.35 <= period <= 0.75:              # 80-170 bpm
                        votes.append(period)
        if votes:
            votes.sort()
            self.bpm = self.bpm * 0.6 + (60.0 / votes[len(votes) // 2]) * 0.4   # settle gently

    # ---- reading ----
    def snapshot(self):
        now = time.monotonic()
        silent = now - self._last_data > 0.15              # loopback sends nothing during silence
        with self._lock:
            if silent:
                self.bands = [v * 0.9 for v in self.bands]
                self.level *= 0.9
                self.bass *= 0.9
            return SimpleNamespace(bands=list(self.bands), level=self.level, bass=self.bass,
                                   beat_age=now - self.last_beat, beat_count=self.beat_count,
                                   bpm=self.bpm, silent=silent)
