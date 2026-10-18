"""Tetris on the curtain, played from the keyboard, over DDP.

    python scripts/tetris.py                      # you play: run it in a normal terminal window
    python scripts/tetris.py --demo               # a bot plays, to watch it
    python scripts/tetris.py --simple             # only square blocks
    python scripts/tetris.py --cols 6 --cell-h 10 --speed 1.2

Keys (the terminal window must be focused):
    Left / Right arrow   move the piece
    Up arrow             rotate clockwise         Z   rotate counter-clockwise
    Down arrow           drop the piece all the way (--down soft: one row per press instead)
    Space                drop all the way         P pause   R restart   Q or Esc quit
    --wasd               also accept A/D move, W rotate, S drop (reserved for a 2nd player later)

The board is --cols wide (default 6) and every row is --cell-h cm tall (default 10). Set the height with
--rows N (the rows then share the playfield height equally) and/or --height cm (e.g. --height 60 plays in
the bottom 60 cm only); --cell-h and --rows together fix both. Only the
dense part of the curtain is used (59 x 103 cm after skipping the sparse first LEDs), so 30 cm
rows would give just 3 rows. Empty cells show a faint checkerboard, a
dim ghost shows where the piece will land, full rows flash before they disappear and a lost
game flashes red. Score and level are printed in the terminal.
Host, LED count, map, boundaries and background live in scripts/config.py.
"""
import argparse
import random

import common

try:
    import msvcrt                                    # Windows console keys
except ImportError:
    msvcrt = None

SHAPES = {                                           # cells (x, y), y pointing up
    "I": [(0, 0), (1, 0), (2, 0), (3, 0)],
    "O": [(0, 0), (1, 0), (0, 1), (1, 1)],
    "T": [(0, 0), (1, 0), (2, 0), (1, 1)],
    "S": [(1, 0), (2, 0), (0, 1), (1, 1)],
    "Z": [(0, 0), (1, 0), (1, 1), (2, 1)],
    "J": [(0, 0), (1, 0), (2, 0), (0, 1)],
    "L": [(0, 0), (1, 0), (2, 0), (2, 1)],
}
COLOURS = {"I": (0, 220, 255), "O": (255, 200, 0), "T": (170, 40, 255), "S": (0, 220, 60),
           "Z": (255, 20, 20), "J": (30, 60, 255), "L": (255, 110, 0)}
FLASH_S = 0.35
BOT = (8, 6, 0.6, 0.8, 0)                # the demo bot's weights: lines, holes, total height, bumpiness, tallest column
SCORES = {1: 100, 2: 300, 3: 500, 4: 800}


def normalise(cells):
    mx, my = min(x for x, _ in cells), min(y for _, y in cells)
    return [(x - mx, y - my) for x, y in cells]


def rotated(cells, clockwise):
    return normalise([(y, -x) if clockwise else (-y, x) for x, y in cells])


def size(cells):
    return max(x for x, _ in cells) + 1, max(y for _, y in cells) + 1


class Game:
    def __init__(self, cols, rows, speed, rng, simple=0):
        self.cols, self.rows, self.speed, self.rng = cols, rows, speed, rng
        self.simple = simple                                              # 0 = normal tetrominoes, n = n x n square blocks
        self.reset(0.0)

    def reset(self, now):
        self.board = [[None] * self.cols for _ in range(self.rows)]       # board[row][col] = piece letter, row 0 = bottom
        self.score = self.lines = self.level = 0
        self.over, self.paused = False, False
        self.over_at = 0.0
        self.flash_rows, self.flash_until = [], 0.0
        self.bag = []
        self.piece = None
        self.next_fall = now + self.interval()
        self.spawn()
        print(f"\nnew game: {self.cols} x {self.rows} board", flush=True)

    def interval(self):
        return max(0.15, self.speed * 0.85 ** self.level)

    def fits(self, cells, px, py):
        for x, y in cells:
            cx, cy = px + x, py + y
            if cx < 0 or cx >= self.cols or cy < 0 or cy >= self.rows or self.board[cy][cx]:
                return False
        return True

    def spawn(self):
        if self.simple:                                                   # square blocks in random colours
            kind = self.rng.choice(list(COLOURS))
            cells = [(x, y) for y in range(self.simple) for x in range(self.simple)]
        else:
            if not self.bag:
                self.bag = list(SHAPES)
                self.rng.shuffle(self.bag)
            kind = self.bag.pop()
            cells = SHAPES[kind]
        w, h = size(cells)
        self.piece = {"kind": kind, "cells": cells, "x": (self.cols - w) // 2, "y": self.rows - h, "square": bool(self.simple)}
        if not self.fits(cells, self.piece["x"], self.piece["y"]):
            self.over = True
            self.over_at = None                                           # set on the first update
            print(f"GAME OVER   score {self.score}   lines {self.lines}   (press R to restart)", flush=True)

    def landing_y(self):
        p = self.piece
        y = p["y"]
        while self.fits(p["cells"], p["x"], y - 1):
            y -= 1
        return y

    # ---- player actions ----
    def move(self, dx):
        p = self.piece
        if p and not self.flash_rows and self.fits(p["cells"], p["x"] + dx, p["y"]):
            p["x"] += dx

    def rotate(self, clockwise):
        p = self.piece
        if not p or self.flash_rows or p["kind"] == "O" or p["square"]:
            return
        new = rotated(p["cells"], clockwise)
        shift = (size(p["cells"])[0] - size(new)[0]) // 2                # keep it roughly centred on the old box
        for kick in (0, -1, 1, -2, 2):
            x = p["x"] + shift + kick
            for lift in (0, -1, -2, -3, 1):                                # taller shape near the top: step it down to fit
                if self.fits(new, x, p["y"] + lift):
                    p["cells"], p["x"], p["y"] = new, x, p["y"] + lift
                    return

    def soft_drop(self, now):
        if self.piece and not self.flash_rows:
            self.step_down(now)
            self.score += 1

    def hard_drop(self, now):
        if self.piece and not self.flash_rows:
            y = self.landing_y()
            self.score += 2 * (self.piece["y"] - y)
            self.piece["y"] = y
            self.lock(now)

    # ---- the clock ----
    def step_down(self, now):
        p = self.piece
        if self.fits(p["cells"], p["x"], p["y"] - 1):
            p["y"] -= 1
        else:
            self.lock(now)
        self.next_fall = now + self.interval()

    def lock(self, now):
        p = self.piece
        for x, y in p["cells"]:
            self.board[p["y"] + y][p["x"] + x] = p["kind"]
        full = [r for r in range(self.rows) if all(self.board[r])]
        self.piece = None
        if full:
            self.flash_rows, self.flash_until = full, now + FLASH_S
            n = len(full)
            self.score += SCORES.get(n, 800) * (self.level + 1)
            self.lines += n
            self.level = self.lines // 5
            print(f"{n} line{'s' if n > 1 else ''}!   score {self.score}   lines {self.lines}   level {self.level}", flush=True)
        else:
            self.spawn()

    def update(self, now):
        if self.paused:
            self.next_fall += 0                                           # (clock handled in tick: see run loop)
            return
        if self.over:
            if self.over_at is None:
                self.over_at = now
            return
        if self.flash_rows:
            if now >= self.flash_until:
                for r in sorted(self.flash_rows, reverse=True):
                    del self.board[r]
                    self.board.append([None] * self.cols)
                self.flash_rows = []
                self.next_fall = now + self.interval()
                self.spawn()
            return
        if self.piece and now >= self.next_fall:
            self.step_down(now)


# ---- a simple bot for --demo ----
def plan(game):
    """Best (rotations, x) for the current piece: fewest holes, lowest stack, most lines."""
    p = game.piece
    best, cells = None, p["cells"]
    for rot in range(4):
        w, _ = size(cells)
        for x in range(game.cols - w + 1):
            y = game.rows - size(cells)[1]               # this orientation's own spawn height
            if not game.fits(cells, x, y):
                continue
            while game.fits(cells, x, y - 1):
                y -= 1
            grid = [row[:] for row in game.board]
            for cx, cy in cells:
                grid[y + cy][x + cx] = 1
            full = sum(all(r) for r in grid)
            grid = [r for r in grid if not all(r)]
            heights = [max([i + 1 for i, r in enumerate(grid) if r[c]] or [0]) for c in range(game.cols)]
            holes = sum(1 for c in range(game.cols) for i in range(heights[c]) if not grid[i][c])
            bump = sum(abs(heights[i] - heights[i + 1]) for i in range(game.cols - 1))
            score = full * BOT[0] - holes * BOT[1] - sum(heights) * BOT[2] - bump * BOT[3] - max(heights) * BOT[4]
            if best is None or score > best[0]:
                best = (score, rot, x)
        cells = rotated(cells, True)
    return (best[1], best[2]) if best else (0, p["x"])


class Bot:
    def __init__(self):
        self.target, self.next_act, self.piece_id = None, 0.0, None

    def act(self, game, now):
        if game.over or not game.piece or game.flash_rows or now < self.next_act:
            return
        if self.piece_id is not game.piece:
            self.piece_id, self.target = game.piece, plan(game)
            self.rot_left = self.target[0]
        if self.rot_left > 0:
            game.rotate(True)
            self.rot_left -= 1
        elif game.piece["x"] < self.target[1]:
            game.move(1)
        elif game.piece["x"] > self.target[1]:
            game.move(-1)
        else:
            game.hard_drop(now)
        self.next_act = now + 0.14


def read_keys(wasd):
    """Pending key presses from the console as names."""
    keys = []
    while msvcrt and msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):                          # arrow keys come as a prefix + a code
            keys.append({"K": "left", "M": "right", "H": "rotate", "P": "down"}.get(msvcrt.getwch(), ""))
        elif ch == "\x1b":
            keys.append("quit")
        else:
            ch = ch.lower()
            keys.append({" ": "drop", "z": "rotate_ccw", "p": "pause", "r": "restart", "q": "quit"}.get(ch, "")
                        or (({"a": "left", "d": "right", "w": "rotate", "s": "down"}.get(ch, "")) if wasd else ""))
    return keys


def handle(game, key, now, down_soft=False):
    if key == "left":
        game.move(-1)
    elif key == "right":
        game.move(1)
    elif key == "rotate":
        game.rotate(True)
    elif key == "rotate_ccw":
        game.rotate(False)
    elif key == "down":
        game.soft_drop(now) if down_soft else game.hard_drop(now)
    elif key == "drop":
        game.hard_drop(now)
    elif key == "pause":
        game.paused = not game.paused
        print("paused" if game.paused else "go", flush=True)
    elif key == "restart":
        game.reset(now)
    elif key == "quit":
        raise KeyboardInterrupt


def cell_w_cm(xmin, xmax, cols):
    return (xmax - xmin) / cols


def build(leds, args, rng=None):
    xs = [l.xcm for l in leds]
    ys = [l.ycm for l in leds]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    height = min(args.height or 1e9, ymax - ymin)             # playfield height in cm, from the bottom of the lit area
    cell_h = args.cell_h or (height / args.rows if args.rows else 10.0)
    rows = args.rows or max(3, int(height / cell_h))
    print(f"board: {args.cols} columns x {rows} rows, cells {cell_w_cm(xmin, xmax, args.cols):.0f} x {cell_h:.0f} cm", flush=True)
    game = Game(args.cols, rows, args.speed, rng or random.Random(), args.block_size if args.simple else 0)
    bot = Bot() if args.demo else None
    cell_w = (xmax - xmin) / args.cols + 1e-9
    bg = common.background(args)
    state = {"last": 0.0, "pause_start": None}

    def render(t):
        dt, state["last"] = t - state["last"], t
        if game.paused:                                   # stop the clock while paused
            game.next_fall += dt
            game.flash_until += dt
        else:
            if bot:
                bot.act(game, t)
        for key in read_keys(args.wasd):
            if args.invert_lr:                                   # the curtain is seen mirrored: swap left and right
                key = {"left": "right", "right": "left"}.get(key, key)
            handle(game, key, t, args.down == "soft")
        game.update(t)
        if game.over and bot and t - (game.over_at or t) > 2.0:        # the demo starts a new game by itself
            game.reset(t)

        ghost = set()
        piece = set()
        if game.piece and not game.over:
            p = game.piece
            gy = game.landing_y()
            ghost = {(p["x"] + x, gy + y) for x, y in p["cells"]}
            piece = {(p["x"] + x, p["y"] + y) for x, y in p["cells"]}
        lost = game.over and game.over_at is not None
        flash_on = int(t * 12) % 2 == 0
        frame = common.new_frame(args)
        for l in leds:
            if l.i >= args.leds:
                continue
            col = min(args.cols - 1, int((l.xcm - xmin) / cell_w))
            row = int((l.ycm - ymin) / cell_h)
            if row >= rows:
                continue                                  # the strip above the board stays background
            kind = game.board[row][col]
            if kind:
                c = (255, 255, 255) if (row in game.flash_rows and flash_on) else COLOURS[kind]
            elif (col, row) in piece:
                c = COLOURS[game.piece["kind"]]
            elif (col, row) in ghost:
                c = tuple(v // 6 for v in COLOURS[game.piece["kind"]])
            else:
                c = tuple(int(v * (1.8 if (col + row) % 2 == 0 else 0.9)) for v in bg)    # faint checkerboard
            if lost:                                      # game over: red flashes, then a red glow with the stack
                red = (255, 0, 0) if int((t - game.over_at) * 5) % 2 == 0 and t - game.over_at < 2.0 else (60, 0, 0)
                c = red if not kind else tuple((a + b) // 2 for a, b in zip(c, red))
            frame[l.i * 3:l.i * 3 + 3] = bytes(c)
        return frame

    render.game = game
    return render


def main():
    ap = common.parser(__doc__)
    ap.add_argument("--cols", type=int, default=6, help="board width in cells")
    ap.add_argument("--cell-h", type=float, default=None, help="height of a row in cm (default 10, or the board height divided by --rows)")
    ap.add_argument("--rows", type=int, default=0, help="number of rows (0 = as many --cell-h tall rows as fit)")
    ap.add_argument("--height", type=float, default=0, help="playfield height in cm from the bottom of the lit area (0 = all of it)")
    ap.add_argument("--speed", type=float, default=1.2, help="seconds per row at the start (gets faster every 5 lines)")
    ap.add_argument("--down", choices=["hard", "soft"], default="hard", help="Down arrow: drop all the way, or one row")
    ap.add_argument("--invert-lr", action=argparse.BooleanOptionalAction, default=False,
                    help="swap the Left and Right keys (off by default: the mirrored map is fixed by --flip-x)")
    ap.add_argument("--wasd", action="store_true", help="also accept WASD (meant for a second player later)")
    ap.add_argument("--simple", action="store_true", help="only square blocks (no rotating needed), in random colours")
    ap.add_argument("--block-size", type=int, default=2, help="--simple: the squares are N x N cells")
    ap.add_argument("--demo", action="store_true", help="a bot plays instead of you")
    args = ap.parse_args()
    if not args.demo and msvcrt is None:
        raise SystemExit("keyboard control needs a Windows console; try --demo")
    leds = common.leds_from_args(args)
    print("keys: Left/Right move   Up rotate (Z = other way)   Down/Space drop   P pause   R restart   Q quit", flush=True)
    common.run(args, build(leds, args))


if __name__ == "__main__":
    main()
