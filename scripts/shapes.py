"""Signed-distance shapes (negative inside, in the caller's units) for drawing pictures per LED."""
import math


def circle(x, y, cx, cy, r):
    return math.hypot(x - cx, y - cy) - r


def capsule(x, y, ax, ay, bx, by, r):
    dx, dy = bx - ax, by - ay
    k = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / ((dx * dx + dy * dy) or 1)))
    return math.hypot(x - ax - k * dx, y - ay - k * dy) - r


def box(x, y, cx, cy, hw, hh):
    qx, qy = abs(x - cx) - hw, abs(y - cy) - hh
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0)


def triangle(x, y, p0, p1, p2):
    pts = (p0, p1, p2)
    edges = [(pts[k], pts[(k + 1) % 3]) for k in range(3)]
    dist = min(capsule(x, y, a[0], a[1], b[0], b[1], 0) for a, b in edges)
    side = [(b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]) for a, b in edges]
    inside = all(v >= 0 for v in side) or all(v <= 0 for v in side)
    return -dist if inside else dist


def blend(c, sd, col, soft):
    """Paint col over c with a soft edge `soft` wide (same units as sd)."""
    a = max(0.0, min(1.0, 0.5 - sd / soft))
    return c if a == 0 else tuple(int(p * (1 - a) + q * a) for p, q in zip(c, col))


def diamond(x, y, cx, cy, r):
    return (abs(x - cx) + abs(y - cy) - r) * 0.7071


def ring(x, y, cx, cy, r, thickness):
    return abs(math.hypot(x - cx, y - cy) - r) - thickness
