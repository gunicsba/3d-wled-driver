"""Settings shared by all scripts and the app. Command-line flags override these.

Put your own values in scripts/config_local.py (copy config_local.example.py): it is git-ignored,
so your IP address and map tweaks stay out of the repository.
"""

HOST = "192.168.4.1"     # the module: 192.168.4.1 when you are on its own access point, or its IP on your WiFi
LEDS = 200               # LED count on the module
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

FLIP_V = False           # True if "up" runs down in your map
FLIP_X = False           # True if left and right are mirrored in your map (e.g. it was shot from behind the curtain)

try:
    from config_local import *  # noqa: F401,F403  (your own settings, see config_local.example.py)
except ImportError:
    pass
