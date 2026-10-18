"""Flag drawing shared by the scripts (colours tuned for LEDs: saturated and bright)."""
import math

GREEN, WHITE, RED = (0, 170, 35), (235, 235, 235), (255, 12, 12)
EDGE = 0.035                                   # softness of the stripe edges (fraction of the height)


def smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def _mix(a, b, k):
    return tuple(a[i] + (b[i] - a[i]) * k for i in range(3))


def hungarian(h):
    """Hungarian tricolour, red on top: h runs from 0 (bottom) to 1 (top)."""
    c = _mix(GREEN, WHITE, smooth((h - 1 / 3) / EDGE + 0.5))
    return _mix(c, RED, smooth((h - 2 / 3) / EDGE + 0.5))


def waving(x, h, t, amp=0.09, cycles=1.3, speed=1.0, extra_disp=0.0, extra_shade=1.0):
    """Colour at a point of a Hungarian flag flying in the wind.

    x: 0 at the pole, 1 at the free edge; h: 0 bottom, 1 top. amp is the sway of the
    free edge as a fraction of the flag's height, cycles the number of waves along
    it and speed how fast they travel. extra_disp / extra_shade let a caller add its
    own ripples (shift the stripes, brighten or darken).
    """
    gust = 0.8 + 0.2 * math.sin(t * 0.37) + 0.12 * math.sin(t * 0.91 + 1.0)   # the wind comes and goes
    phase = 2 * math.pi * (cycles * x - speed * 0.6 * t) + 1.5 * h            # waves run towards the free edge
    disp = amp * gust * x * math.sin(phase) + extra_disp                      # the pole end stays put
    shade = 1 + 0.30 * gust * x * math.cos(phase)                             # folds: light on the slopes
    r, g, b = hungarian(h + disp)
    s = max(0.55, min(1.25, shade * extra_shade))
    return (min(255, int(r * s)), min(255, int(g * s)), min(255, int(b * s)))
