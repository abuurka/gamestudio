#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the starter template's background music: blender/gamestudio/project_templates/starter/assets/music/theme.ogg.

  python tools/make_music.py              # write it
  python tools/make_music.py --check      # exit 1 if the file is not what this script makes
  python tools/make_music.py --out DIR    # write it somewhere else
  python tools/make_music.py --blender PATH   # the Blender to encode with (default: $BLENDER, PATH, install folders)

The music: a cheerful 16-bar loop for a "collect the gems on a lawn" game, synthesised here with the
standard library (no recordings or samples, so no licences). 128 BPM with a half-time beat, in C major
(Cmaj7 Am7 Fmaj7 G | Cmaj7 Am7 Dm7 G | Fmaj7 G Em7 Am7 | Fmaj7 G Dm7 G7, back to Cmaj7): a soft
vibraphone-like melody with a ping-pong echo, a warm detuned pad, a round bass, harp chops on the offbeats
from bar 5 and rolling harp eighths from bar 9, a glockenspiel doubling the last four bars, a soft kick,
a clap from bar 5 and a shaker, all in a small reverb. No square or saw waves: every tone is a few
decaying sine partials. 30.0 s, stereo, 48 kHz (the rate the engine decodes at before the first click, so
no resampling touches the seam).

The loop is seamless: every note, echo and reverb tail is mixed into a circular buffer, so whatever rings
past bar 16 sounds at the start of bar 1, exactly as when the file loops, and the reverb is warmed up on
the loop's last seconds. Nothing fades. The mix is normalised to -14 LUFS (BS.1770 K-weighting with
gating), with peaks kept under -1.4 dBFS by a soft limiter. Noise is seeded, so the PCM is the same on
every run.

Encoding: Ogg Vorbis (96 kbit/s nominal) through the FFmpeg inside Blender 5.2+ (`aud.Sound.write` in a
background Blender with a scratch profile under .dev/tmp/, as tools/blender_dev.py sets one up), so nothing
is installed. The file is read back and checked: 48 kHz stereo, exactly 1,440,000 frames (the loop length,
so it loops in time), its peak, and the step across the seam.

--check makes the PCM and a fresh encoding in a temporary folder under .dev/tmp/ and compares them with the
file: the same rate, channels and frame count, a size within 15 % of the fresh encoding, and the decoded
file matching the PCM (its levels below 250 Hz and from 250 Hz to 2 kHz within 0.3 dB in every half
second, and its waveform at least 12 dB SNR in every quarter second). It does not compare bytes: Vorbis
files are not bit-identical across Blender versions (another FFmpeg/libvorbis encodes the same PCM to other
bytes), so "up to date" means the same notes, length, levels below 2 kHz and roughly the same size; a small
change to a quiet, high part (the shaker, say) can pass, so regenerate after changing the music.
"""

import argparse
import array
import functools
import io
import itertools
import json
import math
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
import wave

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "blender" / "gamestudio" / "project_templates" / "starter" / "assets" / "music"
NAME = "theme.ogg"
SCRATCH = REPO / ".dev" / "tmp"  # gitignored; never the system temp folder
RATE = 48000
BPM = 128
BEAT = RATE * 60 // BPM  # 22500 samples: every note starts on a whole sample
BARS = 16
LENGTH = BARS * 4 * BEAT  # 1,440,000 frames: 30.0 s
LOUDNESS = -14.0  # LUFS
CEILING = 0.85  # sample peak, of full scale (-1.4 dBFS: Vorbis overshoots a little)
BITRATE = 96000
# --check tolerances. A fresh encoding measures 0.13 dB and 20 dB; the file before a 1.2 dB change to
# the bass, 0.95 dB and 18 dB: the band levels catch mix changes, the SNR different notes.
SIZE_TOLERANCE = 0.15  # the file's size against a fresh encoding
MAX_BAND_DB = 0.3  # level below 250 Hz and 250 Hz-2 kHz in every 0.5 s, decoded file against the PCM
MIN_WINDOW_SNR = 12.0  # dB, waveform of every 0.25 s, decoded file against the PCM
TAU = 2 * math.pi


# --- The score --------------------------------------------------------------------------------

PROGRESSION = "Cmaj7 Am7 Fmaj7 G Cmaj7 Am7 Dm7 G Fmaj7 G Em7 Am7 Fmaj7 G Dm7 G7".split()
VOICINGS = {  # the pad and the harp, around middle C
    "Cmaj7": "C4 E4 G4 B4", "Am7": "C4 E4 G4 A4", "Fmaj7": "C4 E4 F4 A4", "G": "B3 D4 G4 B4",
    "Dm7": "C4 D4 F4 A4", "Em7": "B3 D4 E4 G4", "G7": "B3 D4 F4 G4",
}
ROOTS = {"Cmaj7": "C2", "Am7": "A1", "Fmaj7": "F2", "G": "G2", "Dm7": "D2", "Em7": "E2", "G7": "G2"}
# One bar per string: note:beats, "-" rests.
MELODY = [
    "E5:1 G5:1 C6:1.5 B5:.5", "A5:1 G5:1 E5:2", "F5:1 A5:1 C6:1 A5:1", "G5:3 -:1",
    "E5:1 G5:1 C6:1 D6:1", "E6:1.5 D6:.5 C6:1 A5:1", "F5:1 A5:1 G5:1 F5:1", "D5:1 G5:1 B5:2",
    "A5:1.5 G5:.5 F5:1 A5:1", "G5:1.5 F5:.5 D5:1 G5:1", "E5:1 G5:1 B5:1 D6:1", "C6:3 -:1",
    "A5:1 C6:1 E6:1.5 D6:.5", "D6:1 B5:1 G5:2", "A5:1.5 G5:.5 F5:1 E5:1", "D5:2 B4:1 D5:1",
]
BASS_LAST_BAR = "G2:1.5 G2:1 F2:.5 D2:1"  # walks down into the loop's first C
ROLL = (0, 1, 2, 3, 4, 3, 2, 1)  # harp eighths: indexes into the voicing, 4 = its lowest note an octave up
SHAKER = (0.4, 0.8, 0.5, 0.8, 0.4, 0.8, 0.5, 0.9)  # eighths, offbeats leaning
STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def midi(name: str) -> int:
    return 12 * (int(name[-1]) + 1) + STEPS[name[0]] + name[1:-1].count("#") - name[1:-1].count("b")


def hz(note: int) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def bar_notes(text: str) -> list[tuple[float, float, int | None]]:
    """(beat, beats, midi note or None for a rest) for one bar of note:beats."""
    out, beat = [], 0.0
    for token in text.split():
        name, beats = token.split(":")
        out.append((beat, float(beats), None if name == "-" else midi(name)))
        beat += float(beats)
    if abs(beat - 4) > 1e-9:
        raise ValueError(f"a bar of {beat} beats: {text}")
    return out


def bass_bar(chord: str) -> str:
    root = ROOTS[chord]
    fifth, octave = midi(root) + 7, midi(root) + 12
    return f"{root}:1.5 {root}:1 {_name(fifth)}:.5 {_name(octave)}:1"


def _name(note: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[note % 12]}{note // 12 - 1}"


# --- Instruments (lists of floats; mono unless they say otherwise) ----------------------------


def samples(seconds: float) -> int:
    return max(1, round(seconds * RATE))


def partials(freq: float, n: int, parts, phase: float = 0.0) -> list[float]:
    """Sum of sines (ratio, amplitude, decay per second); partials at or above Nyquist are left out."""
    out = [0.0] * n
    for ratio, amp, decay in parts:
        w = TAU * freq * ratio / RATE
        if w >= math.pi:
            continue
        g = math.exp(-decay / RATE)
        p = phase * ratio
        out = [o + amp * math.sin(w * k + p) * g ** k for k, o in enumerate(out)]
    return out


def shaped(signal: list[float], attack: int, hold: int, release: int) -> list[float]:
    """Raised-cosine attack over `attack` samples, then from `hold` a raised-cosine release to exactly 0."""
    out = signal[:hold + release]
    for i in range(min(attack, len(out))):
        out[i] *= 0.5 - 0.5 * math.cos(math.pi * i / attack)
    for i in range(hold, len(out)):
        out[i] *= 0.5 + 0.5 * math.cos(math.pi * (i - hold + 1) / release)
    return out


def lowpass(signal: list[float], cutoff: float) -> list[float]:
    """One-pole low-pass."""
    a, y, out = 1 - math.exp(-TAU * cutoff / RATE), 0.0, []
    for x in signal:
        y += a * (x - y)
        out.append(y)
    return out


def peak_normalised(signal: list[float]) -> list[float]:
    peak = max(abs(s) for s in signal) or 1.0
    return [s / peak for s in signal]


@functools.lru_cache(maxsize=None)
def vibes(note: int, beats: float) -> list[float]:
    """The melody: a soft mallet with a slow tremolo, damped after its written length."""
    hold, release = round(beats * BEAT), samples(0.35)
    tone = partials(hz(note), hold + release, ((1, 1.0, 1.6), (2, 0.16, 3.5), (3, 0.06, 6.0), (4, 0.12, 9.0),
                                              (9.2, 0.03, 30.0)))
    tremolo = [1 - 0.09 * (0.5 - 0.5 * math.cos(TAU * 4.8 * k / RATE)) for k in range(len(tone))]
    return shaped([s * t for s, t in zip(tone, tremolo)], samples(0.004), hold, release)


@functools.lru_cache(maxsize=None)
def harp(note: int, seconds: float) -> list[float]:
    """A plucked string: bright partials that die away fast, the fundamental slower."""
    n = samples(seconds)
    release = min(samples(0.08), n // 2)
    tone = partials(hz(note), n, ((1, 1.0, 3.5), (2, 0.5, 6.0), (3, 0.25, 9.0), (4, 0.14, 13.0), (5, 0.08, 18.0),
                                  (6, 0.04, 24.0), (8, 0.02, 34.0)))
    return shaped(tone, samples(0.0015), n - release, release)


@functools.lru_cache(maxsize=None)
def bass(note: int, beats: float) -> list[float]:
    """A round bass with a little brightness on the attack."""
    hold, release = round(beats * BEAT * 0.9), samples(0.05)
    tone = partials(hz(note), hold + release, ((1, 1.0, 1.2), (2, 0.4, 5.0), (3, 0.13, 9.0)))
    return shaped(tone, samples(0.006), hold, release)


PAD_HOLD, PAD_RELEASE = 4 * BEAT, samples(0.9)


@functools.lru_cache(maxsize=None)
def pad_voice(note: int, cents: int) -> list[float]:
    """One note of the pad, a bar and its release long (chords share notes, so each is made once)."""
    freq = hz(note) * 2 ** (cents / 1200)
    phase = random.Random(f"pad {note} {cents}").uniform(0, TAU)
    return partials(freq, PAD_HOLD + PAD_RELEASE, ((1, 1.0, 0.15), (2, 0.28, 0.4), (3, 0.1, 0.6), (4, 0.04, 0.8)),
                    phase)


@functools.lru_cache(maxsize=None)
def pad(chord: str) -> tuple[list[float], list[float]]:
    """A warm chord for one bar plus its release (left, right): each voice detuned 5 cents down on the
    left and up on the right, so it slowly moves between the speakers."""
    sides = []
    for cents in (-5, 5):
        mixed = [0.0] * (PAD_HOLD + PAD_RELEASE)
        for name in VOICINGS[chord].split():
            mixed = [m + v for m, v in zip(mixed, pad_voice(midi(name), cents))]
        sides.append(shaped(mixed, samples(0.3), PAD_HOLD, PAD_RELEASE))
    return sides[0], sides[1]


@functools.lru_cache(maxsize=None)
def glock(note: int) -> list[float]:
    """A glockenspiel: a bright bell with inharmonic partials, for the last four bars' sparkle."""
    n, release = samples(1.2), samples(0.1)
    tone = partials(hz(note), n, ((1, 1.0, 3.0), (2.76, 0.25, 9.0), (5.4, 0.08, 18.0)))
    return shaped(tone, samples(0.001), n - release, release)


@functools.lru_cache(maxsize=None)
def kick() -> list[float]:
    """A soft thump: a sine falling from about 165 Hz to 55 Hz."""
    n, phase, out = samples(0.32), 0.0, []
    for k in range(n):
        t = k / RATE
        phase += TAU * (55 + 110 * math.exp(-t / 0.03)) / RATE
        out.append((math.sin(phase) + 0.15 * math.sin(2 * phase) * math.exp(-t * 30)) * math.exp(-t * 10))
    return shaped(out, samples(0.0015), n - samples(0.03), samples(0.03))


@functools.lru_cache(maxsize=None)
def clap() -> list[float]:
    """A soft clap: three quick bursts of band-passed noise and a short tail."""
    rng = random.Random(5)
    noise = [rng.uniform(-1, 1) for _ in range(samples(0.3))]
    band = [a - b for a, b in zip(lowpass(noise, 3500), lowpass(noise, 1000))]
    env = []
    for k in range(len(band)):
        t = k / RATE
        bursts = sum(math.exp(-(t - s) * 350) for s in (0.0, 0.008, 0.016) if t >= s)
        env.append(bursts + 0.6 * math.exp(-t * 28))
    return peak_normalised(shaped([b * e for b, e in zip(band, env)], samples(0.001), len(band) - samples(0.03),
                                  samples(0.03)))


@functools.lru_cache(maxsize=None)
def shaker(variant: int) -> list[float]:
    """A shaker: high noise with a soft attack (four seeded variants, so repeats don't sound copied)."""
    rng = random.Random(100 + variant)
    noise = [rng.uniform(-1, 1) for _ in range(samples(0.11))]
    high = lowpass([a - b for a, b in zip(noise, lowpass(noise, 4000))], 11000)
    env = [math.exp(-k / RATE * 40) for k in range(len(high))]
    return peak_normalised(shaped([h * e for h, e in zip(high, env)], samples(0.006), len(high) - samples(0.015),
                                  samples(0.015)))


# --- Mixing into a circular buffer ------------------------------------------------------------


def zeros() -> array.array:
    return array.array("d", bytes(8 * LENGTH))


def add(buffer: array.array, start: int, signal: list[float], gain: float) -> None:
    """Add gain * signal into the loop from sample `start`; whatever runs past the end wraps to the start."""
    if not gain:
        return
    pos, k = start % LENGTH, 0
    while k < len(signal):
        take = min(len(signal) - k, LENGTH - pos)
        buffer[pos:pos + take] = array.array("d", [a + gain * b for a, b in
                                                    zip(buffer[pos:pos + take], signal[k:k + take])])
        k += take
        pos = 0


def delayed(buffer: array.array, delay: int) -> array.array:
    """The loop delayed by `delay` samples, circularly."""
    delay %= LENGTH
    return buffer[LENGTH - delay:] + buffer[:LENGTH - delay] if delay else array.array("d", buffer)


def pan(position: float) -> tuple[float, float]:
    """Constant-power gains for -1 (left) .. 1 (right)."""
    angle = (position + 1) * math.pi / 4
    return math.cos(angle), math.sin(angle)


class Mix:
    def __init__(self):
        self.left, self.right, self.send, self.melody = zeros(), zeros(), zeros(), zeros()

    def place(self, beat: float, signal: list[float], gain: float, position: float = 0.0, send: float = 0.0):
        start = round(beat * BEAT)
        gl, gr = pan(position)
        add(self.left, start, signal, gain * gl)
        add(self.right, start, signal, gain * gr)
        add(self.send, start, signal, gain * send)


def lowpass_loop(buffer: array.array, cutoff: float) -> array.array:
    """One-pole low-pass of the loop, warmed up on its last second so the seam is continuous."""
    a, y = 1 - math.exp(-TAU * cutoff / RATE), 0.0
    out = zeros()
    for i in itertools.chain(range(LENGTH - RATE, LENGTH), range(LENGTH)):
        y += a * (buffer[i] - y)
        out[i] = y
    return out


def reverb(send: array.array, combs: tuple[int, ...], allpasses: tuple[int, int], feedback: float = 0.8,
           damp: float = 0.3) -> array.array:
    """A small Freeverb-style room (four damped combs into two all-passes) for one channel, run over the
    loop's last 3 s first so the tail of bar 16 is already ringing at the start."""
    d1, d2, d3, d4 = combs
    b1, b2, b3, b4 = [0.0] * d1, [0.0] * d2, [0.0] * d3, [0.0] * d4
    i1 = i2 = i3 = i4 = 0
    s1 = s2 = s3 = s4 = 0.0
    e1, e2 = allpasses
    a1, a2 = [0.0] * e1, [0.0] * e2
    j1 = j2 = 0
    keep = 1 - damp
    out = zeros()
    for i in itertools.chain(range(LENGTH - 3 * RATE, LENGTH), range(LENGTH)):
        x = send[i]
        y1, y2, y3, y4 = b1[i1], b2[i2], b3[i3], b4[i4]
        s1 = y1 * keep + s1 * damp
        s2 = y2 * keep + s2 * damp
        s3 = y3 * keep + s3 * damp
        s4 = y4 * keep + s4 * damp
        b1[i1] = x + s1 * feedback
        b2[i2] = x + s2 * feedback
        b3[i3] = x + s3 * feedback
        b4[i4] = x + s4 * feedback
        i1 = i1 + 1 if i1 + 1 < d1 else 0
        i2 = i2 + 1 if i2 + 1 < d2 else 0
        i3 = i3 + 1 if i3 + 1 < d3 else 0
        i4 = i4 + 1 if i4 + 1 < d4 else 0
        y = y1 + y2 + y3 + y4
        z = a1[j1]
        a1[j1] = y + z * 0.5
        y = z - y
        j1 = j1 + 1 if j1 + 1 < e1 else 0
        z = a2[j2]
        a2[j2] = y + z * 0.5
        y = z - y
        j2 = j2 + 1 if j2 + 1 < e2 else 0
        out[i] = y
    return out


# --- The arrangement --------------------------------------------------------------------------


def arrange() -> tuple[array.array, array.array]:
    """The dry and wet loop, (left, right), before loudness and limiting."""
    mix = Mix()
    rng = random.Random(2026)  # small velocity differences, the same every run
    for bar, chord in enumerate(PROGRESSION):
        at = 4 * bar
        voicing = [midi(name) for name in VOICINGS[chord].split()]
        # Pad: one stereo chord per bar.
        left, right = pad(chord)
        add(mix.left, at * BEAT, left, 0.05)
        add(mix.right, at * BEAT, right, 0.05)
        add(mix.send, at * BEAT, [a + b for a, b in zip(left, right)], 0.012)
        # Bass.
        for beat, beats, note in bar_notes(BASS_LAST_BAR if bar == BARS - 1 else bass_bar(chord)):
            mix.place(at + beat, bass(note, beats), 0.26 if beat == 0 else 0.21)
        # Melody (mono; it gets an echo below).
        for beat, beats, note in bar_notes(MELODY[bar]):
            if note is not None:
                accent = 1.0 if beat in (0, 2) else 0.85
                add(mix.melody, round((at + beat) * BEAT), vibes(note, beats), 0.30 * accent * rng.uniform(0.94, 1.0))
                if bar >= 12:  # the last four bars: a glockenspiel an octave up
                    mix.place(at + beat, glock(note + 12), 0.07, 0.2, 0.4)
        # Harp: offbeat chops in bars 5-8, rolling eighths from bar 9.
        if 4 <= bar < 8:
            for eighth in (1, 3, 5, 7):
                for note in voicing[1:]:
                    mix.place(at + eighth / 2, harp(note, 0.22), 0.06 * rng.uniform(0.9, 1.0), 0.25, 0.5)
        elif bar >= 8:
            notes = voicing + [voicing[0] + 12]
            for eighth, index in enumerate(ROLL):
                position = -0.35 if eighth % 2 == 0 else 0.35
                accent = 1.0 if eighth % 2 == 0 else 0.8
                mix.place(at + eighth / 2, harp(notes[index], 0.7), 0.10 * accent * rng.uniform(0.9, 1.0),
                          position, 0.5)
        # Drums: a half-time kick and clap, shaker eighths.
        mix.place(at, kick(), 0.5)
        mix.place(at + 1.5, kick(), 0.22)
        if bar >= 4:
            mix.place(at + 2, clap(), 0.2, -0.1, 0.9)
        last = bar == BARS - 1
        for eighth, velocity in enumerate(SHAKER[:6] if last else SHAKER):
            level = 0.14 * velocity * (0.8 if bar < 4 else 1.0) * rng.uniform(0.85, 1.0)
            mix.place(at + eighth / 2, shaker((bar * 8 + eighth) % 4), level, 0.3, 0.2)
        if last:  # the last beat: a sixteenth-note shaker fill into the loop's start
            for i, velocity in enumerate((0.4, 0.55, 0.7, 0.9)):
                mix.place(at + 3 + i / 4, shaker(i), 0.14 * velocity, 0.3, 0.2)
    # Melody, its ping-pong echo (dotted eighths, low-passed) and its reverb send.
    for buffer in (mix.left, mix.right):
        add(buffer, 0, mix.melody, math.sqrt(0.5))
    add(mix.send, 0, mix.melody, 0.35)
    echo = lowpass_loop(mix.melody, 3000)
    for tap in range(1, 5):
        gl, gr = pan(0.7 if tap % 2 else -0.7)
        moved = delayed(echo, round(tap * 0.75 * BEAT))
        gain = 0.28 * 0.55 ** (tap - 1)
        add(mix.left, 0, moved, gain * gl)
        add(mix.right, 0, moved, gain * gr)
        add(mix.send, 0, moved, gain * 0.3)
    wet_left = reverb(mix.send, (1215, 1390, 1548, 1695), (605, 480))
    wet_right = reverb(mix.send, (1242, 1417, 1575, 1722), (632, 507))
    add(mix.left, 0, wet_left, 0.11)
    add(mix.right, 0, wet_right, 0.11)
    return mix.left, mix.right


# --- Loudness and the limiter -----------------------------------------------------------------


def _biquad(signal, b0, b1, b2, a0, a1, a2) -> list[float]:
    b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0
    x1 = x2 = y1 = y2 = 0.0
    out = []
    for x in signal:
        y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, x, y1, y
        out.append(y)
    return out


def k_weighted(signal) -> list[float]:
    """ITU-R BS.1770 K-weighting at RATE: the head's high shelf, then the RLB high-pass."""
    gain, q, fc = 3.99984385397, 0.7071752369554193, 1681.9744509555319
    a = 10 ** (gain / 40)
    w = TAU * fc / RATE
    alpha, cos = math.sin(w) / (2 * q), math.cos(w)
    shelf = _biquad(signal, a * ((a + 1) + (a - 1) * cos + 2 * math.sqrt(a) * alpha),
                    -2 * a * ((a - 1) + (a + 1) * cos), a * ((a + 1) + (a - 1) * cos - 2 * math.sqrt(a) * alpha),
                    (a + 1) - (a - 1) * cos + 2 * math.sqrt(a) * alpha, 2 * ((a - 1) - (a + 1) * cos),
                    (a + 1) - (a - 1) * cos - 2 * math.sqrt(a) * alpha)
    q, fc = 0.5003270373253953, 38.13547087613982
    w = TAU * fc / RATE
    alpha, cos = math.sin(w) / (2 * q), math.cos(w)
    return _biquad(shelf, (1 + cos) / 2, -(1 + cos), (1 + cos) / 2, 1 + alpha, -2 * cos, 1 - alpha)


def loudness(left, right) -> float:
    """Integrated loudness in LUFS (BS.1770-4: 400 ms blocks every 100 ms, absolute and relative gates)."""
    block, hop = samples(0.4), samples(0.1)
    sums = []
    for channel in (left, right):
        running, total = [0.0], 0.0
        for s in k_weighted(channel):
            total += s * s
            running.append(total)
        sums.append(running)
    powers = [sum(running[i + block] - running[i] for running in sums) / block
              for i in range(0, len(left) - block + 1, hop)]
    gated = [p for p in powers if p > 10 ** ((-70 + 0.691) / 10)]
    threshold = sum(gated) / len(gated) * 10 ** (-10 / 10)
    gated = [p for p in gated if p > threshold]
    return -0.691 + 10 * math.log10(sum(gated) / len(gated))


def limited(signal, gain: float) -> list[float]:
    """gain * signal through a soft limiter: unchanged up to 70 % of CEILING, then bending smoothly to
    stay under it."""
    knee = 0.7 * CEILING
    room = CEILING - knee
    out = []
    for s in signal:
        s *= gain
        if s > knee:
            s = knee + room * math.tanh((s - knee) / room)
        elif s < -knee:
            s = -knee - room * math.tanh((-s - knee) / room)
        out.append(s)
    return out


def master(left, right) -> tuple[list[float], list[float], dict]:
    """The loop without DC, at LOUDNESS, peaks under CEILING."""
    mean_left, mean_right = sum(left) / LENGTH, sum(right) / LENGTH
    left = [s - mean_left for s in left]
    right = [s - mean_right for s in right]
    level = loudness(left, right)
    gain = 10 ** ((LOUDNESS - level) / 20)
    for _ in range(3):  # the limiter takes a little loudness away: make it up
        out_left, out_right = limited(left, gain), limited(right, gain)
        level = loudness(out_left, out_right)
        if abs(level - LOUDNESS) < 0.05:
            break
        gain *= 10 ** ((LOUDNESS - level) / 20)
    raw_peak = gain * max(max(map(abs, left)), max(map(abs, right)))
    peak = max(max(map(abs, out_left)), max(map(abs, out_right)))
    return out_left, out_right, {"lufs": round(level, 2), "peak_dbfs": round(20 * math.log10(peak), 2),
                                 "limited_db": round(20 * math.log10(raw_peak / peak), 2)}


def render() -> tuple[bytes, dict]:
    """The loop as 16-bit stereo WAV bytes, and its loudness figures."""
    left, right, info = master(*arrange())
    frames = array.array("h", (round(32767 * s) for pair in zip(left, right) for s in pair))
    if sys.byteorder == "big":
        frames.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames.tobytes())
    return out.getvalue(), info


# --- Ogg Vorbis: the header (standard library) and encoding (Blender) --------------------------


def ogg_info(data: bytes) -> dict:
    """Rate, channels, nominal bitrate and frame count of an Ogg Vorbis file, from its headers alone: the
    identification header in the first page and the granule position of the last page."""
    pos, first, frames = 0, None, 0
    while pos < len(data):
        if data[pos:pos + 4] != b"OggS" or pos + 27 > len(data):
            raise ValueError(f"not an Ogg page at byte {pos}")
        segments = data[pos + 26]
        body = pos + 27 + segments
        first = data[body:body + 30] if first is None else first
        frames = int.from_bytes(data[pos + 6:pos + 14], "little")
        pos = body + sum(data[pos + 27:body])
    if first is None or first[:7] != b"\x01vorbis":
        raise ValueError("not Ogg Vorbis")
    channels, rate, nominal = first[11], int.from_bytes(first[12:16], "little"), int.from_bytes(first[20:24], "little")
    return {"rate": rate, "channels": channels, "frames": frames, "seconds": frames / rate,
            "nominal_bitrate": nominal, "bytes": len(data)}


def run_blender(blender: str | None, job: dict, work: pathlib.Path) -> dict:
    """Run `job` (see blender_job) in a background Blender with a scratch profile in `work`."""
    sys.path.insert(0, str(REPO / "tools"))
    import blender_dev

    exe = blender_dev.find_blender(blender)
    profile = work / "profile"
    report = work / "report.json"
    job = dict(job, report=str(report))
    cmd = [str(exe), "--background", "--factory-startup", "--python-exit-code", "1",
           "--python", str(pathlib.Path(__file__).resolve()), "--", "--blender-job", json.dumps(job)]
    done = subprocess.run(cmd, env=blender_dev.dev_env(profile), capture_output=True, text=True, errors="replace")
    if done.returncode or not report.is_file():
        sys.exit(f"Blender failed ({done.returncode}):\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}")
    return json.loads(report.read_text("utf-8"))


def blender_job(text: str) -> None:
    """Inside Blender: encode a WAV to Ogg Vorbis and/or decode Ogg files and measure them against the
    WAV: frames, peak, the step across the seam, the SNR of every 0.25 s and the level of every 0.5 s
    below 250 Hz and from 250 Hz to 2 kHz (where Vorbis keeps levels to about 0.1 dB)."""
    import aud
    import numpy as np

    job = json.loads(text)
    report = {}
    if job.get("encode"):
        wav, ogg = job["encode"]
        aud.Sound.file(wav).write(ogg, RATE, aud.CHANNELS_STEREO, aud.FORMAT_FLOAT32, aud.CONTAINER_OGG,
                                  aud.CODEC_VORBIS, BITRATE, 4096)
    reference = aud.Sound.file(job["wav"]).cache().data().astype(np.float64)

    def windows(x, seconds):
        size = round(RATE * seconds)
        usable = x.shape[0] // size * size
        return (x[:usable] ** 2).reshape(-1, size * x.shape[1]).sum(axis=1)

    def band_levels(x):
        spectrum, freqs = np.fft.rfft(x, axis=0), np.fft.rfftfreq(x.shape[0], 1 / RATE)
        return [windows(np.fft.irfft(np.where(((freqs >= lo) & (freqs < hi))[:, None], spectrum, 0), n=x.shape[0],
                                     axis=0), 0.5) for lo, hi in ((0, 250), (250, 2000))]
    reference_bands = band_levels(reference)
    for ogg in job["inspect"]:
        sound = aud.Sound.file(ogg)
        rate, channels = sound.specs
        data = sound.cache().data().astype(np.float64)
        steps = np.abs(np.diff(data, axis=0)).max(axis=1)
        info = {"rate": int(rate), "channels": int(channels), "frames": int(data.shape[0]),
                "peak_dbfs": float(20 * np.log10(np.abs(data).max())),
                "rms_dbfs": float(10 * np.log10(np.mean(data ** 2))),
                "seam_step": float(np.abs(data[0] - data[-1]).max()),
                "step_p99": float(np.percentile(steps, 99)), "step_max": float(steps.max())}
        if data.shape == reference.shape:
            snr = 10 * np.log10(windows(reference, 0.25) / np.maximum(windows(data - reference, 0.25), 1e-12))
            band_db = max(float(np.abs(10 * np.log10(np.maximum(got, 1e-9) / np.maximum(want, 1e-9))).max())
                          for got, want in zip(band_levels(data), reference_bands))
            info.update(snr_min=float(snr.min()), snr_median=float(np.median(snr)), band_db=band_db)
        report[ogg] = info
    pathlib.Path(job["report"]).write_text(json.dumps(report, indent=1), "utf-8")


# --- Main -------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=pathlib.Path, default=OUT, help=f"folder to write (default: {OUT})")
    parser.add_argument("--check", action="store_true", help="compare with the file instead of writing")
    parser.add_argument("--blender", help="the Blender 5.2+ to encode with")
    args = parser.parse_args()
    target = args.out / NAME
    if args.check:  # the quick checks first: is it there, and is it Ogg Vorbis of the loop's length?
        if not target.is_file():
            print(f"Missing: {target}")
            return 1
        try:
            header = ogg_info(target.read_bytes())
        except ValueError as error:
            print(f"Out of date: {target.name}: {error}")
            return 1
        if (header["rate"], header["channels"], header["frames"]) != (RATE, 2, LENGTH):
            print(f"Out of date: {target.name} is {header['frames']} frames at {header['rate']} Hz x "
                  f"{header['channels']}, not {LENGTH} at {RATE} Hz x 2")
            return 1
    wav, levels = render()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="make_music_", dir=SCRATCH) as tmp:
        work = pathlib.Path(tmp)
        (work / "theme.wav").write_bytes(wav)  # 5.5 MB, deleted with the folder
        fresh = work / NAME
        inspect = [str(fresh)] + ([str(target)] if args.check else [])
        wav_path = str(work / "theme.wav")
        job = {"encode": [wav_path, str(fresh)], "wav": wav_path, "inspect": inspect}
        report = run_blender(args.blender, job, work)
        made = report[str(fresh)]
        problems = _problems(made, "the new encoding")
        if args.check:
            info = report[str(target)]
            size, fresh_size = target.stat().st_size, fresh.stat().st_size
            problems += _problems(info, target.name)
            if abs(size - fresh_size) > SIZE_TOLERANCE * fresh_size:
                problems.append(f"{target.name} is {size // 1024} KB, a fresh encoding {fresh_size // 1024} KB")
            if "band_db" in info and (info["band_db"] > MAX_BAND_DB or info["snr_min"] < MIN_WINDOW_SNR):
                problems.append(f"{target.name} is not the music this script makes (levels off by up to "
                                f"{info['band_db']:.2f} dB, worst 0.25 s {info['snr_min']:.1f} dB SNR)")
            print("Out of date: " + "; ".join(problems) if problems else
                  f"{target} up to date ({info['frames'] / RATE:.2f} s, {size // 1024} KB; levels within "
                  f"{info['band_db']:.2f} dB, worst 0.25 s {info['snr_min']:.1f} dB SNR)")
            return 1 if problems else 0
        if problems:
            sys.exit("; ".join(problems))
        args.out.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fresh, target)
    size = target.stat().st_size
    print(f"{target}  {size / 1024:.0f} KB, {made['frames'] / RATE:.2f} s ({BARS} bars at {BPM} BPM), "
          f"{levels['lufs']} LUFS, peak {made['peak_dbfs']:.1f} dBFS decoded ({levels['limited_db']} dB limited); "
          f"seam step {made['seam_step']:.4f} (99th percentile step {made['step_p99']:.4f}); "
          f"levels within {made['band_db']:.2f} dB, worst 0.25 s {made['snr_min']:.1f} dB SNR")
    return 0


def _problems(info: dict, what: str) -> list[str]:
    problems = []
    if (info["rate"], info["channels"], info["frames"]) != (RATE, 2, LENGTH):
        problems.append(f"{what} is {info['frames']} frames at {info['rate']} Hz x {info['channels']}, "
                        f"not {LENGTH} at {RATE} Hz x 2 (it would not loop in time)")
    if info["seam_step"] > max(info["step_p99"] * 3, 0.02):
        problems.append(f"{what} clicks at the seam (step {info['seam_step']:.4f})")
    return problems


if __name__ == "__main__":
    if "--blender-job" in sys.argv:
        blender_job(sys.argv[sys.argv.index("--blender-job") + 1])
    else:
        sys.exit(main())
