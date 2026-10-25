#!/usr/bin/env python3
"""Kraken V4 Pro control panel: local web UI on 127.0.0.1 over krakenctl's hidraw functions. Stdlib only."""
import glob, json, os, re, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krakenctl as k
import thx as thx_chain
import lighting
import knobsync
import razerdev
import keyfx
import remap
import atexit, signal

PORT = 8799
UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui", "index.html")
EE = ["flatpak", "run", "com.github.wwmm.easyeffects"]
lock = threading.Lock()  # one HID conversation at a time
fd = None
info = {}
thx = {"on": True}  # ponytail: assumed on after boot (autostart runs thx.py on); not re-read from the system

hub_key = None

def hub():
    """The dock gets a new USB device number (and maybe hidraw node) whenever it is replugged: reopen instead of holding a stale fd."""
    global fd, hub_key
    node = k.find_hidraw()
    d = f"/sys/class/hidraw/{os.path.basename(node)}/device/../../"
    key = (node, open(d + "devnum").read())
    if fd is None or key != hub_key:
        if fd is not None:
            try: os.close(fd)
            except OSError: pass
        fd, hub_key = os.open(node, os.O_RDWR), key
    return fd

pfd = [None, None]  # [fd, node] of the headphones' own control channel while they are on a cable

def wired_node():
    """USB interface 5 of 1532:0567: the headphones themselves (cable). Same command set as the dock, not forwarded."""
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            ev = open(d + "/device/uevent").read()
        except OSError:
            continue
        if "00001532:00000567" in ev and "/input5" in ev:
            return "/dev/" + os.path.basename(d)

def phones():
    """fd for headphone-side settings (EQ, haptics, mic, sidetone...): their cable when plugged in, else the dock, which relays to the wireless link."""
    node = wired_node()
    if pfd[0] is not None and pfd[1] != node:  # unplugged (or a different node)
        k.DIRECT.discard(pfd[0])
        try: os.close(pfd[0])
        except OSError: pass
        pfd[:] = [None, None]
    if node and pfd[0] is None:
        pfd[:] = [os.open(node, os.O_RDWR), node]
        k.DIRECT.add(pfd[0])
    return pfd[0] if pfd[0] is not None else hub()

engine = lighting.Engine(hub, lock)
keyfx.engine.audio = lighting.audio  # one pw-record capture shared by the headset and keyboard audio effects
keyfx.engine.audio_busy = lambda: engine.running and engine.cfg["effect"] in lighting.AUDIO_EFFECTS
lighting.other_audio_user = lambda: keyfx.engine.running and keyfx.engine.cfg["effect"] == "audio"
atexit.register(engine.stop)  # puts the hub back to Normal mode on exit

def mic_muted():
    c = thx_chain.active_card()
    if c is None:
        return None
    out = subprocess.run(["amixer", "-c", c, "cget", "iface=MIXER,name=Mic Capture Switch"], capture_output=True, text=True).stdout
    return "values=off" in out

# --- mouse + keyboard (shared dongle, razerdev.py). Reads are cached: wireless devices answer slowly and batteries change slowly.
rz = {"mouse": None, "keyboard": None}
razer_last = {}  # lighting can't be read back from the devices, so remember what was last set (profiles + restore use this)

def razer_read():
    m, k = razerdev.mouse, razerdev.keyboard
    try:
        mdpi = m.dpi()
        rz["mouse"] = {"battery": m.battery(), "charging": m.charging(), "dpi": mdpi[0] if mdpi else None, "poll": m.poll_rate(),
                       "idle": m.idle_seconds(), "logo": m.brightness("logo"), "serial": m.serial(), "firmware": m.firmware()}
        rz["keyboard"] = {"battery": k.battery(), "charging": k.charging(), "brightness": k.brightness("backlight"),
                          "serial": k.serial(), "firmware": k.firmware()}
    except OSError:  # dongle unplugged
        rz["mouse"] = rz["keyboard"] = None

def razer_loop():
    while True:
        razer_read()
        time.sleep(30)

RAZER_KINDS = {"mdpi", "mpoll", "midle", "mbright", "mlight", "kbright", "klight", "kframe", "kfx", "sync"}

def _light(dev, zone, v):
    e, col = v["effect"], tuple(v.get("color", (255, 255, 255)))
    if e == "off": return dev.none(zone)
    if e == "static": return dev.static(zone, col)
    if e == "spectrum": return dev.spectrum(zone)
    if e == "breathing": return dev.breathing(zone, col)
    if e == "wave": return dev.wave(zone, v.get("direction", 1))
    raise ValueError(e)

def apply_razer(kind, v):
    m, k = razerdev.mouse, razerdev.keyboard
    if kind == "mdpi":
        ok = 100 <= v <= 30000 and m.set_dpi(v)
    elif kind == "mpoll":
        ok = v in m.POLL and m.set_poll_rate(v)
    elif kind == "midle":
        ok = 60 <= v <= 900 and m.set_idle_seconds(v)
    elif kind == "mbright":  # {"zone": "logo"|"thumb", "pct": 0..100}
        ok = 0 <= v["pct"] <= 100 and m.set_brightness(v["zone"], v["pct"])
    elif kind == "mlight":  # {"zone", "effect", "color", "direction"}
        ok = _light(m, v["zone"], v)
        razer_last["mlight_" + v["zone"]] = v
    elif kind == "kbright":
        ok = 0 <= v <= 100 and k.set_brightness("backlight", v)
    elif kind == "klight":
        keyfx.engine.stop()
        ok = _light(k, "backlight", v)
        razer_last["klight"] = v
        razer_last.pop("kframe", None)  # last one set wins on restore
        razer_last.pop("kfx", None)
    elif kind == "kfx":  # {"effect": wave|fire|ripple|reactive|audio, "color", "base", "speed"}; "off" stops the animation
        if v["effect"] == "off":
            keyfx.engine.stop(); razer_last.pop("kfx", None); ok = True
        else:
            keyfx.engine.start(v["effect"], v.get("color", (68, 214, 44)), v.get("speed", 5), v.get("base", (0, 0, 0)))
            razer_last["kfx"] = v
            razer_last.pop("klight", None); razer_last.pop("kframe", None)
            ok = True
    elif kind == "kframe":  # {"base": [r,g,b], "keys": {"row,col": [r,g,b]}}, columns in the standard Razer map
        base = tuple(v.get("base", (0, 0, 0)))
        mat = [[base] * razerdev.COLS for _ in range(razerdev.ROWS)]
        for rc, col in v["keys"].items():
            row, c = map(int, rc.split(","))
            c += razerdev.KEY_COL_OFFSET  # this keyboard has no macro column
            if 0 <= row < razerdev.ROWS and 0 <= c < razerdev.COLS:
                mat[row][c] = tuple(col)
        keyfx.engine.stop()
        ok = k.frame(mat)
        razer_last["kframe"] = v
        razer_last.pop("klight", None); razer_last.pop("kfx", None)
    elif kind == "sync":  # one colour everywhere: keyboard, mouse (logo + thumb), headset
        col = list(v["color"])
        keyfx.engine.stop()
        ok = _light(k, "backlight", {"effect": "static", "color": col}) and _light(m, "logo", {"effect": "static", "color": col})
        _light(m, "thumb", {"effect": "static", "color": col})
        razer_last["klight"] = {"effect": "static", "color": col}
        razer_last.pop("kframe", None); razer_last.pop("kfx", None)
        razer_last["mlight_logo"] = razer_last["mlight_thumb"] = {"effect": "static", "color": col, "zone": "logo"}
        set_effect({"effect": "static", "color": col, "speed": 5})
    else:
        raise ValueError(kind)
    threading.Timer(1.0, razer_read).start()  # refresh the cache after any change
    return bool(ok)

def sbyte(v):
    return v - 256 if v > 127 else v

def card():
    m = re.search(r"^\s*(\d+) \[\w+\s*\]: USB-Audio - Razer Kraken", open("/proc/asound/cards").read(), re.M)
    return m.group(1) if m else None

def hub_db():
    c = thx_chain.active_card()  # the headphones' own card while they are on a cable, else the dock's
    if c is None:
        return None
    out = subprocess.run(["amixer", "-c", c, "cget", "iface=MIXER,name=PCM Playback Volume,index=1"],
                         capture_output=True, text=True).stdout
    m, mx = re.search(r": values=(\d+)", out), re.search(r"max=(\d+)", out)  # dock range 0..18944, the headphones' card 0..74; both = -74..0 dB
    return round(int(m.group(1)) / int(mx.group(1)) * 74 - 74) if m and mx else None

def set_hub_db(db):
    # volume is the default sink's level; volsync.py mirrors it to the hub on volsync's curve (dB = 25*log10(pct))
    subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{min(1.0, 10 ** (db / 25)):.3f}"], capture_output=True, check=True)

def state():
    f = hub()
    if not info:
        info["serial"] = (k.get(f, 0x00) or b"").decode("ascii", "replace")
        fw = k.get(f, 0x02) or b""
        info["firmware"] = ".".join(map(str, fw[:4])) if fw else "?"
    r = {**info, "thx": thx["on"], "hubDb": hub_db(), "micMuted": mic_muted(), "surround": thx_chain.surround_active(),
         "effect": {**engine.cfg, "color": list(engine.cfg["color"]), "running": engine.running}}
    pf = phones()
    r["wired"] = pf is not f  # headphones on their own cable
    g = lambda c: k.get(pf, c)
    b = g(0x21); r["battery"] = b[0] if b else None
    r["linked"] = bool(b) and b[0] <= 100  # the dock reads 255 for everything while the headphones are on their own cable or off
    c = g(0x2A); r["charging"] = bool(c[0]) if c else (True if r["wired"] and r["battery"] is not None and r["battery"] < 100 else None)  # over the cable the headphones do not report it, but the cable charges them
    v, on = g(0x41), g(0x43)
    r["light"] = (round(v[0] * 100 / 255) if on and on[0] else 0) if v else None
    x = g(0x5C); r["balance"] = x[0] if x else None
    h = k.haptics(pf, 0xA4); r["haptics"] = h[13] if h else None
    pid, bands = g(0x13), g(0x15)
    r["eq"] = {"id": pid[0] if pid else None, "bands": [sbyte(v) for v in bands] if bands else None}
    o = k.oled(f, 0x83); r["oled"] = o[0] if o else None
    s, l = g(0x18), g(0x19)
    r["sidetone"] = {"on": bool(s[0]) if s else None, "level": l[0] if l else None}
    m = g(0x16); r["micEq"] = m[0] if m else None
    hi = k.haptics_intensity(pf); r["hapInt"] = (hi[1] if hi[0] else 0) if hi else None  # 0 = off, 1..6
    hf, hv = k.haptics_custom(pf); r["hapFreq"], r["hapVol"] = hf, hv
    r["mouse"], r["keyboard"], r["razerLast"] = rz["mouse"], rz["keyboard"], razer_last
    r["eqPresets"] = {n: {"id": i, "bands": b} for n, (i, b) in k.EQ.items()}
    return r

def apply(kind, v):
    if kind in RAZER_KINDS:
        return apply_razer(kind, v)
    f = hub()
    pf = phones()  # headphone-side settings go to the cable when it is plugged in
    if kind == "light":  # same 0%-means-off rule as krakenctl
        ok = k.get(pf, 0xC3, bytes([v > 0])) == b"\x00" and k.get(pf, 0xC1, bytes([round(v * 2.55) if v else 1])) == b"\x00"
    elif kind == "balance":
        ok = k.get(pf, 0xDC, bytes([v])) == b"\x00"
    elif kind == "haptics":
        r = k.haptics(pf, 0x24, bytes([v])); k.haptics_apply(pf); ok = bool(r and r[1] == 2 and r[13] == 0)
    elif kind == "eq":  # v = {"id": n, "bands": [10 signed]}; preset id first, then bands
        if v["id"] == 255:  # remembered so knobsync.py can restore it when the hub's knob menu picks "custom"
            os.makedirs(os.path.dirname(knobsync.CUSTOM), exist_ok=True)
            json.dump(v["bands"], open(knobsync.CUSTOM, "w"))
        ok = k.get(pf, 0x93, bytes([v["id"]])) == b"\x00" and k.get(pf, 0x95, bytes(b & 255 for b in v["bands"])) == b"\x00"
    elif kind == "hapint":  # 0 = off, 1..6
        r = k.haptics_intensity(pf, v); ok = bool(r and r[1] == 2 and r[13] == 0)
    elif kind == "hapfreq":  # experimental range 40..250 Hz
        ok = 40 <= v <= 250 and k.haptics_custom(pf, freq=v)
    elif kind == "hapvol":  # experimental range 0..100
        ok = 0 <= v <= 100 and k.haptics_custom(pf, vol=v)
    elif kind == "miceq":  # 0 default, 1 micboost, 2 broadcast, 3 conference
        ok = k.get(pf, 0x96, bytes([v])) == b"\x00"
    elif kind == "oled":
        ok = k.oled(f, 0x03, bytes([v])) is not None
    elif kind == "sidetone":  # v = {"on": bool, "level": 0..15}
        ok = k.get(pf, 0x99, bytes([v["level"]])) == b"\x00" and k.get(pf, 0x98, bytes([v["on"]])) == b"\x00"
    elif kind == "hubdb":
        set_hub_db(v); ok = True
    elif kind == "micmute":  # the hub's mic mute is the ALSA capture switch; sidetone is refused while muted
        for c, _ in thx_chain.cards():  # dock and (on a cable) the headphones' own card
            subprocess.run(["amixer", "-c", c, "cset", "iface=MIXER,name=Mic Capture Switch", "off" if v else "on"], capture_output=True, check=True)
        ok = True
    elif kind == "surround":  # virtual 7.1 HRTF as default output on/off
        thx_chain.set_surround(bool(v)); ok = True
    elif kind == "thx":
        (thx_chain.on if v else thx_chain.off)()
        thx["on"] = bool(v); ok = True
    else:
        raise ValueError(kind)
    return ok

STORE = os.path.expanduser("~/.config/kraken-control/profiles.json")
# what a preset can hold, grouped like the panel's tabs; "scope" of a saved preset is "all" or one of these keys
SECTIONS = {"sound": ("eq", "balance", "thx", "surround"), "haptics": ("haptics", "hapint", "hapfreq", "hapvol"),
            "light": ("light", "effect"), "display": ("oled",), "mic": ("micmute", "miceq", "sidetone"),
            "mouse": ("mdpi", "mpoll", "midle", "mbright", "mlight_logo", "mlight_thumb"), "keyboard": ("kbright", "klight", "kframe", "kfx")}
HEADPHONE_KINDS = ("eq", "balance", "haptics", "hapint", "hapfreq", "hapvol", "light", "oled", "miceq", "sidetone")  # live in the headphones, reached through the dock
ORDER = ["micmute", "eq", "balance", "haptics", "hapint", "hapfreq", "hapvol", "light", "oled", "miceq", "sidetone"]  # mic unmuted before sidetone

def load_store():
    try:
        return json.load(open(STORE))
    except (OSError, ValueError):
        return {"presets": [], "autoRestore": True, "last": None}

def save_store(s):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    json.dump(s, open(tmp, "w"), indent=1)
    os.replace(tmp, STORE)  # atomic: a crash can't leave half a file

def snapshot(scope="all"):
    """Current settings as a dict, read back from the hub (caller holds the lock)."""
    s = state()
    e = s["effect"]
    full = {"eq": {"id": s["eq"]["id"], "bands": s["eq"]["bands"]}, "balance": s["balance"], "thx": s["thx"], "surround": s["surround"],
            "haptics": s["haptics"], "hapint": s["hapInt"], "hapfreq": s["hapFreq"], "hapvol": s["hapVol"],
            "light": s["light"], "effect": {"effect": e["effect"] if e["running"] else "off", "color": e["color"], "speed": e["speed"]},
            "oled": s["oled"], "micmute": s["micMuted"], "miceq": s["micEq"], "sidetone": s["sidetone"]}
    mo, ke = rz["mouse"] or {}, rz["keyboard"] or {}  # lighting can't be read back, so the last-set looks are saved
    full.update({"mdpi": mo.get("dpi"), "mpoll": mo.get("poll"), "midle": mo.get("idle"),
                 "mbright": {"zone": "logo", "pct": mo["logo"]} if mo.get("logo") is not None else None,
                 "mlight_logo": razer_last.get("mlight_logo"), "mlight_thumb": razer_last.get("mlight_thumb"),
                 "kbright": ke.get("brightness"), "klight": razer_last.get("klight"), "kframe": razer_last.get("kframe"), "kfx": razer_last.get("kfx")})
    if not s["linked"]:  # headphone-side values are garbage right now: leave them out so they never overwrite what was saved
        for k in HEADPHONE_KINDS:
            full.pop(k, None)
    keep = {k for sec in (SECTIONS if scope == "all" else [scope]) for k in SECTIONS[sec]}
    return {k: v for k, v in full.items() if k in keep and v is not None and v != {"on": None, "level": None}}

def set_effect(v):
    if v["effect"] == "off":
        engine.stop()
    else:
        engine.start(v["effect"], v["color"], v["speed"])

def apply_snapshot(data):
    """Write a saved preset back. HID writes take the lock one at a time; lighting, EasyEffects and surround run outside it.
    The sound-chain steps go last and wait for the chain to exist (at login it is still starting); no step can abort the others."""
    res = {}
    for kind in ORDER:
        if data.get(kind) is not None:
            try:
                with lock:
                    res[kind] = apply(kind, data[kind])
            except BaseException as e:  # incl. SystemExit from krakenctl helpers
                res[kind] = str(e)
    if "effect" in data:
        try:
            set_effect(data["effect"])
        except BaseException as e:
            res["effect"] = str(e)
    for key in ("mdpi", "mpoll", "midle", "mbright", "mlight_logo", "mlight_thumb", "kbright", "klight", "kframe", "kfx"):
        if data.get(key) is not None:  # mouse/keyboard: own dongle, own lock; a sleeping device just reports failure
            try:
                kind, v = ("mlight", {**data[key], "zone": key[7:]}) if key.startswith("mlight_") else (key, data[key])
                res[key] = apply_razer(kind, v)
            except BaseException as e:
                res[key] = str(e)
    for kind in ("thx", "surround"):
        if kind in data:
            for attempt in range(30):  # up to ~60 s for EasyEffects + the surround sink to appear
                try:
                    res[kind] = apply(kind, data[kind])
                    break
                except BaseException as e:
                    res[kind] = str(e)
                    time.sleep(2)
    return res

_autosave = [None]
def schedule_autosave():
    """Remember the latest settings so they come back after a reboot (the hub forgets EQ curves and lighting effects)."""
    if _autosave[0]:
        _autosave[0].cancel()
    def go():
        try:
            with lock:
                snap = snapshot()
            s = load_store()
            old = s.get("last") or {}  # a device that can't answer right now (asleep, mid-reconnect) must not erase its saved settings
            looks = ("effect", "klight", "kframe", "kfx")  # these are always complete (and mutually exclusive): take them as read
            s["last"] = {**{k: v for k, v in old.items() if k not in looks}, **snap}
            save_store(s)
        except Exception:
            pass
    _autosave[0] = threading.Timer(2.0, go)
    _autosave[0].start()

def restore_at_start():
    for _ in range(30):  # the hub may not be enumerated yet right after login
        try:
            with lock:
                hub()
            break
        except Exception:
            time.sleep(2)
    s = load_store()
    if s.get("autoRestore") and s.get("last"):
        last = s["last"]
        res = apply_snapshot(last)
        wireless = ("mdpi", "mpoll", "midle", "mbright", "mlight_logo", "mlight_thumb", "kbright", "klight", "kframe", "kfx")
        todo = {k: last[k] for k in wireless if k in last and res.get(k) is not True}
        for _ in range(10):  # mouse/keyboard may be asleep at login; try again until they answer
            if not todo:
                break
            time.sleep(6)
            res = apply_snapshot(todo)
            todo = {k: v for k, v in todo.items() if res.get(k) is not True}

def link_watch():
    """The dock loses the headphones while they are charging on their own cable (and when they power off). When the wireless
    link comes back the headphone settings may be gone, so put the saved ones back."""
    was = None
    while True:
        try:
            with lock:
                b = k.get(hub(), 0x21)
            now = bool(b) and b[0] <= 100
            if was is False and now:
                time.sleep(4)  # let the link settle
                st = load_store()
                if st.get("autoRestore") and st.get("last"):
                    apply_snapshot({k_: v for k_, v in st["last"].items() if k_ in HEADPHONE_KINDS})
            was = now
        except BaseException:  # hub not there yet / unplugged: try again
            pass
        time.sleep(5)

def audio_watch():
    """Sound follows the headphones between wireless (dock) and cable: EasyEffects' output is re-pointed, strays move to our chain.
    The surround/THX/volume chain stays the same either way, so nothing needs re-tweaking when you plug in to charge."""
    while True:
        try:
            want = thx_chain.wired_sink() or thx_chain.hub_sink_name()
            if want and thx_chain.route(want, thx["on"]):
                print("audio: output ->", want, flush=True)
            thx_chain.fix_right_channel()
        except BaseException as e:  # sinks not there yet at login, flatpak hiccup: try again
            print("audio_watch:", e, flush=True)
        time.sleep(3)

def clean_name(n):
    n = " ".join(str(n).split())[:40]
    if not n:
        raise ValueError("name is empty")
    return n

def upload(pixels):
    if len(pixels) != 64 * 232 or not all(isinstance(p, int) and 0 <= p <= 15 for p in pixels):
        raise ValueError("need 232x64 values 0..15")
    f = hub()
    px = [pixels[y * 232:(y + 1) * 232] for y in range(64)]
    guid, n = k.oled_upload(f, 0, px)
    k.oled(f, 0x02, bytes([1, 0])); k.oled(f, 0x0D, bytes([1]))
    return n

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def reply(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def ok_host(self):  # blocks DNS rebinding; POSTs also need a custom header (forces CORS preflight)
        return self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        if not self.ok_host(): return self.reply(403, {"error": "host"})
        if self.path == "/":
            return self.reply(200, open(UI, "rb").read(), "text/html; charset=utf-8")
        if self.path == "/api/profiles":
            s = load_store()
            return self.reply(200, {"presets": [{"name": x["name"], "scope": x["scope"]} for x in s["presets"]], "autoRestore": s.get("autoRestore", True)})
        if self.path == "/api/remap":
            return self.reply(200, {**remap.remapper.cfg, **remap.remapper.status(), "keys": sorted(remap.NAMES), "recording": remap.recorder.fd is not None})
        if self.path == "/api/state":
            try:
                with lock: return self.reply(200, state())
            except Exception as e:
                return self.reply(500, {"error": str(e)})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        if not self.ok_host() or self.headers.get("X-Kraken") != "1": return self.reply(403, {"error": "forbidden"})
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if self.path == "/api/set" and d["kind"] == "effect":  # engine takes the HID lock itself
                set_effect(d["value"])
                schedule_autosave()
                return self.reply(200, {"ok": True})
            if self.path == "/api/profile/save":
                name, scope = clean_name(d["name"]), d.get("scope", "all")
                if scope != "all" and scope not in SECTIONS:
                    raise ValueError("bad scope")
                with lock:
                    snap = snapshot(scope)
                s = load_store()
                s["presets"] = [x for x in s["presets"] if not (x["name"] == name and x["scope"] == scope)] + [{"name": name, "scope": scope, "data": snap}]
                save_store(s)
                return self.reply(200, {"ok": True})
            if self.path in ("/api/profile/load", "/api/profile/delete"):
                name, scope = d["name"], d.get("scope", "all")
                s = load_store()
                hit = [x for x in s["presets"] if x["name"] == name and x["scope"] == scope]
                if not hit:
                    return self.reply(404, {"error": "no such preset"})
                if self.path.endswith("delete"):
                    s["presets"] = [x for x in s["presets"] if x not in hit]; save_store(s)
                    return self.reply(200, {"ok": True})
                res = apply_snapshot(hit[0]["data"])
                schedule_autosave()
                return self.reply(200, {"ok": all(v is True for v in res.values()), "results": res})
            if self.path == "/api/remap":
                remap.remapper.set_config(d)
                return self.reply(200, {"ok": True})
            if self.path == "/api/macro/record":  # {"on": true} starts listening to the keyboard, {"on": false} returns the macro text
                if d["on"]:
                    remap.recorder.start()
                    return self.reply(200, {"ok": True})
                return self.reply(200, {"ok": True, "text": remap.recorder.stop()})
            if self.path == "/api/profile/autorestore":
                s = load_store(); s["autoRestore"] = bool(d["on"]); save_store(s)
                return self.reply(200, {"ok": True})
            if self.path == "/api/set" and d["kind"] in ({"thx", "surround"} | RAZER_KINDS):  # external programs / the other dongle: not under the hub lock
                ok = apply(d["kind"], d["value"])
                schedule_autosave()
                asleep = (not ok) and d["kind"] in RAZER_KINDS and 4 in (razerdev.mouse.last, razerdev.keyboard.last)
                return self.reply(200, {"ok": ok, "asleep": asleep})
            with lock:
                if self.path == "/api/set":
                    ok = apply(d["kind"], d["value"])
                    schedule_autosave()
                    return self.reply(200, {"ok": ok})
                if self.path == "/api/oled-image":
                    return self.reply(200, {"ok": True, "packets": upload(d["pixels"])})
            self.reply(404, {"error": "not found"})
        except SystemExit as e:  # krakenctl aborts with a message
            self.reply(500, {"error": str(e)})
        except Exception as e:
            self.reply(500, {"error": str(e)})

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))  # so atexit restores Normal mode
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:  # already running: just open the page
        if "--no-browser" not in sys.argv:
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        sys.exit(0)
    threading.Thread(target=razer_loop, daemon=True).start()
    threading.Thread(target=link_watch, daemon=True).start()
    threading.Thread(target=audio_watch, daemon=True).start()
    remap.remapper.start()
    atexit.register(remap.remapper.stop)
    if "--restore" in sys.argv:
        threading.Thread(target=restore_at_start, daemon=True).start()
    if "--no-browser" not in sys.argv:
        threading.Timer(0.7, webbrowser.open, [f"http://127.0.0.1:{PORT}/"]).start()
    print(f"Kraken panel on http://127.0.0.1:{PORT}/  (Ctrl+C to stop)")
    srv.serve_forever()
