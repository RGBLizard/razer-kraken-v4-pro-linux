#!/usr/bin/env python3
"""Razer mouse + keyboard over the shared wireless dongle (1532:0290), classic 90-byte report protocol.
Protocol facts are from OpenRazer (driver/razercommon.c, razerchromacommon.c) and were checked against the hardware:
feature reports on the dongle's HID interface 2, the top bit of the transaction id picks the paired device.
Naga V2 Pro (mouse) = tid 0x1F, DeathStalker V2 Pro (keyboard) = tid 0x9F. Stdlib only."""
import fcntl, glob, os, struct, sys, threading, time

VID_PID = "00001532:00000290"
LOGO, SCROLL, BACKLIGHT, ZERO = 0x04, 0x01, 0x05, 0x00  # LED ids (OpenRazer razercommon.h)
VARSTORE = 0x01
MOUSE_TID, KEYBOARD_TID = 0x1F, 0x9F

def _ioc(typ, nr, size):
    return (3 << 30) | (size << 16) | (ord(typ) << 8) | nr
HIDIOCSFEATURE = lambda n: _ioc("H", 0x06, n)
HIDIOCGFEATURE = lambda n: _ioc("H", 0x07, n)

WIRED_MOUSE = "00001532:000000A7"  # the Naga V2 Pro on its own USB cable (charging); the dongle link goes quiet while it is plugged in

def find_node(vid_pid=VID_PID, iface="/input2"):
    """A control interface: by default the dongle's (input2)."""
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            ev = open(d + "/device/uevent").read()
        except OSError:
            continue
        if vid_pid in ev and iface in ev:
            return "/dev/" + os.path.basename(d)
    return None

class Link:
    """One serialized conversation with the dongle (HID feature report out, feature report back)."""
    def __init__(self, vid_pid=VID_PID, iface="/input2"):
        self.fd, self.lock, self.vid_pid, self.iface, self.node = None, threading.RLock(), vid_pid, iface, None

    def open(self):
        if self.fd is not None and self.node and not os.path.exists(self.node):  # device unplugged: drop the stale handle
            os.close(self.fd); self.fd = None
        if self.fd is None:
            self.node = find_node(self.vid_pid, self.iface)
            if not self.node:
                raise OSError(f"Razer device {self.vid_pid} not found")
            self.fd = os.open(self.node, os.O_RDWR)
        return self.fd

    def present(self):
        try:
            self.open()
            return True
        except OSError:
            return False

    def xfer(self, tid, cls, cmd, size, args=b"", retries=4, wait=0.06):
        """Send one command, return (status, response bytes[8:8+size]). status 2 = ok, 3 = failed, 4 = timeout (device asleep or out of range), 5 = not supported."""
        r = bytearray(90)
        r[1], r[5], r[6], r[7] = tid, size, cls, cmd
        r[8:8 + len(args)] = args
        c = 0
        for b in r[2:88]:
            c ^= b
        r[88] = c
        with self.lock:
            fd = self.open()
            for _ in range(retries):
                buf = bytearray(b"\x00" + bytes(r))
                try:
                    fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)
                    time.sleep(wait)
                    out = bytearray(len(buf))
                    fcntl.ioctl(fd, HIDIOCGFEATURE(len(out)), out)
                except OSError:  # replugged (even under the same hidraw name): the old handle is dead, reopen once and go on
                    os.close(self.fd); self.fd = None
                    fd = self.open()
                    continue
                resp = bytes(out[1:])
                if resp[0] != 0x01:  # 1 = busy, ask again
                    return resp[0], resp[8:8 + max(resp[5], 1)]
                time.sleep(0.1)
            return 1, b""

link = Link()
wired_mouse_link = Link(WIRED_MOUSE, "/input0")

class Device:
    def __init__(self, name, tid, lights):
        self.name, self.tid, self.lights = name, tid, lights  # lights: {zone name: led id}
        self.last = 2

    def link(self):
        return link

    def get(self, cls, cmd, size, args=b""):
        st, data = self.link().xfer(self.tid, cls, cmd, size, args)
        return data if st == 2 else None

    def put(self, cls, cmd, size, args=b""):
        st, _ = self.link().xfer(self.tid, cls, cmd, size, args)
        self.last = st  # 4 = timeout: the wireless device is asleep
        return st == 2

    # --- reads
    def serial(self):
        d = self.get(0x00, 0x82, 0x16)
        return d.split(b"\0")[0].decode("ascii", "replace") if d else None

    def firmware(self):
        d = self.get(0x00, 0x81, 0x02)
        return f"{d[0]}.{d[1]}" if d else None

    def battery(self):
        """Percent, from the raw 0..255 value."""
        d = self.get(0x07, 0x80, 0x02)
        return round(d[1] * 100 / 255) if d and len(d) > 1 else None

    def charging(self):
        d = self.get(0x07, 0x84, 0x02)
        return bool(d[1]) if d and len(d) > 1 else None

    def brightness(self, zone):
        d = self.get(0x0F, 0x84, 0x03, bytes([VARSTORE, self.lights[zone]]))
        return round(d[2] * 100 / 255) if d and len(d) > 2 else None

    # --- lighting (extended matrix effects: class 0x0F id 0x02, args [store, led, effect, ...])
    def _effect(self, zone, effect, size, extra=b""):
        led = self.lights[zone]
        a = bytearray([VARSTORE, led, effect]) + extra
        return self.put(0x0F, 0x02, size, bytes(a))

    def set_brightness(self, zone, pct):
        return self.put(0x0F, 0x04, 0x03, bytes([VARSTORE, self.lights[zone], round(max(0, min(100, pct)) * 2.55)]))

    def none(self, zone):
        return self._effect(zone, 0x00, 0x06)

    def static(self, zone, rgb):
        return self._effect(zone, 0x01, 0x09, bytes([0, 0, 1]) + bytes(rgb))

    def spectrum(self, zone):
        return self._effect(zone, 0x03, 0x06)

    def breathing(self, zone, rgb):
        return self._effect(zone, 0x02, 0x09, bytes([0, 1, 0, 1]) + bytes(rgb))

    def wave(self, zone, direction=1):
        return self._effect(zone, 0x04, 0x06, bytes([max(0, min(2, direction)), 0x28]))

class Mouse(Device):
    def __init__(self):
        super().__init__("Naga V2 Pro", MOUSE_TID, {"logo": LOGO, "thumb": ZERO})

    def link(self):  # on its cable the mouse answers there; otherwise over the dongle
        return wired_mouse_link if wired_mouse_link.present() else link

    def dpi(self):
        d = self.get(0x04, 0x85, 0x07, bytes([VARSTORE]))
        return (d[1] << 8 | d[2], d[3] << 8 | d[4]) if d and len(d) > 4 else None

    def set_dpi(self, x, y=None):
        y = x if y is None else y
        x, y = max(100, min(30000, x)), max(100, min(30000, y))  # Naga V2 Pro maximum is 30000
        return self.put(0x04, 0x05, 0x07, bytes([VARSTORE, x >> 8, x & 255, y >> 8, y & 255, 0, 0]))

    POLL = {1000: 0x01, 500: 0x02, 125: 0x08}  # classic encoding, as OpenRazer uses for the Naga V2 Pro
    def poll_rate(self):
        d = self.get(0x00, 0x85, 0x01)
        return {v: k for k, v in self.POLL.items()}.get(d[0]) if d else None

    def set_poll_rate(self, hz):
        if hz not in self.POLL:
            raise ValueError("poll rate must be one of %s" % sorted(self.POLL))
        return self.put(0x00, 0x05, 0x01, bytes([self.POLL[hz]]))

    def idle_seconds(self):
        d = self.get(0x07, 0x83, 0x02)
        return d[0] << 8 | d[1] if d else None

    def set_idle_seconds(self, s):
        s = max(60, min(900, s))
        return self.put(0x07, 0x03, 0x02, bytes([s >> 8, s & 255]))

# Standard Razer 6x22 key matrix (OpenRazer daemon/keyboard.py KEY_MAPPING). The DeathStalker V2 Pro has no macro-key column,
# so its real columns are one lower than the standard map (found on the hardware: a test frame landed one key to the right).
ROWS, COLS = 6, 22
KEY_COL_OFFSET = -1

class Keyboard(Device):
    def __init__(self):
        super().__init__("DeathStalker V2 Pro", KEYBOARD_TID, {"backlight": BACKLIGHT})

    def frame(self, matrix, wait=0.01):
        """Per-key lighting: matrix = 6 rows x 22 (r, g, b). One row command each (class 0F id 03, 0x47 bytes), then show the custom frame."""
        ok = True
        for row in range(ROWS):
            cells = b"".join(bytes(matrix[row][c]) for c in range(COLS))
            st, _ = link.xfer(self.tid, 0x0F, 0x03, 0x47, bytes([0, 0, row, 0, COLS - 1]) + cells, wait=wait)
            ok = ok and st == 2
        st, _ = link.xfer(self.tid, 0x0F, 0x02, 0x0C, bytes([0, 0, 0x08]), wait=wait)  # effect 8 = custom frame, not stored
        self.last = st
        return ok and st == 2

mouse, keyboard = Mouse(), Keyboard()

if __name__ == "__main__":  # read-only report of everything this module can read
    for dev in (mouse, keyboard):
        print(f"{dev.name}: firmware {dev.firmware()} | battery {dev.battery()}% | charging {dev.charging()}")
        print("  brightness:", {z: dev.brightness(z) for z in dev.lights})
    print("mouse dpi:", mouse.dpi(), "| poll rate:", mouse.poll_rate(), "Hz | idle:", mouse.idle_seconds(), "s")
