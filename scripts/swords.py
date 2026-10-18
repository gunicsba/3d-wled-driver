"""Two laser swords duelling, over DDP. Loops forever.

    python scripts/swords.py
    python scripts/swords.py --bout 1.4 --blade-width 3

A blue saber enters from the lower left, a red one from the lower right. Each
round they wind up, strike, clash at a new spot (white-hot flash + flying
sparks), recoil and go again. Positions are in the map's cm.
Host, LED count, map and boundaries live in scripts/config.py.
"""
import math
import random

import common

BLUE, RED = (20, 70, 255), (255, 15, 10)
WINDUP, STRIKE = 0.95, 0.22          # s within a bout; the rest is clash + recoil
SPARKS = 9


def smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def seg_dist(px, py, ax, ay, bx, by):
    """Distance from point p to segment a-b."""
    dx, dy = bx - ax, by - ay
    k = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - k * dx, py - ay - k * dy)


def build(leds, args):
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    w, h = xmax - xmin, ymax - ymin
    pivots = ((xmin - 0.25 * w, ymin + 0.22 * h), (xmax + 0.25 * w, ymin + 0.22 * h))  # blue, red
    core_r, glow_r = args.blade_width / 2, args.blade_width * 2.2
    bout = args.bout
    bg = common.background(args)

    def params(n):                                    # deterministic per round
        rng = random.Random(n * 7919 + 13)
        cx = (xmin + xmax) / 2 + rng.uniform(-0.15, 0.15) * w
        cy = ymin + rng.uniform(0.42, 0.80) * h
        offs = (math.radians(rng.uniform(16, 34)), math.radians(rng.uniform(16, 34)))
        sparks = [(rng.uniform(0, 6.283), rng.uniform(60, 170)) for _ in range(SPARKS)]
        return (cx, cy), offs, sparks

    def render(t):
        n, local = int(t // bout), t % bout
        (cx, cy), offs, sparks = params(n)
        _, prev_offs, _ = params(n - 1)
        frame = common.new_frame(args)

        blades = []
        for side in (0, 1):
            px, py = pivots[side]
            base = math.atan2(cy - py, cx - px)
            length = math.hypot(cx - px, cy - py) + 14       # tip pokes past the clash point
            off0, rec_start = offs[side], 0.3 * prev_offs[side]
            sway = math.radians(5) * math.sin(local * 9 + side * 2)
            if local < WINDUP:                                # raise the blade back, sway
                off = rec_start + (off0 - rec_start) * smooth(local / (WINDUP * 0.8)) + sway * (1 - smooth(local / WINDUP))
            elif local < WINDUP + STRIKE:                     # fast strike, accelerating
                off = off0 * (1 - ((local - WINDUP) / STRIKE) ** 2.5)
            else:                                             # recoil + a little vibration
                c = (local - WINDUP - STRIKE) / (bout - WINDUP - STRIKE)
                off = 0.3 * off0 * smooth(c * 1.6) + math.radians(2.5) * math.sin(local * 70) * (1 - smooth(c * 3))
            ang = base + off if side == 0 else base - off     # blue swings away counter-clockwise
            blades.append((px, py, px + length * math.cos(ang), py + length * math.sin(ang)))

        clash = local - (WINDUP + STRIKE)                     # seconds since contact (negative before)
        flash = math.exp(-clash * 7) if clash >= 0 else 0.0

        for l in leds:
            i = l.i
            if i >= args.leds:
                continue
            r, g, b = (float(v) for v in bg)
            for (ax, ay, bx, by), colour in zip(blades, (BLUE, RED)):
                d = seg_dist(l.xcm, l.ycm, ax, ay, bx, by)
                if d < glow_r:
                    k = (1 - d / glow_r) ** 2 * (0.92 + 0.08 * math.sin(t * 40 + ax))   # humming glow
                    core = 1.0 if d < core_r else 0.0
                    r += colour[0] * k + 160 * core
                    g += colour[1] * k + 160 * core
                    b += colour[2] * k + 160 * core
            if clash >= 0:                                    # flash at the contact point
                d = math.hypot(l.xcm - cx, l.ycm - cy)
                disc = max(0.0, 1 - d / (28 + 40 * smooth(clash / 0.15))) * flash
                r += 255 * disc
                g += 235 * disc
                b += 190 * disc
                for sa, sv in sparks:                         # sparks fly out and cool
                    life = clash / 0.55
                    if life < 1:
                        sx, sy = cx + math.cos(sa) * sv * clash, cy + math.sin(sa) * sv * clash
                        sd = math.hypot(l.xcm - sx, l.ycm - sy)
                        if sd < 7:
                            kk = (1 - sd / 7) * (1 - life)
                            r += 255 * kk
                            g += (220 - 150 * life) * kk
                            b += 60 * kk * (1 - life)
            frame[i * 3:i * 3 + 3] = bytes((min(255, int(r)), min(255, int(g)), min(255, int(b))))
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--bout", type=float, default=1.8, help="seconds per round (wind-up, strike, clash)")
    ap.add_argument("--blade-width", type=float, default=4.0, help="bright core width in cm")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
