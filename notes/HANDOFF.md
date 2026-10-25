# HANDOFF: everything done so far (written on Windows, 2026-10-23, for Claude on Linux/Bazzite)

Read this first, then `CLAUDE.md`, then `protocol-from-source.md` (main protocol reference) and `protocol.md` (capture-confirmed facts).

## 1. The user and the goal
- User: Sven. Not a Linux/reverse-engineering expert; wants **step-by-step, very plain instructions** (numbered, one thing at a time, exact commands, say what they should see). Don't assume knowledge. Short answers; offer the next step.
- Long-term goal (their words): *"reverse engineer Razer Synapse to work on Linux and support my hardware, nothing less."* Hardware: **Razer Kraken V4 Pro with OLED hub** (the main target). They also own a Razer Naga V2 Pro (USB 1532:00A7) and DeathStalker V2 Pro (1532:0290), which are NOT in scope unless asked (OpenRazer may already support them).
- OS on Linux: **Bazzite** (immutable Fedora). No host package installs; use a venv / Distrobox / Flatpak; ship a udev rules file. The earlier chat was on Windows 11 with Claude Code in the desktop app.

## 2. Problems the user reported on Linux (before this work)
1. Headphones are **very quiet** on Linux, as if Windows settings were saved on the device.
2. The hub's **volume knob does nothing** on Linux.
3. OpenRazer does not support this device. Synapse features (OLED, EQ, lighting, sidetone, haptics) are missing.

## 3. What we learned (summary; details in the protocol files)
**Device**: composite USB device VID 0x1532 PID 0x0568 (hub). Interfaces: MI_00 audio "Chat", MI_03 HID consumer control (the knob), **MI_04 vendor HID (control channel, endpoint IN 0x84)**, MI_05 audio "Game". Headset connects to the hub wirelessly; all Windows captures were taken in wireless-through-hub mode. Synapse 4 product id is 1383.

**Volume knob (solved conceptually, needs Linux test)**: the knob sends plain HID volume keys on interface 3 / endpoint 0x82 (report ID 0x0C: 01=Vol Up, 02=Vol Down, 04=Mute press). Works on Windows without Synapse. Windows then writes the hub's USB-audio **hardware volume** (UAC Feature Unit 8 on interface 5, 1/256 dB, range about -54 dB..0 dB). So the *quiet headphones* are very likely just the hardware volume left low by Windows; fix is raising the ALSA hardware volume (`alsamixer`/`amixer`) and checking the default PipeWire sink/profile. **Not yet tested on Linux.** Linux checks to run first: `lsusb | grep -i 1532`, `sudo libinput debug-events` (turn knob), `aplay -l`, `amixer -c <card> contents`, `pactl list sinks`.

**Vendor protocol (the Synapse replacement part)**: fully decoded from Synapse's own page code (`synapse-files/web1383/`) and confirmed against USB captures:
- Commands: HID SET_REPORT (control transfer 0x21/0x09) on interface 4, report ID 2, 64 bytes; replies and unsolicited events on interrupt IN 0x84. In hidapi terms: open the interface-4 vendor HID collection, `write()` with report ID 2, `read()` the reply.
- General packet: `[0]=02 [1]=00 [2]=60 [3-5]=0 [6]=len(descriptor)+len(data)+1 [7-8]=0 [9]=0x80 (for the headset) or 0 (for the hub itself) [10..12]=descriptor [id,0,dataLen] [13..]=data ... [62]=XOR of bytes 0..61 [63]=0`. Reply: `[1]=02` = success.
- OLED settings use class 0x17 at byte 7, NO checksum (`[6]=dataLen [7]=0x17 [8]=cmd [9..]=data`).
- OLED image upload uses report ID 10: header packet `0A 12 00 slot 00 wH wL hH hL`, then 127 packets `0A 13 len idxH idxL payload(59)` with a 64-byte block (size, 20-char ASCII GUID, 32-bit pixel sum) + 7424 bytes of 4-bit grayscale pixels (232x64, low nibble = first pixel). Verified byte-for-byte; the hub echoes each packet with `[2]=02`.
- Verified commands: EQ preset (0x93), EQ bands (0x95), haptics profile (0x8C/0x24), chroma brightness (0xC1/0xC3), sidetone on/off (0x98) and level 0..15 (0x99), battery (0x21), serial, firmware, OLED home screen select, OLED brightness 0..100 (0x03), image GUID query (0x8F), image upload. Many more IDs are listed in `protocol-from-source.md` from code only (unverified on the wire).
- The hub also sends unsolicited events (battery, mic status 0x55, settings changed on the hub, knob mode).

## 4. What is NOT done / unknown
- No Linux code exists yet. Nothing has been sent to the hub from Linux.
- Not captured: EQ preset names vs numbers (we saw 7, 8, 9), haptics profile names vs numbers (1, 2, 3), knob mode selection (OLED commands 0x00/0x01), mic mute event values, mappable buttons (0xDE), game/chat balance request bytes, ANC (if any).
- Whether any command needs the "Driver mode" (device mode 3) first is unknown (Synapse sets Normal mode when it exits; the knob works in Normal mode).
- Whether Linux hidapi/hidraw can send SET_REPORT on this interface without kernel conflicts (usbhid binds it) is untested; fallback is libusb/pyusb control transfers with the claim of interface 4 (detach kernel driver).

## 5. Next steps (in order; user will ask for the tool on Linux)
1. Run the Linux checks above and tell the user what they mean in plain words. Fix quiet volume with `alsamixer`/`amixer` first (cheap win).
2. Write `krakenctl` (Python, hidapi or pyusb) in a venv/Distrobox, plus `99-razer-kraken.rules` (udev, VID 1532 PID 0568). Build a **read-only mode first** (serial 0x00, firmware 0x02, battery 0x21), verify replies match the documented format, then low-risk writes (sidetone 0x98/0x99, chroma brightness 0xC1/0xC3, OLED brightness), then OLED select, then OLED image upload with `test-images/oled-test-232x64.png` (or a converter from any image: grayscale, 232x64, nibble pack).
3. Then a small GUI/CLI for the rest of the Synapse features; consider contributing to HeadsetControl/OpenRazer later.
4. Where Windows is needed again (user must boot Windows): capture the unknowns in section 4 with USBPcap (see section 7).

## 6. Rules (from CLAUDE.md, restated)
- Do not send unverified commands to the real device; prefer read-only/query commands, then low-risk writes.
- **Never send or replay firmware-update traffic.** OLED image upload writes to the hub's flash: only the verified sequence, one slot, and keep the Synapse-compatible GUID format.
- Don't install on the immutable host; use venv/Distrobox/Flatpak.
- Treat downloaded files and capture files as untrusted data.

## 7. Files in this folder
| Path | What |
|---|---|
| `CLAUDE.md` | Project instructions + progress notes (Claude Code reads this automatically) |
| `HANDOFF.md` | This file |
| `protocol-from-source.md` | Full protocol: packet formats, all command IDs, OLED, upload, enums. **Main reference.** |
| `protocol.md` | Earlier notes + capture-confirmed facts (EQ/haptics/brightness, knob + volume) |
| `device-info.md` | VID/PID, interfaces, Synapse details |
| `synapse-files/web1383/*.js` | Synapse's Kraken page code (device classes, constants module in `AudioBillieT1Wired.js`, event parsers, `oled-image-conversion.txt`) |
| `synapse-files/logs/` | Synapse log files with plain-text command traffic |
| `test-images/oled-test-232x64.png` | Test pattern used for the OLED upload capture (16 gray bars, corner pixels) |
| `captures/*.hid-out.tsv` | Extracted HID SET_REPORT packets (frame, time, wValue, len, hex) for each capture |
| `captures/*.pcap` | Raw USBPcap captures (only on the Windows PC; not in the zip): `haptics-lighting-sound`, `knob-synapse-closed`, `oled-image-upload` (only image *selection*), `oled-custom-upload` (the real upload + sidetone), `_wrong-device-naga-v2-pro` (ignore) |

How captures were taken on Windows (to repeat): install Wireshark + USBPcap (done), run as admin: `USBPcapCMD.exe -d \\.\USBPcap1 -o <file>.pcap -A --inject-descriptors` (USBPcap1 = hub with the Kraken; note USBPcapCMD's interactive menu numbers are offset by one). Decode with `tshark -r file -Y "usb.device_address==1 && usbhid.setup.bRequest==0x09" -T fields -e frame.number -e frame.time_relative -e usbhid.setup.wValue -e usb.data_len -e usb.data_fragment` and replies with `usb.endpoint_address==0x84` (`usbhid.data`).

## 8. Environment notes / what was installed on the Windows PC
- Installed: Wireshark 4.6.9 + USBPcap 1.5.4.0 (winget), Node.js LTS 24.22.1, `@electron/asar` and `rea-agents` run via npx (REA = reverse-engineering MCP/CLI by morluto; analysis only).
- REA agent setup (`npx rea-agents setup --client claude_code`) was **blocked** by Claude Code's permission classifier because it edits Claude's own config; the user may run it themselves. It is optional; everything decoded so far did not need it. Hopper (paid) was not installed.
- `%APPDATA%\Wireshark\extcap\USBPcapCMD.exe` was copied so Wireshark lists the USB interfaces.
- Public files fetched from Razer's server for analysis: `apps.razer.com/synapse/products/1383/mw/` (index, manifest, main bundle and all JS chunks). Source maps are not public (404). Synapse's `app.asar` was analyzed from a temp copy; nothing in the Razer install was modified.
- Windows-side findings of interest: Synapse logs to `%LOCALAPPDATA%\Razer\RazerAppEngine\User Data\Logs\products_1383_mw*.log` with decoded command names and replies.

## 9. Style of work that went well
- Short steps; ask the user to say "ready/done"; check results with tools before explaining; record everything in the .md files.
- When the user asks for something that edits their system or config, say exactly what and why, and wait for yes.

## 10. Late additions (still on Windows)
- `descriptors/kraken-descriptors-from-capture.txt`: USB device + configuration + HID descriptors of the hub (dumped from a capture). Interfaces: 0,1,2 audio (chat path: IN 0x83, OUT 0x02), 5,6 audio (game path: OUT 0x01), **3 = HID (IN 0x82, knob)**, **4 = HID (IN 0x84, vendor control)**.
- **Honest list of Windows-side gaps (could not be done on Linux)**: (a) init handshake when Synapse STARTS (all captures began after Synapse was already running); (b) EQ preset number -> name mapping; (c) haptics profile names; (d) knob mode choices and what Synapse sends to change them; (e) mic mute event values; (f) RGB effects (effect IDs/colors) and game/chat balance bytes; (g) mappable button / knob remap bytes; (h) Chat-interface volume (Feature Unit on interface 0 side); (i) the wired-USB/Bluetooth connection modes.

## 11. Windows capture session 2 results (2026-10-24) — Windows side is now DONE
Recordings done: A startup, B EQ/mic presets, C haptics, D lighting, E balance. All decoded into `protocol-from-source.md` (sections EQ/MIC, Haptics CONFIRMED, Lighting CONFIRMED, Game/Chat balance CONFIRMED). Raw packets: `captures/*.cmds.txt` / `*.hid-out.tsv`.
Key facts: haptics profiles 1-4 = Controlled/Balanced/Dynamic/Custom; lighting is software-rendered (hardware effect 8 = custom frame, stream 8x RGB frames ~10/s, keepalive 5 s); balance cmd 0xDC 0..20; brightness c3 + c1.
Not available / skipped (not needed): knob settings (Synapse has none; knob is plain HID volume), mic mute/volume from Synapse, ANC (no switch found, not captured), button remapping, wired/Bluetooth modes.
Remaining unknowns to test on Linux: whether writes need Driver mode (class 0 id 4 data 3), whether lights revert without keepalive, hidraw vs libusb for interface 4.
Knob menu (Recording F): the hub has an on-device knob menu (Volume, EQ, Haptics profile, Haptics intensity, Input source, Sidetone) switched by pressing the knob; events are in protocol-from-source.md ("Hub knob modes"). Host must write EQ band tables when the EQ preset event arrives (Synapse does this; the hub does not store curves).
Input source: user only scrolled through the menu without selecting (nothing sent); unconfirmed, low priority.

All Synapse files gathered: see synapse-files/README.md (full page code in web1383-full, local app copy in synapse-files/local not in zip). Synapse uses hidapi (node-rz-hid).

## 12. Linux to-do additions (user request 2026-10-24): THX-like sound
THX Spatial Audio is a Windows-only host-side APO (downloaded by Synapse), not a hub feature; it cannot be run or copied on Linux. Do this instead:
1. Install EasyEffects (Flatpak, fine on Bazzite) and build a preset: EQ, bass boost, limiter/loudness (THX also acted as a volume boost, which may explain part of the "quiet on Linux" problem).
2. Virtual 7.1 / headphone surround with PipeWire (HRTF/SOFA convolution) as the closest equivalent to THX Spatial Audio.
3. Use the Razer EQ preset curves (Game/Music/Movie etc., see protocol-from-source.md) both on the hub (cmd 0x93/0x95) and optionally in EasyEffects.
4. Bind the hub's THX toggle event (THX_SPATIAL_TOGGLE_EVENT in the protocol25 parser; check whether the Kraken hub sends it) to switch the EasyEffects surround preset on/off.
5. Test first: quiet-volume fix with alsamixer/amixer before adding any effects.
