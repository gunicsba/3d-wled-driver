"""Horizontal line that sweeps up through the curtain map, streamed over DDP.

    python scripts/sweep.py
    python scripts/sweep.py --color ff0000 --width 0.08 --period 4
    python scripts/sweep.py --rainbow --mode bounce

Host, LED count, map and boundaries live in scripts/config.py.
"""
import colorsys

import common


def build(leds, args):
    bg = common.background(args)
    r, g, b = (int(args.color[k:k + 2], 16) for k in (0, 2, 4))

    def render(t):
        phase = (t / args.period) % 1.0
        pos = phase if args.mode == "up" else 1 - phase if args.mode == "down" else 1 - abs(2 * phase - 1)
        line = pos * (1 + 2 * args.width) - args.width  # run past both ends so the line fully leaves
        frame = common.new_frame(args)
        for idx, h, *_ in leds:
            if idx >= args.leds:
                continue
            k = max(0.0, 1 - abs(h - line) / args.width)  # soft-edged triangle
            if k <= 0:
                continue
            c = tuple(int(255 * v) for v in colorsys.hsv_to_rgb(h, 1, 1)) if args.rainbow else (r, g, b)
            frame[idx * 3:idx * 3 + 3] = bytes(int(v * k + b0 * (1 - k)) for v, b0 in zip(c, bg))
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--color", default="ffffff", help="RRGGBB (ignored with --rainbow)")
    ap.add_argument("--rainbow", action="store_true", help="colour follows the line's height")
    ap.add_argument("--width", type=float, default=0.06, help="half thickness of the line, 0-1 of the height")
    ap.add_argument("--period", type=float, default=5.0, help="seconds per sweep")
    ap.add_argument("--mode", choices=["up", "down", "bounce"], default="up")
    args = ap.parse_args()

    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
