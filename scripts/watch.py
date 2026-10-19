"""Shows the module's own view of the stream once a second (reads /status, sends nothing).

    python scripts/watch.py

  rx    frames the module received in the last second
  show  times it updated the LEDs in that second
  gap   its longest wait between two frames, in ms
  then (newer firmware) uptime and how often the WiFi connection dropped; a reboot is announced
  then the WiFi network it is on, the channel and the signal in dBm (-50 great, -70 weak, -80 bad)

Run it next to music.py (which prints what the PC sent). If music.py shows a long
"max gap" at the same moment, the stall is on the PC; if only the module's gap is
long, the frames were delayed or lost on the WiFi.
"""
import json
import time
import urllib.request

import common
import config


def watch(host, last_uptime, label):
    try:
        with urllib.request.urlopen(f"http://{host}/status", timeout=2) as r:
            d = json.load(r)
        flag = "  <-- stall" if d["rt_gap_ms"] > 80 and d["live"] else ""
        net = f"  | {d['wifi']} ch {d['channel']} {d['rssi']} dBm" if "rssi" in d else ""   # newer firmware only
        if "uptime_s" in d:                                                  # newer firmware only
            if last_uptime is not None and d["uptime_s"] < last_uptime:
                print(f"{time.strftime('%H:%M:%S')}{label}  *** MODULE REBOOTED (reset reason {d['reset']}) ***", flush=True)
            last_uptime = d["uptime_s"]
            net += f" | up {d['uptime_s']}s, wifi drops {d['sta_drops']}"
            if d["sta_drops"]:
                net += f" (last: reason {d['sta_reason']}, {d['sta_drop_ago_s']}s ago)"
        print(f"{time.strftime('%H:%M:%S')}{label}  rx {d['rt_fps']:3d}  show {d['show_fps']:3d}  gap {d['rt_gap_ms']:4d} ms{net}{flag}", flush=True)
    except Exception as e:
        print(f"{time.strftime('%H:%M:%S')}{label}  no answer from {host}: {e}", flush=True)
    return last_uptime


hosts = common.parse_hosts(config.HOST)
uptimes = [None] * len(hosts)
while True:
    for k, host in enumerate(hosts):
        uptimes[k] = watch(host, uptimes[k], f" [{host}]" if len(hosts) > 1 else "")
    time.sleep(1)
