"""Coloured lines rush in from all directions and collide in a burst, over DDP. Loops.

    python scripts/collide.py
    python scripts/collide.py --lines 9 --approach 1.5 --center 30,100

1. Approach: a few comets in different colours race towards one point, each from its own direction,
   with a glowing tail behind its white-hot head.
2. Collision: a white flash at the meeting point, a ring of the colours blended by direction expands
   from it, and the comets shoot on out the other side.
3. Fade out, a short dark pause, and again from new directions.
Host, LED count, map and boundaries live in scripts/config.py.
"""
import colorsys
import math
import random

import common

FLASH = 0.18     # s, white flash when they meet
EXPAND = 1.4     # s, the ring grows to full size
FADE = 1.0       # s, everything fades out


def build(leds, args):
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    if args.center:
        cx, cy = (float(v) for v in args.center.split(","))
    else:
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    far = max(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys))
    pitch = common.pitch_of(leds)
    width = args.width or max(1.8 * pitch, 0.09 * far)             # half the thickness of a line, cm
    length = far * 1.1                                             # length of a comet's tail
    start = far + length                                           # how far out they begin, so they come in from off the picture
    speed = start / args.approach
    radius = 1.05 * far
    rel = [(l.i, l.xcm - cx, l.ycm - cy) for l in leds]
    angle_of = {i: math.atan2(dy, dx) for i, dx, dy in rel}
    total = args.approach + FLASH + EXPAND + FADE + args.pause
    cycles = {}

    def comets(n):
        """The directions and colours of round n: spread round the circle with some jitter, new every round."""
        if n not in cycles:
            rng = random.Random(args.seed + n)
            base, hue0 = rng.random() * 2 * math.pi, rng.random()
            step = 2 * math.pi / args.lines
            cycles.clear()
            cycles[n] = [(base + k * step + rng.uniform(-0.35, 0.35) * step,
                          common.XMAS[k % len(common.XMAS)] if args.xmas
                          else tuple(255 * v for v in colorsys.hsv_to_rgb((hue0 + k / args.lines) % 1.0, 1.0, 1.0)),
                          rng.uniform(-0.12, 0.12)) for k in range(args.lines)]     # direction, colour, arrives a bit early or late
        return cycles[n]

    def streak(a, b, head, c, out=False):
        """Brightness 0..1 and colour of an LED at (a along, b off the axis) of a comet whose head is at `head`."""
        if b >= width:
            return 0.0, c
        behind = (head - a) if out else (a - head)                 # distance behind the head
        if behind < -width or behind > length:
            return 0.0, c
        edge = 1 - b / width
        glow = max(0.0, 1 - behind / length) ** 1.6 * edge          # the tail fades away
        if behind < 0:                                             # the rounded front of the head
            glow *= max(0.0, 1 + behind / width)
        hot = max(0.0, 1 - max(behind, 0) / (0.18 * length)) * edge
        return glow, tuple(v * (1 - hot) + 255 * hot for v in c)

    def render(t):
        n, ct = int(t // total), t % total
        frame = common.new_frame(args)
        bg = tuple(frame[0:3]) if len(frame) >= 3 else (0, 0, 0)
        group = comets(n)

        def put(i, c):
            if i < args.leds:
                frame[i * 3:i * 3 + 3] = bytes(max(0, min(255, int(v))) for v in c)

        if ct < args.approach:                                     # ---- coming in
            for i, dx, dy in rel:
                best, col = 0.0, None
                for ang, rgb, early in group:
                    ux, uy = math.cos(ang), math.sin(ang)
                    head = start - speed * (ct - early * args.approach)
                    g, c = streak(dx * ux + dy * uy, abs(dx * uy - dy * ux), max(head, 0.0), rgb)
                    if g > best:
                        best, col = g, c
                if col is not None:
                    put(i, tuple(b + (v - b) * best for v, b in zip(col, bg)))
            return frame

        te = ct - args.approach
        if te >= FLASH + EXPAND + FADE:                            # ---- dark pause
            return frame
        fade = 1.0 if te < FLASH + EXPAND else max(0.0, 1 - (te - FLASH - EXPAND) / FADE)
        ring = radius * (1 - (1 - min(1.0, te / (FLASH + EXPAND))) ** 3)
        thick = max(2.5 * pitch, 0.12 * radius)
        for i, dx, dy in rel:
            d = math.hypot(dx, dy)
            c = bg
            if d <= ring:                                          # the ring, coloured by which comet is nearest in direction
                weights = [(max(0.0, math.cos(angle_of[i] - ang)) ** 3, rgb) for ang, rgb, _ in group]
                total_w = sum(w for w, _ in weights) or 1.0
                col = tuple(sum(w * rgb[k] for w, rgb in weights) / total_w for k in range(3))
                behind = ring - d
                glow = 0.08 + 0.92 * max(0.0, 1 - behind / (0.3 * radius))
                if behind < thick:                                 # hot edge
                    hot = 1 - behind / thick
                    col = tuple(v * (1 - 0.6 * hot) + 255 * 0.6 * hot for v in col)
                    glow = max(glow, 0.55 + 0.45 * hot)
                c = tuple(b + (v - b) * glow for v, b in zip(col, bg))
            for ang, rgb, _ in group:                              # the comets carry on out of the other side
                ux, uy = -math.cos(ang), -math.sin(ang)
                g, sc = streak(dx * ux + dy * uy, abs(dx * uy - dy * ux), speed * 1.5 * te, rgb, out=True)
                if g > 0:
                    c = tuple(max(a_, b_ * g + a_ * (1 - g)) for a_, b_ in zip(c, sc))
            if te < FLASH:                                         # the flash at the meeting point
                flash = max(0.0, 1 - d / (0.5 * radius * (0.3 + te / FLASH))) * (1 - 0.5 * te / FLASH)
                c = tuple(v * (1 - flash) + 255 * flash for v in c)
            put(i, tuple(b + (v - b) * fade for v, b in zip(c, bg)))
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--lines", type=int, default=6, help="how many coloured lines come in")
    ap.add_argument("--approach", type=float, default=2.2, help="seconds the lines take to arrive")
    ap.add_argument("--xmas", action="store_true", help="only Christmas colours: red, green, warm white and gold")
    ap.add_argument("--pause", type=float, default=0.8, help="dark seconds before the next round")
    ap.add_argument("--width", type=float, default=0, help="half the thickness of a line in cm (default: from the LED spacing)")
    ap.add_argument("--center", help="where they meet as x,y in cm (default: middle of the picture)")
    ap.add_argument("--seed", type=int, default=1, help="random seed: the same seed gives the same sequence of rounds")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
