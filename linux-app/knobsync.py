#!/usr/bin/env python3
"""Hub knob menu -> sound. The hub tells us when its EQ preset changes (event 13 02 01 <id>) but stores no curves;
Synapse answers by writing 0x93 (preset) + 0x95 (10 bands). Do the same, so the knob menu really changes the sound.
Haptics/sidetone/intensity are applied by the hub itself and need nothing from us."""
import json, os, select, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krakenctl as k

CUSTOM = os.path.expanduser("~/.config/kraken-control/custom-eq.json")  # written by the panel when you save a custom curve

def bands_for(pid):
    for _, (i, b) in k.EQ.items():
        if i == pid:
            return b
    if pid == 255:
        try:
            return json.load(open(CUSTOM))
        except (OSError, ValueError):
            return [0] * 10
    return None  # unknown preset id: leave the hub alone

def wired_fd():
    """The headphones' own control channel while they are on a cable; the dock's EQ menu should reach them there too."""
    for d in os.listdir("/sys/class/hidraw"):
        try:
            ev = open(f"/sys/class/hidraw/{d}/device/uevent").read()
        except OSError:
            continue
        if "00001532:00000567" in ev and "/input5" in ev:
            try:
                fd = os.open("/dev/" + d, os.O_RDWR)
            except OSError:
                return None
            k.DIRECT.add(fd)
            return fd

def main():
    for _ in range(60):  # at login the hub can enumerate a moment after we start
        try:
            fd = os.open(k.find_hidraw(), os.O_RDWR)
            break
        except SystemExit:
            time.sleep(2)
    else:
        sys.exit("Kraken hub not found")
    last = (None, 0.0)
    print("knobsync: listening for hub EQ changes", flush=True)
    while True:
        if not select.select([fd], [], [])[0]:
            continue
        r = os.read(fd, 64)
        # unsolicited event: [10]=0x13 preset-EQ, [11]=0x02 (event marker; our own replies carry 0x01), [13]=preset id
        if r[0] == 2 and r[10] == 0x13 and r[11] == 0x02:
            pid = r[13]
            if pid == last[0] and time.time() - last[1] < 1.0:
                continue
            bands = bands_for(pid)
            if bands is None:
                print(f"knobsync: unknown EQ preset {pid}, ignored", flush=True)
                continue
            target = wired_fd() or fd
            ok = k.get(target, 0x93, bytes([pid])) == b"\x00" and k.get(target, 0x95, bytes(b & 255 for b in bands)) == b"\x00"
            if target != fd:
                k.DIRECT.discard(target); os.close(target)
            last = (pid, time.time())
            print(f"knobsync: preset {pid} -> bands {bands} {'ok' if ok else 'REJECTED'}", flush=True)

if __name__ == "__main__":
    main()
