# Status (written 2026-10-25)

## Working and verified on the hardware
- Kraken V4 Pro: EQ + custom curve, balance, hardware volume sync (knob/slider/mute), virtual 7.1 surround (HRTF) + THX-like chain,
  haptics (profiles, intensity, custom sliders), mic mute / sidetone / mic EQ, lighting (brightness, 5 effects, 4 audio-reactive),
  OLED brightness + own picture, hub knob menu -> EQ, saved lighting presets, full profiles + restore at login.
- Naga V2 Pro: battery, DPI, polling rate, sleep time, logo + thumb-panel effects and brightness.
- DeathStalker V2 Pro: battery, backlight effects + brightness, per-key painter (matrix shifted -1 column), keyboard presets.

## Open items
1. Dropped (2026-10-25): hub display volume % and OLED System Info screen. The Windows capture came back empty, so no more work on them (sysinfo.py stays unused).
2. Discord crackle when someone starts talking (heard once): not reproduced. Ruled out xruns, device suspend, realtime priority.
   If it returns, A/B in this order: THX bypass only -> surround off -> default sink straight to Kraken pro-output-1 (no EasyEffects).
3. Built 2026-10-25, waiting for hardware confirmation: animated per-key effects (keyfx.py, Keyboard tab), Naga side-button remap + macros (remap.py, Buttons tab).

## Animated effects, remap, macros (2026-10-25)
- keyfx.py: wave, fire, ripple, reactive (both follow key presses via evdev), audio bars. Sent as per-key frames (~8 fps). Saved in profiles as `kfx`.
- remap.py: the 12 Naga side buttons arrive as KEY_1..KEY_EQUAL on the dongle's `-event-kbd` node (separate from the DeathStalker, which is `-if01-event-mouse`).
  The panel grabs that node only while a button is bound, re-emits everything else through /dev/uinput. Config: ~/.config/kraken-control/remap.json.
  Needs read access to /dev/input/by-id/usb-Razer_DSV2_Pro_* and /dev/uinput (both fine on this Bazzite via ACL; another distro may need a udev rule).
- Macros are text: `ctrl+c 200 alt+tab`, `+shift`/`-shift` hold/release; modes once / repeat while held / toggle. Recorder reads the keyboard node read-only.
- Fixed: `time` was not imported in kraken_gui.py (battery refresh thread died after the first read); profile load/restore now applies mouse + keyboard settings too.

## Resume
Panel: "Kraken Control" in the app menu (http://127.0.0.1:8799). Code: ~/kraken-linux (this folder's app/ is the same code). `./install.sh` sets up a fresh machine.
Settings: ~/.config/kraken-control/profiles.json. Everything autostarts at login (thx.py, knobsync.py, kraken_gui.py --restore, filter-chain service).
EasyEffects settings that must stay: "Process all output streams" OFF, its own autostart OFF, output device = Kraken Game output (pro-output-1).

## App + tray (2026-10-25)
`app/kraken_app.py` = native window (PySide6 WebEngine, system python) around the panel; tray icon, close hides to tray, `--tray` autostart (~/.config/autostart/kraken-app.desktop). Settings are saved 2 s after every change and restored at login (mouse/keyboard retried while asleep).

## USB cable behaviour (tested 2026-10-25)
- Naga on cable: separate device (1532:00a7), panel + remap follow it automatically; back to dongle on unplug.
- DeathStalker on cable: only charges, stays on the dongle.
- Headphones on cable: wired USB headset (new sound output); the dock cannot reach them, the panel says "not linked", auto-save ignores the unreadable headphone settings, and saved EQ/haptics/mic settings are re-applied when the wireless link returns.

## Tidy sound menu (2026-10-25)
KDE's volume menu only lists devices with the HARDWARE flag, i.e. `node.virtual` not true. `config/52-razer-clean.conf` (WirePlumber, ~/.config/wireplumber/wireplumber.conf.d/) marks HDMI and motherboard USB audio as virtual (hidden, still usable). The Kraken's raw outputs are only renamed: hiding them removes the left-channel port (right ear only); the surround chain is `node.virtual=false` and named "Razer Kraken V4 Pro" so it is the one visible, selected output. Mics shown: "Razer Kraken V4 Pro Mic" (+ "(cable)" when plugged) and "USB Microphone". Delete the file and restart wireplumber to show everything again.
