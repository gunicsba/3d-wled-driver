"""Dancing Santa over DDP, drawn from simple shapes at each LED's position.

    python scripts/santa.py
    python scripts/santa.py --bpm 110

Santa bobs and sways to the beat, waves his arms alternately and kicks his legs;
the background pulses softly in changing colours on every beat. All sizes are
scaled from the curtain height (drawn for 200 cm).
Host, LED count, map and boundaries live in scripts/config.py.
"""
import colorsys
import math

import common
from shapes import box, capsule, circle, triangle

RED, WHITE, SKIN = (230, 0, 0), (255, 255, 255), (255, 150, 110)
BLACK, GOLD, PINK = (6, 6, 6), (255, 190, 0), (255, 90, 90)
SOFT = 5.0                       # cm: edge softness, LEDs are ~10 cm apart


def cap(ax, ay, bx, by, r, col, m):
    return capsule, (ax, ay, bx, by, r), col, (min(ax, bx) - r - m, max(ax, bx) + r + m, min(ay, by) - r - m, max(ay, by) + r + m)


def disc(cx, cy, r, col, m):
    return circle, (cx, cy, r), col, (cx - r - m, cx + r + m, cy - r - m, cy + r + m)


def rect(cx, cy, hw, hh, col, m):
    return box, (cx, cy, hw, hh), col, (cx - hw - m, cx + hw + m, cy - hh - m, cy + hh + m)


def tri(p0, p1, p2, col, m):
    return triangle, (p0, p1, p2), col, (min(p[0] for p in (p0, p1, p2)) - m, max(p[0] for p in (p0, p1, p2)) + m,
                                         min(p[1] for p in (p0, p1, p2)) - m, max(p[1] for p in (p0, p1, p2)) + m)


def figure(t, beat, m):
    """Santa at time t as shapes in paint order: (distance function, its arguments, colour, bounding box).

    Everything that depends on the dance is worked out here, once per frame, not once per LED.
    """
    ph = 2 * math.pi * t / beat
    bob = 3 * abs(math.sin(ph / 2))                    # bounce on each beat
    sway = 5 * math.sin(ph / 2)                        # shoulders and head swing side to side
    pom = -8 * math.sin(ph / 2 - 0.7)                  # pompom lags behind
    shapes = []

    # legs: one foot kicks up while the other stays down, alternating
    for side in (-1, 1):
        kick = max(0.0, math.sin(ph / 2 + (0 if side < 0 else math.pi)))
        fx = side * (9 + 8 * kick)
        fy = 8 + 22 * kick + bob * 0.5
        hip = (side * 7 + sway * 0.3, 72 + bob)
        shapes.append(cap(hip[0], hip[1], fx, fy + 12, 7, RED, m))
        shapes.append(cap(fx - side * 4, fy, fx + side * 9, fy, 7, BLACK, m))             # boot
    # coat: wide body with a belly, white hem and front stripe
    sx = sway * 0.6
    shapes.append(cap(sx * 0.5, 82 + bob, sx, 118 + bob, 17, RED, m))
    shapes.append(rect(sx * 0.5, 74 + bob, 18, 3.5, WHITE, m))                           # hem
    shapes.append(rect(sx * 0.8, 100 + bob, 2, 20, WHITE, m))                            # front stripe
    shapes.append(rect(sx * 0.7, 96 + bob, 18, 4.5, BLACK, m))                           # belt
    shapes.append(rect(sx * 0.7, 96 + bob, 5.5, 6, GOLD, m))                             # buckle
    # arms wave up and down, alternating
    for side in (-1, 1):
        a = math.radians(-10 + 120 * (0.5 + 0.5 * math.sin(ph + (0 if side > 0 else math.pi))))
        ang = a if side > 0 else math.pi - a
        sh = (side * 15 + sx, 120 + bob)
        hand = (sh[0] + 28 * math.cos(ang), sh[1] + 28 * math.sin(ang))
        shapes.append(cap(sh[0], sh[1], hand[0], hand[1], 7, RED, m))
        shapes.append(disc(hand[0], hand[1], 7.5, WHITE, m))                             # mitten
    # head, beard, hat
    hx, hy = sway * 0.9, 148 + bob
    shapes.append(disc(hx, hy, 11, SKIN, m))
    shapes.append(disc(hx - 3, hy + 3, 2.2, BLACK, m))                                   # eyes
    shapes.append(disc(hx + 3, hy + 3, 2.2, BLACK, m))
    shapes.append(disc(hx, hy - 0.5, 3, PINK, m))                                        # nose
    shapes.append(disc(hx, hy - 9, 12, WHITE, m))                                        # beard
    shapes.append(disc(hx - 9, hy - 2, 4.5, WHITE, m))
    shapes.append(disc(hx + 9, hy - 2, 4.5, WHITE, m))
    tip = (hx + 12 + pom * 0.4, hy + 36)
    shapes.append(tri((hx - 12, hy + 10), (hx + 12, hy + 10), tip, RED, m))
    shapes.append(rect(hx, hy + 10.5, 13, 3.5, WHITE, m))                                # hat brim
    shapes.append(disc(tip[0], tip[1] - 2, 6, WHITE, m))                                 # pompom
    return shapes


def build(leds, args):
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    cx, S = (xmin + xmax) / 2, (ymax - ymin) / 200.0      # S: scale from the 200 cm design
    beat = 60.0 / args.bpm
    bg = common.background(args)
    soft = SOFT / S                                       # softness in design units, which is also how far a shape reaches
    # where the LEDs are in design units (centre x = 0, floor y = 0): they never move
    placed = [(l.i, (l.xcm - cx) / S, (l.ycm - ymin) / S) for l in leds]

    def colour_at(x, y, base, shapes):
        c = base
        for sd_function, shape_args, col, (x0, x1, y0, y1) in shapes:
            if x < x0 or x > x1 or y < y0 or y > y1:      # far from this shape: nothing to paint
                continue
            a = max(0.0, min(1.0, 0.5 - sd_function(x, y, *shape_args) / soft))
            if a:
                c = tuple(int(p * (1 - a) + q * a) for p, q in zip(c, col))
        return c

    def background_at(t):
        pulse = 0.5 + 0.5 * math.cos(2 * math.pi * t / beat)             # soft colour pulse on the beat
        r, g, b = (int(255 * v) for v in colorsys.hsv_to_rgb((t * 0.07) % 1, 1, 0.025 + 0.06 * pulse))
        return tuple(min(255, v + base) for v, base in zip((r, g, b), bg))

    def scene(x, y, t):
        """Colour of the picture at (x, y) in cm."""
        return colour_at((x - cx) / S, (y - ymin) / S, background_at(t), figure(t, beat, soft))

    def render(t):
        frame = common.new_frame(args)
        base, shapes = background_at(t), figure(t, beat, soft)
        for i, x, y in placed:
            if i < args.leds:
                frame[i * 3:i * 3 + 3] = bytes(colour_at(x, y, base, shapes))
        return frame

    render.scene, render.bounds = scene, (xmin, xmax, ymin, ymax)
    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--bpm", type=float, default=100, help="dance tempo (beats per minute)")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
