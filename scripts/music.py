"""Music visualiser: lights react to whatever the PC is playing (Spotify, YouTube, ...), over DDP.

    python scripts/music.py                          # spectrum bars
    python scripts/music.py --effect vu
    python scripts/music.py --effect rainbow --center 50%,20%
    python scripts/music.py --effect rings --gain 2
    python scripts/music.py --low-latency --fps 60   # react sooner
    python scripts/music.py --list-devices

Effects:
  spectrum  columns left to right = bass to treble, rising bars with falling peak dots
  vu        one tall bar for the overall volume, with a peak marker and a bass flash
  rainbow   rainbow rings from --center, flowing at the detected BPM, brightness kick on each beat
  rings     a coloured ring bursts out of --center on every beat
  owl       an owl on a branch: rocks on the beat, wings flap, pupils glance left/right, and every
            --wink-every beats it winks (idle: slow sway and an occasional blink); it sits at the x of --center
  stickman  a stick figure dancing on the beat: a new move every --change-every beats (hands up, disco
            point, kicks, squat, jump), bouncing on every beat, standing still when the music stops;
            it dances at the x of --center
  flag_hu   the Hungarian flag, gently waving; every beat drops a water droplet at --center
            whose ripples spread over it (--scatter makes the droplets land at random spots)

Captures the default speakers (WASAPI loopback), so volume level does not matter
much (auto-gain); --gain boosts quiet sources. Start the music, then run this.
Host, LED count, map and boundaries live in scripts/config.py.
"""
import colorsys
import random
import math
import sys
import time

import audio
import common
import flags
from shapes import blend, box, capsule, circle, triangle
from rainbow import parse_center


# ---- stickman ----
# A pose is 10 numbers: right arm (upper, fore), left arm (upper, fore), right leg (thigh, shin),
# left leg (thigh, shin) as angles in degrees (0 = pointing right, 90 = up, -90 = down; "right" is the
# screen's right), then the lean of the body in degrees (+ = to the right) and how high it jumps (design cm).
POSES = [
    (-80, -85, -100, -95, -84, -88, -96, -92, 0, 0),         # 0 stand (used when the music stops)
    (70, 85, 110, 95, -80, -88, -100, -92, 0, 6),            # 1 hands up
    (50, 50, -130, -130, -65, -85, -112, -95, 6, 0),         # 2 disco point
    (5, 5, 175, 175, -88, -90, -130, -155, 0, 2),            # 3 arms out, left leg kicks
    (5, 5, 175, 175, -50, -25, -92, -90, 0, 2),              # 4 arms out, right leg kicks
    (80, 115, -100, -95, -84, -90, -96, -90, -4, 0),         # 5 wave
    (-25, -5, 205, 185, -55, -95, -125, -85, 0, -14),        # 6 squat
    (65, 65, 115, 115, -70, -130, -110, -50, 0, 22),         # 7 jump
]
THIGH, SHIN, TORSO, NECK, UPPER, FORE, HEAD_R = 42, 43, 38, 16, 18, 16, 11   # design cm (a 150 cm figure)


def ellipse(u, v, cx, cy, ax, ay):
    """Approximate signed distance to an ellipse."""
    return (math.hypot((u - cx) / ax, (v - cy) / ay) - 1) * min(ax, ay)


def smooth01(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def cur_pose(state, t):
    """The pose now: easing from the previous pose to the target over ~0.22 s."""
    old, new, t0 = state["pose"]
    k = smooth01((t - t0) / 0.22)
    return tuple(o + (n - o) * k for o, n in zip(old, new))


def figure(a, S, cx, floor_y, bounce):
    """Stick figure for pose angles `a`: returns (line segments, head (x, y, radius)); scale S, feet on floor_y."""
    ru, rf, lu, lf, rt, rs, lt, ls, lean, jump = a

    def vec(length, deg):
        return length * S * math.cos(math.radians(deg)), length * S * math.sin(math.radians(deg))

    def chain(x, y, l1, a1, l2, a2):                      # joint and end point of a two-bone limb
        j = (x + vec(l1, a1)[0], y + vec(l1, a1)[1])
        return j, (j[0] + vec(l2, a2)[0], j[1] + vec(l2, a2)[1])

    rk, rfoot = chain(0, 0, THIGH, rt, SHIN, rs)          # legs relative to the hip, to plant the lower foot
    lk, lfoot = chain(0, 0, THIGH, lt, SHIN, ls)
    hip = (cx, floor_y - min(rfoot[1], lfoot[1]) + (jump + bounce) * S)
    tdir = 90 - lean
    neck = (hip[0] + vec(TORSO, tdir)[0], hip[1] + vec(TORSO, tdir)[1])
    head = (neck[0] + vec(NECK, 90 - lean * 1.3)[0], neck[1] + vec(NECK, 90 - lean * 1.3)[1], HEAD_R * S)
    sh = (neck[0] - vec(3, tdir)[0], neck[1] - vec(3, tdir)[1])
    segs = [(hip[0], hip[1], neck[0], neck[1])]
    for l1, a1, l2, a2 in ((UPPER, ru, FORE, rf), (UPPER, lu, FORE, lf)):
        elbow, hand = chain(sh[0], sh[1], l1, a1, l2, a2)
        segs += [(sh[0], sh[1], elbow[0], elbow[1]), (elbow[0], elbow[1], hand[0], hand[1])]
    for t1, s1 in ((rt, rs), (lt, ls)):
        knee, foot = chain(hip[0], hip[1], THIGH, t1, SHIN, s1)
        segs += [(hip[0], hip[1], knee[0], knee[1]), (knee[0], knee[1], foot[0], foot[1])]
    return segs, head


def hsv(h, s=1.0, v=1.0):
    return tuple(int(255 * c) for c in colorsys.hsv_to_rgb(h % 1.0, s, max(0.0, min(1.0, v))))


def lerp_band(bands, pos):                                # pos 0..1 across the bands, interpolated
    f = pos * (len(bands) - 1)
    k = int(f)
    return bands[k] if k >= len(bands) - 1 else bands[k] * (1 - (f - k)) + bands[k + 1] * (f - k)


def build(leds, args, an):
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    bounds = (min(xs), max(xs), min(ys), max(ys))
    cx, cy = parse_center(args.center, bounds)
    dist = {l.i: math.hypot(l.xcm - cx, l.ycm - cy) for l in leds}
    cols = args.columns
    bg = common.background(args)
    scale = args.figure_size * (bounds[3] - bounds[2]) / 150.0   # the figure is 150 design-cm tall
    floor_y = bounds[2]
    state = {"peaks": [0.0] * cols, "vu_peak": 0.0, "phase": 0.0, "last_t": 0.0, "rings": [], "beats": 0, "hue": 0.0, "drops": [], "pose": (POSES[0], POSES[0], -9.0), "pose_idx": 0, "bag": [], "sway": 0.0, "look": 0.0, "wink": (0, -9.0), "dir": 1}

    def render(t):
        s = an.snapshot()
        dt, state["last_t"] = t - state["last_t"], t
        frame = common.new_frame(args)

        def put(i, c):
            if i < args.leds:
                frame[i * 3:i * 3 + 3] = bytes(c)

        if args.effect == "spectrum":
            levels = [lerp_band(s.bands, (c + 0.5) / cols) for c in range(cols)]
            for c in range(cols):                          # peak dots fall slowly
                state["peaks"][c] = max(levels[c], state["peaks"][c] - 0.6 * dt)
            for l in leds:
                col = min(cols - 1, int(l.x * cols))
                if l.h <= levels[col]:
                    put(l.i, hsv(0.33 - 0.33 * l.h, 1, 0.35 + 0.65 * l.h))      # green low, red high
                elif abs(l.h - state["peaks"][col]) < 0.045 and state["peaks"][col] > 0.03:
                    put(l.i, (255, 255, 255))
        elif args.effect == "vu":
            lvl = min(1.0, s.level * 1.6)
            state["vu_peak"] = max(lvl, state["vu_peak"] - 0.4 * dt)
            flash = max(0.0, 1 - s.beat_age * 6)
            for l in leds:
                if l.h <= lvl:
                    put(l.i, hsv(0.33 - 0.33 * l.h, 1, 0.45 + 0.55 * flash))
                elif abs(l.h - state["vu_peak"]) < 0.04:
                    put(l.i, (255, 255, 255))
        elif args.effect == "rainbow":
            state["phase"] += dt * s.bpm / 60.0 / args.beats
            kick = math.exp(-s.beat_age * 5)
            bright = (0.25 + 0.75 * kick) * (0.15 if s.silent else 1.0)
            for l in leds:
                put(l.i, hsv(dist[l.i] / args.bands - state["phase"], 1, bright))
        elif args.effect == "rings":
            if s.beat_count != state["beats"]:
                state["beats"] = s.beat_count
                state["hue"] += 0.137
                state["rings"].append((t, state["hue"]))
            state["rings"] = [r for r in state["rings"] if t - r[0] < 1.6]
            for l in leds:
                r_, g_, b_ = bg
                for born, hue in state["rings"]:
                    age = t - born
                    d = abs(dist[l.i] - age * args.ring_speed)
                    if d < 20:
                        k = (1 - d / 20) * (1 - age / 1.6)
                        c = hsv(hue, 1, k)
                        r_, g_, b_ = r_ + c[0], g_ + c[1], b_ + c[2]
                put(l.i, (min(255, r_), min(255, g_), min(255, b_)))
        elif args.effect == "flag_hu":
            if s.beat_count != state["beats"]:                 # a droplet hits the flag on every beat
                state["beats"] = s.beat_count
                dx, dy = (random.uniform(-1, 1) * args.scatter, random.uniform(-1, 1) * args.scatter)
                state["drops"].append((t, cx + dx, cy + dy, 0.6 + 0.4 * s.bass))
            state["drops"] = [d for d in state["drops"] if t - d[0] < 2.8]
            speed = args.ring_speed * 0.55                    # ripples spread slower than the rings
            for l in leds:
                wave = 0.0
                for born, dx0, dy0, amp in state["drops"]:
                    age = t - born
                    front = age * speed
                    d = math.hypot(l.xcm - dx0, l.ycm - dy0)
                    if d <= front:
                        trail = max(0.0, 1 - (front - d) / 70)         # waves trail off behind the front
                    else:
                        trail = math.exp(-((d - front) / 4) ** 2)      # and the front itself is a soft edge
                    wave += amp * math.exp(-age * 1.4) * trail * math.sin((d - front) * 2 * math.pi / 20)
                c = flags.waving(l.x, l.h, t, args.flag_wind, 1.3, 0.6,
                                 extra_disp=wave * 0.045,          # ripples bend the stripes like refraction
                                 extra_shade=1 + 0.5 * wave)       # crests catch the light, troughs are darker
                crest = max(0.0, wave)
                put(l.i, (min(255, c[0] + int(crest * 40)), min(255, c[1] + int(crest * 70)),
                          min(255, c[2] + int(crest * 110))))   # cool water-blue sparkle on the crests
        elif args.effect == "stickman":
            if s.beat_count != state["beats"]:
                state["beats"] = s.beat_count
                if s.beat_count % args.change_every == 0:           # a new move every few beats
                    if not state["bag"]:                             # each move once per round, in random order
                        state["bag"] = random.sample(range(1, len(POSES)), len(POSES) - 1)
                        if state["bag"][0] == state["pose_idx"]:
                            state["bag"].reverse()
                    nxt = state["bag"].pop()
                    state["pose"] = (cur_pose(state, t), POSES[nxt], t)
                    state["pose_idx"] = nxt
            if s.silent and state["pose_idx"] != 0:                  # music stopped: stand still
                state["pose"] = (cur_pose(state, t), POSES[0], t)
                state["pose_idx"] = 0
            a = cur_pose(state, t)
            bounce = -3.5 * math.exp(-s.beat_age * 7) if not s.silent else 0.0
            segs, head = figure(a, scale, cx, floor_y, bounce)
            body = hsv(0.52 + 0.04 * (s.beat_count % 25), 0.3, 1.0)       # pale, shifting a little each beat
            for l in leds:
                c = bg
                d = min(capsule(l.xcm, l.ycm, *sg_, 0) for sg_ in segs)
                c = blend(c, d - args.thickness / 2, body, 6.0)
                c = blend(c, circle(l.xcm, l.ycm, head[0], head[1], head[2]), body, 6.0)
                put(l.i, c)
        elif args.effect == "owl":
            if s.beat_count != state["beats"]:
                state["beats"] = s.beat_count
                state["dir"] = -state["dir"]                        # rock to the other side on every beat
                if s.beat_count % 2 == 0:
                    state["look"] = -state["look"] or 1             # glance the other way every 2 beats
                if s.beat_count % args.wink_every == 0:             # wink with the other eye each time
                    state["wink"] = (-state["wink"][0] or 1, t)
            target = state["dir"] * 6.0 if not s.silent else 4.0 * math.sin(t * 1.1)
            state["sway"] += (target - state["sway"]) * min(1.0, dt * 9)
            sway = state["sway"]
            bob = -3.0 * math.exp(-s.beat_age * 8) if not s.silent else 0.0
            flap = math.exp(-s.beat_age * 6) * (0.5 + 0.5 * s.bass) if not s.silent else 0.0
            wink_eye, wink_t = state["wink"]
            winking = (t - wink_t) < 0.30
            blink_both = s.silent and (t % 3.7) < 0.16                  # idle: a slow blink now and then
            look = state["look"] if not s.silent else math.sin(t * 0.6)
            glow = 0.65 + 0.35 * s.level
            S = args.owl_size * (bounds[3] - bounds[2]) / 200.0
            soft = 6.0 / S
            fx, fy = cx, bounds[2]
            brown, dark, belly = (255, 135, 25), (190, 85, 10), (255, 235, 170)
            branch, orange = (170, 105, 40), (255, 90, 0)
            for l in leds:
                u, v = (l.xcm - fx) / S, (l.ycm - fy) / S
                c = bg
                c = blend(c, box(u, v, 0, 3, 1e3, 3), branch, soft)                        # branch
                bx = sway * 0.6                                                            # body swings less than the head
                hx = sway * 1.6
                for side in (-1, 1):                                                      # wings flap on the beat
                    tip = (side * (26 + 12 * flap) + bx, 36 + 34 * flap)
                    c = blend(c, capsule(u, v, side * 20 + bx, 78 + bob, tip[0], tip[1], 8), dark, soft)
                    c = blend(c, triangle(u, v, (side * 11 + hx, 118 + bob), (side * 22 + hx, 122 + bob),
                                          (side * 24 + hx * 1.1, 142 + bob)), dark, soft)  # ear tufts
                c = blend(c, ellipse(u, v, bx, 46 + bob * 0.5, 25, 44), brown, soft)       # body
                c = blend(c, ellipse(u, v, bx, 40 + bob * 0.5, 15, 30), belly, soft)       # belly
                c = blend(c, circle(u, v, hx, 100 + bob, 27), brown, soft)                 # head
                for side, eye in ((-1, 1), (1, -1)):
                    ex, ey = hx + side * 12.5, 103 + bob
                    closed = blink_both or (winking and eye == wink_eye)
                    ring = tuple(int(q * glow) for q in (255, 190, 0))
                    if closed:                                                             # eyelid: a flat dark slit
                        c = blend(c, capsule(u, v, ex - 9, ey - 1, ex + 9, ey - 1, 2.2), dark, soft)
                    else:
                        c = blend(c, circle(u, v, ex, ey, 15.5), ring, soft)
                        c = blend(c, circle(u, v, ex, ey, 12), (255, 255, 235), soft)
                        c = blend(c, circle(u, v, ex + look * 4, ey, 6.5), (0, 0, 0), soft)    # pupil follows the glance
                c = blend(c, triangle(u, v, (hx - 4.5, 100 + bob), (hx + 4.5, 100 + bob), (hx, 88 + bob)), orange, soft)   # beak
                for side in (-1, 1):                                                       # feet gripping the branch
                    c = blend(c, circle(u, v, bx + side * 10, 7, 4.5), orange, soft)
                put(l.i, c)
        return frame

    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--effect", choices=["spectrum", "vu", "rainbow", "rings", "flag_hu", "stickman", "owl"], default="spectrum")
    ap.add_argument("--center", default="50%,50%", help='rainbow/rings centre: cm "x,y" or percent "50%%,50%%"')
    ap.add_argument("--gain", type=float, default=1.0, help="boost for quiet sources")
    ap.add_argument("--device", type=int, help="loopback device index (see --list-devices)")
    ap.add_argument("--low-latency", action="store_true",
                    help="shorter audio window and blocks: detects kicks ~15 ms sooner, coarser bass (try with --fps 60)")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--columns", type=int, default=6, help="spectrum: number of bars")
    ap.add_argument("--beats", type=float, default=4, help="rainbow: beats per full colour cycle")
    ap.add_argument("--bands", type=float, default=70, help="rainbow: cm per full rainbow")
    ap.add_argument("--figure-size", type=float, default=0.8, help="stickman: height as a fraction of the curtain")
    ap.add_argument("--change-every", type=int, default=2, help="stickman: beats between new moves")
    ap.add_argument("--owl-size", type=float, default=1.4, help="owl: size (1.0 = about 70%% of the curtain height)")
    ap.add_argument("--wink-every", type=int, default=6, help="owl: beats between winks")
    ap.add_argument("--thickness", type=float, default=7.0, help="stickman: line thickness in cm")
    ap.add_argument("--scatter", type=float, default=0, help="flag_hu: droplets land up to N cm from --center")
    ap.add_argument("--flag-wind", type=float, default=0.025, help="flag_hu: sway of the flag, fraction of its height")
    ap.add_argument("--ring-speed", type=float, default=140, help="rings: cm per second")
    args = ap.parse_args()
    if args.list_devices:
        audio.list_devices()
        return
    an = audio.Analyzer(args.device, args.gain, args.low_latency).start()
    print(f"listening to: {an.name} ({an.rate} Hz)")
    render = build(common.leds_from_args(args), args, an)

    last_print = [0.0]

    def with_status(t):                                    # also print the audio state once a second
        frame = render(t)
        if time.time() - last_print[0] > 1:
            last_print[0] = time.time()
            s = an.snapshot()
            sys.stdout.write(f"\rlevel {s.level:4.2f}  bass {s.bass:4.2f}  bpm {s.bpm:5.1f}  beats {s.beat_count:5d}  "
                             f"{'(silence)' if s.silent else '         '} | sent {common.STATS['fps']} fps, "
                             f"max gap {common.STATS['gap_ms']} ms, max render {common.STATS['render_ms']} ms   ")
            sys.stdout.flush()
        return frame

    try:
        common.run(args, with_status)
    finally:
        an.stop()


if __name__ == "__main__":
    main()
