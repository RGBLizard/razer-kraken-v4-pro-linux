#!/usr/bin/env python3
"""Naga V2 Pro side-button remap + macros, in software (evdev + uinput, stdlib only).
The 12 side buttons arrive as the keys 1..0 - = on their own input device (the dongle's "-event-kbd" node), separate from the
DeathStalker's keys, so grabbing that node only touches the side panel. Unbound buttons are passed through unchanged.
Macros are plain text: `+ctrl` press, `-ctrl` release, `a` tap, `ctrl+c` tap combo, `120` wait in ms (e.g. `ctrl+c 200 alt+tab`)."""
import fcntl, glob, json, os, re, select, struct, threading, time

CONFIG = os.path.expanduser("~/.config/kraken-control/remap.json")
SIDE_NODES = ("/dev/input/by-id/usb-Razer_DSV2_Pro_*-event-kbd",  # over the dongle
              "/dev/input/by-id/usb-Razer_Razer_Naga_V2_Pro-if02-event-kbd")  # on its own cable (charging); both can exist, both are watched
KEYBOARD_NODE = "/dev/input/by-id/usb-Razer_DSV2_Pro_*-if01-event-mouse"  # keyboard + mouse half, used to record macros
FIRST_SIDE_CODE = 2  # side button 1 = KEY_1 ... button 12 = KEY_EQUAL
MODES = ("once", "hold", "toggle")
TAP_GAP_MS = 15  # pause after every tapped key so games don't drop quick presses

NAMES = {"esc": 1, "minus": 12, "equal": 13, "backspace": 14, "tab": 15, "lbracket": 26, "rbracket": 27, "enter": 28, "ctrl": 29,
         "semicolon": 39, "apostrophe": 40, "grave": 41, "shift": 42, "backslash": 43, "comma": 51, "dot": 52, "slash": 53,
         "rshift": 54, "kpmul": 55, "alt": 56, "space": 57, "capslock": 58, "numlock": 69, "scrolllock": 70, "kpminus": 74,
         "kpplus": 78, "kpdot": 83, "iso": 86, "kpenter": 96, "rctrl": 97, "kpdiv": 98, "print": 99, "ralt": 100, "home": 102,
         "up": 103, "pageup": 104, "left": 105, "right": 106, "end": 107, "down": 108, "pagedown": 109, "insert": 110,
         "delete": 111, "mute": 113, "voldown": 114, "volup": 115, "pause": 119, "super": 125, "rsuper": 126, "menu": 127,
         "next": 163, "playpause": 164, "prev": 165, "stop": 166}
for _i, _c in enumerate("qwertyuiop"): NAMES[_c] = 16 + _i
for _i, _c in enumerate("asdfghjkl"): NAMES[_c] = 30 + _i
for _i, _c in enumerate("zxcvbnm"): NAMES[_c] = 44 + _i
for _i in range(10): NAMES["1234567890"[_i]] = 2 + _i
for _i in range(10): NAMES[f"f{_i + 1}"] = 59 + _i
NAMES.update({"f11": 87, "f12": 88})
for _i in range(12): NAMES[f"f{13 + _i}"] = 183 + _i
for _i, _c in enumerate((82, 79, 80, 81, 75, 76, 77, 71, 72, 73)): NAMES[f"kp{_i}"] = _c
CODE_NAME = {}
for _n, _c in NAMES.items(): CODE_NAME.setdefault(_c, _n)

# --- text <-> steps
def parse_combo(text):
    codes = []
    for part in text.lower().split("+"):
        if part not in NAMES:
            raise ValueError(f"unknown key '{part}'")
        codes.append(NAMES[part])
    return codes

def parse_macro(text):
    """Text -> [("k", code, 1|0) | ("d", ms)]."""
    steps = []
    for tok in text.split():
        if tok.isdigit():
            steps.append(("d", min(int(tok), 60000)))
        elif tok[0] in "+-" and len(tok) > 1 and "+" not in tok[1:]:
            if tok[1:].lower() not in NAMES:
                raise ValueError(f"unknown key '{tok[1:]}'")
            steps.append(("k", NAMES[tok[1:].lower()], int(tok[0] == "+")))
        else:
            codes = parse_combo(tok)
            steps += [("k", c, 1) for c in codes] + [("d", 8)] + [("k", c, 0) for c in reversed(codes)] + [("d", TAP_GAP_MS)]
    return steps

def validate(cfg):
    """Check and normalise a config dict; raises ValueError on anything bad."""
    macros = {}
    for name, text in cfg.get("macros", {}).items():
        name = " ".join(str(name).split())[:40]
        if not name or len(text) > 20000 or len(macros) >= 50:
            raise ValueError("bad macro")
        parse_macro(text)
        macros[name] = text
    buttons = {}
    for b, a in cfg.get("buttons", {}).items():
        if b not in [str(i) for i in range(1, 13)]:
            raise ValueError("buttons are 1..12")
        t = a.get("type", "pass")
        if t == "key":
            parse_combo(a["keys"]); buttons[b] = {"type": "key", "keys": a["keys"].lower()}
        elif t == "macro":
            if a["macro"] not in macros or a.get("mode", "once") not in MODES:
                raise ValueError(f"button {b}: unknown macro or mode")
            buttons[b] = {"type": "macro", "macro": a["macro"], "mode": a.get("mode", "once")}
        elif t in ("off", "pass"):
            if t == "off": buttons[b] = {"type": "off"}
        else:
            raise ValueError("bad button type")
    return {"buttons": buttons, "macros": macros}

# --- uinput
def _ioc(d, typ, nr, size):
    return (d << 30) | (size << 16) | (ord(typ) << 8) | nr
UI_SET_EVBIT, UI_SET_KEYBIT = _ioc(1, "U", 100, 4), _ioc(1, "U", 101, 4)
UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = _ioc(1, "U", 3, 92), _ioc(0, "U", 1, 0), _ioc(0, "U", 2, 0)
EVIOCGRAB = _ioc(1, "E", 0x90, 4)

class Uinput:
    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, 1)  # EV_KEY
        for code in range(1, 249):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        for code in range(183, 195):  # F13..F24
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 3, 0x1532, 0xFFFF, 1, b"Kraken Control remap", 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.lock = threading.Lock()

    def key(self, code, val):
        with self.lock:
            os.write(self.fd, struct.pack("llHHi", 0, 0, 1, code, val) + struct.pack("llHHi", 0, 0, 0, 0, 0))

    def close(self):
        fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        os.close(self.fd)

# --- remapper
class Remapper:
    def __init__(self):
        self.cfg = {"buttons": {}, "macros": {}}
        self.out, self.fds = None, {}  # fds: node path -> open grabbed fd
        self.active = {}  # code -> what to do on release
        self.runs = {}  # button -> (stop event) for hold/toggle macros
        self.stop_ev = threading.Event()
        self.thread = None
        try:
            self.cfg = validate(json.load(open(CONFIG)))
        except (OSError, ValueError, KeyError, AttributeError):
            pass

    def set_config(self, cfg):
        self.cfg = validate(cfg)
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        tmp = CONFIG + ".tmp"
        json.dump(self.cfg, open(tmp, "w"), indent=1)
        os.replace(tmp, CONFIG)

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()
        for ev in list(self.runs.values()): ev.set()
        if self.thread: self.thread.join(3)
        self._release()
        if self.out: self.out.close(); self.out = None

    def status(self):
        return {"grabbed": bool(self.fds), "bound": len(self.cfg["buttons"])}

    def _release(self, path=None):
        for p in ([path] if path else list(self.fds)):
            fd = self.fds.pop(p, None)
            if fd is not None:
                os.close(fd)  # closing the fd drops the grab, the side buttons work as plain keys again

    def _play(self, steps, stop, loop):
        down = set()
        try:
            while True:
                for s in steps:
                    if s[0] == "d":
                        if stop.wait(s[1] / 1000): return
                    else:
                        self.out.key(s[1], s[2]); (down.add if s[2] else down.discard)(s[1])
                if not loop or stop.wait(0.02): return
        finally:
            for c in down: self.out.key(c, 0)

    def _press(self, code):
        b = str(code - FIRST_SIDE_CODE + 1)
        a = self.cfg["buttons"].get(b)
        if not a:
            self.out.key(code, 1); self.active[code] = ("pass",)
        elif a["type"] == "off":
            self.active[code] = ("off",)
        elif a["type"] == "key":
            codes = parse_combo(a["keys"])
            for c in codes: self.out.key(c, 1)
            self.active[code] = ("key", codes)
        else:  # macro
            mode = a["mode"]
            if mode == "toggle" and b in self.runs:
                self.runs.pop(b).set()
            elif b not in self.runs:
                stop = threading.Event()
                steps = parse_macro(self.cfg["macros"][a["macro"]])
                if mode != "once": self.runs[b] = stop
                def go():
                    self._play(steps, stop, mode != "once")
                    if self.runs.get(b) is stop: del self.runs[b]
                threading.Thread(target=go, daemon=True).start()
            self.active[code] = ("hold", b) if mode == "hold" else ("none",)

    def _release_code(self, code):
        what = self.active.pop(code, ("pass",))
        if what[0] == "pass": self.out.key(code, 0)
        elif what[0] == "key":
            for c in reversed(what[1]): self.out.key(c, 0)
        elif what[0] == "hold" and what[1] in self.runs: self.runs.pop(what[1]).set()

    def _loop(self):
        while not self.stop_ev.is_set():
            need = bool(self.cfg["buttons"])  # only take the device over while something is bound
            if not need:
                self._release()
                self.stop_ev.wait(1); continue
            try:
                if self.out is None: self.out = Uinput()
            except OSError:
                self.stop_ev.wait(3); continue
            found = {p for pat in SIDE_NODES for p in glob.glob(pat)}
            for p in set(self.fds) - found:
                self._release(p)  # unplugged
            for p in found - set(self.fds):
                try:
                    fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
                    fcntl.ioctl(fd, EVIOCGRAB, 1)
                    self.fds[p] = fd
                except OSError:
                    pass
            if not self.fds:
                self.stop_ev.wait(2); continue
            for fd in select.select(list(self.fds.values()), [], [], 1)[0]:
                try:
                    data = os.read(fd, 24 * 64)
                except OSError:  # replugged: dropped on the next pass
                    self._release(next(p for p, f in self.fds.items() if f == fd)); continue
                for i in range(0, len(data) - 23, 24):
                    _, _, typ, code, val = struct.unpack("llHHi", data[i:i + 24])
                    if typ != 1: continue  # scan codes and syncs: uinput adds its own sync
                    if FIRST_SIDE_CODE <= code < FIRST_SIDE_CODE + 12:
                        if val == 1: self._press(code)
                        elif val == 0: self._release_code(code)
                        # val 2 (autorepeat) is dropped: games/compositors repeat by themselves
                    elif val in (0, 1):
                        self.out.key(code, val)

class Recorder:
    """Records the keyboard's key presses (read-only) into macro text."""
    def __init__(self):
        self.events, self.fd, self.thread = [], None, None

    def start(self):
        nodes = glob.glob(KEYBOARD_NODE)
        if not nodes: raise OSError("keyboard not found")
        self.stop()
        self.events, self.fd = [], os.open(nodes[0], os.O_RDONLY | os.O_NONBLOCK)
        self.thread = threading.Thread(target=self._read, args=(self.fd, time.time()), daemon=True)
        self.thread.start()

    def _read(self, fd, t0):
        while self.fd == fd and time.time() - t0 < 120:  # recording ends by itself after two minutes
            if select.select([fd], [], [], 0.5)[0]:
                try: data = os.read(fd, 24 * 32)
                except OSError: return
                for i in range(0, len(data) - 23, 24):
                    _, _, typ, code, val = struct.unpack("llHHi", data[i:i + 24])
                    if typ == 1 and val in (0, 1) and code in CODE_NAME:
                        self.events.append((time.time(), code, val))

    def stop(self):
        """Ends the recording and returns the macro text."""
        fd, self.fd = self.fd, None
        if fd is not None:
            time.sleep(0.6); os.close(fd)
        out, prev = [], None
        for t, code, val in self.events:
            if prev is not None and (gap := round((t - prev) * 1000 / 5) * 5) >= 5:
                out.append(str(gap))
            out.append(("+" if val else "-") + CODE_NAME[code]); prev = t
        self.events = []
        return " ".join(out)

remapper, recorder = Remapper(), Recorder()

if __name__ == "__main__":  # offline self-check
    assert parse_macro("ctrl+c 200 alt+tab") == [("k", 29, 1), ("k", 46, 1), ("d", 8), ("k", 46, 0), ("k", 29, 0), ("d", 15), ("d", 200),
                                                 ("k", 56, 1), ("k", 15, 1), ("d", 8), ("k", 15, 0), ("k", 56, 0), ("d", 15)]
    assert parse_macro("+shift a -shift") == [("k", 42, 1), ("k", 30, 1), ("d", 8), ("k", 30, 0), ("d", 15), ("k", 42, 0)]
    for bad in ("nope", "+nope", "ctrl+nope"):
        try: parse_macro(bad); raise SystemExit("accepted " + bad)
        except ValueError: pass
    c = validate({"macros": {"m": "a b"}, "buttons": {"1": {"type": "key", "keys": "Ctrl+C"}, "2": {"type": "macro", "macro": "m", "mode": "hold"}, "3": {"type": "pass"}}})
    assert c["buttons"]["1"]["keys"] == "ctrl+c" and "3" not in c["buttons"]
    for bad in ({"buttons": {"13": {"type": "off"}}}, {"buttons": {"1": {"type": "macro", "macro": "x"}}}):
        try: validate(bad); raise SystemExit("accepted bad config")
        except ValueError: pass
    assert all(CODE_NAME[v] for v in NAMES.values())
    print("remap self-check ok")
