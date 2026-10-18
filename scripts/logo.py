"""Spinning logo(s) over DDP: a picture sampled at each LED's position.

    python scripts/logo.py                         # AgOpenGPS logo, a few copies, spinning like coins
    python scripts/logo.py --mode plane            # rotating flat instead
    python scripts/logo.py --count 1 --period 3
    python scripts/logo.py --image assets/other.png

Needs Pillow (pip install pillow). Transparent pixels stay dark.
Host, LED count, map and boundaries live in scripts/config.py.
"""
import math
import os

try:
    from PIL import Image, ImageFilter
except ImportError:
    raise SystemExit("Pillow is missing: run  py -m pip install -r scripts/requirements.txt")

import common

DEFAULT_IMAGE = os.path.join(common.HERE, "..", "assets", "aog_ico.png")
SIZE = 128                                  # working resolution of the logo


def load_logo(path, bg):
    img = Image.open(path).convert("RGBA")
    flat = Image.new("RGBA", img.size, bg + (255,))
    img = Image.alpha_composite(flat, img)  # transparent -> background
    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    return img.filter(ImageFilter.GaussianBlur(2.5)).convert("RGB")  # LEDs are ~10 cm apart: soften


def build(leds, args):
    bg = common.background(args)
    img = load_logo(args.image, bg)
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    cx = (xmin + xmax) / 2
    size = args.size or 0.95 * (xmax - xmin)            # logo width in cm
    count = args.count or max(1, round((ymax - ymin) / (size * 1.15)))
    pitch = (ymax - ymin) / count
    centres = [ymin + pitch * (k + 0.5) for k in range(count)]

    def sample(u, v):                                   # u, v in -1..1, v up
        px = int((u + 1) / 2 * (SIZE - 1) + 0.5)
        py = int((1 - v) / 2 * (SIZE - 1) + 0.5)
        return img.getpixel((px, py))

    def render(t):
        frame = common.new_frame(args)
        for l in leds:
            if l.i >= args.leds:
                continue
            k = min(range(count), key=lambda n: abs(l.ycm - centres[n]))
            ang = 2 * math.pi * (t / args.period + k * args.offset)
            u, v = (l.xcm - cx) / (size / 2), (l.ycm - centres[k]) / (size / 2)
            shade = 1.0
            if args.mode == "axis":                     # turning around the vertical axis
                c = math.cos(ang)
                if abs(c) < 0.08:
                    u = 9                               # edge-on: nothing to see
                else:
                    u /= abs(c)                         # foreshortened
                    if c < 0:
                        u = -u                          # back side shows the mirror image
                    shade = 0.45 + 0.55 * abs(c)        # darker when turned away
            else:                                       # flat rotation
                u, v = u * math.cos(ang) + v * math.sin(ang), -u * math.sin(ang) + v * math.cos(ang)
            c = bg if abs(u) > 1 or abs(v) > 1 else tuple(int(a * shade) for a in sample(u, v))
            frame[l.i * 3:l.i * 3 + 3] = bytes(c)
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--image", default=DEFAULT_IMAGE, help="logo image (png with or without alpha)")
    ap.add_argument("--mode", choices=["axis", "plane"], default="axis", help="spin around the vertical axis, or flat")
    ap.add_argument("--period", type=float, default=4.0, help="seconds per full turn")
    ap.add_argument("--count", type=int, default=0, help="copies down the curtain (default: as many as fit)")
    ap.add_argument("--size", type=float, default=0, help="logo size in cm (default: nearly the curtain width)")
    ap.add_argument("--offset", type=float, default=0.18, help="phase shift between copies, in turns")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
