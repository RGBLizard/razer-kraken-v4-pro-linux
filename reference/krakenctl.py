#!/usr/bin/env python3
"""krakenctl: Razer Kraken V4 Pro hub (1532:0568), read-only for now. Stdlib only."""
import glob, os, select, sys, time

def find_hidraw():
    # vendor control channel = USB interface 4 of 1532:0568
    for d in glob.glob("/sys/class/hidraw/hidraw*"):
        ev = open(d + "/device/uevent").read()
        if "00001532:00000568" in ev and "/input4" in ev:
            return "/dev/" + os.path.basename(d)
    sys.exit("Kraken hub (interface 4) not found")

DIRECT = set()  # fds of the headphones' own USB control channel (cable): same protocol, but not forwarded through the dock

def packet(cmd, data=b"", headset=True):
    p = bytearray(64)
    p[0], p[2] = 2, 0x60
    p[6] = 3 + len(data) + 1
    p[9] = 0x80 if headset else 0
    p[10:13] = bytes([cmd, 0, len(data)])
    p[13:13 + len(data)] = data
    for b in p[:62]:
        p[62] ^= b
    return bytes(p)

def get(fd, cmd, data=b"", headset=True):
    """Send a command (Get when data is empty, Set otherwise), return reply data bytes or None."""
    os.write(fd, packet(cmd, data, headset and fd not in DIRECT))
    end = time.time() + 1.0
    while time.time() < end:
        if not select.select([fd], [], [], 0.1)[0]:
            continue
        r = os.read(fd, 64)
        if r[0] == 2 and r[10] == cmd and r[11] == 1:
            if r[1] != 2:
                return None
            return r[13:13 + r[12]]
    return None

def haptics_apply(fd):
    """Synapse sends this constant 'perception volume' command after every haptics change (captures C/A); sliders felt dead without it."""
    haptics(fd, 0x09, bytes([1, 0, 0xD8, 0x00, 0x43]))

def haptics_raw(fd, desc):
    """Haptics-variant packet with an arbitrary descriptor (e.g. 8a/8b intensity); reply matched on descriptor[0]."""
    p = bytearray(64)
    p[0], p[2], p[6], p[7], p[8], p[9] = 2, 0x60, 0x25, 0x16, 0x5F, 0 if fd in DIRECT else 0x80
    p[10:10 + len(desc)] = desc
    p[62] = -sum(p[10:62]) & 255
    os.write(fd, bytes(p))
    end = time.time() + 1.0
    while time.time() < end:
        if select.select([fd], [], [], 0.1)[0]:
            r = os.read(fd, 64)
            if r[0] == 2 and r[10] == desc[0] and r[11] == 1:
                return r[:20]
    return None

def haptics_intensity(fd, level=None):
    """Get = 8b (reply: state at [13], level at [14]); Set = 8a 01 <on> <level>, level 1..6, off = 8a 01 00 00 (captured)."""
    if level is None:
        r = haptics_raw(fd, bytes([0x8B]))
        return (r[13], r[14]) if r else None
    r = haptics_raw(fd, bytes([0x8A, 0x01, 1 if level else 0, level]))
    haptics_apply(fd)
    return r

def haptics_custom(fd, freq=None, vol=None):
    """Custom profile: frequency Hz (Get sub a3 -> [14..15], Set 23 `01 hi lo`), volume 0..100 (Get 8c -> [15..16], Set 0c `01 01 hi lo`).
    ponytail: the hub exposes one volume value (band 1 and 2 are the same), ranges are not documented: callers cap them."""
    if freq is not None:
        r = haptics(fd, 0x23, bytes([1, freq >> 8, freq & 255]))
        haptics_apply(fd)
        return bool(r and r[1] == 2 and r[13] == 0)
    if vol is not None:
        r = haptics(fd, 0x0C, bytes([1, 1, vol >> 8, vol & 255]))
        haptics_apply(fd)
        return bool(r and r[1] == 2 and r[13] == 0)
    f, v = haptics(fd, 0xA3), haptics(fd, 0x8C, b"\x01")
    return ((f[14] << 8 | f[15]) if f else None, (v[15] << 8 | v[16]) if v else None)

def haptics(fd, sub, data=b""):
    """Haptics variant (descriptor 8c <sub>): checksum is -sum(bytes 10..61), verified on 6 captured frames."""
    p = bytearray(64)
    p[0], p[2], p[6], p[7], p[8], p[9] = 2, 0x60, 0x25, 0x16, 0x5F, 0 if fd in DIRECT else 0x80
    p[10:12] = bytes([0x8C, sub])
    p[12:12 + len(data)] = data
    p[62] = -sum(p[10:62]) & 255
    os.write(fd, bytes(p))
    end = time.time() + 1.0
    while time.time() < end:
        if select.select([fd], [], [], 0.1)[0]:
            r = os.read(fd, 64)
            if r[0] == 2 and r[10] == 0x8C and r[11] == 1 and r[12] == sub:
                return r[:20]
    return None

def oled(fd, cmd, data=b""):
    """OLED settings (class 0x17): no checksum, [6]=len(data), data from byte 9. Get = 0x80 + Set id."""
    p = bytearray(64)
    p[0], p[2], p[6], p[7], p[8] = 2, 0x60, len(data), 0x17, cmd
    p[9:9 + len(data)] = data
    os.write(fd, bytes(p))
    end = time.time() + 1.0
    while time.time() < end:
        if select.select([fd], [], [], 0.1)[0]:
            r = os.read(fd, 64)
            if r[0] == 2 and r[7] == 0x17 and r[8] == cmd:
                return r[9:19] if r[1] == 2 else None  # reply [6] is 0 even when data follows
    return None

def get_oled(fd, cmd):
    d = oled(fd, cmd)
    return d[0] if d else None

FONT = {"L": "10000 10000 10000 10000 10000 10000 11111", "I": "11111 00100 00100 00100 00100 00100 11111",
        "N": "10001 11001 10101 10101 10011 10001 10001", "U": "10001 10001 10001 10001 10001 10001 01110",
        "X": "10001 01010 00100 00100 00100 01010 10001"}

def demo_pixels(text="LINUX", scale=6):
    """232x64 grayscale (0..15): text in a frame. 5x7 font, no deps."""
    px = [[0] * 232 for _ in range(64)]
    for x in range(232):
        px[0][x] = px[63][x] = 5
    for y in range(64):
        px[y][0] = px[y][231] = 5
    x0 = (232 - (len(text) * 6 - 1) * scale) // 2
    for n, ch in enumerate(text):
        for r, row in enumerate(FONT[ch].split()):
            for c, bit in enumerate(row):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            px[11 + r * scale + dy][x0 + n * 6 * scale + c * scale + dx] = 15
    return px

def read_pgm(path):
    d = open(path, "rb").read()
    parts = d.split(None, 4)  # P5 w h maxval data (maxval is followed by one whitespace byte)
    if parts[0] != b"P5" or parts[1:4] != [b"232", b"64", b"255"]:
        sys.exit("need a binary 232x64 8-bit PGM (P5). Make one: magick in.png -resize 232x64! -colorspace Gray out.pgm")
    data = parts[4]
    return [[data[y * 232 + x] // 16 for x in range(232)] for y in range(64)]

def oled_upload(fd, slot, px):
    """Home-screen image upload (report ID 10), verified byte-for-byte against capture. Aborts on any non-ok ack."""
    flat = [v for row in px for v in row]
    pix = bytes(flat[i] | flat[i + 1] << 4 for i in range(0, len(flat), 2))  # first pixel = low nibble
    guid = os.urandom(10).hex().encode()  # 20 ASCII hex chars, same shape as Synapse's
    block = bytearray(64)
    block[0:4] = (232).to_bytes(2, "big") + (64).to_bytes(2, "big")
    block[4:24] = guid
    block[60:64] = sum(pix).to_bytes(4, "big")
    body = bytes(block) + pix
    def send(p):
        os.write(fd, bytes(p).ljust(64, b"\0"))
        end = time.time() + 2.0
        while time.time() < end:
            if select.select([fd], [], [], 0.1)[0]:
                r = os.read(fd, 64)
                if r[0] == 0x0A:
                    return r[2] == 2
        return False
    if not send(bytes([0x0A, 0x12, 0, slot, 0, 0, 232, 0, 64])):
        sys.exit("upload header rejected, nothing written")
    time.sleep(0.3)
    chunks = [body[i:i + 59] for i in range(0, len(body), 59)]
    for n, c in enumerate(chunks):
        if not send(bytes([0x0A, 0x13, len(c), n >> 8, n & 255]) + c):
            sys.exit(f"packet {n}/{len(chunks)} not acknowledged, aborting")
    return guid.decode(), len(chunks)

def mode(fd, new=None):
    """Device mode (class 0): Get = id 0x84, Set = id 4 (0 Normal, 3 Driver). Returns raw reply."""
    p = bytearray(64)
    p[0] = 2
    p[6] = 1 + (new is not None)
    p[8] = 4 if new is not None else 0x84
    if new is not None:
        p[10] = new
    for b in p[:62]:
        p[62] ^= b
    os.write(fd, bytes(p))
    end = time.time() + 1.0
    while time.time() < end:
        if select.select([fd], [], [], 0.1)[0]:
            r = os.read(fd, 64)
            if r[0] == 2 and r[8] == p[8]:
                return r[:14].hex(" ")
    return None

# Speaker EQ (from Synapse main.js; bands = 31 63 125 250 500 1k 2k 4k 8k 16k Hz, dB).
# The hub stores only the 10 bands, so the preset id (0x93) is sent first, then the bands (0x95).
EQ = {"default": (0, [0] * 10), "game": (7, [-4, -1, 1, 2, 3, 3, 3, 3, 3, 2]),
      "music": (8, [4, 3, 2, 1, 1, 1, 2, 2, 1, 0]), "movie": (9, [5, 5, 0, -2, -2, 1, 4, 5, 4, 2])}

def main():
    fd = os.open(find_hidraw(), os.O_RDWR)
    if sys.argv[1:2] == ["mode"]:
        print(mode(fd, int(sys.argv[2]) if len(sys.argv) > 2 else None))
        return
    if sys.argv[1:2] == ["brightness"]:
        # Get = 0x41 value / 0x43 on-off, Set = 0xC1 / 0xC3; 0% = off + value 1 (as Synapse does)
        v, on = get(fd, 0x41), get(fd, 0x43)
        print(f"brightness: {round(v[0] * 100 / 255)}% ({'on' if on[0] else 'off'})")
        if len(sys.argv) == 3:
            pct = int(sys.argv[2])
            if not 0 <= pct <= 100:
                sys.exit("usage: brightness [0..100]")
            ok = get(fd, 0xC3, bytes([pct > 0])) == b"\x00" and \
                 get(fd, 0xC1, bytes([round(pct * 2.55) if pct else 1])) == b"\x00"
            print("set:", "ok" if ok else "REJECTED")
        return
    if sys.argv[1:2] == ["oled-upload"]:
        # usage: oled-upload [file.pgm]  -> home-screen image slot 0, then selects it
        guid, n = oled_upload(fd, 0, read_pgm(sys.argv[2]) if len(sys.argv) > 2 else demo_pixels())
        print(f"uploaded {n} packets, guid {guid}")
        oled(fd, 0x02, bytes([1, 0])); oled(fd, 0x0D, bytes([1]))
        print("selected image slot 0")
        return
    if sys.argv[1:2] == ["oled-screen"]:
        # Home screen: Get = 0x82, Set = 0x02 data [type, param]; image captures used [1, index]
        T = ["animation", "image", "banner", "device-info", "emote", "audio", "system-info", "off"]
        d = oled(fd, 0x82)
        print("screen:", (T[d[0]] if d[0] < len(T) else d[0], d[1]) if d else None)
        if len(sys.argv) >= 3:
            if sys.argv[2] not in T:
                sys.exit("usage: oled-screen [" + "|".join(T) + "] [param]")
            ok = oled(fd, 0x02, bytes([T.index(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 0])) is not None
            print("set:", "ok" if ok else "REJECTED")
        return
    if sys.argv[1:2] == ["oled-brightness"]:
        # Get = 0x83, Set = 0x03, 0..100 percent
        print("oled brightness:", get_oled(fd, 0x83))
        if len(sys.argv) == 3:
            v = int(sys.argv[2])
            if not 0 <= v <= 100:
                sys.exit("usage: oled-brightness [0..100]")
            ok = oled(fd, 0x03, bytes([v])) is not None
            print("set:", "ok" if ok else "REJECTED")
            print("oled brightness:", get_oled(fd, 0x83))
        return
    if sys.argv[1:2] == ["mic-eq"]:
        # Get = 0x16, Set = 0x96; presets: 0 default, 1 micboost, 2 broadcast, 3 conference (id only, as Synapse does)
        names = {0: "default", 1: "micboost", 2: "broadcast", 3: "conference"}
        print("mic eq:", names.get(get(fd, 0x16)[0], "custom"))
        if len(sys.argv) == 3:
            if sys.argv[2] not in names.values():
                sys.exit("usage: mic-eq [default|micboost|broadcast|conference]")
            ok = get(fd, 0x96, bytes([list(names.values()).index(sys.argv[2])])) == b"\x00"
            print("set:", "ok" if ok else "REJECTED")
            print("mic eq:", names.get(get(fd, 0x16)[0], "custom"))
        return
    if sys.argv[1:2] == ["eq"]:
        # Get = 0x13 preset / 0x15 bands, Set = 0x93 / 0x95 (signed bytes)
        pid, bands = get(fd, 0x13)[0], list(get(fd, 0x15))
        print("eq:", next((n for n, (i, _) in EQ.items() if i == pid), "custom" if pid == 255 else pid), [b - 256 if b > 127 else b for b in bands])
        if len(sys.argv) > 2:
            a = sys.argv[2]
            if a in EQ:
                pid, bands = EQ[a]
            elif a == "custom" and len(sys.argv) == 13:
                pid, bands = 255, [int(x) for x in sys.argv[3:]]
                if not all(-12 <= b <= 12 for b in bands):
                    sys.exit("bands must be -12..12")
            else:
                sys.exit("usage: eq [default|game|music|movie | custom b0..b9]")
            ok = get(fd, 0x93, bytes([pid])) == b"\x00" and get(fd, 0x95, bytes(b & 255 for b in bands)) == b"\x00"
            print("set:", "ok" if ok else "REJECTED")
        return
    if sys.argv[1:2] == ["haptics-custom"]:
        # experimental ranges: frequency 40..250 Hz, volume 0..100 (hub default 200 Hz / 100)
        f, v = haptics_custom(fd)
        print(f"custom haptics: {f} Hz, volume {v}")
        if len(sys.argv) == 4:
            nf, nv = int(sys.argv[2]), int(sys.argv[3])
            if not (40 <= nf <= 250 and 0 <= nv <= 100):
                sys.exit("usage: haptics-custom [40..250 Hz] [0..100]")
            print("set:", "ok" if haptics_custom(fd, freq=nf) and haptics_custom(fd, vol=nv) else "REJECTED")
            f, v = haptics_custom(fd)
            print(f"custom haptics: {f} Hz, volume {v}")
        return
    if sys.argv[1:2] == ["haptics-intensity"]:
        cur = haptics_intensity(fd)
        print("haptics intensity:", "off" if cur and not cur[0] else cur[1] if cur else "no reply")
        if len(sys.argv) == 3:
            n = int(sys.argv[2])
            if not 0 <= n <= 6:
                sys.exit("usage: haptics-intensity [0..6]  (0 = off)")
            r = haptics_intensity(fd, n)
            print("set:", "no reply" if not r else "ok" if r[1] == 2 and r[13] == 0 else "REJECTED " + r.hex(" "))
            cur = haptics_intensity(fd)
            print("haptics intensity:", "off" if cur and not cur[0] else cur[1] if cur else "no reply")
        return
    if sys.argv[1:2] == ["haptics"]:
        # profile: Set sub 0x24 (1 Controlled, 2 Balanced, 3 Dynamic, 4 Custom), Get sub 0xA4
        names = {1: "Controlled", 2: "Balanced", 3: "Dynamic", 4: "Custom"}
        if len(sys.argv) == 2:
            r = haptics(fd, 0xA4)
            print("haptics:", names.get(r[13], r[13]) if r else "no reply")
            return
        n = int(sys.argv[2])
        if n not in names:
            sys.exit("usage: haptics [1-4]  (1 Controlled, 2 Balanced, 3 Dynamic, 4 Custom)")
        r = haptics(fd, 0x24, bytes([n]))
        haptics_apply(fd)
        print(f"set {names[n]}:", "no reply" if not r else "ok" if r[1] == 2 and r[13] == 0 else "REJECTED " + r.hex(" "))
        return
    if sys.argv[1:2] == ["balance"]:
        # Game/Chat balance: Get = 0x5C, Set = 0xDC; 0 = all chat, 10 = middle, 20 = all game
        print("balance:", get(fd, 0x5C)[0])
        if len(sys.argv) == 3:
            v = int(sys.argv[2])
            if not 0 <= v <= 20:
                sys.exit("usage: balance [0..20]")
            print("set:", "ok" if get(fd, 0xDC, bytes([v])) == b"\x00" else "REJECTED")
            print("balance:", get(fd, 0x5C)[0])
        return
    if len(sys.argv) == 3 and sys.argv[1] in ("sidetone", "sidetone-level"):
        # Get = 0x18 / 0x19, Set = 0x98 / 0x99 (bit 0x80); level 0..15
        get_id = 0x18 if sys.argv[1] == "sidetone" else 0x19
        v = {"on": 1, "off": 0}.get(sys.argv[2]) if get_id == 0x18 else int(sys.argv[2])
        if v is None or not 0 <= v <= 15:
            sys.exit("usage: sidetone on|off | sidetone-level 0..15")
        print("before:", get(fd, get_id))
        print("set reply:", get(fd, get_id | 0x80, bytes([v])))
        print("after: ", get(fd, get_id))
        return
    # ids from protocol-from-source.md section 3: 0x00 serial, 0x02 firmware, 0x21 battery
    for name, cmd in (("serial", 0x00), ("firmware", 0x02), ("battery", 0x21)):
        d = get(fd, cmd)
        if d is None:
            print(f"{name}: no reply")
        elif name == "serial":
            print(f"{name}: {d.decode('ascii', 'replace')}")
        elif name == "battery":
            print(f"{name}: {d[0]}%")
        else:
            print(f"{name}: {d.hex(' ')}")

if __name__ == "__main__":
    main()
