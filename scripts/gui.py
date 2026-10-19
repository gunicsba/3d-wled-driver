"""XMasTractor light show: a desktop app with two decks, A and B.

    py scripts/gui.py

One deck is live on the strip, the other you set up in the background; then take it live (with a
crossfade). Every deck has its own preview that shows the effect on a much finer grid, as it would look
with many more LEDs. The settings form of each effect is generated from the effect script's own options,
so new scripts and new options show up here by themselves. The panel in the middle controls the ESP32
(brightness, colour order, LED count) and the global map / background settings.

Games (Tetris, Pong) take their keys from this window while their deck is live: arrow keys, Space,
Z, P, R. Settings are remembered in scripts/gui_state.json.
"""
import argparse
import copy
import csv
import importlib
import json
import math
import os
import queue
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageTk

import common
import config

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "gui_state.json")
EFFECTS = [("Sweep line", "sweep"), ("Christmas tree", "tree"), ("Explosion", "boom"), ("Laser swords", "swords"),
           ("Pac-Man", "pacman"), ("Spinning logo", "logo"), ("Dancing Santa", "santa"), ("Tractor", "tractor"),
           ("Rainbow", "rainbow"), ("Hungarian flag", "flag_hu"), ("Snowfall", "snow"), ("DVD shapes", "dvd"),
           ("Tetris (keys)", "tetris"), ("Pong (keys)", "pong"), ("Music visualiser", "music")]
GLOBAL_DESTS = {"host", "leds", "leds_per_module", "map", "fps", "skip_first", "skip_last", "min_y", "max_y", "flip_v", "flip_x", "background",
                "bg_brightness", "duration", "list_devices",
                "map_orient", "map_rotate_x", "map_rotate_y", "map_rotate_z", "view_azimuth", "view_elevation", "view_roll", "view_distance", "mirror_leds"}
ORDERS = ["GRB", "RGB", "BRG", "RBG", "GBR", "BGR"]
ORIG_PARSE = argparse.ArgumentParser.parse_args
PREVIEW_W = 260
FORM_W = 250
BG, PANEL, FG, DIM, ACCENT, LIVE = "#1b1d22", "#24272e", "#e8e8e8", "#8a8f98", "#3b82f6", "#e5484d"

KEYS = queue.Queue()                                   # key presses from the window, read by the games on the live deck


def default_globals():
    return {"host": config.HOST, "leds": config.LEDS, "leds_per_module": config.LEDS_PER_MODULE, "map": config.MAP or "", "fps": 30, "skip_first": config.SKIP_FIRST,
            "skip_last": config.SKIP_LAST, "min_y": config.MIN_Y, "max_y": config.MAX_Y, "flip_v": bool(config.FLIP_V), "flip_x": bool(config.FLIP_X),
            "background": config.BACKGROUND, "bg_brightness": config.BG_BRIGHTNESS, "preview_pitch": 2.5,
            "crossfade": 1.0, "stream": True,
            "map_orient": config.MAP_ORIENT, "map_rotate_x": config.MAP_ROTATE_X, "map_rotate_y": config.MAP_ROTATE_Y,
            "map_rotate_z": config.MAP_ROTATE_Z, "view_azimuth": config.VIEW_AZIMUTH, "view_elevation": config.VIEW_ELEVATION,
            "view_roll": config.VIEW_ROLL, "view_distance": config.VIEW_DISTANCE,
            "mirror_leds": list(common.parse_mirror(config.MIRROR_LEDS) or [])}


def view_from_globals(g):
    return common.View(g["map_orient"], g["map_rotate_x"], g["map_rotate_y"], g["map_rotate_z"],
                       g["view_azimuth"], g["view_elevation"], g["view_roll"], g["view_distance"],
                       common.parse_mirror(g["mirror_leds"]))


# ---------------------------------------------------------------- effects and their options
class _Captured(Exception):
    pass


def capture_parser(mod):
    """Runs the script's main() just far enough to get hold of its argument parser."""
    box = {}

    def fake(self, args=None, namespace=None):
        box["parser"] = self
        raise _Captured

    argparse.ArgumentParser.parse_args = fake
    old_argv, sys.argv = sys.argv, [mod.__name__]
    try:
        mod.main()
    except _Captured:
        pass
    finally:
        argparse.ArgumentParser.parse_args = ORIG_PARSE
        sys.argv = old_argv
    return box["parser"]


def param_specs(parser):
    specs = []
    for a in parser._actions:
        if a.dest in GLOBAL_DESTS or isinstance(a, argparse._HelpAction):
            continue
        cls = type(a).__name__
        if cls in ("_StoreTrueAction", "BooleanOptionalAction"):
            kind = "bool"
        elif a.choices:
            kind = "choice"
        elif a.type is int:
            kind = "int"
        elif a.type is float:
            kind = "float"
        else:
            kind = "str"
        specs.append({"dest": a.dest, "kind": kind, "default": a.default, "choices": list(a.choices) if a.choices else None,
                      "help": (a.help or "").replace("%%", "%"), "label": a.option_strings[0].lstrip("-").replace("-", " ")})
    return specs


def gui_read_keys(wasd):
    """Replaces the games' console key reader: only the live thread gets the window's key presses."""
    if threading.current_thread().name != "live":
        return []
    keys = []
    while True:
        try:
            k = KEYS.get_nowait()
        except queue.Empty:
            return keys
        if k in ("a", "d", "w", "s"):
            if wasd:
                keys.append({"a": "left", "d": "right", "w": "rotate", "s": "down"}[k])
        else:
            keys.append(k)


class Effect:
    def __init__(self, label, name):
        self.label, self.name, self.error, self.parser, self.specs, self.module = label, name, "", None, [], None
        try:
            self.module = importlib.import_module(name)
            if hasattr(self.module, "read_keys"):
                self.module.read_keys = gui_read_keys
            self.parser = capture_parser(self.module)
            self.specs = param_specs(self.parser)
        except (Exception, SystemExit) as e:
            self.error = str(e) or type(e).__name__


# ---------------------------------------------------------------- the engine: live stream + previews
class Deck:
    def __init__(self, ident, effect, values):
        self.id, self.effect, self.values, self.version = ident, effect, values, 0
        self.preview, self.error = None, ""

    def cur(self):
        return self.values.setdefault(self.effect, {})


class Engine:
    def __init__(self, decks, g, effects):
        self.decks, self.g, self.effects = decks, g, effects
        self.live, self.fade, self.insts = "A", None, {"A": None, "B": None}
        self.stop, self.blackout, self.gver = False, False, 0
        self.analyzer, self.lock = None, threading.Lock()
        self.sent_fps, self.live_error = 0, ""
        self._leds_cache = (None, None)

    # -- helpers
    def namespace(self, deck):
        eff = self.effects[deck.effect]
        ns = ORIG_PARSE(eff.parser, [])
        for k, v in deck.cur().items():
            if hasattr(ns, k):
                setattr(ns, k, v)
        g = self.g
        ns.host, ns.leds, ns.map, ns.fps = g["host"], g["leds"], g["map"] or None, g["fps"]
        ns.leds_per_module = g["leds_per_module"]
        ns.skip_first, ns.skip_last, ns.min_y, ns.max_y, ns.flip_v = g["skip_first"], g["skip_last"], g["min_y"], g["max_y"], g["flip_v"]
        ns.flip_x = g["flip_x"]
        ns.map_orient, ns.map_rotate_x, ns.map_rotate_y, ns.map_rotate_z = (
            g["map_orient"], g["map_rotate_x"], g["map_rotate_y"], g["map_rotate_z"])
        ns.view_azimuth, ns.view_elevation, ns.view_roll, ns.view_distance = (
            g["view_azimuth"], g["view_elevation"], g["view_roll"], g["view_distance"])
        ns.mirror_leds = g["mirror_leds"]
        ns.background, ns.bg_brightness, ns.duration = g["background"], g["bg_brightness"], 0
        return ns

    def real_leds(self):
        g = self.g
        key = (g["map"], g["skip_first"], g["skip_last"], g["min_y"], g["max_y"], g["flip_v"], g["flip_x"],
               tuple(view_from_globals(g)))
        if self._leds_cache[0] != key:
            path = g["map"] or common.newest_map()
            self._leds_cache = (key, common.load_map(path, g["flip_v"], g["skip_first"], g["skip_last"], g["min_y"], g["max_y"], g["flip_x"],
                                                   view_from_globals(g)))
        return self._leds_cache[1]

    def virtual_leds(self):
        """A fine grid over the same area as the real LEDs, numbered in a snake from the bottom."""
        real = self.real_leds()
        xs, ys = [l.xcm for l in real], [l.ycm for l in real]
        xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
        pitch = max(0.8, self.g["preview_pitch"])
        nx, ny = max(2, round((xmax - xmin) / pitch)), max(2, round((ymax - ymin) / pitch))
        leds, offsets, i = [], [], 0
        for row in range(ny):
            for col in (range(nx) if row % 2 == 0 else range(nx - 1, -1, -1)):
                leds.append(common.Led(i, (row + 0.5) / ny, (col + 0.5) / nx, xmin + (col + 0.5) / nx * (xmax - xmin),
                                       ymin + (row + 0.5) / ny * (ymax - ymin)))
                offsets.append(((ny - 1 - row) * nx + col) * 3)
                i += 1
        return leds, offsets, nx, ny

    def get_analyzer(self, ns):
        with self.lock:
            if self.analyzer is None:
                import audio
                self.analyzer = audio.Analyzer(getattr(ns, "device", None), getattr(ns, "gain", 1.0),
                                               getattr(ns, "low_latency", False)).start()
            return self.analyzer

    def make_render(self, deck, leds, ns, preview):
        eff = self.effects[deck.effect]
        if eff.error:
            raise RuntimeError(f"{eff.label}: {eff.error}")
        if preview:
            ns = copy.copy(ns)
            ns.leds = len(leds)
            if hasattr(ns, "demo"):
                ns.demo = True                              # games play themselves in the preview
        if deck.effect == "music":
            return eff.module.build(leds, ns, self.get_analyzer(ns))
        return eff.module.build(leds, ns)

    # -- live stream
    def render_real(self, ident):
        deck = self.decks[ident]
        inst = self.insts[ident]
        if inst is None or inst["version"] != deck.version or inst["gver"] != self.gver:
            ns = self.namespace(deck)
            inst = {"render": self.make_render(deck, self.real_leds(), ns, False), "version": deck.version,
                    "gver": self.gver, "t0": time.time()}
            self.insts[ident] = inst
        return inst["render"](time.time() - inst["t0"])

    def live_frame(self):
        fade = self.fade
        if not fade:
            return self.render_real(self.live)
        k = (time.time() - fade["t0"]) / max(0.001, fade["dur"])
        b = self.render_real(fade["to"])
        if k >= 1:
            self.live, self.fade = fade["to"], None
            self.insts[fade["from"]] = None
            return b
        a = self.render_real(fade["from"])
        return bytearray(int(x + (y - x) * k) for x, y in zip(a, b))

    def switch(self):
        target = "B" if self.live == "A" else "A"
        dur = float(self.g["crossfade"])
        if dur <= 0:
            self.live, self.fade = target, None
        else:
            self.fade = {"from": self.live, "to": target, "t0": time.time(), "dur": dur}

    def shown(self):
        """The deck the UI should mark live (the target while fading)."""
        return self.fade["to"] if self.fade else self.live

    def live_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        seq, next_tick, win_start, win_n = 0, time.perf_counter(), time.perf_counter(), 0
        while not self.stop:
            fps = max(5, min(100, float(self.g["fps"])))
            frame = None
            try:
                frame = self.live_frame()
                self.live_error = ""
            except Exception as e:                          # a broken effect must not kill the stream
                self.live_error = f"{type(e).__name__}: {e}"
                self.insts[self.live] = None
                time.sleep(0.5)
            if self.blackout:
                frame = bytearray(self.g["leds"] * 3)
            if frame is not None and self.g["stream"]:
                seq = seq % 15 + 1
                try:
                    common.send_frame(sock, self.g["host"], self.g["leds_per_module"], frame, seq)
                except OSError:
                    pass
                win_n += 1
            now = time.perf_counter()
            if now - win_start >= 1.0:
                self.sent_fps, win_start, win_n = win_n, now, 0
            next_tick += 1 / fps
            delay = next_tick - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.25:
                next_tick = time.perf_counter()

    # -- previews
    def preview_loop(self, deck):
        inst = None
        while not self.stop:
            t_start = time.perf_counter()
            try:
                pitch = self.g["preview_pitch"]
                if inst is None or inst["version"] != deck.version or inst["gver"] != self.gver or inst["pitch"] != pitch:
                    leds, offsets, nx, ny = self.virtual_leds()
                    ns = self.namespace(deck)
                    ns.leds = len(leds)
                    inst = {"render": self.make_render(deck, leds, ns, True), "version": deck.version, "gver": self.gver,
                            "pitch": pitch, "t0": time.time(), "offsets": offsets, "nx": nx, "ny": ny}
                frame = inst["render"](time.time() - inst["t0"])
                img = bytearray(inst["nx"] * inst["ny"] * 3)
                for i, off in enumerate(inst["offsets"]):
                    img[off:off + 3] = frame[i * 3:i * 3 + 3]
                deck.preview = (inst["nx"], inst["ny"], bytes(img))
                deck.error = ""
            except Exception as e:
                deck.error = f"{type(e).__name__}: {e}"
                inst = None
                time.sleep(0.7)
            time.sleep(max(0.0, 0.1 - (time.perf_counter() - t_start)))

    def start(self):
        threading.Thread(target=self.live_loop, name="live", daemon=True).start()
        for deck in self.decks.values():
            threading.Thread(target=self.preview_loop, args=(deck,), name="preview-" + deck.id, daemon=True).start()


# ---------------------------------------------------------------- the ESP32
class Module:
    def __init__(self, g):
        self.g, self.status, self.stop = g, None, False
        self.statuses = []                                  # one per module, None where it did not answer

    def hosts(self):
        return common.parse_hosts(self.g["host"])

    @staticmethod
    def url(host, path, **params):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return f"http://{host}{path}{q}"

    def poll_loop(self):
        while not self.stop:
            statuses = []
            for host in self.hosts():
                try:
                    with urllib.request.urlopen(self.url(host, "/status"), timeout=2) as r:
                        statuses.append(json.load(r))
                except Exception:
                    statuses.append(None)
            self.statuses = statuses
            self.status = next((s for s in statuses if s), None)      # the first module that answers
            time.sleep(1.0)

    def call(self, path, **params):
        """The same setting for every module."""
        self.call_each(path, lambda k: params)

    def call_each(self, path, params_for):
        """params_for(k) gives the settings for the k-th module."""
        def go(host, params):
            try:
                urllib.request.urlopen(self.url(host, path, **params), timeout=4).read()
            except Exception:
                pass
        for k, host in enumerate(self.hosts()):
            threading.Thread(target=go, args=(host, params_for(k)), daemon=True).start()


# ---------------------------------------------------------------- the window
class DeckPanel:
    def __init__(self, app, parent, deck):
        self.app, self.deck, self.timer, self.building = app, deck, None, False
        self.frame = tk.Frame(parent, bg=PANEL, highlightthickness=3, highlightbackground=PANEL)
        head = tk.Frame(self.frame, bg=PANEL)
        head.pack(fill="x", padx=8, pady=(8, 2))
        tk.Label(head, text=f"DECK {deck.id}", bg=PANEL, fg=FG, font=("Segoe UI", 13, "bold")).pack(side="left")
        self.badge = tk.Label(head, text="", bg=PANEL, fg=DIM, font=("Segoe UI", 10, "bold"))
        self.badge.pack(side="right")
        self.combo = ttk.Combobox(self.frame, state="readonly", values=[e.label for e in app.effects.values()])
        self.combo.set(app.effects[deck.effect].label)
        self.combo.pack(fill="x", padx=8, pady=4)
        self.combo.bind("<<ComboboxSelected>>", self.effect_chosen)
        cols = tk.Frame(self.frame, bg=PANEL)
        cols.pack(fill="both", expand=True, padx=8, pady=4)
        left = tk.Frame(cols, bg=PANEL)
        left.pack(side="left", anchor="n")
        self.image = tk.Label(left, bg="black", width=PREVIEW_W, height=int(PREVIEW_W * 1.7))
        self.image.pack()
        self.image.configure(width=PREVIEW_W, height=int(PREVIEW_W * 1.7))
        self.error = tk.Label(left, text="", bg=PANEL, fg=LIVE, wraplength=PREVIEW_W, justify="left", font=("Segoe UI", 8))
        self.error.pack(fill="x")
        box = tk.Frame(cols, bg=PANEL)
        box.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.canvas = tk.Canvas(box, bg=PANEL, highlightthickness=0, width=FORM_W)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=PANEL)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", lambda ev: self.canvas.yview_scroll(-1 * (ev.delta // 120), "units")))
        self.photo = None
        self.shown_preview = None
        self.build_params()

    def effect_chosen(self, _event=None):
        label = self.combo.get()
        self.deck.effect = next(e.name for e in self.app.effects.values() if e.label == label)
        self.deck.version += 1
        self.build_params()
        self.app.save_later()

    def build_params(self):
        for w in self.inner.winfo_children():
            w.destroy()
        eff = self.app.effects[self.deck.effect]
        self.building = True
        if eff.error:
            tk.Label(self.inner, text=eff.error, bg=PANEL, fg=LIVE, wraplength=FORM_W - 20, justify="left").pack(anchor="w")
        for spec in eff.specs:
            value = self.deck.cur().get(spec["dest"], spec["default"])
            row = tk.Frame(self.inner, bg=PANEL)
            row.pack(fill="x", pady=(5, 0))
            if spec["kind"] == "bool":
                var = tk.BooleanVar(value=bool(value))
                ttk.Checkbutton(row, text=spec["label"], variable=var).pack(anchor="w")
            else:
                tk.Label(row, text=spec["label"], bg=PANEL, fg=FG, font=("Segoe UI", 9)).pack(anchor="w")
                var = tk.StringVar(value="" if value is None else str(value))
                if spec["kind"] == "choice":
                    ttk.Combobox(row, textvariable=var, values=spec["choices"], state="readonly").pack(fill="x")
                elif spec["kind"] in ("int", "float"):
                    base = abs(spec["default"]) if isinstance(spec["default"], (int, float)) and spec["default"] else 1.0
                    step = 1 if spec["kind"] == "int" else 10 ** (math.floor(math.log10(base)) - 1)
                    ttk.Spinbox(row, textvariable=var, from_=-1e6, to=1e6, increment=step).pack(fill="x")
                else:
                    ttk.Entry(row, textvariable=var).pack(fill="x")
            if spec["help"]:
                tk.Label(row, text=spec["help"], bg=PANEL, fg=DIM, wraplength=FORM_W - 24, justify="left",
                         font=("Segoe UI", 8)).pack(anchor="w")
            var.trace_add("write", lambda *_a, s=spec, v=var: self.changed(s, v))
        self.building = False
        self.canvas.yview_moveto(0)

    def changed(self, spec, var):
        if self.building:
            return
        raw = var.get()
        try:
            if spec["kind"] == "bool":
                value = bool(raw)
            elif spec["kind"] in ("int", "float"):
                if str(raw).strip() == "":
                    value = None if spec["default"] is None else spec["default"]
                else:
                    value = int(float(raw)) if spec["kind"] == "int" else float(raw)
            else:
                value = raw if (raw != "" or spec["default"] is not None) else None
        except ValueError:
            return                                          # half-typed number: wait for the rest
        self.deck.cur()[spec["dest"]] = value
        if self.timer:
            self.app.root.after_cancel(self.timer)
        self.timer = self.app.root.after(250, self.apply)

    def apply(self):
        self.timer = None
        self.deck.version += 1
        self.app.save_later()

    def refresh(self, live):
        self.badge.configure(text="● LIVE" if live else "standby", fg=LIVE if live else DIM)
        self.frame.configure(highlightbackground=LIVE if live else PANEL)
        self.error.configure(text=self.deck.error)
        pv = self.deck.preview
        if pv is not None and pv is not self.shown_preview:
            self.shown_preview = pv
            nx, ny, data = pv
            h = int(PREVIEW_W * ny / nx)
            img = Image.frombytes("RGB", (nx, ny), data).resize((PREVIEW_W, h), Image.BICUBIC)
            self.photo = ImageTk.PhotoImage(img)
            self.image.configure(image=self.photo, width=PREVIEW_W, height=h)


class MapViewer:
    """Looks at the LEDs of a 3D map: turn it with the mouse, pick the mirror plane, choose where the effects look from.

    The picture has two modes. The 3D view (default) shows the whole map as it is, turns with the mouse (drag, wheel
    to zoom) and lets you click three LEDs to define a mirror plane. "What the effects see" shows the flat picture
    from the viewpoint of the sliders, with the mirror folded in, which is what the decks draw on.
    """

    SIZE = 560
    SLIDERS = [("Turn the map about x", "map_rotate_x", -180, 180), ("Turn the map about y", "map_rotate_y", -180, 180),
               ("Turn the map about z", "map_rotate_z", -180, 180), ("Look from: left - right", "view_azimuth", -180, 180),
               ("Look from: below - above", "view_elevation", -90, 90), ("Tilt the picture", "view_roll", -180, 180),
               ("Perspective: viewer distance (cm, 0 = none)", "view_distance", 0, 1500)]

    def __init__(self, app):
        self.app, self.rows, self.rows_path, self.photo = app, None, None, None
        self.orbit = {"az": 30.0, "el": 15.0, "zoom": 1.0}              # how the 3D view is turned (not the effects' viewpoint)
        self.picks = list(app.g["mirror_leds"])                          # LED numbers picked for the mirror plane
        self.shown = []                                                  # (x, y, LED number) on screen, for picking
        self.press = None
        self.win = tk.Toplevel(app.root)
        self.win.title("Map viewer")
        self.win.configure(bg=PANEL)
        self.picture = tk.Label(self.win, bg="black", cursor="fleur")
        self.picture.pack(side="left", padx=10, pady=10, anchor="n")
        self.picture.bind("<ButtonPress-1>", self.mouse_down)
        self.picture.bind("<B1-Motion>", self.mouse_drag)
        self.picture.bind("<ButtonRelease-1>", self.mouse_up)
        self.picture.bind("<MouseWheel>", self.mouse_wheel)
        side = tk.Frame(self.win, bg=PANEL)
        side.pack(side="left", fill="y", padx=(0, 12), pady=10)
        self.info = tk.Label(side, bg=PANEL, fg=DIM, justify="left", wraplength=320, font=("Segoe UI", 9))
        self.info.pack(anchor="w")

        self.folded = tk.BooleanVar(value=False)
        ttk.Checkbutton(side, text="Show what the effects see (flat, mirror folded)", variable=self.folded,
                        command=self.redraw).pack(anchor="w", pady=(8, 0))

        self.section(side, "Mirror (a symmetric object, e.g. the tractor)")
        self.pick_mode = tk.BooleanVar(value=False)
        ttk.Checkbutton(side, text="Pick mode: click 3 LEDs in the 3D view", variable=self.pick_mode,
                        command=self.redraw).pack(anchor="w")
        self.pick_text = tk.StringVar(value=", ".join(str(n) for n in self.picks))
        row = tk.Frame(side, bg=PANEL)
        row.pack(fill="x", pady=2)
        tk.Label(row, text="LED numbers", bg=PANEL, fg=FG, font=("Segoe UI", 9)).pack(side="left")
        entry = ttk.Entry(row, textvariable=self.pick_text, width=18)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self.picks_typed())
        ttk.Button(row, text="Set", width=5, command=self.picks_typed).pack(side="left")
        ttk.Button(side, text="No mirror", command=self.clear_picks).pack(anchor="w", pady=2)
        tk.Label(side, text="Pick three LEDs that lie in the middle plane of the object (not in a line). The side of the "
                            "plane away from the viewpoint then copies the near side, so both sides show the same picture.",
                 bg=PANEL, fg=DIM, wraplength=320, justify="left", font=("Segoe UI", 8)).pack(anchor="w")

        self.section(side, "Stand the map upright")
        self.orient = tk.BooleanVar(value=app.g["map_orient"] == "auto")
        ttk.Checkbutton(side, text="Stand a flat sheet upright by itself (curtain)", variable=self.orient,
                        command=self.orient_changed).pack(anchor="w", pady=(2, 2))
        tk.Label(side, text="Not a flat sheet (a tractor)? Untick it and turn the map until the tractor stands "
                            "upright with its front towards you.", bg=PANEL, fg=DIM, wraplength=320, justify="left",
                 font=("Segoe UI", 8)).pack(anchor="w")
        self.vars = {}
        for label, key, lo, hi in self.SLIDERS:
            if key == "view_azimuth":
                self.section(side, "Where the effects look from")
            tk.Label(side, text=label, bg=PANEL, fg=FG, font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
            row = tk.Frame(side, bg=PANEL)
            row.pack(fill="x")
            var = tk.DoubleVar(value=app.g[key])
            self.vars[key] = var
            value = tk.Label(row, text=f"{app.g[key]:.0f}", bg=PANEL, fg=FG, width=5)
            scale = ttk.Scale(row, from_=lo, to=hi, variable=var, length=250,
                              command=lambda v, k=key, lab=value: self.moved(k, float(v), lab))
            scale.pack(side="left")
            scale.bind("<ButtonRelease-1>", lambda e: self.released())
            value.pack(side="left", padx=4)
        presets = tk.Frame(side, bg=PANEL)
        presets.pack(anchor="w", pady=8)
        for text, az, el in (("Front", 0, 0), ("Right", 90, 0), ("Back", 180, 0), ("Left", -90, 0), ("Top", 0, 90)):
            ttk.Button(presets, text=text, width=6, command=lambda a=az, e=el: self.preset(a, e)).pack(side="left", padx=2)
        ttk.Button(side, text="Reset everything", command=self.reset).pack(anchor="w")
        self.redraw()

    @staticmethod
    def section(parent, text):
        tk.Frame(parent, bg="#3a3f4a", height=1).pack(fill="x", pady=(10, 4))
        tk.Label(parent, text=text, bg=PANEL, fg=FG, font=("Segoe UI", 10, "bold")).pack(anchor="w")

    # -- settings
    def preset(self, azimuth, elevation):
        for key, value in (("view_azimuth", azimuth), ("view_elevation", elevation)):
            self.vars[key].set(value)
            self.app.g[key] = float(value)
        self.released()

    def reset(self):
        for _label, key, _lo, _hi in self.SLIDERS:
            self.vars[key].set(0)
            self.app.g[key] = 0.0
        self.orient.set(True)
        self.app.g["map_orient"] = "auto"
        self.clear_picks()

    def orient_changed(self):
        self.app.g["map_orient"] = "auto" if self.orient.get() else "none"
        self.released()

    def moved(self, key, value, label):
        label.configure(text=f"{value:.0f}")
        self.app.g[key] = value
        self.redraw()                                      # the picture follows the slider, the effects catch up on release

    def released(self):
        self.app.engine.gver += 1                          # the decks rebuild on the new view
        self.app.save_later()
        self.redraw()

    # -- the mirror plane
    def set_picks(self, picks):
        self.picks = list(picks)
        self.pick_text.set(", ".join(str(n) for n in self.picks))
        if len(self.picks) == 3 or not self.picks:         # the effects only see a finished plane
            self.app.g["mirror_leds"] = list(self.picks)
            self.released()
        else:
            self.redraw()

    def picks_typed(self):
        try:
            picks = [int(v) for v in self.pick_text.get().replace(";", ",").split(",") if v.strip()]
        except ValueError:
            return
        self.set_picks(picks[:3])

    def clear_picks(self):
        self.set_picks([])

    # -- the mouse
    def mouse_down(self, event):
        self.press = (event.x, event.y, event.x, event.y)

    def mouse_drag(self, event):
        if self.press is None or self.folded.get():
            return
        x0, y0, x, y = self.press
        self.orbit["az"] -= (event.x - x) * 0.5
        self.orbit["el"] = max(-89.0, min(89.0, self.orbit["el"] + (event.y - y) * 0.5))
        self.press = (x0, y0, event.x, event.y)
        self.redraw()

    def mouse_up(self, event):
        if self.press is None:
            return
        x0, y0 = self.press[:2]
        self.press = None
        if abs(event.x - x0) > 4 or abs(event.y - y0) > 4 or not self.pick_mode.get() or self.folded.get():
            return                                          # it was a drag, or picking is off
        near = [(math.hypot(x - event.x, y - event.y), n) for x, y, n in self.shown]
        near = [c for c in near if c[0] <= 12]
        if near:
            led = min(near)[1]
            picks = self.picks if len(self.picks) < 3 else []
            if led not in picks:
                self.set_picks(picks + [led])

    def mouse_wheel(self, event):
        self.orbit["zoom"] = max(0.3, min(6.0, self.orbit["zoom"] * (1.1 if event.delta > 0 else 1 / 1.1)))
        self.redraw()

    # -- drawing
    def load_rows(self, path):
        if self.rows_path != path:
            with open(path, newline="") as f:
                self.rows, self.rows_path = list(csv.DictReader(f)), path

    def dot_colour(self, number, top, near):
        import colorsys
        return tuple(int(255 * c * near) for c in colorsys.hsv_to_rgb(0.85 * number / max(top, 1), 0.85, 1.0))

    def redraw(self):
        from PIL import ImageDraw
        g, size = self.app.g, self.SIZE
        path = g["map"] or common.newest_map()
        image = Image.new("RGB", (size, size), (20, 22, 27))
        draw = ImageDraw.Draw(image)
        self.shown = []
        if not common.is_3d_map(path):
            draw.text((20, 20), "This is a flat map, there is no viewpoint to set.\nChoose a MariMapper 3D map\n"
                                "(the file with x, y, z columns).", fill=(232, 232, 232))
            self.info.configure(text=os.path.basename(path))
        else:
            self.load_rows(path)
            if self.folded.get():
                self.draw_folded(draw, path)
            else:
                image = self.draw_3d(image, path)
        self.photo = ImageTk.PhotoImage(image)
        self.picture.configure(image=self.photo)

    def draw_folded(self, draw, path):
        import numpy as np
        g, size = self.app.g, self.SIZE
        view = view_from_globals(g)
        pts = common.map_3d_to_view(self.rows, view)
        xs = np.array([p[1] for p in pts])
        ys = np.array([p[2] for p in pts])
        depth = np.array([p[3] for p in pts])
        span = max(xs.max() - xs.min(), ys.max() - ys.min(), 1e-6)
        scale = (size - 120) / span
        ox = (size - (xs.max() - xs.min()) * scale) / 2 - xs.min() * scale
        oy = (size + (ys.max() - ys.min()) * scale) / 2 + ys.min() * scale
        span_d = max(depth.max() - depth.min(), 1e-6)
        top = max(p[0] for p in pts)
        for k in np.argsort(depth):                         # far dots first
            near = 0.45 + 0.55 * (depth[k] - depth.min()) / span_d
            px, py = xs[k] * scale + ox, oy - ys[k] * scale
            draw.ellipse([px - 4, py - 4, px + 4, py + 4], fill=self.dot_colour(pts[k][0], top, near))
        self.draw_axes(draw, view)
        mirror = f" The mirror ({', '.join(str(n) for n in view.mirror)}) is folded in: the far side copies the near side." if view.mirror else ""
        self.info.configure(text=f"{os.path.basename(path)}\n{len(pts)} LEDs, the picture is {xs.max() - xs.min():.0f} x "
                                 f"{ys.max() - ys.min():.0f} cm. This is what the decks draw on.{mirror}")

    def draw_axes(self, draw, view):
        import numpy as np
        ax, ay, _ = common.project_3d(np.eye(3) * 1.0, view)
        size = self.SIZE
        for k, (name, col) in enumerate((("x right", (255, 90, 90)), ("y up", (90, 255, 120)), ("z front", (110, 160, 255)))):
            end = (60 + ax[k] * 40, size - 60 - ay[k] * 40)
            draw.line([(60, size - 60), end], fill=col, width=2)
            draw.text((end[0] + 4, end[1] - 6), name, fill=col)

    def draw_3d(self, image, path):
        import numpy as np
        from PIL import ImageDraw
        g, size = self.app.g, self.SIZE
        view = view_from_globals(g)
        index, points, _ = common.oriented_map(self.rows, view)          # upright, not folded and not yet viewed from anywhere
        centre = (points.min(axis=0) + points.max(axis=0)) / 2
        extent = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0))) or 1.0
        cam = common.View("none", 0, 0, 0, self.orbit["az"], self.orbit["el"], 0, extent * 2.5)   # perspective, so it is really 3D
        plane = None
        extra = []                                                       # corners of the plane, to be projected with the LEDs
        if len(self.picks) == 3:
            try:
                a, normal = common.mirror_plane(points, index, tuple(self.picks))
                u = np.cross(normal, [0.0, 1.0, 0.0])
                if np.linalg.norm(u) < 1e-6:
                    u = np.cross(normal, [1.0, 0.0, 0.0])
                u /= np.linalg.norm(u)
                v = np.cross(normal, u)
                half = extent * 0.5
                plane = [a + u * s * half + v * t * half for s, t in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
                extra = plane
            except SystemExit as e:
                self.info.configure(text=str(e))
        everything = np.vstack([points - centre] + ([np.array(extra) - centre] if extra else []))
        x, y, depth = common.project_3d(everything, cam)
        scale = (size - 100) / extent * self.orbit["zoom"]
        sx, sy = size / 2 + x * scale, size / 2 - y * scale
        n = len(points)
        top = int(index.max())
        near_all = (depth[:n] - depth[:n].min()) / max(depth[:n].max() - depth[:n].min(), 1e-6)
        image = image.convert("RGBA")
        if plane is not None:                                            # the plane, as a see-through sheet behind the LEDs
            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).polygon([(sx[n + k], sy[n + k]) for k in range(4)], fill=(255, 190, 0, 55), outline=(255, 190, 0, 230))
            image = Image.alpha_composite(image, layer)
        draw = ImageDraw.Draw(image)
        order = np.argsort(depth[:n])                                    # far dots first
        by_number = {int(index[k]): k for k in range(n)}
        for k in order:
            number = int(index[k])
            px, py, near = sx[k], sy[k], near_all[k]
            r = 2.2 + 3.2 * near
            self.shown.append((px, py, number))
            draw.ellipse([px - r, py - r, px + r, py + r], fill=self.dot_colour(number, top, 0.35 + 0.65 * near))
        for slot, number in enumerate(self.picks):                       # picked LEDs ring and label
            k = by_number.get(number)
            if k is not None:
                draw.ellipse([sx[k] - 9, sy[k] - 9, sx[k] + 9, sy[k] + 9], outline=(255, 190, 0), width=2)
                draw.text((sx[k] + 11, sy[k] - 6), f"{slot + 1}: LED {number}", fill=(255, 190, 0))
        self.draw_axes(draw, common.View("none", 0, 0, 0, self.orbit["az"], self.orbit["el"], 0, 0))
        mode = "Pick mode is ON: click three LEDs in the middle plane." if self.pick_mode.get() else "Drag to turn, wheel to zoom."
        planes = f" Mirror plane through LEDs {', '.join(str(n) for n in self.picks)}." if plane is not None else ""
        self.info.configure(text=f"{os.path.basename(path)}\n{n} LEDs, {extent:.0f} cm across. {mode}{planes} Colours follow the "
                                 f"LED numbers (red to violet), bigger dots are nearer.")
        return image.convert("RGB")


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("XMasTractor light show")
        self.root.configure(bg=BG)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()      # fit small / scaled screens
        self.root.geometry(f"{min(1560, sw - 16)}x{min(980, sh - 90)}+8+8")
        self.root.minsize(1100, 700)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=PANEL, foreground=FG, fieldbackground="#30343d", bordercolor="#3a3f4a")
        style.map("TCombobox", fieldbackground=[("readonly", "#30343d")], foreground=[("readonly", FG)],
                  selectbackground=[("readonly", "#30343d")], selectforeground=[("readonly", FG)])
        style.configure("TCombobox", arrowcolor=FG, background="#3a3f4a")
        style.configure("TSpinbox", arrowcolor=FG, background="#3a3f4a", foreground=FG)
        style.configure("Horizontal.TScale", background=PANEL, troughcolor="#30343d")
        self.root.option_add("*TCombobox*Listbox.background", "#30343d")
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        style.configure("TCheckbutton", background=PANEL, foreground=FG)
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("Big.TButton", font=("Segoe UI", 13, "bold"), padding=10)
        state = self.load_state()
        self.g = default_globals()
        self.g.update(state.get("g", {}))
        self.effects = {name: Effect(label, name) for label, name in EFFECTS}
        decks = {}
        for ident, default in (("A", "tree"), ("B", "snow")):
            d = state.get("decks", {}).get(ident, {})
            effect = d.get("effect", default)
            decks[ident] = Deck(ident, effect if effect in self.effects else default, d.get("values", {}))
        self.engine = Engine(decks, self.g, self.effects)
        self.module = Module(self.g)
        self.save_timer = None
        self.viewer = None
        self.synced = False
        self.vars = {}
        self.build_ui(decks)
        self.engine.start()
        threading.Thread(target=self.module.poll_loop, daemon=True).start()
        self.root.bind_all("<KeyPress>", self.on_key)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self.tick)

    # -- persistence
    def load_state(self):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_later(self):
        if self.save_timer:
            self.root.after_cancel(self.save_timer)
        self.save_timer = self.root.after(800, self.save_now)

    def save_now(self):
        self.save_timer = None
        data = {"g": self.g, "decks": {i: {"effect": d.effect, "values": d.values} for i, d in self.engine.decks.items()}}
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        except OSError:
            pass

    # -- layout
    def build_ui(self, decks):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=10, pady=(8, 2))
        self.status = tk.Label(top, text="connecting...", bg=BG, fg=DIM, anchor="w", font=("Segoe UI", 10))
        self.status.pack(side="left", fill="x", expand=True)
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=6)
        body.columnconfigure(0, weight=1, uniform="deck")
        body.columnconfigure(2, weight=1, uniform="deck")
        body.rowconfigure(0, weight=1)
        self.panels = {}
        self.panels["A"] = DeckPanel(self, body, decks["A"])
        self.panels["A"].frame.grid(row=0, column=0, sticky="nsew")
        self.build_middle(body).grid(row=0, column=1, sticky="ns", padx=10)
        self.panels["B"] = DeckPanel(self, body, decks["B"])
        self.panels["B"].frame.grid(row=0, column=2, sticky="nsew")
        self.hint = tk.Label(self.root, bg=BG, fg=DIM, font=("Segoe UI", 9),
                             text="Games on the live deck take their keys from this window (arrows, Space, Z, P, R) - "
                                  "click anywhere outside a text box first.")
        self.hint.pack(fill="x", padx=10, pady=(0, 6))

    def add_var(self, key, var):
        self.vars[key] = var
        return var

    def build_middle(self, parent):
        mid = tk.Frame(parent, bg=PANEL, width=250)
        mid.pack_propagate(False)
        self.take = ttk.Button(mid, text="Take B live  >", style="Big.TButton", command=self.take_live)
        self.take.pack(fill="x", padx=10, pady=(12, 4))
        self.fade_label = tk.Label(mid, bg=PANEL, fg=DIM, font=("Segoe UI", 9))
        self.fade_label.pack()
        fade = tk.DoubleVar(value=self.g["crossfade"])
        ttk.Scale(mid, from_=0, to=5, variable=fade, command=lambda v: self.set_g("crossfade", round(float(v), 1))).pack(fill="x", padx=14)
        self.fade_var = fade
        self.stream_var = tk.BooleanVar(value=self.g["stream"])
        ttk.Checkbutton(mid, text="Stream to the module", variable=self.stream_var,
                        command=lambda: self.set_g("stream", self.stream_var.get())).pack(anchor="w", padx=12, pady=(8, 0))
        self.black_btn = ttk.Button(mid, text="Blackout", command=self.toggle_blackout)
        self.black_btn.pack(fill="x", padx=10, pady=6)
        self.section(mid, "ESP32 module")
        self.entry_row(mid, "Address(es)", "host", str, live=True)
        self.entry_row(mid, "Most LEDs per module", "leds_per_module", int, live=True)
        tk.Label(mid, text="Brightness", bg=PANEL, fg=FG, font=("Segoe UI", 9)).pack(anchor="w", padx=12)
        self.bri = tk.IntVar(value=80)
        scale = ttk.Scale(mid, from_=1, to=255, variable=self.bri)
        scale.pack(fill="x", padx=14)
        scale.bind("<ButtonRelease-1>", lambda e: self.module.call("/set", bri=int(self.bri.get())))
        tk.Label(mid, text="LED colour order", bg=PANEL, fg=FG, font=("Segoe UI", 9)).pack(anchor="w", padx=12)
        self.order = ttk.Combobox(mid, values=ORDERS, state="readonly")
        self.order.pack(fill="x", padx=12)
        self.order.bind("<<ComboboxSelected>>", lambda e: self.module.call("/set", order=self.order.get()))
        self.skipboot = tk.BooleanVar()
        ttk.Checkbutton(mid, text="Skip the boot light show", variable=self.skipboot,
                        command=lambda: self.module.call("/set", skipboot=int(self.skipboot.get()))).pack(anchor="w", padx=12, pady=4)
        row = tk.Frame(mid, bg=PANEL)
        row.pack(fill="x", padx=12)
        tk.Label(row, text="LEDs in total", bg=PANEL, fg=FG, font=("Segoe UI", 9)).pack(side="left")
        self.led_count = tk.StringVar(value=str(self.g["leds"]))
        ttk.Entry(row, textvariable=self.led_count, width=6).pack(side="left", padx=6)
        ttk.Button(row, text="Apply", width=6, command=self.apply_leds).pack(side="left")
        self.section(mid, "Map and look")
        self.map_var = tk.StringVar(value=self.g["map"] or "(newest curtain map)")
        tk.Label(mid, textvariable=self.map_var, bg=PANEL, fg=DIM, font=("Segoe UI", 8), wraplength=220, justify="left").pack(anchor="w", padx=12)
        ttk.Button(mid, text="Choose map file...", command=self.choose_map).pack(fill="x", padx=12, pady=2)
        ttk.Button(mid, text="3D map view...", command=self.open_map_viewer).pack(fill="x", padx=12, pady=2)
        self.flipx = tk.BooleanVar(value=self.g["flip_x"])
        ttk.Checkbutton(mid, text="Mirror left-right", variable=self.flipx,
                        command=lambda: self.set_g("flip_x", self.flipx.get(), rebuild=True)).pack(anchor="w", padx=12, pady=2)
        self.entry_row(mid, "Skip first LEDs", "skip_first", int)
        self.entry_row(mid, "Skip last LEDs", "skip_last", int)
        self.entry_row(mid, "Background", "background", str)
        self.entry_row(mid, "Background brightness", "bg_brightness", float)
        self.entry_row(mid, "Frames per second", "fps", int, live=True)
        self.entry_row(mid, "Preview pixel size (cm)", "preview_pitch", float, live=True)
        return mid

    def section(self, parent, text):
        tk.Frame(parent, bg="#3a3f4a", height=1).pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(parent, text=text, bg=PANEL, fg=FG, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12)

    def entry_row(self, parent, label, key, cast, live=False):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=12, pady=2)
        tk.Label(row, text=label, bg=PANEL, fg=FG, font=("Segoe UI", 9)).pack(side="left")
        var = tk.StringVar(value="" if self.g[key] is None else str(self.g[key]))
        entry = ttk.Entry(row, textvariable=var, width=11)
        entry.pack(side="right")

        def commit(_e=None):
            try:
                self.set_g(key, cast(var.get()), rebuild=not live)
            except ValueError:
                pass
        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        self.add_var(key, var)

    # -- actions
    def set_g(self, key, value, rebuild=False):
        if self.g.get(key) == value:
            return
        self.g[key] = value
        if rebuild:
            self.engine.gver += 1                           # decks rebuild with the new map / background
        self.save_later()

    def take_live(self):
        self.engine.switch()

    def toggle_blackout(self):
        self.engine.blackout = not self.engine.blackout
        self.black_btn.configure(text="Blackout ON (click to resume)" if self.engine.blackout else "Blackout")

    def open_map_viewer(self):
        if self.viewer is not None and self.viewer.win.winfo_exists():
            self.viewer.win.lift()
        else:
            self.viewer = MapViewer(self)

    def choose_map(self):
        path = filedialog.askopenfilename(initialdir=os.path.join(HERE, "..", "maridata"), filetypes=[("CSV maps", "*.csv")])
        if path:
            self.map_var.set(os.path.basename(path))
            self.set_g("map", path, rebuild=True)

    def apply_leds(self):
        try:
            n = int(self.led_count.get())
        except ValueError:
            return
        self.set_g("leds", n, rebuild=True)
        counts = common.module_leds(n, self.g["leds_per_module"], len(self.module.hosts()))
        self.module.call_each("/config", lambda k: {"leds": counts[k]})

    def on_key(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox, tk.Text)):
            return
        mapping = {"Left": "left", "Right": "right", "Up": "rotate", "Down": "down", "space": "drop"}
        key = mapping.get(event.keysym) or {"z": "rotate_ccw", "p": "pause", "r": "restart", "a": "a", "d": "d", "w": "w", "s": "s"}.get(event.keysym.lower())
        if key:
            KEYS.put(key)

    # -- the UI clock
    def tick(self):
        shown = self.engine.shown()
        for ident, panel in self.panels.items():
            panel.refresh(ident == shown)
        other = "B" if shown == "A" else "A"
        self.take.configure(text=f"Take {other} live  {'>' if other == 'B' else '<'}")
        self.fade_label.configure(text=f"crossfade {self.g['crossfade']:.1f} s" + ("   (fading...)" if self.engine.fade else ""))
        st = self.module.status
        if st:
            if not self.synced:                              # take the module's current settings once
                self.synced = True
                self.bri.set(st.get("bri", 80))
                self.order.set(st.get("order", "GRB"))
                self.skipboot.set(bool(st.get("skipboot")))
            net = f"{st.get('wifi', '')} {st.get('rssi', '')} dBm" if "rssi" in st else ""
            answered = [s for s in self.module.statuses if s]
            if len(self.module.statuses) > 1 and answered:
                st = dict(st, name=f"{len(answered)} of {len(self.module.statuses)} modules answering",
                          rt_fps=min(s.get("rt_fps", 0) for s in answered),
                          show_fps=min(s.get("show_fps", 0) for s in answered),
                          rt_gap_ms=max(s.get("rt_gap_ms", 0) for s in answered),
                          sta_drops=sum(s.get("sta_drops", 0) for s in answered))
            self.status.configure(fg=FG, text=f"{st.get('name', 'module')}  |  sending {self.engine.sent_fps} fps  |  module got {st.get('rt_fps', 0)} fps, "
                                              f"shows {st.get('show_fps', 0)}, longest gap {st.get('rt_gap_ms', 0)} ms  |  {net}  "
                                              f"|  up {st.get('uptime_s', 0) // 60} min, wifi drops {st.get('sta_drops', 0)}")
        else:
            self.status.configure(fg=LIVE, text=f"no answer from the module at {self.g['host']}  (sending {self.engine.sent_fps} fps)")
        if self.engine.live_error:
            self.status.configure(text=self.status.cget("text") + f"   |   live deck error: {self.engine.live_error}")
        self.root.after(100, self.tick)

    def close(self):
        self.save_now()
        self.engine.stop = True
        self.module.stop = True
        if self.engine.analyzer:
            try:
                self.engine.analyzer.stop()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
