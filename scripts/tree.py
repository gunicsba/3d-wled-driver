"""Green Christmas tree with falling snow and twinkling coloured baubles, over DDP.

    python scripts/tree.py
    python scripts/tree.py --snow 20 --baubles 16 --seed 3

The tree is a tiered triangle centred on the curtain, with a trunk and a star.
Host, LED count, map and boundaries live in scripts/config.py.
"""
import colorsys
import math
import random

import common

TRUNK = (150, 70, 0)
STAR = (255, 200, 0)
BAUBLE_HUES = (0.0, 0.08, 0.6, 0.83, 0.5, 0.15)  # red, orange, blue, magenta, cyan, yellow
FLAKE_RADIUS = 7.0           # cm


def build(leds, args):
    rng = random.Random(args.seed)
    bg = common.background(args)
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    cx, half_w, height = (xmin + xmax) / 2, (xmax - xmin) / 2, (ymax - ymin) or 1.0

    tree, trunk, star = {}, set(), set()
    for l in leds:
        h = l.h
        if h < 0.10:                                    # trunk
            if abs(l.xcm - cx) <= half_w * 0.18:
                trunk.add(l.i)
            continue
        th = (h - 0.10) / 0.90                           # 0 at the tree's base, 1 at the tip
        if th > 0.97:
            star.add(l.i)
            continue
        tier = (th * 4) % 1                              # four tiers, each wider at its bottom
        width = half_w * (1 - th * 0.92) * (0.72 + 0.28 * (1 - tier))
        if abs(l.xcm - cx) <= width:
            tree[l.i] = 0.55 + 0.45 * rng.random()       # per-LED green brightness

    baubles = {}
    candidates = [i for i, v in tree.items() if v]
    for i in rng.sample(candidates, min(args.baubles, len(candidates))):
        baubles[i] = (rng.choice(BAUBLE_HUES), rng.random() * 6.28, 0.6 + rng.random() * 1.4)

    flakes = [[rng.uniform(xmin, xmax), rng.uniform(ymin, ymax), rng.uniform(12, 35), rng.random() * 6.28]
              for _ in range(args.snow)]
    pos = {l.i: (l.xcm, l.ycm) for l in leds}
    last_t = [0.0]

    def render(t):
        dt, last_t[0] = t - last_t[0], t
        for f in flakes:                                 # fall, drift sideways, respawn on top
            f[1] -= f[2] * dt
            f[0] += math.sin(t * 0.8 + f[3]) * 6 * dt
            if f[1] < ymin - FLAKE_RADIUS:
                f[0], f[1], f[2] = rng.uniform(xmin, xmax), ymax + FLAKE_RADIUS, rng.uniform(12, 35)

        frame = common.new_frame(args)
        for l in leds:
            i = l.i
            if i >= args.leds:
                continue
            if i in tree:
                g = tree[i] * (0.92 + 0.08 * math.sin(t * 1.3 + i))
                c = (0, int(235 * g), int(28 * g))
            elif i in trunk:
                c = TRUNK
            elif i in star:
                k = 0.75 + 0.25 * math.sin(t * 3)
                c = tuple(int(v * k) for v in STAR)
            else:
                c = bg
            if i in baubles:                             # slow independent pulse, full colour
                hue, phase, rate = baubles[i]
                k = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * rate + phase))
                c = tuple(int(255 * v * k) for v in colorsys.hsv_to_rgb(hue, 1, 1))
            x, y = pos[i]
            snow = 0.0
            for fx, fy, *_ in flakes:
                d = math.hypot(x - fx, y - fy)
                if d < FLAKE_RADIUS:
                    snow = max(snow, 1 - d / FLAKE_RADIUS)
            if snow:
                c = tuple(min(255, int(v * (1 - snow) + 255 * snow)) for v in c)
            frame[i * 3:i * 3 + 3] = bytes(c)
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--snow", type=int, default=14, help="number of snowflakes")
    ap.add_argument("--baubles", type=int, default=14, help="number of coloured dots on the tree")
    ap.add_argument("--seed", type=int, default=1, help="random seed: same seed, same tree")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
