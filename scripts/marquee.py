"""Running text over DDP: a message scrolls across the map. Loops.

    python scripts/marquee.py
    python scripts/marquee.py --text "HO HO HO" --color ff2020 --speed 0.3
    python scripts/marquee.py --rainbow --direction right --size 0.5 --y 0.3

The letters are drawn from a bold font, as big as you ask for (--size is their height as a part of the
picture), and every LED shows the piece of the text that passes over it. Long words need big gaps between
LEDs to read: keep the text short and the letters big.
Host, LED count, map and boundaries live in scripts/config.py.
"""
import colorsys
import os

import common

FONTS = ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arial.ttf")
FONT_PX = 96     # size of the letters in the bitmap they are drawn on (the picture is sampled from it)


def load_font(px):
    from PIL import ImageFont

    folders = [os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"), "/usr/share/fonts/truetype/dejavu",
               "/usr/share/fonts/truetype/liberation", "/Library/Fonts", "/System/Library/Fonts/Supplemental"]
    for name in FONTS:
        for folder in [""] + folders:
            try:
                return ImageFont.truetype(os.path.join(folder, name), px)
            except OSError:
                continue
    return ImageFont.load_default(px)                      # pillow's own font, if it has none of the above


def build(leds, args):
    from PIL import Image, ImageDraw, ImageFilter

    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    width, height = (xmax - xmin) or 1.0, (ymax - ymin) or 1.0
    pitch = common.pitch_of(leds)
    bg = common.background(args)
    r, g, b = common.parse_colour(args.color)

    # the text on a bitmap: baseline at `base`, capital letters FONT_PX tall
    font = load_font(FONT_PX)
    text = args.text or " "
    left, top, right, bottom = font.getbbox(text, anchor="ls")
    cap = -font.getbbox("H", anchor="ls")[1]                # height of a capital
    base = int(FONT_PX * 1.3)
    text_w = right - min(left, 0) + 8
    image = Image.new("L", (text_w, int(FONT_PX * 1.9)), 0)
    ImageDraw.Draw(image).text((8 - min(left, 0), base), text, fill=255, font=font, anchor="ls")
    cm_per_px = args.size * height / cap                    # how big the letters are on the map
    image = image.filter(ImageFilter.GaussianBlur(max(0.5, 0.45 * pitch / cm_per_px)))   # a sparse LED still catches a stroke
    pix = image.load()
    iw, ih = image.size
    text_cm = text_w * cm_per_px
    gap = args.gap * width
    travel = width + text_cm + gap                           # from fully off one side to fully off the other, plus a gap
    speed = args.speed * width                               # cm per second
    mid = base - cap / 2                                     # the middle of the capitals
    # where each LED sits in the bitmap, vertically (never changes)
    row = {l.i: mid + (ymin + args.y * height - l.ycm) / cm_per_px for l in leds}
    side = -1 if args.direction == "left" else 1

    def render(t):
        shift = (t * speed) % travel
        frame = common.new_frame(args)
        for l in leds:
            if l.i >= args.leds:
                continue
            # position along the text (cm from its start), for a text that enters on one side and leaves on the other
            if side < 0:                                     # runs left: its start comes in at the right edge
                along = (l.xcm - xmin) - (width - shift)
            else:                                            # runs right: its end comes in at the left edge
                along = (l.xcm - xmin) + text_cm - shift
            u, v = int(along / cm_per_px), int(round(row[l.i]))
            if not (0 <= u < iw and 0 <= v < ih):
                continue
            k = min(1.0, pix[u, v] / 255 * 1.8)
            if k <= 0.02:
                continue
            if args.xmas:
                c = common.xmas_colour((l.xcm - xmin) / width * 1.2 + t * 0.15)
            elif args.rainbow:
                c = tuple(int(255 * q) for q in colorsys.hsv_to_rgb(((l.xcm - xmin) / width * 0.8 + t * 0.15) % 1.0, 1, 1))
            else:
                c = (r, g, b)
            frame[l.i * 3:l.i * 3 + 3] = bytes(int(q * k + b0 * (1 - k)) for q, b0 in zip(c, bg))
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--text", default="MERRY XMAS", help="what to show")
    ap.add_argument("--color", default="ff2020", help="colour name or RRGGBB (ignored with --rainbow or --xmas)")
    ap.add_argument("--xmas", action="store_true", help="Christmas colours (red, green, warm white, gold) drifting along the text")
    ap.add_argument("--rainbow", action="store_true", help="colours that drift along the text")
    ap.add_argument("--size", type=float, default=0.5, help="height of the capital letters as a part of the picture height")
    ap.add_argument("--y", type=float, default=0.5, help="where the text sits: 0 bottom, 1 top")
    ap.add_argument("--speed", type=float, default=0.25, help="picture widths per second")
    ap.add_argument("--gap", type=float, default=0.3, help="empty space between two rounds, as a part of the picture width")
    ap.add_argument("--direction", choices=["left", "right"], default="left", help="which way the text runs")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
