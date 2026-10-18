"""Bouncing shapes like the DVD logo, over DDP: random shapes and colours going around.

    python scripts/dvd.py
    python scripts/dvd.py --objects 5 --size 16 --speed 55
    python scripts/dvd.py --shapes circle,ring --no-morph --trail 0

Each shape drifts across the lit area and bounces off its edges. Every bounce picks a new random
colour (and a new random shape unless --no-morph), shapes spin slowly and leave a fading trail.
When a shape hits a corner exactly, the whole curtain flashes in its colour, like the DVD logo
famously does. Host, LED count, map, boundaries and background live in scripts/config.py.
"""
import argparse
import colorsys
import collections
import math
import random

import common
from shapes import blend, box, circle, diamond, ring, triangle

SHAPES = ("circle", "square", "triangle", "diamond", "ring", "cross")


def hue_colour(h):
    return tuple(int(255 * c) for c in colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0))


def distance(shape, dx, dy, r):
    """Signed distance to a shape of size r centred on the origin (dx, dy already rotated)."""
    if shape == "circle":
        return math.hypot(dx, dy) - r
    if shape == "square":
        return box(dx, dy, 0, 0, r * 0.85, r * 0.85)
    if shape == "triangle":
        return triangle(dx, dy, (0, r), (-r * 0.95, -r * 0.7), (r * 0.95, -r * 0.7))
    if shape == "diamond":
        return diamond(dx, dy, 0, 0, r * 1.15)
    if shape == "ring":
        return ring(dx, dy, 0, 0, r * 0.75, r * 0.25)
    return min(box(dx, dy, 0, 0, r, r * 0.34), box(dx, dy, 0, 0, r * 0.34, r))     # cross


def build(leds, args):
    rng = random.Random(args.seed)
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    r = args.size / 2
    kinds = [k for k in args.shapes.split(",") if k in SHAPES] or list(SHAPES)
    bg = common.background(args)
    state = {"last": 0.0, "flash_t": -9.0, "flash_hue": 0.0}

    def new_hue(old):
        h = rng.random()
        while abs((h - old + 0.5) % 1 - 0.5) < 0.2:                 # clearly different from the last colour
            h = rng.random()
        return h

    objs = []
    for _ in range(args.objects):
        ang = rng.uniform(0.3, 1.27) + rng.choice((0, math.pi / 2, math.pi, 3 * math.pi / 2))   # not along an axis
        sp = args.speed * rng.uniform(0.8, 1.2)
        objs.append({"x": rng.uniform(xmin + r, max(xmin + r, xmax - r)), "y": rng.uniform(ymin + r, max(ymin + r, ymax - r)),
                     "vx": sp * math.cos(ang), "vy": sp * math.sin(ang), "shape": rng.choice(kinds),
                     "hue": rng.random(), "spin": rng.choice((-1, 1)) * rng.uniform(0.5, 1.5) * (1 if args.spin else 0),
                     "angle": rng.random() * 6.28, "hit_x": -9.0, "hit_y": -9.0, "trail": collections.deque(maxlen=max(1, args.trail)),
                     "trail_t": 0.0})

    def bounce(o, t):
        hx = hy = False
        if o["x"] - r < xmin:
            o["x"], o["vx"], hx = 2 * (xmin + r) - o["x"], abs(o["vx"]), True
        elif o["x"] + r > xmax:
            o["x"], o["vx"], hx = 2 * (xmax - r) - o["x"], -abs(o["vx"]), True
        if o["y"] - r < ymin:
            o["y"], o["vy"], hy = 2 * (ymin + r) - o["y"], abs(o["vy"]), True
        elif o["y"] + r > ymax:
            o["y"], o["vy"], hy = 2 * (ymax - r) - o["y"], -abs(o["vy"]), True
        if hx:
            o["hit_x"] = t
        if hy:
            o["hit_y"] = t
        if hx or hy:
            o["hue"] = new_hue(o["hue"])
            if args.morph:
                o["shape"] = rng.choice(kinds)
            near_x = o["x"] - r - xmin < args.corner or xmax - (o["x"] + r) < args.corner     # touching a side wall too?
            near_y = o["y"] - r - ymin < args.corner or ymax - (o["y"] + r) < args.corner
            if near_x and near_y:                                      # right into a corner!
                state["flash_t"], state["flash_hue"] = t, o["hue"]
                print("CORNER!", flush=True)

    def render(t):
        dt, state["last"] = min(0.1, t - state["last"]), t
        for o in objs:
            o["x"] += o["vx"] * dt
            o["y"] += o["vy"] * dt
            o["angle"] += o["spin"] * dt
            bounce(o, t)
            if args.trail and t - o["trail_t"] > 0.05:
                o["trail_t"] = t
                o["trail"].append((o["x"], o["y"], o["angle"], o["shape"], o["hue"]))
        flash = max(0.0, 1 - (t - state["flash_t"]) / 0.9)           # corner flash fades out in 0.9 s
        frame = common.new_frame(args)
        soft = 5.0
        reach = r * 1.45 + soft                                      # beyond this no shape can touch the LED
        for l in leds:
            if l.i >= args.leds:
                continue
            c = bg
            if flash:
                fc = hue_colour(state["flash_hue"] + (t - state["flash_t"]) * 0.7)
                c = tuple(int(b0 + (f - b0) * flash * 0.55) for b0, f in zip(bg, fc))
            for o in objs:
                n = len(o["trail"])
                for k, (px, py, pa, sh, hu) in enumerate(o["trail"]):     # oldest first: fainter and smaller
                    dx, dy = l.xcm - px, l.ycm - py
                    if abs(dx) > reach or abs(dy) > reach:
                        continue
                    fade = (k + 1) / (n + 1) * 0.45
                    ca, sa = math.cos(-pa), math.sin(-pa)
                    c = blend(c, distance(sh, dx * ca - dy * sa, dx * sa + dy * ca, r * (0.6 + 0.4 * fade)),
                              tuple(int(v * fade) for v in hue_colour(hu)), soft)
                dx, dy = l.xcm - o["x"], l.ycm - o["y"]
                if abs(dx) > reach or abs(dy) > reach:
                    continue
                ca, sa = math.cos(-o["angle"]), math.sin(-o["angle"])
                c = blend(c, distance(o["shape"], dx * ca - dy * sa, dx * sa + dy * ca, r), hue_colour(o["hue"]), soft)
            frame[l.i * 3:l.i * 3 + 3] = bytes(c)
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--objects", type=int, default=3, help="how many shapes")
    ap.add_argument("--size", type=float, default=20, help="shape size (diameter) in cm")
    ap.add_argument("--speed", type=float, default=40, help="speed in cm/s")
    ap.add_argument("--shapes", default=",".join(SHAPES), help="which shapes, comma separated: " + ", ".join(SHAPES))
    ap.add_argument("--trail", type=int, default=6, help="length of the fading trail (0 = none)")
    ap.add_argument("--corner", type=float, default=0.5, help="how close to a corner counts as a corner hit, in cm")
    ap.add_argument("--morph", action=argparse.BooleanOptionalAction, default=True, help="new shape on every bounce")
    ap.add_argument("--spin", action=argparse.BooleanOptionalAction, default=True, help="shapes rotate slowly")
    ap.add_argument("--seed", type=int, default=None, help="random seed (default: different every run)")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
