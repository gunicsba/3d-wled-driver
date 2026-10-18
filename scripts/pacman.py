"""Pac-Man chomping, over DDP.

    python scripts/pacman.py                      # stays in the middle, mouth opening and closing
    python scripts/pacman.py --facing up --travel # runs up the curtain eating dots
    python scripts/pacman.py --radius 24 --chomp 3

Host, LED count, map and boundaries live in scripts/config.py.
"""
import math

import common

YELLOW, DOT = (255, 190, 0), (255, 255, 255)
MAX_MOUTH = math.radians(40)                      # half-angle of the widest mouth
FACING = {"right": 0, "up": 90, "left": 180, "down": 270}


def build(leds, args):
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    radius = args.radius or 0.47 * min(xmax - xmin, ymax - ymin)
    bg = common.background(args)
    face = math.radians(FACING[args.facing])
    fx, fy = math.cos(face), math.sin(face)
    # distance travelled along the facing direction, from just outside one edge to the other
    extent = max(abs(fx) * (xmax - xmin), abs(fy) * (ymax - ymin)) + 2 * radius
    spacing = 3.2 * radius                         # distance between dots

    def render(t):
        pac_x, pac_y = cx, cy
        shift = 0.0
        if args.travel:
            shift = (t * args.speed) % extent - extent / 2
            pac_x, pac_y = cx + fx * shift, cy + fy * shift
        mouth = MAX_MOUTH * abs(math.sin(math.pi * args.chomp * t))   # 0 = closed
        frame = common.new_frame(args)
        for l in leds:
            if l.i >= args.leds:
                continue
            dx, dy = l.xcm - pac_x, l.ycm - pac_y
            d = math.hypot(dx, dy)
            c = bg
            if d <= radius:
                off = abs((math.atan2(dy, dx) - face + math.pi) % (2 * math.pi) - math.pi)  # angle from facing
                if d < 3 or off > mouth:           # body (the mouth wedge stays dark)
                    c = YELLOW
                    ex, ey = math.cos(face + 1.05) * 0.5 * radius, math.sin(face + 1.05) * 0.5 * radius
                    if math.hypot(dx - ex, dy - ey) < 4:   # eye
                        c = (0, 0, 0)
            elif args.travel:                       # dots ahead of Pac-Man, eaten as he passes
                along = (l.xcm - cx) * fx + (l.ycm - cy) * fy
                across = abs((l.xcm - cx) * -fy + (l.ycm - cy) * fx)
                near = (along - spacing / 2) % spacing - spacing / 2
                if along > shift + radius and abs(near) < 5 and across < 6:
                    c = DOT
            frame[l.i * 3:l.i * 3 + 3] = bytes(c)
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--radius", type=float, default=0, help="Pac-Man's radius in cm (default: fits the curtain width)")
    ap.add_argument("--chomp", type=float, default=2.0, help="mouth openings per second")
    ap.add_argument("--facing", choices=list(FACING), default="right")
    ap.add_argument("--travel", action="store_true", help="run along the facing direction eating dots")
    ap.add_argument("--speed", type=float, default=45, help="travel speed in cm/s")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
