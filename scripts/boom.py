"""A burning fuse that ends in a big red explosion, over DDP. Loops.

    python scripts/boom.py
    python scripts/boom.py --fuse 8 --radius 120 --center 30,100

1. Fuse: a sparkling spark runs along the LEDs in index order (the strip is the
   rope), leaving glowing embers that cool down behind it.
2. Boom: white flash, then a red shockwave ring expanding from --center (default:
   the middle of the curtain) with a glowing red disc behind it.
3. Fade out, a dark pause, and again.
Host, LED count, map and boundaries live in scripts/config.py.
"""
import math
import random

import common

FLASH = 0.12     # s, white flash when the fuse reaches the end
EXPAND = 1.6     # s, ring grows to full size
FADE = 1.4       # s, everything fades out
RING = 16.0      # cm, bright thickness of the ring's edge


def mix(a, b, k):
    return tuple(int(x * (1 - k) + y * k) for x, y in zip(a, b))


def scale(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def build(leds, args):
    rng = random.Random()
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    if args.center:
        cx, cy = (float(v) for v in args.center.split(","))
    else:
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    radius = args.radius or 1.05 * max(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys))
    order = sorted(leds, key=lambda l: l.i)             # fuse order = strip order
    rank = {l.i: n for n, l in enumerate(order)}
    dist = {l.i: math.hypot(l.xcm - cx, l.ycm - cy) for l in leds}
    total = args.fuse + FLASH + EXPAND + FADE + args.pause

    def render(t):
        ct = t % total
        frame = common.new_frame(args)

        def put(i, c):
            if i < args.leds:
                frame[i * 3:i * 3 + 3] = bytes(max(0, min(255, int(v))) for v in c)

        if ct < args.fuse:                              # ---- fuse burning
            head = ct / args.fuse * len(order)
            panic = 1.0 if args.fuse - ct > 0.4 else 2.5  # sizzles faster just before the boom
            for l in order:
                n = rank[l.i]
                if n > head + 0.5:                      # unburnt rope
                    c = (22, 12, 4)
                else:                                   # embers cooling behind the spark
                    age = max(0.0, head - n) / len(order) * args.fuse
                    heat = math.exp(-age * 1.6)
                    c = mix((10, 2, 0), (255, 70, 0), heat)
                    if rng.random() < 0.06 * heat * panic:  # crackle
                        c = (255, 200, 80)
                put(l.i, c)
            for n in range(int(head) - 1, int(head) + 3):   # the spark itself
                if 0 <= n < len(order):
                    k = max(0.0, 1 - abs(n - head) / 2.5) * (0.7 + 0.3 * rng.random())
                    put(order[n].i, mix((255, 120, 0), (255, 255, 200), k * k) if k > 0 else (0, 0, 0))
            return frame

        te = ct - args.fuse
        if te < FLASH:                                  # ---- flash
            put_all = scale((255, 255, 255), 1 - te / FLASH * 0.6)
            for l in order:
                put(l.i, put_all)
            return frame

        te -= FLASH
        fade = 1.0 if te < EXPAND else max(0.0, 1 - (te - EXPAND) / FADE)
        if te >= EXPAND + FADE:                         # ---- dark pause
            return frame
        grow = min(1.0, te / EXPAND)
        r = radius * (1 - (1 - grow) ** 3)              # fast out, slowing down
        for l in order:
            d = dist[l.i]
            if d > r:
                continue
            behind = r - d
            glow = 0.35 + 0.65 * max(0.0, 1 - behind / (0.55 * radius))  # brighter near the front
            c = scale((255, 0, 0), glow)
            if behind < RING:                           # hot edge: orange to white-yellow
                c = mix(c, (255, 170 if behind > RING / 2 else 230, 40), 1 - behind / RING)
            put(l.i, scale(c, fade))
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--fuse", type=float, default=6.0, help="seconds the fuse burns")
    ap.add_argument("--pause", type=float, default=1.5, help="dark seconds before it starts again")
    ap.add_argument("--radius", type=float, default=0, help="final ring radius in cm (default: covers the curtain)")
    ap.add_argument("--center", help="explosion centre as x,y in cm (default: middle of the curtain)")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
