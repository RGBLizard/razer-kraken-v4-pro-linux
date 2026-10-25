#!/usr/bin/env python3
"""PC stats on the hub's System Info screen: CPU/GPU usage + temperature, memory, time. Stdlib only.
Layout from Synapse's code: home screen type 6 with three slides (left/right item each), then a 40-byte update about once a second.
Item ids: 1 CPU use, 2 CPU temp, 3 GPU use, 4 GPU temp, 5 memory, 7 time."""
import glob, os, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krakenctl as k

SLIDES = ((1, 2), (3, 4), (5, 7))  # (left, right) per slide: CPU, GPU, memory + time
HOME = bytes([6] + [(l << 4) | r for l, r in SLIDES] + [3, 0, 0] + [0] * 12)  # type, 3 slide bytes, interval 3 s, Celsius, no notification, positions 0

def _read(path):
    try:
        return open(path).read().strip()
    except OSError:
        return None

def hwmon_temp(*names):
    """First temp1_input (deg C) of a hwmon chip with one of these names; k10temp = AMD CPU, coretemp = Intel CPU, amdgpu = AMD GPU."""
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if _read(d + "/name") in names:
            v = _read(d + "/temp1_input")
            if v:
                return round(int(v) / 1000)
    return 0

def gpu_busy():
    for f in glob.glob("/sys/class/drm/card*/device/gpu_busy_percent"):
        v = _read(f)
        if v is not None:
            return int(v)
    return 0

def mem_percent():
    m = dict(l.split(":") for l in open("/proc/meminfo").read().splitlines())
    total, avail = int(m["MemTotal"].split()[0]), int(m["MemAvailable"].split()[0])
    return round(100 * (total - avail) / total)

class Cpu:
    def __init__(self):
        self.prev = self._sample()

    @staticmethod
    def _sample():
        v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        return sum(v), v[3] + v[4]  # total, idle+iowait

    def percent(self):
        t, i = self._sample()
        dt, di = t - self.prev[0], i - self.prev[1]
        self.prev = (t, i)
        return round(100 * (dt - di) / dt) if dt else 0

def update_packet(cpu_pct):
    """The 40 bytes Synapse sends (layout from its setOLEDUpdateSystemInformation)."""
    n = bytearray(40)
    now = time.localtime()
    ct, gt = hwmon_temp("k10temp", "coretemp"), hwmon_temp("amdgpu")
    n[0], n[1] = 1, cpu_pct
    n[5], n[6], n[7], n[8] = 2, ct >> 8, ct & 255, 0
    n[10], n[11] = 3, gpu_busy()
    n[15], n[16], n[17], n[18] = 4, gt >> 8, gt & 255, 0
    n[20], n[21] = 5, mem_percent()
    n[25] = 6  # date item (Synapse leaves its bytes 26..29 zero)
    n[30], n[31], n[32], n[33], n[34] = 7, now.tm_hour, now.tm_min, now.tm_sec, 1  # 24-hour clock
    n[35], n[36] = 8, 0
    return bytes(n)

class Runner:
    def __init__(self, fd_getter, lock):
        self.fd, self.lock = fd_getter, lock
        self.stop_ev = threading.Event()
        self.thread = None
        self.saved_saver = None

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self):
        if self.running:
            return
        self.stop_ev.clear()
        with self.lock:
            fd = self.fd()
            cur = k.oled(fd, 0x96)  # screensaver [enabled, picture]: it would hide the stats after a minute of idling
            if cur and cur[0]:
                self.saved_saver = bytes(cur[:2])
                k.oled(fd, 0x16, bytes([0, cur[1]]))
            k.oled(fd, 0x02, HOME)  # home screen -> System Info with our slides
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        cpu = Cpu()
        while not self.stop_ev.wait(1.0):
            with self.lock:
                k.oled(self.fd(), 0x09, update_packet(cpu.percent()))

    def stop(self):
        if not self.running:
            return
        self.stop_ev.set()
        self.thread.join(3)
        with self.lock:  # back to the picture in image slot 0, screensaver as it was
            fd = self.fd()
            k.oled(fd, 0x02, bytes([1, 0]))
            if self.saved_saver:
                k.oled(fd, 0x16, self.saved_saver)
                self.saved_saver = None

if __name__ == "__main__":  # offline self-check of the packet layout, plus a live sensor reading
    p = update_packet(42)
    assert len(p) == 40 and p[0] == 1 and p[1] == 42 and p[5] == 2 and p[10] == 3 and p[15] == 4 and p[20] == 5 and p[30] == 7
    assert HOME[:4] == bytes([6, 0x12, 0x34, 0x57]) and len(HOME) == 19
    print(f"sysinfo packet ok | CPU {hwmon_temp('k10temp','coretemp')}C | GPU {hwmon_temp('amdgpu')}C {gpu_busy()}% | RAM {mem_percent()}%")
