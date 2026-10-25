#!/usr/bin/env python3
"""Software lighting effects: the hub renders nothing itself, Synapse streams RGB frames (~10/s) in Driver mode.
Frame layout and start/stop sequence are from captures D and A (see protocol-from-source.md)."""
import array, colorsys, math, os, select, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import krakenctl as k

LEDS = 9  # frame carries 9 RGB triples; captures show index 8 repeating index 1, mirrored below for the wheel
FPS = 10
EFFECT_IDS = {"static": 1, "breathing": 2, "spectrum": 3, "wheel": 0x0C, "meter": 0x31, "pulse": 0x31, "bands": 0x31, "flash": 0x31}  # UI effect ids Synapse sends in 0xC2

RATE, BLOCK = 16000, 800  # 50 ms blocks of mono s16
A_BASS, A_MID = 1 - math.exp(-2 * math.pi * 200 / RATE), 1 - math.exp(-2 * math.pi * 2000 / RATE)  # one-pole splits at 200 Hz / 2 kHz

class Audio:
    """Levels (0..1, self-normalising so volume doesn't matter) of what is playing, from the sink monitor via pw-record.
    ponytail: one-pole filters, not an FFT: three coarse bands are enough for lights, and numpy isn't installed."""
    def __init__(self):
        self.rms = self.bass = self.mid = self.treb = 0.0
        self.peak = [0.05] * 4
        self.proc = None
        self._lp = [0.0, 0.0]
        self.flash = 0.0  # beat flash level, decays every block
        self._last_bass = 0.0

    def feed(self, samples):
        l1, l2 = self._lp
        eb = em = et = es = 0.0
        for x in samples:
            l1 += A_BASS * (x - l1)
            l2 += A_MID * (x - l2)
            eb += l1 * l1; em += (l2 - l1) ** 2; et += (x - l2) ** 2; es += x * x
        self._lp = [l1, l2]
        n = max(1, len(samples)) * 32768.0 ** 2
        cur = [math.sqrt(v / n) for v in (es, eb, em, et)]
        self.raw = cur[1:]  # un-normalised bass/mid/treble energy (used by the self-check)
        for i, v in enumerate(cur):
            self.peak[i] = max(v, self.peak[i] * 0.995, 0.01)  # slow-decay peak = automatic gain
            cur[i] = min(1.0, v / self.peak[i])
        # beat = bass jumping up sharply (not merely being loud); flash then fades over ~0.3 s
        self.flash *= 0.75
        if cur[1] > 0.7 and cur[1] - self._last_bass > 0.25:
            self.flash = 1.0
        self._last_bass = cur[1]
        # fast attack, slow release
        self.rms, self.bass, self.mid, self.treb = (max(c, o * 0.8) for c, o in zip(cur, (self.rms, self.bass, self.mid, self.treb)))

    def start(self, target):
        if self.proc:
            return
        self.proc = subprocess.Popen(["pw-record", "--target", target, "-P", "{ stream.capture.sink=true }", "--format", "s16",
                                      "--rate", str(RATE), "--channels", "1", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        while proc.poll() is None:
            data = proc.stdout.read(BLOCK * 2)
            if len(data) < BLOCK * 2:
                break
            self.feed(array.array("h", data))

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc = None
            self.rms = self.bass = self.mid = self.treb = 0.0

audio = Audio()
other_audio_user = lambda: False  # set by the panel: the keyboard's audio effect shares this capture
AUDIO_EFFECTS = ("meter", "pulse", "bands", "flash")

def sink_target():
    import json
    for o in json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True).stdout):
        if o.get("info", {}).get("props", {}).get("node.name") == "easyeffects_sink":
            return "easyeffects_sink"
    return "@DEFAULT_AUDIO_SINK@"

def frame(rgb):
    p = bytearray(64)
    p[0], p[2], p[6], p[7], p[8], p[9] = 2, 0x60, 0x21, 0x0F, 0x03, 0x80
    p[14] = 8  # header bytes 10..14 = 00 00 00 00 08, as captured
    for i, c in enumerate(rgb):
        p[15 + 3 * i:18 + 3 * i] = bytes(c)
    for b in p[:62]:
        p[62] ^= b
    return bytes(p)

def hsv(h, v=1.0):
    return tuple(round(x * 255) for x in colorsys.hsv_to_rgb(h % 1.0, 1.0, v))

def render(effect, color, speed, t):
    """speed 1..10; returns 9 RGB triples for time t seconds."""
    if effect == "static":
        return [color] * LEDS
    if effect == "breathing":
        v = 0.12 + 0.88 * (0.5 - 0.5 * math.cos(2 * math.pi * t * speed * 0.1))
        return [tuple(round(c * v) for c in color)] * LEDS
    if effect == "spectrum":
        return [hsv(t * speed * 0.03)] * LEDS
    if effect == "wheel":
        ring = [hsv(t * speed * 0.05 + i / 8) for i in range(8)]
        return ring + [ring[1]]
    g = speed / 5  # for audio effects the speed slider is the sensitivity: 5 = normal, 10 = twice as reactive
    lv = lambda x: min(1.0, x * g)
    if effect == "meter":  # lights fill like a VU meter, green -> red
        ring = []
        for i in range(8):
            lit = max(0.0, min(1.0, lv(audio.rms) * 8 - i))
            ring.append(hsv(0.33 * (1 - i / 7), lit) if lit else (0, 0, 0))
        return ring + [ring[1]]
    if effect == "pulse":  # chosen colour breathes with the bass, treble adds a white flicker
        v = 0.05 + 0.95 * lv(audio.bass)
        w = 0.5 * lv(audio.treb)
        return [tuple(min(255, round(c * v + 255 * w * v)) for c in color)] * LEDS
    if effect == "bands":  # bass = red, mids = green, treble = blue, spread across the ring
        lit = [lv(audio.bass)] * 3 + [lv(audio.mid)] * 3 + [lv(audio.treb)] * 2
        col = [(255, 0, 0)] * 3 + [(0, 255, 0)] * 3 + [(0, 90, 255)] * 2
        ring = [tuple(round(c * (0.04 + 0.96 * v)) for c in rgb) for rgb, v in zip(col, lit)]
        return ring + [ring[1]]
    if effect == "flash":  # chosen colour flashes on every beat
        v = 0.04 + 0.96 * lv(audio.flash)
        return [tuple(round(c * v) for c in color)] * LEDS
    raise ValueError(effect)

class Engine:
    def __init__(self, fd_getter, lock):
        self.fd, self.lock = fd_getter, lock
        self.thread = None
        self.stop_ev = threading.Event()
        self.cfg = {"effect": "static", "color": (255, 255, 255), "speed": 5}
        self.running = False

    def _drain(self, fd):
        while select.select([fd], [], [], 0)[0]:
            os.read(fd, 64)

    def _loop(self):
        t0 = time.time()
        while not self.stop_ev.is_set():
            with self.lock:
                fd = self.fd()
                self._drain(fd)  # hub acks every frame; keep its queue empty
                os.write(fd, frame(render(self.cfg["effect"], tuple(self.cfg["color"]), self.cfg["speed"], time.time() - t0)))
            self.stop_ev.wait(1 / (16 if self.cfg["effect"] in AUDIO_EFFECTS else FPS))

    def start(self, effect, color=(255, 255, 255), speed=5):
        self.cfg = {"effect": effect, "color": tuple(color), "speed": speed}
        if effect in AUDIO_EFFECTS:
            audio.start(sink_target())
        elif not other_audio_user():
            audio.stop()
        if self.running:
            return  # config is read live by the loop
        with self.lock:
            fd = self.fd()
            # same order Synapse uses: take over chroma, Driver mode, effect id, hardware effect 8 = custom frame
            os.write(fd, _cls(0x0F, 0x10, b"\x01"))
            k.mode(fd, 3)
            k.get(fd, 0xC2, bytes([EFFECT_IDS[effect]]))
            k.get(fd, 0xC0, bytes([8]))
        self.stop_ev.clear()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        if not self.running:
            return
        self.stop_ev.set()
        if not other_audio_user():
            audio.stop()
        self.thread.join(2)
        self.running = False
        with self.lock:  # Synapse's exit sequence: effect 0x0C, then Normal mode
            fd = self.fd()
            self._drain(fd)
            k.get(fd, 0xC0, bytes([0x0C]))
            k.mode(fd, 0)

def _cls(cls, cid, data):
    p = bytearray(64)
    p[0], p[6], p[7], p[8], p[9] = 2, len(data) + 1, cls, cid, 0x80
    p[10:10 + len(data)] = data
    for b in p[:62]:
        p[62] ^= b
    return bytes(p)

if __name__ == "__main__":  # offline self-check of the frame format against a captured frame
    cap = bytes.fromhex("02006000000021" "0f0380" "0000000008" "ff6503e8ff0072ff0007ccff1664ff1858ff1858ff8739ffe8ff00" + "00" * 20 + "2b00")
    rgb = [tuple(cap[15 + 3 * i:18 + 3 * i]) for i in range(9)]
    assert frame(rgb) == cap, "frame layout/checksum does not match capture"
    assert len(render("wheel", (0, 0, 0), 5, 1.0)) == 9
    a = Audio()
    quiet = array.array("h", [0] * BLOCK)
    loud = array.array("h", (int(20000 * math.sin(2 * math.pi * 80 * i / RATE)) for i in range(BLOCK)))
    for _ in range(40):
        a.feed(array.array("h", (int(300 * math.sin(2 * math.pi * 80 * i / RATE)) for i in range(BLOCK))))
    a.feed(loud)
    assert a.flash > 0.9, "a sudden bass hit should trigger the beat flash"
    for _ in range(12):
        a.feed(quiet)
    assert a.flash < 0.1, "flash should fade"
    for hz, want in ((100, "bass"), (500, "mid"), (5000, "treb")):  # band split sanity check
        a = Audio()
        for blk in range(30):
            a.feed(array.array("h", (int(12000 * math.sin(2 * math.pi * hz * (blk * BLOCK + i) / RATE)) for i in range(BLOCK))))
        got = dict(zip(("bass", "mid", "treb"), a.raw))
        assert max(got, key=got.get) == want, (hz, got)
    print("lighting frame matches capture byte-for-byte")
