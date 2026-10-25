#!/usr/bin/env python3
"""THX-like chain: `thx.py on|off`. on = EasyEffects running + preset + default sink -> EasyEffects + volsync; off = effects bypassed."""
import json, os, re, subprocess, sys, time

EE = ["flatpak", "run", "com.github.wwmm.easyeffects"]
PRESET = "THX-like"

def run(*a, **kw):
    return subprocess.run(list(a), capture_output=True, text=True, **kw)

def node_id(match):
    for o in json.loads(run("pw-dump").stdout):
        n = o.get("info", {}).get("props", {}).get("node.name", "")
        if o.get("type") == "PipeWire:Interface:Node" and match(n):
            return o["id"]

def kraken_sink():  # the Game output; the Chat one is pro-output-0
    return node_id(lambda n: n.startswith("alsa_output.usb-Razer_Kraken") and n.endswith("pro-output-1"))

EE_ID = "com.github.wwmm.easyeffects"
EE_RC = os.path.expanduser(f"~/.var/app/{EE_ID}/config/easyeffects/db/easyeffectsrc")
HUB_SINK_END = "pro-output-1"

def wired_sink():
    """The headphones' own USB sound output (while they are on a cable)."""
    for o in json.loads(run("pw-dump").stdout):
        n = o.get("info", {}).get("props", {}).get("node.name", "")
        if o.get("type") == "PipeWire:Interface:Node" and n.startswith("alsa_output.usb-Razer_Inc_Razer_Kraken") and n.endswith("analog-stereo"):
            return n

def hub_sink_name():
    for o in json.loads(run("pw-dump").stdout):
        n = o.get("info", {}).get("props", {}).get("node.name", "")
        if o.get("type") == "PipeWire:Interface:Node" and n.startswith("alsa_output.usb-Razer_Kraken") and n.endswith(HUB_SINK_END):
            return n

def cards():
    """[(alsa card number, is_wired)] for the Kraken dock and the headphones' own card."""
    lines = open("/proc/asound/cards").read().splitlines()
    out = []
    for i, l in enumerate(lines):
        m = re.match(r"\s*(\d+) \[\w+\s*\]: USB-Audio - Razer Kraken", l)
        if m:
            out.append((m.group(1), "Razer Inc" in (lines[i + 1] if i + 1 < len(lines) else "")))
    return out

def active_card():
    """The card whose hardware volume matters now: the headphones' own on a cable, else the dock's."""
    cs = cards()
    return next((c for c, w in cs if w), None) or next((c for c, w in cs if not w), None)

def ee_output_device():
    m = re.search(r"^outputDevice=(.*)$", open(EE_RC).read(), re.M)
    return m.group(1) if m else None

def strays_to_default():
    """After the chain moved, apps that grabbed the new device directly go back to the default output (our chain)."""
    out = json.loads(run("pactl", "-f", "json", "list", "sink-inputs").stdout or "[]")
    for si in out:
        p = si.get("properties", {})
        who = " ".join(str(p.get(k, "")) for k in ("application.name", "node.name", "application.id", "media.name")).lower()
        if any(x in who for x in ("easy effects", "easyeffects", "ee_", "kraken_surround")):
            continue
        run("pactl", "move-sink-input", str(si["index"]), "@DEFAULT_SINK@")

def route(device, thx_on=True):
    """Point EasyEffects' output at `device` (hub Game output or the headphones' cable). EasyEffects only reads this at start, so it restarts
    (a few seconds of silence, which happens anyway when the headphones switch between wireless and cable)."""
    if ee_output_device() == device:
        return False
    surround = surround_active() if surround_node() else False
    running = ee_sink() is not None
    if running:
        run("flatpak", "kill", EE_ID)
        for _ in range(20):
            time.sleep(0.5)
            if not ee_sink():
                break
    rc = open(EE_RC).read()
    open(EE_RC, "w").write(re.sub(r"^outputDevice=.*$", "outputDevice=" + device, rc, flags=re.M))
    if running:  # not running: the next start (login autostart) simply reads the new device
        on()
        if not thx_on:
            off()
        if surround:
            set_surround(True)
        strays_to_default()
    return True

def fix_right_channel():
    """After the dock re-enumerates, the hub sink's right port can be named playback_2 instead of AUX1, so EasyEffects never links it
    (left ear only). Link EE's FR to whichever second port exists."""
    n = hub_sink_name()
    if not n or not ee_sink():
        return
    ports = [l for l in run("pw-link", "-i").stdout.split("\n") if l.startswith(n + ":playback_")]
    linked = run("pw-link", "-l").stdout
    if len(ports) == 2 and ports[1] not in linked:
        run("pw-link", "ee_soe_output_level:output_FR", ports[1])

def ee_sink():
    return node_id(lambda n: n == "easyeffects_sink")

def volume(node):
    return run("wpctl", "get-volume", str(node)).stdout.split()[1]

def surround_node():
    return node_id(lambda n: n == "kraken_surround")

def default_sink_name():
    return run("pactl", "get-default-sink").stdout.strip()

def surround_feeds_ee():
    out = run("pw-link", "-l").stdout.splitlines()
    for i, line in enumerate(out):
        if "kraken_surround_out:output_FL" in line and i > 0:
            return out[i - 1].startswith("easyeffects_sink")
    return False

def surround_active():
    return default_sink_name() == "kraken_surround"

def set_surround(on):
    """Virtual 7.1 (HRTF) as the default output, or back to the plain EasyEffects sink. Your volume carries across."""
    s, e = surround_node(), ee_sink()
    if not (s and e):
        raise RuntimeError("surround or EasyEffects sink missing")
    src, dst = (e, s) if on else (s, e)
    run("wpctl", "set-volume", str(dst), volume(src))
    run("wpctl", "set-default", str(dst))

def on():
    started = False
    if not ee_sink():
        started = True
        subprocess.Popen(EE + ["--service-mode"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            time.sleep(0.5)
            if ee_sink():
                break
    if (started or not surround_feeds_ee()) and surround_node():
        # the surround sink's output targets EasyEffects' sink by name; after EasyEffects (re)starts it must re-resolve it
        run("systemctl", "--user", "restart", "filter-chain.service")
        for _ in range(20):
            time.sleep(0.5)
            if surround_node():
                break
    e = ee_sink()
    if not e:
        sys.exit("EasyEffects sink did not appear")
    run(*EE, "-l", PRESET)
    run(*EE, "-b", "2")  # bypass off
    if not surround_active():  # keep virtual surround if you chose it
        run("wpctl", "set-default", str(e))
    if run("pgrep", "-f", "^python3 -I .*volsync.py").stdout.strip() == "":
        # volume lives on the hub hardware (volsync.py); the OLED shows it and EasyEffects no longer attenuates twice
        subprocess.Popen(["python3", "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "volsync.py")],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def off():
    """Effects off = EasyEffects bypass. Routing stays, so volume keeps working through volsync."""
    run(*EE, "-b", "1")

if __name__ == "__main__":
    {"on": on, "off": off}[sys.argv[1]]()
