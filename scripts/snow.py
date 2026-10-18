"""Snowfall (hóesés) over DDP: flakes of different sizes drifting down, with an optional snow pile.

    python scripts/snow.py
    python scripts/snow.py --flakes 50 --wind 12
    python scripts/snow.py --pile 40 --speed 1.5     # deeper pile, faster snow
    python scripts/snow.py --pile 0                   # no pile

Big flakes are bright and fall faster, small ones are dim and slow, which gives some depth.
Each flake sways a little and twinkles. Snow settles at the bottom, up to --pile cm (25 by
default) over about a minute, then it melts away and starts again; --pile 0 turns it off.
Host, LED count, map, boundaries and background live in scripts/config.py.
"""
import math
import random

import common

SNOW = (225, 238, 255)                      # slightly blue-white


def build(leds, args):
    rng = random.Random(args.seed)
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    margin = 12.0
    fall = (ymax - ymin) + 2 * margin
    span = (xmax - xmin) + 2 * margin
    flakes = []
    for _ in range(args.flakes):
        depth = rng.random()                                # 0 far (small, dim, slow) .. 1 near (big, bright, fast)
        flakes.append({
            "x": rng.uniform(xmin - margin, xmax + margin),
            "y": rng.uniform(0, fall),
            "v": (8 + 28 * depth) * args.speed,             # cm/s
            "r": 3.5 + 5.5 * depth,                         # cm radius
            "b": 0.45 + 0.55 * depth,                       # brightness
            "sway": 3 + 6 * rng.random(),
            "phase": rng.random() * 6.283,
            "rate": 0.4 + 0.8 * rng.random(),
        })
    bg = common.background(args)

    def render(t):
        frame = common.new_frame(args)
        pos = []
        for f in flakes:
            y = ymax + margin - (f["y"] + t * f["v"]) % fall
            x = f["x"] + math.sin(t * f["rate"] + f["phase"]) * f["sway"] + t * args.wind * (0.4 + f["b"])
            x = xmin - margin + (x - (xmin - margin)) % span                     # wrap around the sides
            twinkle = 0.8 + 0.2 * math.sin(t * 5 + f["phase"] * 3)
            pos.append((x, y, f["r"], f["b"] * twinkle))
        pile = 0.0
        if args.pile > 0:                                                         # snow settles, then melts
            c = (t % 70) / 70
            pile = args.pile * (c / 0.8 if c < 0.8 else 1 - (c - 0.8) / 0.2)
        for l in leds:
            if l.i >= args.leds:
                continue
            k = 0.0
            for x, y, r, b in pos:
                d = math.hypot(l.xcm - x, l.ycm - y)
                if d < r:
                    k = max(k, (1 - d / r) ** 0.8 * b)
            if pile > 0:                                                          # lumpy surface, soft edge
                top = ymin + pile * (0.85 + 0.15 * math.sin(l.xcm * 0.25 + 1.0))
                k = max(k, max(0.0, min(1.0, (top - l.ycm) / 5 + 0.5)) * 0.9)
            if k > 0:
                frame[l.i * 3:l.i * 3 + 3] = bytes(int(c * k + b0 * (1 - k)) for c, b0 in zip(SNOW, bg))
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--flakes", type=int, default=30, help="number of snowflakes")
    ap.add_argument("--speed", type=float, default=1.0, help="fall speed multiplier")
    ap.add_argument("--wind", type=float, default=4.0, help="sideways drift in cm/s (negative = to the left)")
    ap.add_argument("--pile", type=float, default=25, help="snow pile height in cm at its highest (0 = no pile)")
    ap.add_argument("--seed", type=int, default=1, help="random seed: same seed, same snowfall")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
