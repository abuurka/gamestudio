# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender half of runtime/scripts/stream_probe.mjs: plays a game URL through core/stream.py (the
real stream host and browser) in background Blender, and prints one JSON line of measurements.

    blender --background --factory-startup --python runtime/scripts/stream_probe_blender.py -- \
        URL PROJECT_ROOT OUT_DIR [SECONDS] [WIDTH] [HEIGHT]

The page is expected to toggle a 48x48 marker in its top-left corner, black <-> white, on F8 (the
probe's copy of the demo game does): the input round trip is the time from send_key to the first
decoded frame that shows the change. Decoded frames are saved as PNGs in OUT_DIR.
"""

import json
import os
import pathlib
import statistics
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "blender"))

from gamestudio.core import stream  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
URL, ROOT, OUT = argv[0], pathlib.Path(argv[1]), pathlib.Path(argv[2])
SECONDS = float(argv[3]) if len(argv) > 3 else 10.0
WIDTH = int(argv[4]) if len(argv) > 4 else 960
HEIGHT = int(argv[5]) if len(argv) > 5 else 540
TICK = 1 / 120  # the poll timer


def save_png(frame, path):
    import imbuf
    import numpy as np

    width, height, pixels = frame
    image = imbuf.new((width, height))
    with image.with_buffer(write=True) as buf:
        np.asarray(buf)[:] = np.frombuffer(pixels, np.uint8).reshape(height, width, 4)[::-1]
    image.file_type = "PNG"
    imbuf.write(image, filepath=str(path))


def marker(frame) -> str:
    """'white', 'black' or '?' at (24, 24) from the top left."""
    width, _height, pixels = frame
    i = (24 * width + 24) * 4
    r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
    if min(r, g, b) > 200:
        return "white"
    if max(r, g, b) < 60:
        return "black"
    return "?"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ok, why = stream.available(ROOT)
    if not ok:
        print(json.dumps({"error": why}))
        return
    statuses = []
    t_start = time.perf_counter()
    session = stream.start(URL, WIDTH, HEIGHT, project_root=ROOT,
                           on_status=lambda s: statuses.append((round(time.perf_counter() - t_start, 3), s.status, s.message)))
    polls, decode, latency = [], [], []
    first_frame = None
    last_id = 0

    def pump(until, stop_when=None):
        nonlocal first_frame, last_id
        while time.perf_counter() < until:
            t0 = time.perf_counter()
            session.poll()
            polls.append((time.perf_counter() - t0) * 1000)
            if session.frame_id != last_id:
                last_id = session.frame_id
                decode.append(session.decode_ms)
                latency.append(session.latency_ms)
                if first_frame is None:
                    first_frame = time.perf_counter() - t_start
                if stop_when is not None and stop_when(session.latest_frame()):
                    return True
            if session.status in ("error", "stopped"):
                return False
            time.sleep(TICK)
        return False

    # Wait for the game, then measure.
    deadline = time.perf_counter() + 60
    while session.status == "starting" and time.perf_counter() < deadline:
        pump(time.perf_counter() + 0.1)
    if session.status != "running":
        print(json.dumps({"error": f"{session.status}: {session.message}", "statuses": statuses}))
        session.stop(wait=True)
        return
    pump(time.perf_counter() + 1.5)  # let the first frames settle
    polls.clear(), decode.clear(), latency.clear()
    id0, t0 = session.frame_id, time.perf_counter()
    fps_samples = []
    end = t0 + SECONDS
    while time.perf_counter() < end:
        pump(min(end, time.perf_counter() + 1.0))
        fps_samples.append(session.fps)
    shown = (session.frame_id - id0) / (time.perf_counter() - t0)
    frame = session.latest_frame()
    save_png(frame, OUT / "blender-frame-1.png")

    # Input round trip: F8 toggles the marker; time until a decoded frame shows it.
    trips = []
    for i in range(8):
        before = marker(session.latest_frame())
        want = "white" if before != "white" else "black"
        sent = time.perf_counter()
        session.send_key("down", "F8", "F8")
        session.send_key("up", "F8", "F8")
        if pump(sent + 2.0, lambda f: marker(f) == want):
            trips.append((time.perf_counter() - sent) * 1000)
        if i == 0:
            save_png(session.latest_frame(), OUT / "blender-frame-marker.png")
        pump(time.perf_counter() + 0.15)

    # Resize and pause.
    session.resize(640, 360)
    resized = pump(time.perf_counter() + 3.0, lambda f: f[0] == 640 and f[1] == 360)
    save_png(session.latest_frame(), OUT / "blender-frame-640.png")
    session.set_paused(True)
    pump(time.perf_counter() + 0.5)
    paused_id = session.frame_id
    pump(time.perf_counter() + 1.0)
    paused_ok = session.frame_id == paused_id
    session.set_paused(False)
    resumed = pump(time.perf_counter() + 3.0, lambda f: True)

    pid = session.pid
    t_stop = time.perf_counter()
    session.stop(wait=True)
    stop_ms = (time.perf_counter() - t_stop) * 1000
    q = lambda xs, p: round(sorted(xs)[min(len(xs) - 1, int(len(xs) * p))], 2) if xs else None  # noqa: E731
    print(json.dumps({
        "mode": session.mode, "browser": session.browser, "renderer": session.renderer, "audio": session.audio,
        "first_frame_s": round(first_frame or 0, 2),
        "received_fps": round(statistics.mean(fps_samples), 1) if fps_samples else 0,
        "shown_fps": round(shown, 1),
        "decode_ms": {"mean": round(statistics.mean(decode), 2) if decode else None, "p90": q(decode, 0.9), "max": q(decode, 1.0)},
        "frame_latency_ms": {"p50": q(latency, 0.5), "p90": q(latency, 0.9)},
        "poll_ms": {"mean": round(statistics.mean(polls), 3) if polls else None, "p99": q(polls, 0.99), "max": q(polls, 1.0)},
        "input_round_trip_ms": {"n": len(trips), "p50": q(trips, 0.5), "max": q(trips, 1.0)},
        "resized": resized, "paused_ok": paused_ok, "resumed": resumed,
        "stop_ms": round(stop_ms), "host_alive_after_stop": session.alive,
        "host_pid": pid, "statuses": statuses[:6],
    }))


try:
    main()
finally:
    stream.stop_all(wait=True)
    sys.stdout.flush()
