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


def build(leds, args):
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    cx, S = (xmin + xmax) / 2, (ymax - ymin) / 200.0      # S: scale from the 200 cm design
    beat = 60.0 / args.bpm
    bg = common.background(args)

    def scene(x, y, t):
        """Colour of the picture at (x, y) in cm."""
        ph = 2 * math.pi * t / beat
        x, y = (x - cx) / S, (y - ymin) / S               # design units: centre x = 0, floor y = 0
        bob = 3 * abs(math.sin(ph / 2))                    # bounce on each beat
        sway = 5 * math.sin(ph / 2)                        # shoulders and head swing side to side
        pom = -8 * math.sin(ph / 2 - 0.7)                  # pompom lags behind

        # background: soft colour pulse on the beat
        pulse = 0.5 + 0.5 * math.cos(ph)
        r, g, b = (int(255 * v) for v in colorsys.hsv_to_rgb((t * 0.07) % 1, 1, 0.025 + 0.06 * pulse))
        c = tuple(min(255, v + base) for v, base in zip((r, g, b), bg))

        def paint(sd, col):
            nonlocal c
            a = max(0.0, min(1.0, 0.5 - sd / (SOFT / S)))
            if a:
                c = tuple(int(p * (1 - a) + q * a) for p, q in zip(c, col))

        # legs: one foot kicks up while the other stays down, alternating
        for side in (-1, 1):
            kick = max(0.0, math.sin(ph / 2 + (0 if side < 0 else math.pi)))
            fx = side * (9 + 8 * kick)
            fy = 8 + 22 * kick + bob * 0.5
            hip = (side * 7 + sway * 0.3, 72 + bob)
            paint(capsule(x, y, hip[0], hip[1], fx, fy + 12, 7), RED)
            paint(capsule(x, y, fx - side * 4, fy, fx + side * 9, fy, 7), BLACK)   # boot
        # coat: wide body with a belly, white hem and front stripe
        sx = sway * 0.6
        paint(capsule(x, y, sx * 0.5, 82 + bob, sx, 118 + bob, 17), RED)
        paint(box(x, y, sx * 0.5, 74 + bob, 18, 3.5), WHITE)                        # hem
        paint(box(x, y, sx * 0.8, 100 + bob, 2, 20), WHITE)                         # front stripe
        paint(box(x, y, sx * 0.7, 96 + bob, 18, 4.5), BLACK)                        # belt
        paint(box(x, y, sx * 0.7, 96 + bob, 5.5, 6), GOLD)                          # buckle
        # arms wave up and down, alternating
        for side in (-1, 1):
            a = math.radians(-10 + 120 * (0.5 + 0.5 * math.sin(ph + (0 if side > 0 else math.pi))))
            ang = a if side > 0 else math.pi - a
            sh = (side * 15 + sx, 120 + bob)
            hand = (sh[0] + 28 * math.cos(ang), sh[1] + 28 * math.sin(ang))
            paint(capsule(x, y, sh[0], sh[1], hand[0], hand[1], 7), RED)
            paint(circle(x, y, hand[0], hand[1], 7.5), WHITE)                       # mitten
        # head, beard, hat
        hx, hy = sway * 0.9, 148 + bob
        paint(circle(x, y, hx, hy, 11), SKIN)
        paint(circle(x, y, hx - 3, hy + 3, 2.2), BLACK)                             # eyes
        paint(circle(x, y, hx + 3, hy + 3, 2.2), BLACK)
        paint(circle(x, y, hx, hy - 0.5, 3), PINK)                                  # nose
        paint(circle(x, y, hx, hy - 9, 12), WHITE)                                  # beard
        paint(circle(x, y, hx - 9, hy - 2, 4.5), WHITE)
        paint(circle(x, y, hx + 9, hy - 2, 4.5), WHITE)
        tip = (hx + 12 + pom * 0.4, hy + 36)
        paint(triangle(x, y, (hx - 12, hy + 10), (hx + 12, hy + 10), tip), RED)
        paint(box(x, y, hx, hy + 10.5, 13, 3.5), WHITE)                             # hat brim
        paint(circle(x, y, tip[0], tip[1] - 2, 6), WHITE)                           # pompom
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
    ap.add_argument("--bpm", type=float, default=100, help="dance tempo (beats per minute)")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
