"""A line that sweeps across the map (up by default, any angle), streamed over DDP.

    python scripts/sweep.py
    python scripts/sweep.py --color ff0000 --width 0.08 --period 4
    python scripts/sweep.py --rainbow --mode bounce
    python scripts/sweep.py --angle 45        (a diagonal line moving up and to the right)

Host, LED count, map and boundaries live in scripts/config.py.
"""
import colorsys
import math

import common


def build(leds, args):
    bg = common.background(args)
    r, g, b = common.parse_colour(args.color)
    # where each LED is along the direction the line travels: 0 = up, 90 = to the right, 180 = down, -90 = to the left
    a = math.radians(args.angle)
    along = {l.i: l.xcm * math.sin(a) + l.ycm * math.cos(a) for l in leds}
    lo, hi = min(along.values()), max(along.values())
    along = {i: (v - lo) / ((hi - lo) or 1.0) for i, v in along.items()}

    def render(t):
        phase = (t / args.period) % 1.0
        pos = phase if args.mode == "up" else 1 - phase if args.mode == "down" else 1 - abs(2 * phase - 1)
        line = pos * (1 + 2 * args.width) - args.width  # run past both ends so the line fully leaves
        frame = common.new_frame(args)
        for idx, h in along.items():
            if idx >= args.leds:
                continue
            k = max(0.0, 1 - abs(h - line) / args.width)  # soft-edged triangle
            if k <= 0:
                continue
            if args.xmas:
                c = common.xmas_colour(h * 2)
            elif args.rainbow:
                c = tuple(int(255 * v) for v in colorsys.hsv_to_rgb(h, 1, 1))
            else:
                c = (r, g, b)
            frame[idx * 3:idx * 3 + 3] = bytes(int(v * k + b0 * (1 - k)) for v, b0 in zip(c, bg))
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--color", default="ffffff", help="colour name or RRGGBB (ignored with --rainbow or --xmas)")
    ap.add_argument("--angle", type=float, default=0.0,
                    help="direction the line travels in degrees: 0 up, 90 right, 180 down, -90 left, 45 diagonal")
    ap.add_argument("--xmas", action="store_true", help="Christmas colours (red, green, warm white, gold) along the line's way")
    ap.add_argument("--rainbow", action="store_true", help="colour follows the line's height")
    ap.add_argument("--width", type=float, default=0.06, help="half thickness of the line, 0-1 of the height")
    ap.add_argument("--period", type=float, default=5.0, help="seconds per sweep")
    ap.add_argument("--mode", choices=["up", "down", "bounce"], default="up")
    args = ap.parse_args()

    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
