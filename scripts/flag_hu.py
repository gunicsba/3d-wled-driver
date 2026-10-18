"""The Hungarian flag blowing in the wind, over DDP.

    python scripts/flag_hu.py
    python scripts/flag_hu.py --wind 1.8 --amplitude 0.14
    python scripts/flag_hu.py --pole right

Red, white and green stripes (red on top) wave like cloth on a pole: the pole side
stays still, the free side sways more, light and dark folds travel along the flag and
the wind comes and goes in gusts.
Host, LED count, map and boundaries live in scripts/config.py.
"""
import common
import flags


def build(leds, args):
    pole_right = args.pole == "right"

    def render(t):
        frame = common.new_frame(args)
        for l in leds:
            if l.i < args.leds:
                x = 1 - l.x if pole_right else l.x
                c = flags.waving(x, l.h, t, args.amplitude, args.cycles, args.wind)
                frame[l.i * 3:l.i * 3 + 3] = bytes(c)
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--wind", type=float, default=1.0, help="wind speed: how fast the waves travel (0.5 breeze, 2 gale)")
    ap.add_argument("--amplitude", type=float, default=0.09, help="sway of the free edge, fraction of the height")
    ap.add_argument("--cycles", type=float, default=1.3, help="number of waves along the flag")
    ap.add_argument("--pole", choices=["left", "right"], default="left", help="which side the flag is attached to")
    args = ap.parse_args()
    common.run(args, build(common.leds_from_args(args), args))


if __name__ == "__main__":
    main()
