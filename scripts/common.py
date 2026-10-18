"""Shared helpers: load the LED map, add the standard CLI flags, stream frames over DDP."""
import argparse
import csv
import glob
import os
import socket
import time
from collections import namedtuple

import config

# i: LED index; h, x: position 0..1 (up / right); xcm, ycm: position in the map's units (cm)
Led = namedtuple("Led", "i h x xcm ycm")

COLOURS = {"red": (230, 20, 10), "green": (0, 170, 50), "blue": (20, 80, 255),
           "orange": (255, 110, 0), "yellow": (255, 200, 0), "purple": (150, 30, 220),
           "white": (255, 255, 255), "pink": (255, 70, 150), "cyan": (0, 200, 220), "black": (0, 0, 0)}


def parse_colour(text):
    """A name from COLOURS or RRGGBB."""
    text = text.lower().lstrip("#")
    if text in COLOURS:
        return COLOURS[text]
    try:
        return tuple(int(text[k:k + 2], 16) for k in (0, 2, 4))
    except ValueError:
        raise SystemExit(f"unknown colour '{text}': use a name ({', '.join(COLOURS)}) or RRGGBB")


def background(args):
    """The configured background colour at its brightness, as an (r, g, b) tuple."""
    return tuple(int(v * args.bg_brightness) for v in parse_colour(args.background))


def new_frame(args):
    """A frame with every LED (mapped or not) set to the background."""
    return bytearray(bytes(background(args)) * args.leds)


HERE = os.path.dirname(os.path.abspath(__file__))


def newest_map():
    """Your own map in maridata/ if there is one, otherwise the example map in maps/."""
    for folder in ("maridata", "maps"):
        base = os.path.join(HERE, "..", folder)
        # post-processed curtain maps (cm) win over raw MariMapper 2D maps
        for pattern in ("curtain_led_map*.csv", "led_map_2d_*.csv", "example_grid.csv"):
            files = sorted(glob.glob(os.path.join(base, pattern)))
            if files:
                return files[-1]
    raise SystemExit("no map CSV found in maridata/ or maps/; set MAP in scripts/config_local.py or pass --map")


def load_map(path, flip_v=False, skip_first=0, skip_last=0, min_y=None, max_y=None, flip_x=False):
    """Returns [Led(i, h, x, xcm, ycm)]; h and x are 0..1, 0 = lowest / leftmost kept LED.

    Reads x_cm/y_cm (y up) or u/v maps. LEDs outside the boundaries are dropped
    (they stay dark) and positions are normalised over the LEDs that remain. flip_x mirrors
    left and right (for a map that was captured from the back of the curtain).
    """
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    xc, yc = ("x_cm", "y_cm") if "y_cm" in rows[0] else ("u", "v")
    pts = [(int(r["index"]), float(r[xc]), float(r[yc])) for r in rows if r[yc] != ""]
    last_index = max(i for i, _, _ in pts)
    pts = [(i, x, y) for i, x, y in pts
           if skip_first <= i <= last_index - skip_last
           and (min_y is None or y >= min_y) and (max_y is None or y <= max_y)]
    if not pts:
        raise SystemExit("no LEDs left inside the boundaries")
    if flip_x:                                            # mirror x around the middle of the kept LEDs
        mid = min(x for _, x, _ in pts) + max(x for _, x, _ in pts)
        pts = [(i, mid - x, y) for i, x, y in pts]

    def norm(vals):
        lo, hi = min(vals), max(vals)
        return [(v - lo) / ((hi - lo) or 1.0) for v in vals]

    hs, xs = norm([y for _, _, y in pts]), norm([x for _, x, _ in pts])
    if flip_v:
        hs = [1 - h for h in hs]
    return [Led(i, h, x, xc_, yc_) for (i, xc_, yc_), h, x in zip(pts, hs, xs)]


def parser(description):
    """ArgumentParser with the common flags; defaults come from config.py."""
    ap = argparse.ArgumentParser(description=description,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--leds", type=int, default=config.LEDS, help="LED count on the module")
    ap.add_argument("--map", default=config.MAP, help="map CSV (default: newest curtain map)")
    ap.add_argument("--fps", type=float, default=config.FPS)
    ap.add_argument("--skip-first", type=int, default=config.SKIP_FIRST, help="leave the first N LEDs dark")
    ap.add_argument("--skip-last", type=int, default=config.SKIP_LAST, help="leave the last N LEDs dark")
    ap.add_argument("--min-y", type=float, default=config.MIN_Y, help="lowest height to use (cm)")
    ap.add_argument("--max-y", type=float, default=config.MAX_Y, help="highest height to use (cm)")
    ap.add_argument("--flip-v", action="store_true", default=config.FLIP_V, help="swap up/down")
    ap.add_argument("--flip-x", action=argparse.BooleanOptionalAction, default=config.FLIP_X,
                    help="mirror left/right so the picture matches what you see (--no-flip-x to turn off)")
    ap.add_argument("--background", default=config.BACKGROUND, help="background colour (name or RRGGBB)")
    ap.add_argument("--bg-brightness", type=float, default=config.BG_BRIGHTNESS, help="background brightness 0-1")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = forever)")
    return ap


def leds_from_args(args):
    path = args.map or newest_map()
    leds = load_map(path, args.flip_v, args.skip_first, args.skip_last, args.min_y, args.max_y, args.flip_x)
    print(f"{len(leds)} LEDs from {os.path.basename(path)} -> {args.host}:4048, Ctrl+C to stop")
    return leds


# Sender-side timing over the last full second (updated by run()): frames sent, longest wait between two
# sends, and the slowest render. Compare with the module's /status (rt_fps, rt_gap_ms) to see where a stall is.
STATS = {"fps": 0, "gap_ms": 0, "render_ms": 0}


def run(args, render):
    """Calls render(t) -> bytearray(leds*3) at args.fps and sends each frame over DDP.

    Ctrl+C stops; the module returns to its saved effect ~2.5 s later.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start, seq = time.time(), 0
    next_tick = time.perf_counter()
    win_start, win_n, win_gap, win_render, last_send = time.perf_counter(), 0, 0.0, 0.0, None
    try:
        while True:
            t = time.time() - start
            if args.duration and t > args.duration:
                break
            t_render = time.perf_counter()
            frame = render(t)
            win_render = max(win_render, (time.perf_counter() - t_render) * 1000)
            seq = seq % 15 + 1
            header = bytes((0x41, seq, 0x01, 0x01)) + (0).to_bytes(4, "big") + len(frame).to_bytes(2, "big")
            sock.sendto(header + bytes(frame), (args.host, 4048))
            now = time.perf_counter()
            if last_send is not None:
                win_gap = max(win_gap, (now - last_send) * 1000)
            last_send, win_n = now, win_n + 1
            if now - win_start >= 1.0:
                STATS.update(fps=win_n, gap_ms=int(win_gap), render_ms=int(win_render))
                win_start, win_n, win_gap, win_render = now, 0, 0.0, 0.0
            next_tick += 1 / args.fps                        # fixed schedule: a slow frame doesn't shift the rest
            delay = next_tick - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.25:                              # fell far behind (PC stalled): restart the schedule
                next_tick = time.perf_counter()
    except KeyboardInterrupt:
        pass
