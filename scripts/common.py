"""Shared helpers: load the LED map, add the standard CLI flags, stream frames over DDP."""
import argparse
import csv
import glob
import os
import socket
import time
from collections import namedtuple

import config

# i: LED index; h, x: position 0..1 (up / right); xcm, ycm: position in the map's units (cm); zcm: depth
Led = namedtuple("Led", "i h x xcm ycm zcm", defaults=(0.0,))

# How a 3D map is turned upright and from where it is looked at (see orient_3d and project_3d)
View = namedtuple("View", "orient rotate_x rotate_y rotate_z azimuth elevation roll distance",
                  defaults=("auto", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))

COLOURS = {"red": (230, 20, 10), "green": (0, 170, 50), "blue": (20, 80, 255),
           "orange": (255, 110, 0), "yellow": (255, 200, 0), "purple": (150, 30, 220),
           "white": (255, 255, 255), "pink": (255, 70, 150), "cyan": (0, 200, 220), "black": (0, 0, 0)}


def parse_colour(text):
    """A name from COLOURS or RRGGBB."""
    text = text.lower().lstrip("#")
    if text in COLOURS:
        return COLOURS[text]
    try:
        return tuple(int(text[k:k + 2], 16) for k in (0, 2, 4))
    except ValueError:
        raise SystemExit(f"unknown colour '{text}': use a name ({', '.join(COLOURS)}) or RRGGBB")


def background(args):
    """The configured background colour at its brightness, as an (r, g, b) tuple."""
    return tuple(int(v * args.bg_brightness) for v in parse_colour(args.background))


def new_frame(args):
    """A frame with every LED (mapped or not) set to the background."""
    return bytearray(bytes(background(args)) * args.leds)


HERE = os.path.dirname(os.path.abspath(__file__))


def newest_map():
    """Your own map in maridata/ if there is one, otherwise the example map in maps/."""
    for folder in ("maridata", "maps"):
        base = os.path.join(HERE, "..", folder)
        # post-processed curtain maps (cm) win over raw MariMapper 2D maps
        for pattern in ("curtain_led_map*.csv", "led_map_2d_*.csv", "example_grid.csv"):
            files = sorted(glob.glob(os.path.join(base, pattern)))
            if files:
                return files[-1]
    raise SystemExit("no map CSV found in maridata/ or maps/; set MAP in scripts/config_local.py or pass --map")


def _rotation(rx, ry, rz):
    """A rotation matrix from angles in degrees, applied in the order x, y, z."""
    import numpy as np

    ax, ay, az = np.radians([rx, ry, rz])
    cx, sx, cy, sy, cz, sz = np.cos(ax), np.sin(ax), np.cos(ay), np.sin(ay), np.cos(az), np.sin(az)
    rot_x = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    rot_y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rot_z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rot_z @ rot_y @ rot_x


def orient_3d(points, normals, index, view):
    """Stands a MariMapper 3D map upright: x to the right, y up and z towards the front.

    The reconstruction can leave the map turned any way. "auto" works for a flat sheet like a curtain: its
    long direction becomes up (higher LED numbers higher up), its thin direction the depth, and the side the
    normals point to (where the cameras were) the front. For anything else (a tractor) use "none" and turn
    the map with map_rotate_x/y/z until it stands the right way, the map viewer shows the result.
    """
    import numpy as np

    centre = points.mean(axis=0)
    p = points - centre
    n = normals
    if view.orient == "auto":
        _, _, axes = np.linalg.svd(p, full_matrices=False)               # long, wide, thin
        up, front = axes[0], axes[2]
        if np.corrcoef(index, p @ up)[0, 1] < 0:
            up = -up
        if n is not None and n.mean(axis=0) @ front < 0:                # the normals point at the cameras
            front = -front
        basis = np.array([np.cross(up, front), up, front])
        p = p @ basis.T
        n = n @ basis.T if n is not None else None
    rotation = _rotation(view.rotate_x, view.rotate_y, view.rotate_z)
    return p @ rotation.T, (n @ rotation.T if n is not None else None)


def project_3d(points, view):
    """Looks at the points from the viewpoint: returns x (right), y (up) and the depth (towards the viewer).

    azimuth turns the viewpoint around the map (0 = the front, 90 = from the right, 180 = from behind),
    elevation lifts it (90 = from above), roll tilts the picture, and distance, when above 0, adds
    perspective for a viewer that far away (cm); 0 is a flat view without perspective.
    """
    import numpy as np

    az, el, ro = np.radians([view.azimuth, view.elevation, view.roll])
    toward = np.array([np.sin(az) * np.cos(el), np.sin(el), np.cos(az) * np.cos(el)])   # from the map to the viewer
    forward = -toward
    right = np.cross(forward, [0.0, 1.0, 0.0])
    if np.linalg.norm(right) < 1e-6:                                    # straight above or below
        right = np.cross(forward, [0.0, 0.0, -1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    right, up = np.cos(ro) * right + np.sin(ro) * up, -np.sin(ro) * right + np.cos(ro) * up
    x, y, depth = points @ right, points @ up, points @ toward
    if view.distance > 0:
        scale = view.distance / np.maximum(view.distance - depth, 1e-3)
        x, y = x * scale, y * scale
    return x, y, depth


def map_3d_to_view(rows, view):
    """(index, x, y, depth) in cm of every LED of a MariMapper 3D map, as seen from the viewpoint"""
    import numpy as np

    index = np.array([int(r["index"]) for r in rows])
    points = np.array([[float(r[k]) for k in "xyz"] for r in rows]) * config.MARIMAPPER_UNIT_CM
    normals = np.array([[float(r[k]) for k in ("xn", "yn", "zn")] for r in rows]) if "xn" in rows[0] else None
    points, _ = orient_3d(points, normals, index, view)
    x, y, depth = project_3d(points, view)
    return [(int(i), float(a - x.min()), float(b - y.min()), float(d - depth.min())) for i, a, b, d in zip(index, x, y, depth)]


def is_3d_map(path):
    with open(path, newline="") as f:
        header = set(next(csv.reader(f), []))
    return {"x", "y", "z"} <= header and "y_cm" not in header


def load_map(path, flip_v=False, skip_first=0, skip_last=0, min_y=None, max_y=None, flip_x=False, view=None):
    """Returns [Led(i, h, x, xcm, ycm, zcm)]; h and x are 0..1, 0 = lowest / leftmost kept LED.

    Reads x_cm/y_cm (y up), u/v or MariMapper 3D (x/y/z) maps. A 3D map is looked at from the viewpoint
    given in `view` (see View) and the effects then work on that flat picture, zcm is the depth. LEDs
    outside the boundaries are dropped (they stay dark) and positions are normalised over the LEDs that
    remain. flip_x mirrors left and right of a flat map (a 3D map has the viewpoint for that).
    """
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    columns = set(rows[0])
    three_d = False
    if "y_cm" in columns:
        pts = [(int(r["index"]), float(r["x_cm"]), float(r["y_cm"]), float(r.get("z_cm") or 0)) for r in rows if r["y_cm"] != ""]
    elif "v" in columns:
        pts = [(int(r["index"]), float(r["u"]), float(r["v"]), 0.0) for r in rows if r["v"] != ""]
    elif {"x", "y", "z"} <= columns:
        three_d = True
        pts = map_3d_to_view(rows, view or View())
    else:
        raise SystemExit(f"{os.path.basename(path)}: not a map I know. Expected columns index,x_cm,y_cm or "
                         f"index,u,v (MariMapper 2D) or index,x,y,z (MariMapper 3D), found {sorted(columns)}")
    last_index = max(p[0] for p in pts)
    pts = [p for p in pts
           if skip_first <= p[0] <= last_index - skip_last
           and (min_y is None or p[2] >= min_y) and (max_y is None or p[2] <= max_y)]
    if not pts:
        raise SystemExit("no LEDs left inside the boundaries")
    if flip_x and not three_d:                            # mirror x around the middle of the kept LEDs
        mid = min(p[1] for p in pts) + max(p[1] for p in pts)
        pts = [(i, mid - x, y, z) for i, x, y, z in pts]

    def norm(vals):
        lo, hi = min(vals), max(vals)
        return [(v - lo) / ((hi - lo) or 1.0) for v in vals]

    hs, xs = norm([p[2] for p in pts]), norm([p[1] for p in pts])
    if flip_v:
        hs = [1 - h for h in hs]
    return [Led(i, h, x, xc_, yc_, zc_) for (i, xc_, yc_, zc_), h, x in zip(pts, hs, xs)]


def parser(description):
    """ArgumentParser with the common flags; defaults come from config.py."""
    ap = argparse.ArgumentParser(description=description,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--leds", type=int, default=config.LEDS, help="LED count on the module")
    ap.add_argument("--map", default=config.MAP, help="map CSV (default: newest curtain map)")
    ap.add_argument("--fps", type=float, default=config.FPS)
    ap.add_argument("--skip-first", type=int, default=config.SKIP_FIRST, help="leave the first N LEDs dark")
    ap.add_argument("--skip-last", type=int, default=config.SKIP_LAST, help="leave the last N LEDs dark")
    ap.add_argument("--min-y", type=float, default=config.MIN_Y, help="lowest height to use (cm)")
    ap.add_argument("--max-y", type=float, default=config.MAX_Y, help="highest height to use (cm)")
    ap.add_argument("--flip-v", action="store_true", default=config.FLIP_V, help="swap up/down")
    ap.add_argument("--flip-x", action=argparse.BooleanOptionalAction, default=config.FLIP_X,
                    help="mirror left/right so the picture matches what you see (--no-flip-x to turn off)")
    view = ap.add_argument_group("3D maps", "MariMapper 3D maps are stood upright, then looked at from a viewpoint")
    view.add_argument("--map-orient", choices=["auto", "none"], default=config.MAP_ORIENT,
                      help="auto: stand a flat sheet upright by itself; none: use the map as it is")
    view.add_argument("--map-rotate-x", type=float, default=config.MAP_ROTATE_X, help="turn the map about x (degrees)")
    view.add_argument("--map-rotate-y", type=float, default=config.MAP_ROTATE_Y, help="turn the map about y (degrees)")
    view.add_argument("--map-rotate-z", type=float, default=config.MAP_ROTATE_Z, help="turn the map about z (degrees)")
    view.add_argument("--view-azimuth", type=float, default=config.VIEW_AZIMUTH,
                      help="look from: 0 front, 90 right, 180 behind (degrees)")
    view.add_argument("--view-elevation", type=float, default=config.VIEW_ELEVATION,
                      help="look from: 0 level, 90 above, -90 below (degrees)")
    view.add_argument("--view-roll", type=float, default=config.VIEW_ROLL, help="tilt the picture (degrees)")
    view.add_argument("--view-distance", type=float, default=config.VIEW_DISTANCE,
                      help="perspective for a viewer this far away in cm, 0 for none")
    ap.add_argument("--background", default=config.BACKGROUND, help="background colour (name or RRGGBB)")
    ap.add_argument("--bg-brightness", type=float, default=config.BG_BRIGHTNESS, help="background brightness 0-1")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = forever)")
    return ap


def view_from_args(args):
    return View(args.map_orient, args.map_rotate_x, args.map_rotate_y, args.map_rotate_z,
                args.view_azimuth, args.view_elevation, args.view_roll, args.view_distance)


def leds_from_args(args):
    path = args.map or newest_map()
    leds = load_map(path, args.flip_v, args.skip_first, args.skip_last, args.min_y, args.max_y, args.flip_x,
                    view_from_args(args))
    print(f"{len(leds)} LEDs from {os.path.basename(path)} -> {args.host}:4048, Ctrl+C to stop")
    return leds


# Sender-side timing over the last full second (updated by run()): frames sent, longest wait between two
# sends, and the slowest render. Compare with the module's /status (rt_fps, rt_gap_ms) to see where a stall is.
STATS = {"fps": 0, "gap_ms": 0, "render_ms": 0}


def run(args, render):
    """Calls render(t) -> bytearray(leds*3) at args.fps and sends each frame over DDP.

    Ctrl+C stops; the module returns to its saved effect ~2.5 s later.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start, seq = time.time(), 0
    next_tick = time.perf_counter()
    win_start, win_n, win_gap, win_render, last_send = time.perf_counter(), 0, 0.0, 0.0, None
    try:
        while True:
            t = time.time() - start
            if args.duration and t > args.duration:
                break
            t_render = time.perf_counter()
            frame = render(t)
            win_render = max(win_render, (time.perf_counter() - t_render) * 1000)
            seq = seq % 15 + 1
            header = bytes((0x41, seq, 0x01, 0x01)) + (0).to_bytes(4, "big") + len(frame).to_bytes(2, "big")
            sock.sendto(header + bytes(frame), (args.host, 4048))
            now = time.perf_counter()
            if last_send is not None:
                win_gap = max(win_gap, (now - last_send) * 1000)
            last_send, win_n = now, win_n + 1
            if now - win_start >= 1.0:
                STATS.update(fps=win_n, gap_ms=int(win_gap), render_ms=int(win_render))
                win_start, win_n, win_gap, win_render = now, 0, 0.0, 0.0
            next_tick += 1 / args.fps                        # fixed schedule: a slow frame doesn't shift the rest
            delay = next_tick - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.25:                              # fell far behind (PC stalled): restart the schedule
                next_tick = time.perf_counter()
    except KeyboardInterrupt:
        pass
