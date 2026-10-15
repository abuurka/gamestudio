#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the starter template's sound effects: blender/gamestudio/project_templates/starter/assets/sfx/.

  python tools/make_sfx.py              # write them
  python tools/make_sfx.py --check      # exit 1 if a file differs from what this script makes
  python tools/make_sfx.py --out DIR    # write them somewhere else

Every sound is synthesised here with the standard library (no recordings, so no licences): 16-bit
mono WAV at 22050 Hz like the genre templates' sounds, normalised to the same peak, without DC, and
faded in and out so neither end clicks. Noise is seeded, so the script writes the same files each run.
Genre templates get these through their base template (the starter).
"""

import argparse
import io
import math
import pathlib
import random
import struct
import sys
import wave

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "blender" / "gamestudio" / "project_templates" / "starter" / "assets" / "sfx"
RATE = 22050
PEAK = 0.85  # of full scale, as the genre templates' sounds
TAU = 2 * math.pi


# --- Building blocks (lists of floats, -1..1) -----------------------------------------------


def samples(seconds: float) -> int:
    return max(1, round(seconds * RATE))


def oscillator(freq, seconds: float, shape: str = "sine") -> list[float]:
    """A tone; freq is Hz or a function of the time in seconds (a sweep keeps its phase)."""
    out, phase = [], 0.0
    for i in range(samples(seconds)):
        f = freq(i / RATE) if callable(freq) else freq
        phase = (phase + TAU * f / RATE) % TAU
        s = math.sin(phase)
        if shape == "soft":  # a rounded square: brighter than a sine, without its harshness
            s = math.tanh(2.5 * s) / math.tanh(2.5)
        elif shape == "triangle":
            s = 2 / math.pi * math.asin(s)
        elif shape == "bell":  # a sine with a quiet octave and twelfth
            s = 0.8 * s + 0.15 * math.sin(2 * phase) + 0.05 * math.sin(3 * phase)
        out.append(s)
    return out


def noise(seconds: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.uniform(-1, 1) for _ in range(samples(seconds))]


def lowpass(signal: list[float], cutoff) -> list[float]:
    """One-pole low-pass; cutoff is Hz or a function of the time in seconds."""
    out, y = [], 0.0
    for i, x in enumerate(signal):
        f = cutoff(i / RATE) if callable(cutoff) else cutoff
        a = 1 - math.exp(-TAU * f / RATE)
        y += a * (x - y)
        out.append(y)
    return out


def envelope(signal: list[float], attack: float = 0.004, decay: float = 8.0, release: float = 0.01) -> list[float]:
    """Linear attack, exponential decay (per second), raised-cosine release to exactly zero."""
    n, a, r = len(signal), samples(attack), samples(release)
    out = []
    for i, x in enumerate(signal):
        gain = min(1.0, i / a) * math.exp(-decay * i / RATE)
        if i >= n - r:
            gain *= 0.5 + 0.5 * math.cos(math.pi * (i - (n - r)) / r)
        out.append(x * gain)
    return out


def note(freq, seconds: float, shape: str = "bell", decay: float = 8.0, volume: float = 1.0) -> list[float]:
    return [volume * s for s in envelope(oscillator(freq, seconds, shape), decay=decay)]


def mix(length: float, *parts: tuple[float, list[float]]) -> list[float]:
    """Sum (start seconds, samples) parts into one buffer of the given length."""
    out = [0.0] * samples(length)
    for start, part in parts:
        offset = samples(start) if start else 0
        for i, s in enumerate(part[:len(out) - offset]):
            out[offset + i] += s
    return out


def finish(signal: list[float], fade_in: float = 0.002, fade_out: float = 0.02) -> list[float]:
    """Remove DC, fade both ends to exactly zero (no clicks), normalise to PEAK."""
    mean = sum(signal) / len(signal)
    signal = [s - mean for s in signal]
    n, a, r = len(signal), samples(fade_in), samples(fade_out)
    for i in range(n):
        if i < a:
            signal[i] *= 0.5 - 0.5 * math.cos(math.pi * i / a)
        if i >= n - r:
            signal[i] *= 0.5 + 0.5 * math.cos(math.pi * (i - (n - r) + 1) / r)
    peak = max(abs(s) for s in signal) or 1.0
    return [PEAK * s / peak for s in signal]


# --- The sounds -------------------------------------------------------------------------------

C5, E5, G5, C6, E6, G6, B6 = 523.25, 659.25, 783.99, 1046.50, 1318.51, 1567.98, 1975.53


def pickup() -> list[float]:
    """A bright two-note chime: a coin, a gem, any collectible."""
    return mix(0.3, (0, note(E6, 0.07, decay=10)), (0.06, note(B6, 0.24, decay=12)))


def jump() -> list[float]:
    """A short rising boing."""
    sweep = oscillator(lambda t: 260 + 2600 * t, 0.2, "soft")
    return envelope([0.7 * s for s in sweep], attack=0.003, decay=9, release=0.04)


def damage() -> list[float]:
    """Hit or hurt: a falling buzz with a soft thump at the start."""
    buzz = envelope(oscillator(lambda t: 420 * math.exp(-4.5 * t), 0.28, "soft"), decay=7, release=0.05)
    thump = envelope(lowpass(noise(0.08, seed=7), 900), attack=0.001, decay=40, release=0.02)
    return mix(0.28, (0, [0.6 * s for s in buzz]), (0, [1.4 * s for s in thump]))


def win() -> list[float]:
    """A rising major arpeggio that rings out on the octave."""
    steps = [(0.0, C5), (0.11, E5), (0.22, G5)]
    parts = [(start, note(f, 0.3, decay=6, volume=0.6)) for start, f in steps]
    chord = [note(f, 0.75, decay=3.2, volume=v) for f, v in ((C6, 0.6), (E6, 0.3), (G6, 0.25))]
    return mix(1.1, *parts, *((0.33, c) for c in chord))


def lose() -> list[float]:
    """Three falling notes, the last one sagging with a slow vibrato: game over or a lost life."""
    def sag(t):
        return 261.63 * (1 - 0.18 * t) * (1 + 0.012 * math.sin(TAU * 5.5 * t))

    return mix(1.0, (0.0, note(392.00, 0.26, "triangle", decay=4)),
               (0.22, note(311.13, 0.26, "triangle", decay=4)),
               (0.44, note(sag, 0.55, "triangle", decay=2.5)))


def click() -> list[float]:
    """A short, soft UI tick."""
    tick = envelope(oscillator(1800, 0.035), attack=0.0005, decay=120, release=0.008)
    grain = envelope(lowpass(noise(0.02, seed=3), 4000), attack=0.0005, decay=200, release=0.006)
    return mix(0.04, (0, tick), (0, [0.3 * s for s in grain]))


def whoosh() -> list[float]:
    """Air rushing past: dash, swing, throw or a fast pass."""
    seconds = 0.45
    air = lowpass(noise(seconds, seed=11), lambda t: 300 + 2500 * math.sin(math.pi * t / seconds) ** 2)
    air = lowpass(air, 3200)
    return [s * math.sin(math.pi * i / len(air)) ** 2 for i, s in enumerate(air)]


SOUNDS = {"pickup": pickup, "jump": jump, "damage": damage, "win": win, "lose": lose, "click": click,
          "whoosh": whoosh}


# --- Writing ----------------------------------------------------------------------------------


def wav_bytes(signal: list[float]) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(signal)}h", *(round(32767 * s) for s in signal)))
    return buffer.getvalue()


def render() -> dict[str, bytes]:
    """File name -> WAV bytes, for every sound."""
    return {f"{name}.wav": wav_bytes(finish(make())) for name, make in SOUNDS.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=pathlib.Path, default=OUT, help=f"folder to write (default: {OUT})")
    parser.add_argument("--check", action="store_true", help="compare with the files instead of writing")
    args = parser.parse_args()
    files = render()
    if args.check:
        stale = [name for name, data in files.items()
                 if not (args.out / name).is_file() or (args.out / name).read_bytes() != data]
        print(f"Out of date: {', '.join(stale)}" if stale else f"{len(files)} sounds up to date in {args.out}")
        return 1 if stale else 0
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (args.out / name).write_bytes(data)
        print(f"{args.out / name}  {len(data) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
