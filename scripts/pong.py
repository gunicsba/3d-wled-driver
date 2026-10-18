"""Pong with a bar at the bottom: keep the ball in the air. Played from the keyboard, over DDP.

    python scripts/pong.py                        # you play: run it in a normal terminal window
    python scripts/pong.py --demo                 # the computer plays, to watch it
    python scripts/pong.py --accel 0.08 --paddle 18

The ball bounces off the left, right and top edges of the lit area and you move the bar along the
bottom to hit it. Every bounce makes the ball faster (--accel, 4% by default) and its colour shifts
from white through yellow to red as it speeds up. Where the ball lands on the bar decides the
angle: the centre sends it straight up, the ends send it sideways. Miss it and the game is over.

Keys (the terminal window must be focused):
    Left / Right arrow   move the bar
    Space                launch the ball now / start again after a game over
    P pause   R restart   Q or Esc quit        --wasd also accepts A / D
Host, LED count, map, boundaries and background live in scripts/config.py.
"""
import argparse
import math
import random

import common
from shapes import blend, box
from tetris import msvcrt, read_keys


def heat(speed, lo, hi):
    """White at the start speed, yellow, then red at the top speed."""
    k = max(0.0, min(1.0, (speed - lo) / (hi - lo)))
    return (255, int(255 * (1 - max(0.0, k - 0.5) * 2)), int(255 * (1 - min(1.0, k * 2))))


def build(leds, args, rng=None):
    rng = rng or random.Random()
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    half_b = args.ball / 2
    half_p = args.paddle / 2
    paddle_y = ymin + 4.0                                 # centre line of the bar
    bar_h = 3.0                                           # half thickness
    bg = common.background(args)
    mid = (xmin + xmax) / 2
    s = {"last": 0.0, "phase": "ready", "phase_t": 0.0, "px": mid, "target": mid,
         "bx": 0.0, "by": 0.0, "vx": 0.0, "vy": 0.0, "speed": args.speed, "hits": 0, "bounces": 0, "best": 0,
         "trail": [], "paused": False, "aim": 0.0}

    def new_game(t):
        s.update(phase="ready", phase_t=t, speed=args.speed, hits=0, bounces=0, trail=[], aim=0.0)
        print(f"\nnew game: keep the ball in the air (best so far {s['best']})", flush=True)

    def launch(t):
        ang = math.radians(rng.uniform(-35, 35))                      # from straight up
        s.update(phase="play", phase_t=t, vx=s["speed"] * math.sin(ang), vy=s["speed"] * math.cos(ang))

    def faster():
        s["bounces"] += 1
        s["speed"] = min(args.max_speed, s["speed"] * (1 + args.accel))
        n = math.hypot(s["vx"], s["vy"]) or 1.0
        s["vx"], s["vy"] = s["vx"] / n * s["speed"], s["vy"] / n * s["speed"]

    def key(k, t):
        if k in ("left", "right"):
            if args.invert_lr:
                k = "right" if k == "left" else "left"
            s["target"] += args.step * (-1 if k == "left" else 1)
        elif k in ("drop", "rotate"):                                 # Space (and Up): launch / start again
            if s["phase"] == "ready":
                launch(t)
            elif s["phase"] == "over":
                new_game(t)
        elif k == "pause":
            s["paused"] = not s["paused"]
            print("paused" if s["paused"] else "go", flush=True)
        elif k == "restart":
            new_game(t)
        elif k == "quit":
            raise KeyboardInterrupt

    def update(t, dt):
        if args.demo:                                                  # a human-ish player: follows the ball, a bit loosely
            s["target"] = s["bx"] + s["aim"]                           # aims at a different spot of the bar each time
        s["target"] = max(xmin + half_p, min(xmax - half_p, s["target"]))
        reach = (130 if args.demo else args.paddle_speed) * dt           # the demo player is only human-fast
        move = max(-reach, min(reach, s["target"] - s["px"]))
        s["px"] += move
        if s["phase"] == "ready":                                      # the ball waits on the bar
            s["bx"], s["by"] = s["px"], paddle_y + bar_h + half_b + 0.5
            if (args.demo and t - s["phase_t"] > 1.0) or t - s["phase_t"] > 3.0:
                launch(t)
            return
        if s["phase"] == "over":
            if args.demo and t - s["phase_t"] > 2.5:
                new_game(t)
            return
        steps = max(1, int(math.hypot(s["vx"], s["vy"]) * dt / 2.0) + 1)    # small steps: no tunnelling at high speed
        for _ in range(steps):
            s["bx"] += s["vx"] * dt / steps
            s["by"] += s["vy"] * dt / steps
            if s["bx"] - half_b < xmin:
                s["bx"], s["vx"] = xmin + half_b + (xmin + half_b - s["bx"]), abs(s["vx"])
                faster()
            elif s["bx"] + half_b > xmax:
                s["bx"], s["vx"] = xmax - half_b - (s["bx"] - (xmax - half_b)), -abs(s["vx"])
                faster()
            if s["by"] + half_b > ymax:
                s["by"], s["vy"] = ymax - half_b - (s["by"] + half_b - ymax), -abs(s["vy"])
                faster()
            top = paddle_y + bar_h
            if (s["vy"] < 0 and s["by"] - half_b <= top and s["by"] > paddle_y - bar_h
                    and abs(s["bx"] - s["px"]) <= half_p + half_b):       # the bar catches it
                rel = max(-1.0, min(1.0, (s["bx"] - s["px"]) / (half_p + half_b)))
                ang = math.radians(rel * 60)                           # centre: straight up, ends: 60 degrees
                faster()
                s["vx"], s["vy"] = s["speed"] * math.sin(ang), s["speed"] * math.cos(ang)
                s["by"] = top + half_b
                s["hits"] += 1
                s["aim"] = rng.uniform(-0.85, 0.85) * half_p            # (only the demo player uses this)
                if rng.random() < 0.08:                                 # now and then the demo player slips and misses
                    s["aim"] = rng.choice((-1, 1)) * (half_p + half_b + 5)
                s["best"] = max(s["best"], s["hits"])
                print(f"hit {s['hits']}   speed {s['speed']:.0f} cm/s", flush=True)
            if s["by"] + half_b < ymin - 4:                            # fell past the bar
                s.update(phase="over", phase_t=t, trail=[])
                print(f"GAME OVER   {s['hits']} hits   best {s['best']}   (Space or R for a new game)", flush=True)
                return
        s["trail"].append((t, s["bx"], s["by"]))
        s["trail"] = [p for p in s["trail"] if t - p[0] < 0.25]

    def render(t):
        dt, s["last"] = min(0.05, t - s["last"]), t
        for k in read_keys(args.wasd):
            key(k, t)
        if not s["paused"]:
            update(t, dt)
        else:
            s["phase_t"] += dt
        over = s["phase"] == "over"
        red_on = over and int((t - s["phase_t"]) * 5) % 2 == 0 and t - s["phase_t"] < 2.0
        ball = heat(s["speed"], args.speed, args.max_speed)
        frame = common.new_frame(args)
        soft = 6.0
        for l in leds:
            if l.i >= args.leds:
                continue
            c = bg
            if over:
                c = (160, 0, 0) if red_on else (40, 0, 0)
            for ts, tx, ty in s["trail"]:                              # a short fading tail behind the ball
                age = min(1.0, (t - ts) / 0.25)
                if abs(l.xcm - tx) < half_b + soft and abs(l.ycm - ty) < half_b + soft:
                    c = blend(c, box(l.xcm, l.ycm, tx, ty, half_b * 0.8, half_b * 0.8),
                              tuple(int(v * (1 - age) * 0.45) for v in ball), soft)
            c = blend(c, box(l.xcm, l.ycm, s["px"], paddle_y, half_p, bar_h), (120, 220, 255), soft)       # the bar
            if not over or red_on:
                if s["phase"] != "ready" or int(t * 6) % 2 == 0:          # blinks while waiting to launch
                    c = blend(c, box(l.xcm, l.ycm, s["bx"], s["by"], half_b, half_b), ball, soft)
            frame[l.i * 3:l.i * 3 + 3] = bytes(max(0, min(255, int(v))) for v in c)
        return frame

    render.state = s
    render.key = key
    new_game(0.0)
    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--speed", type=float, default=45, help="start speed of the ball in cm/s")
    ap.add_argument("--accel", type=float, default=0.04, help="speed-up per bounce (0.04 = 4%%)")
    ap.add_argument("--max-speed", type=float, default=260, help="speed limit in cm/s")
    ap.add_argument("--paddle", type=float, default=22, help="width of the bar in cm")
    ap.add_argument("--ball", type=float, default=12, help="size of the ball in cm")
    ap.add_argument("--step", type=float, default=8, help="how far the bar jumps per key press, in cm")
    ap.add_argument("--paddle-speed", type=float, default=400, help="how fast the bar slides to its target, cm/s")
    ap.add_argument("--invert-lr", action=argparse.BooleanOptionalAction, default=False,
                    help="swap the Left and Right keys (off by default: the mirrored map is fixed by --flip-x)")
    ap.add_argument("--wasd", action="store_true", help="also accept A / D")
    ap.add_argument("--demo", action="store_true", help="the computer plays")
    args = ap.parse_args()
    if not args.demo and msvcrt is None:
        raise SystemExit("keyboard control needs a Windows console; try --demo")
    leds = common.leds_from_args(args)
    print("keys: Left/Right move the bar   Space launch / again   P pause   R restart   Q quit", flush=True)
    common.run(args, build(leds, args))


if __name__ == "__main__":
    main()
