"""Renders every effect on the fine preview grid and tiles them into docs/gallery.png (for the README).

    py scripts/make_gallery.py

Nothing is sent to the module and the screen is not captured: the pictures come straight from the
effects' own frames, the same way the app's previews are made.
"""
import contextlib
import io
import os
import sys

from PIL import Image, ImageDraw, ImageFont

import gui

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "gallery.png")
SKIP = {"music"}                      # needs a sound source to show anything
SECONDS, STEP = 8.0, 0.1              # how long each effect runs before its picture is taken
SECONDS_FOR = {"tetris": 4.0, "pong": 6.0}   # the games should not be caught mid game-over
TILE_W, COLUMNS = 150, 5


def font(size):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    g = gui.default_globals()
    effects = {n: gui.Effect(label, n) for label, n in gui.EFFECTS if n not in SKIP}
    engine = gui.Engine({}, g, effects)
    leds, offsets, nx, ny = engine.virtual_leds()
    tiles = []
    for name, eff in effects.items():
        deck = gui.Deck("A", name, {})
        ns = engine.namespace(deck)
        ns.leds = len(leds)
        with contextlib.redirect_stdout(io.StringIO()):           # the games chatter on stdout
            render = engine.make_render(deck, leds, ns, True)
            t = 0.0
            while t < SECONDS_FOR.get(name, SECONDS):
                frame = render(t)
                t += STEP
        buf = bytearray(nx * ny * 3)
        for i, off in enumerate(offsets):
            buf[off:off + 3] = frame[i * 3:i * 3 + 3]
        h = int(TILE_W * ny / nx)
        tiles.append((eff.label, Image.frombytes("RGB", (nx, ny), bytes(buf)).resize((TILE_W, h), Image.BICUBIC)))
        print("rendered", eff.label)
    rows = -(-len(tiles) // COLUMNS)
    pad, cap = 10, 24
    tile_h = tiles[0][1].height
    sheet = Image.new("RGB", (COLUMNS * (TILE_W + pad) + pad, rows * (tile_h + cap + pad) + pad), (27, 29, 34))
    draw = ImageDraw.Draw(sheet)
    f = font(14)
    for k, (label, img) in enumerate(tiles):
        x = pad + (k % COLUMNS) * (TILE_W + pad)
        y = pad + (k // COLUMNS) * (tile_h + cap + pad)
        sheet.paste(img, (x, y))
        draw.text((x + 2, y + tile_h + 4), label, fill=(232, 232, 232), font=f)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    sheet.save(OUT)
    print("saved", os.path.normpath(OUT), sheet.size)


if __name__ == "__main__":
    main()
