"""A tractor driving from left to right across the curtain, over DDP. Loops.

    python scripts/tractor.py
    python scripts/tractor.py --body red --wheels yellow --glass 88ccff
    python scripts/tractor.py --body 00aa44 --speed 40 --size 60 --ground 0.5

Colours: a name (see COLOURS) or RRGGBB. The default is a green tractor with
yellow wheels. The big wheels turn with the ground speed, the exhaust puffs
smoke and the road dashes scroll the other way.
Host, LED count, map and boundaries live in scripts/config.py.
"""
import math

import common
from shapes import blend, box, capsule, circle

colour = common.parse_colour
TYRE, EXHAUST, LIGHT = (60, 60, 60), (130, 130, 130), (255, 240, 150)


def dim(c, k):
    return tuple(int(v * k) for v in c)


def build(leds, args):
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    width = xmax - xmin
    unit = args.size / 100.0                              # cm per design unit (tractor is ~100 units long)
    body, wheels, glass, bg = colour(args.body), colour(args.wheels), colour(args.glass), common.background(args)
    ground = ymin + args.ground * (ymax - ymin)           # y of the road surface
    lap = width + args.size * 1.3 + args.pause * args.speed   # distance per pass incl. waiting off-screen
    soft = 5.0 / unit                                     # LEDs are ~10 cm apart: soften edges

    def scene(x, y, t):
        travelled = t * args.speed
        tx = xmin + (travelled % lap) - args.size * 0.65  # tractor's middle, starts off the left edge
        c = bg
        # road: a grey band with dashes scrolling the other way
        c = blend(c, box(x, y, (xmin + xmax) / 2, ground - 3, width, 3), (35, 35, 35), 5.0)
        if (x + travelled) % 24 < 11:
            c = blend(c, box(x, y, (xmin + xmax) / 2, ground - 3, width, 0.9), (200, 170, 40), 5.0)
        # tractor in its own coordinates (design units, origin = ground under the middle)
        u, v = (x - tx) / unit, (y - ground) / unit + 0.3 * math.sin(t * 40)   # engine rumble
        if abs(u) > 90 or v < -10 or v > 100:
            return c

        for k in range(5):                                # smoke from the exhaust, left behind as we drive
            age = (t * 1.3 + k / 5) % 1
            px = 30 - age * args.speed / unit * 0.9
            c = blend(c, circle(u, v, px, 60 + age * 22, 2 + age * 5), dim((150, 150, 150), 1 - age), soft)
        c = blend(c, capsule(u, v, 30, 41, 30, 58, 2.2), EXHAUST, soft)        # exhaust stack
        c = blend(c, box(u, v, 28, 30, 22, 11), body, soft)                    # hood
        c = blend(c, box(u, v, -5, 24, 40, 5), dim(body, 0.55), soft)          # chassis
        c = blend(c, box(u, v, -22, 48, 20, 20), body, soft)                   # cab
        c = blend(c, box(u, v, -22, 50, 14, 13), glass, soft)                  # window
        c = blend(c, box(u, v, -22, 70, 25, 3), dim(body, 0.7), soft)          # roof
        for cx_, r, hub in ((-28, 24, 12), (38, 13, 6)):                       # rear (big) and front wheel
            c = blend(c, circle(u, v, cx_, r, r), TYRE, soft)
            c = blend(c, circle(u, v, cx_, r, hub), wheels, soft)
            roll = travelled / unit / r                                        # rolling without slipping
            for s in range(4):                                                 # spokes
                a = roll + s * math.pi / 2
                c = blend(c, capsule(u, v, cx_, r, cx_ + (r - 3) * math.cos(a), r + (r - 3) * math.sin(a), 1.8),
                          wheels, soft)
            c = blend(c, circle(u, v, cx_, r, hub * 0.35), dim(wheels, 0.5), soft)
        c = blend(c, circle(u, v, 51, 33, 3.5), LIGHT, soft)                   # headlight
        return c

    def render(t):
        frame = common.new_frame(args)
        for l in leds:
            if l.i < args.leds:
                frame[l.i * 3:l.i * 3 + 3] = bytes(scene(l.xcm, l.ycm, t))
        return frame

    render.scene, render.bounds = scene, (xmin, xmax, ymin, ymax)
    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--body", default="green", help="tractor body colour (name or RRGGBB)")
    ap.add_argument("--wheels", default="yellow", help="wheel hub and spoke colour")
    ap.add_argument("--glass", default="9fd8ff", help="cab window colour")
    ap.add_argument("--speed", type=float, default=28, help="driving speed in cm/s")
    ap.add_argument("--size", type=float, default=52, help="tractor length in cm")
    ap.add_argument("--ground", type=float, default=0.18, help="road height, 0 (bottom) to 1 (top)")
    ap.add_argument("--pause", type=float, default=0.8, help="seconds off-screen between passes")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
