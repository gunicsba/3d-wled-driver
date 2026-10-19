"""Settings shared by all scripts and the app. Command-line flags override these.

Put your own values in scripts/config_local.py (copy config_local.example.py): it is git-ignored,
so your IP address and map tweaks stay out of the repository.
"""

HOST = "192.168.4.1"     # the module: 192.168.4.1 when you are on its own access point, or its IP on your WiFi.
                         # Several modules: "192.168.1.50,192.168.1.51,192.168.1.52" (LEDs 0-999 on the first, and so on)
LEDS = 200               # LED count in total (over all modules)
LEDS_PER_MODULE = 1000   # most LEDs one module drives; a longer string is split over the modules in HOST
MAP = None               # None = newest curtain map in maridata/ (or maps/ if there is none), else a path
FPS = 30

# Boundaries: LEDs outside are left dark and effects are scaled to what remains.
SKIP_FIRST = 0           # leave the first N LEDs dark (e.g. LEDs that hang loose below the curtain)
SKIP_LAST = 0            # leave the last N LEDs dark
MIN_Y = None             # lowest height to use (cm in the curtain map), None = no limit
MAX_Y = None             # highest height to use

# Background for LEDs an effect leaves unlit (flags: --background, --bg-brightness).
BACKGROUND = "white"     # colour name (red, blue, ...) or RRGGBB
BG_BRIGHTNESS = 0.04     # 0 = off ... 1 = full colour; 0.04 is a barely-there white glow

# MariMapper 3D maps: stand the map upright, then choose where you look at it from. The effects run on that view.
MARIMAPPER_UNIT_CM = 6.6  # cm per unit in a MariMapper 3D map (it measures in LED spacings): your real LED pitch
MAP_ORIENT = "auto"       # "auto" stands a flat sheet (a curtain) upright by itself, "none" for anything else
MAP_ROTATE_X = 0.0        # degrees to turn the map about x, then y, then z (after "auto"): sets which way is up and front
MAP_ROTATE_Y = 0.0
MAP_ROTATE_Z = 0.0
VIEW_AZIMUTH = 0.0        # where you look from: 0 = the front, 90 = from the right, 180 = from behind
VIEW_ELEVATION = 0.0      # 0 = level, 90 = from above, -90 = from below
VIEW_ROLL = 0.0           # tilts the picture
VIEW_DISTANCE = 0.0       # cm: perspective for a viewer this far away, 0 = no perspective

# A mirror plane for a symmetric object (a tractor): two LED numbers on its middle line, e.g. (12, 340), and the angle
# the plane is turned about that line (0 = the plane stands upright through the line). The LEDs on the far side of the
# plane (seen from the viewpoint) copy the LEDs on the near side, so both sides show the same picture. None = no mirror.
MIRROR_LEDS = None
MIRROR_ANGLE = 0.0        # degrees
MIRROR_MODE = "readable"  # "readable": the far side is the near side turned round, so text reads correctly from both sides
                          # "reflect": the far side is the near side reflected, like a real mirror (text is backwards there)

FLIP_V = False           # True if "up" runs down in your map
FLIP_X = False           # True if left and right are mirrored in your map (e.g. it was shot from behind the curtain)

try:
    from config_local import *  # noqa: F401,F403  (your own settings, see config_local.example.py)
except ImportError:
    pass
