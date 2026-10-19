"""Rainbow around a configurable centre, moving in time with a BPM, over DDP.

    python scripts/rainbow.py                          # rings flowing out of the middle, 120 bpm
    python scripts/rainbow.py --center 50%,20% --bpm 90
    python scripts/rainbow.py --center 30,150 --bands 80 --beats 2
    python scripts/rainbow.py --mode sweep --inward --pulse 0

--center takes cm ("30,100") or percent of the curtain ("50%,80%"; 0%,0% is the
bottom left). Modes: rings (concentric, flowing outwards) and sweep (a colour
wheel turning around the centre). The rainbow goes round once every --beats
beats, and the brightness gives a kick on every beat (--pulse 0 turns that off).
Host, LED count, map and boundaries live in scripts/config.py.
"""
import colorsys
import math

import common


def parse_center(text, bounds):
    xmin, xmax, ymin, ymax = bounds
    out = []
    for part, lo, hi in zip(text.split(","), (xmin, ymin), (xmax, ymax)):
        part = part.strip()
        out.append(lo + float(part[:-1]) / 100 * (hi - lo) if part.endswith("%") else float(part))
    if len(out) != 2:
        raise SystemExit("--center needs x,y")
    return out


def build(leds, args):
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    bounds = (min(xs), max(xs), min(ys), max(ys))
    cx, cy = parse_center(args.center, bounds)
    direction = 1 if args.inward else -1                # -1: rings move outwards
    pos = [(l.i, l.xcm - cx, l.ycm - cy) for l in leds]
    hues = []                                           # per-LED static part of the hue
    for i, dx, dy in pos:
        if args.mode == "rings":
            hues.append(math.hypot(dx, dy) / args.bands)
        else:
            hues.append(math.atan2(dy, dx) / (2 * math.pi))

    def render(t):
        beat = t * args.bpm / 60.0
        cycle = beat / args.beats                       # rainbows completed so far
        kick = 1 - args.pulse * (beat % 1.0)            # bright on the beat, easing off until the next
        frame = common.new_frame(args)
        for (i, _, _), h in zip(pos, hues):
            if i >= args.leds:
                continue
            if args.palette == "xmas":
                r, g, b = (v / 255 * kick for v in common.xmas_colour((h + direction * cycle) % 1.0))
            else:
                r, g, b = colorsys.hsv_to_rgb((h + direction * cycle) % 1.0, 1.0, kick)
            frame[i * 3:i * 3 + 3] = bytes((int(r * 255), int(g * 255), int(b * 255)))
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--center", default="50%,50%", help='centre: cm "x,y" or percent "50%%,50%%"')
    ap.add_argument("--bpm", type=float, default=120, help="beats per minute")
    ap.add_argument("--palette", choices=["rainbow", "xmas"], default="rainbow",
                    help="rainbow colours, or just the Christmas ones: red, green, warm white and gold")
    ap.add_argument("--beats", type=float, default=4, help="beats for the rainbow to go round once")
    ap.add_argument("--bands", type=float, default=70, help="rings mode: cm per full rainbow")
    ap.add_argument("--mode", choices=["rings", "sweep"], default="rings")
    ap.add_argument("--inward", action="store_true", help="flow towards the centre / spin the other way")
    ap.add_argument("--pulse", type=float, default=0.35, help="0-1: brightness kick on each beat")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
