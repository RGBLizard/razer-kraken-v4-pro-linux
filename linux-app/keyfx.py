#!/usr/bin/env python3
"""Animated per-key lighting for the DeathStalker V2 Pro: a thread renders a 6x22 frame ~8 times a second and sends it with
razerdev.Keyboard.frame. Key-press effects (ripple, reactive) read key events from the keyboard's evdev node.
Cells are in the standard Razer matrix (the same row,col the panel's painter uses); the column shift is applied when sending."""
import colorsys, glob, math, os, random, select, struct, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import razerdev

FPS = 8  # one frame is 7 HID transfers (~0.12 s), so this is about the ceiling
EFFECTS = ("wave", "fire", "ripple", "reactive", "audio")
PRESS_EFFECTS = ("ripple", "reactive")
KEYBOARD_EVENT = "/dev/input/by-id/usb-Razer_DSV2_Pro_*-if01-event-mouse"  # the keyboard half of the shared dongle

# evdev key code -> (row, col) in the standard ISO matrix (same layout as the panel's KBL)
KEYMAP = {1: (0, 1), 99: (0, 15), 70: (0, 16), 119: (0, 17),
          **{59 + i: (0, 3 + i) for i in range(10)}, 87: (0, 13), 88: (0, 14),
          41: (1, 1), **{2 + i: (1, 2 + i) for i in range(10)}, 12: (1, 12), 13: (1, 13), 14: (1, 14), 110: (1, 15), 102: (1, 16), 104: (1, 17),
          69: (1, 18), 98: (1, 19), 55: (1, 20), 74: (1, 21),
          15: (2, 1), **{16 + i: (2, 2 + i) for i in range(10)}, 26: (2, 12), 27: (2, 13), 111: (2, 15), 107: (2, 16), 109: (2, 17),
          71: (2, 18), 72: (2, 19), 73: (2, 20), 78: (2, 21), 28: (3, 14),
          58: (3, 1), **{30 + i: (3, 2 + i) for i in range(9)}, 39: (3, 11), 40: (3, 12), 43: (3, 13), 75: (3, 18), 76: (3, 19), 77: (3, 20),
          42: (4, 1), 86: (4, 2), **{44 + i: (4, 3 + i) for i in range(7)}, 51: (4, 10), 52: (4, 11), 53: (4, 12), 54: (4, 14), 103: (4, 16),
          79: (4, 18), 80: (4, 19), 81: (4, 20), 96: (4, 21),
          29: (5, 1), 125: (5, 2), 56: (5, 3), 57: (5, 7), 100: (5, 11), 127: (5, 13), 97: (5, 14), 105: (5, 15), 108: (5, 16), 106: (5, 17),
          82: (5, 19), 83: (5, 20)}
COLS = range(1, razerdev.COLS)  # standard columns 1..21

def hsv(h, v=1.0):
    return tuple(round(x * 255) for x in colorsys.hsv_to_rgb(h % 1.0, 1.0, v))

def mix(a, b, f):
    return tuple(round(x + (y - x) * f) for x, y in zip(a, b))

class Engine:
    def __init__(self, keyboard=None, audio=None, audio_busy=lambda: False):
        self.kb = keyboard or razerdev.keyboard
        self.audio, self.audio_busy = audio, audio_busy  # lighting.audio singleton (shared with the headset engine)
        self.cfg = {"effect": "wave", "color": (68, 214, 44), "base": (0, 0, 0), "speed": 5}
        self.running = False
        self.thread, self.stop_ev = None, threading.Event()
        self.presses = []  # (time, row, col) of recent key presses
        self.heat = [0.0] * razerdev.COLS
        self.evfd = None

    # --- rendering: returns a 6x22 matrix (already shifted for this keyboard's columns)
    def render(self, t, now):
        cfg = self.cfg
        eff, col, base, sp = cfg["effect"], tuple(cfg["color"]), tuple(cfg["base"]), cfg["speed"] / 5
        m = [[base] * razerdev.COLS for _ in range(razerdev.ROWS)]
        off = razerdev.KEY_COL_OFFSET
        def put(r, c, rgb):
            if 0 <= c + off < razerdev.COLS:
                m[r][c + off] = rgb
        if eff == "wave":
            for r in range(razerdev.ROWS):
                for c in COLS:
                    put(r, c, hsv(c * 0.035 + r * 0.01 - t * sp * 0.2))
        elif eff == "fire":
            for c in COLS:  # each column's heat random-walks; the bottom row is the hottest
                self.heat[c] = max(0.0, min(1.0, self.heat[c] + random.uniform(-0.25, 0.3) * sp))
                for r in range(razerdev.ROWS):
                    v = self.heat[c] * 1.3 - (5 - r) * 0.2
                    v = max(0.0, min(1.0, v))
                    put(r, c, (round(255 * min(1, v * 1.6)), round(255 * max(0, v * 1.4 - 0.45)), round(80 * max(0, v - 0.85) / 0.15)))
        elif eff == "ripple":
            self.presses = [p for p in self.presses if now - p[0] < 1.6]
            for r in range(razerdev.ROWS):
                for c in COLS:
                    v = 0.0
                    for t0, pr, pc in self.presses:
                        age = now - t0
                        d = math.hypot(c - pc, (r - pr) * 1.8)
                        v = max(v, max(0.0, 1 - abs(d - age * 12 * sp) / 1.8) * max(0.0, 1 - age / 1.6))
                    if v:
                        put(r, c, mix(base, col, v))
        elif eff == "reactive":
            self.presses = [p for p in self.presses if now - p[0] < 0.8]
            for t0, r, c in self.presses:
                put(r, c, mix(base, col, max(0.0, 1 - (now - t0) / (0.8 / sp)) ** 1.5))
        elif eff == "audio":  # equaliser bars: bass on the left, mids in the middle, treble on the right
            a, g = self.audio, sp
            for c in COLS:
                x = (c - 1) / 20
                lvl = (a.bass * (1 - x) + a.mid * (1 - abs(2 * x - 1)) + a.treb * x) * 1.6 if a else 0
                lvl = min(1.0, lvl * g * (0.8 + 0.2 * math.sin(c * 1.7 + t * 5)))
                for r in range(razerdev.ROWS):
                    if (5 - r) < lvl * razerdev.ROWS:
                        put(r, c, hsv(0.33 * r / 5))
        return m

    # --- thread
    def _open_events(self):
        if self.evfd is None and self.cfg["effect"] in PRESS_EFFECTS:
            nodes = glob.glob(KEYBOARD_EVENT)
            if nodes:
                try:
                    self.evfd = os.open(nodes[0], os.O_RDONLY | os.O_NONBLOCK)
                except OSError:
                    pass

    def _wait(self, secs):
        end = time.time() + secs
        self._open_events()
        while not self.stop_ev.is_set() and (left := end - time.time()) > 0:
            if self.evfd is None:
                self.stop_ev.wait(left)
                continue
            if select.select([self.evfd], [], [], left)[0]:
                try:
                    data = os.read(self.evfd, 24 * 32)
                except OSError:  # keyboard went away; try again next frame
                    os.close(self.evfd); self.evfd = None
                    continue
                for i in range(0, len(data) - 23, 24):
                    _, _, typ, code, val = struct.unpack("llHHi", data[i:i + 24])
                    if typ == 1 and val == 1 and code in KEYMAP:
                        self.presses.append((time.time(), *KEYMAP[code]))

    def _loop(self):
        t0, fails = time.time(), 0
        while not self.stop_ev.is_set():
            now = time.time()
            ok = self.kb.frame(self.render(now - t0, now))
            fails = 0 if ok else fails + 1  # asleep / out of range: back off, a key press wakes it
            self._wait(max(0.0, 1 / FPS - (time.time() - now)) if not fails else min(2.0, 0.25 * fails))

    def start(self, effect, color=(68, 214, 44), speed=5, base=(0, 0, 0)):
        if effect not in EFFECTS:
            raise ValueError(effect)
        self.cfg = {"effect": effect, "color": tuple(color), "base": tuple(base), "speed": max(1, min(10, speed))}
        if effect == "audio" and self.audio:
            import lighting
            self.audio.start(lighting.sink_target())
        elif self.audio and not self.audio_busy():
            self.audio.stop()
        if self.evfd is not None and effect not in PRESS_EFFECTS:
            os.close(self.evfd); self.evfd = None
        if not self.running:
            self.stop_ev.clear()
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def stop(self):
        if not self.running:
            return
        self.stop_ev.set()
        self.thread.join(3)
        self.running = False
        if self.evfd is not None:
            os.close(self.evfd); self.evfd = None
        if self.audio and not self.audio_busy():
            self.audio.stop()

engine = Engine()

if __name__ == "__main__":  # offline self-check of the key map and every effect's frame shape; `python3 -I keyfx.py live <effect>` plays on the keyboard
    assert len(set(KEYMAP.values())) == len(KEYMAP), "two keys share a matrix cell"
    assert all(0 <= r < razerdev.ROWS and 1 <= c < razerdev.COLS for r, c in KEYMAP.values())
    e = Engine()
    for eff in EFFECTS:
        e.cfg["effect"] = eff
        e.presses = [(0.0, 2, 5)]
        m = e.render(1.0, 0.2)
        assert len(m) == razerdev.ROWS and all(len(row) == razerdev.COLS for row in m), eff
        assert all(len(px) == 3 and all(0 <= x <= 255 for x in px) for row in m for px in row), eff
    e.cfg["effect"] = "reactive"; e.presses = [(0.0, 3, 4)]  # key pressed at t=0 lights its own cell (col shifted by KEY_COL_OFFSET)
    assert e.render(0, 0.0)[3][4 + razerdev.KEY_COL_OFFSET] == (68, 214, 44)
    print("keyfx self-check ok")
    if sys.argv[1:2] == ["live"]:
        e.kb = razerdev.keyboard
        e.start(sys.argv[2] if len(sys.argv) > 2 else "wave")
        time.sleep(float(sys.argv[3]) if len(sys.argv) > 3 else 15)
        e.stop()
