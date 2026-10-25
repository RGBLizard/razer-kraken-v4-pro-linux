#!/usr/bin/env python3
"""Mirror the default sink's volume/mute (EasyEffects sink) to the Kraken hub's hardware volume, like Windows does.
The hub OLED shows the Game unit's level; Windows writes it on every change, Linux never did.
Hardware does the attenuation, so the EasyEffects sink must not also attenuate in software (monitor.channel-volumes off)."""
import math, os, re, select, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thx


def run(*a):
    return subprocess.run(list(a), capture_output=True, text=True).stdout

def card():
    return thx.active_card()  # the headphones' own card on a cable, else the dock's

def card_max(c):
    m = re.search(r"max=(\d+)", run("amixer", "-c", c, "cget", "iface=MIXER,name=PCM Playback Volume,index=0"))
    return int(m.group(1)) if m else 18944  # dock: 0..18944 = -74..0 dB; the headphones' card: 0..74 in 1 dB steps

def default_sink():
    return run("pactl", "get-default-sink").strip()

def sink_state():
    vol = re.search(r"(\d+)%", run("pactl", "get-sink-volume", "@DEFAULT_SINK@"))
    muted = "yes" in run("pactl", "get-sink-mute", "@DEFAULT_SINK@")
    return (int(vol.group(1)) if vol else 100), muted

CURVE_DB = 25  # dB per decade: this headset is quiet and a steep curve wastes the low end (5% = -33 dB, 30% = -13 dB, 60% = -5.5 dB)
MANAGED = ('easyeffects_sink', 'kraken_surround')  # default sinks whose volume is mirrored to the hub
STEP = 5  # percent per knob click: KDE's default volume step, so the later reconcile writes the same value

class Hub:
    """Long-lived `amixer -s` so a write costs microseconds, not a fork per control (Windows writes within ms of the click)."""
    def __init__(self, c):
        self.c, self.max = c, card_max(c)
        self.p = subprocess.Popen(["amixer", "-c", c, "-s", "-q"], stdin=subprocess.PIPE, text=True, bufsize=1)

    def apply(self, pct, muted):
        db = max(-74, CURVE_DB * math.log10(max(pct, 1) / 100))  # hub range is -74..0 dB
        raw = round(self.max * (1 + db / 74))  # card range 0..max = -74..0 dB
        silent = muted or pct == 0
        for idx in (1, 0):  # Game unit first, then Chat, like the Windows capture
            self.p.stdin.write(f"cset iface=MIXER,name='PCM Playback Switch',index={idx} {'off' if silent else 'on'}\n"
                               f"cset iface=MIXER,name='PCM Playback Volume',index={idx} {raw},{raw}\n")

def ensure_props(state):
    """EasyEffects/surround sinks are recreated with new ids on restart; re-disable their software volume whenever that happens."""
    ids = (thx.ee_sink(), thx.surround_node())
    if ids != state.get("ids"):
        e, s = ids
        if e:
            thx.run("pw-cli", "s", str(e), "Props", '{ params = [ "monitor.channel-volumes" false ] }')
        if s:
            thx.run("pw-cli", "s", str(s), "Props", '{ params = [ "channelmix.disable" true ] }')
        state["ids"] = ids

def wait_for(fn, what):
    for _ in range(60):  # at login the hub / sinks can appear a moment after we start
        r = fn()
        if r:
            return r
        time.sleep(2)
    sys.exit(f"{what} not found")

def main():
    c = wait_for(card, "Kraken sound card")
    e, s = thx.ee_sink(), thx.surround_node()
    if e:  # software volume off on the EasyEffects sink; hardware takes over
        thx.run("pw-cli", "s", str(e), "Props", '{ params = [ "monitor.channel-volumes" false ] }')
    if s:  # same for the surround sink (its config also carries channelmix.disable)
        thx.run("pw-cli", "s", str(s), "Props", '{ params = [ "channelmix.disable" true ] }')
    props_state, props_checked = {}, 0.0
    hub = Hub(c)
    knob = os.open(wait_for(hid_knob, "knob interface"), os.O_RDONLY | os.O_NONBLOCK)
    sub = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, text=True, bufsize=1)
    state = sink_state() if default_sink() in MANAGED else (100, False)
    hub.apply(*state)
    settle = None  # time of the last knob click; PipeWire's report of it arrives late, so reconcile only once clicks stop
    while True:
        ready = select.select([knob, sub.stdout], [], [], 0.4 if settle else None)[0]
        if settle and time.time() - settle > 0.4 and default_sink() in MANAGED:
            settle = None
            real = sink_state()
            if real != state:
                state = real
                hub.apply(*state)
        if knob in ready:  # knob click: write the hub right away, before PipeWire has even heard about it
            r = os.read(knob, 64)
            pct, muted = state
            if r[:2] == b"\x0c\x01":
                state = (min(100, pct + STEP), False)
            elif r[:2] == b"\x0c\x02":
                state = (max(0, pct - STEP), muted)
            elif r[:2] == b"\x0c\x04":
                state = (pct, not muted)
            if state != (pct, muted):
                hub.apply(*state)
                settle = time.time()
        if time.time() - props_checked > 5:  # throttled: pw-dump is not free
            props_checked = time.time()
            ensure_props(props_state)
            c = card()
            if c and c != hub.c:  # headphones plugged into / unplugged from their cable: the volume follows to the card that is playing
                hub.p.terminate()
                hub = Hub(c)
                hub.apply(*state)
        if sub.stdout in ready:
            line = sub.stdout.readline()
            if not line:
                sys.exit("pactl subscribe ended")
            if ("sink" in line or "server" in line) and not settle and default_sink() in MANAGED:
                real = sink_state()  # slider, other apps
                if real != state:
                    state = real
                    hub.apply(*state)

def hid_knob():
    for d in os.listdir("/sys/class/hidraw"):
        ev = open(f"/sys/class/hidraw/{d}/device/uevent").read()
        if "00001532:00000568" in ev and "/input3" in ev:
            return "/dev/" + d
    return None

if __name__ == "__main__":
    main()
