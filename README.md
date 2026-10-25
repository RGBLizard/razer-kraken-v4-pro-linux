# Razer Kraken V4 Pro (1532:0568) on Linux: protocol notes + working reference tool

Everything needed to add Linux/OpenRazer support for the **Razer Kraken V4 Pro** (OLED hub + wireless headphones), gathered by capturing Synapse 4 on Windows and verifying every command on real hardware from Linux (Bazzite / Fedora, kernel 7.x, PipeWire).

## Why this exists
The Kraken V4 Pro has no Linux support. Some forks claim it, but only by reusing the old Kraken 7.1 V2 protocol (RAM-address writes over control transfers), which this device does not speak. The V4 Pro uses a **vendor HID protocol on USB interface 4** (64-byte reports, Report ID 2, plus ID 10 for OLED images).

## What's here
| Path | What |
|---|---|
| `docs/PROTOCOL.md` | Full protocol: framing, checksums, every command, lighting, OLED upload, knob events, cable mode, gotchas. Each item tagged hardware-verified / capture-verified / source-only |
| `reference/krakenctl.py` | CLI, ~340 lines, stdlib only, talks to hidraw. Reads serial/firmware/battery, EQ, balance, haptics (profiles, intensity, custom), sidetone, mic EQ, brightness, OLED brightness/image upload, device mode. A readable executable spec |
| `reference/lighting.py` | Software lighting engine (static, breathing, spectrum, wheel, audio-reactive) streaming RGB frames in Driver mode |
| `descriptors/` | HID report descriptors for interfaces 3 and 4 (hex) and `lsusb -v` |
| `captures/` | Decoded command lists (haptics, lighting, balance, knob) plus the full decoded HID-out traces (`*.tsv`) of the Synapse sessions and the raw USBPcap captures (`*.pcap.xz`: knob with Synapse closed, custom OLED image upload; `xz -d`, open in Wireshark) |
| `windows-capture/capture.ps1` | The script used to record the captures on Windows (USBPcapCMD) |
| `notes/` | Raw working notes: earlier protocol write-ups, device info, handoff, current status |
| `linux-app/` | The full Linux control panel we built on top (web UI + Qt tray app, EQ/haptics/lighting/surround/volume sync, plus Naga/DeathStalker helpers), for reference |

Quick test (reads only, then a harmless write):
```
python3 -I reference/krakenctl.py            # serial, firmware, battery
python3 -I reference/krakenctl.py balance 10 # centre game/chat balance
```
The tool locates `hidraw` for interface 4 by itself. Make sure your user can write to it (udev rule `SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1532", ATTRS{idProduct}=="0568", TAG+="uaccess"` if needed).

## Verified on hardware
Serial/firmware/battery, EQ presets and custom bands, mic EQ presets, game/chat balance, haptics (all profiles, intensity, custom frequency/volume), sidetone on/off/level, chroma brightness, software lighting effects, OLED brightness, OLED image upload (232x64), device mode, the wired (cable) variant 1532:0567, knob events.

## Open
- OLED non-image home screens (system info etc.) and the volume % popup: not reproducible from Linux, no usable capture.
- ANC command (0x92) never exercised. Custom haptics value ranges are guesses.
- Only one unit tested (firmware 1.6.3.0).

## Integrating into OpenRazer
This is a new protocol, not a PID addition to `razerkraken`. A natural shape is a new driver/daemon backend (hidraw or `hid-generic`-compatible, interface 4) that implements the table in `docs/PROTOCOL.md` section 4, with the PC-side lighting engine in the daemon. I'd suggest opening an issue on the OpenRazer repo first to agree on the layout before a PR. Happy to test patches on this hardware.

## Notes
- These notes document the wire protocol for interoperability. No Razer software or Synapse code is included (only logs and traffic captures). The original Synapse UI scripts the command names came from were left out because they are Razer's code; every decoded result is in `docs/PROTOCOL.md`.
- Command ids and enums partly come from reading Synapse's downloaded UI code and were then confirmed on hardware; items tagged [SRC] in the protocol doc are unconfirmed.
