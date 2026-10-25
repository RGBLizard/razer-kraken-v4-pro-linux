# Razer Kraken V4 Pro (OLED hub, USB 1532:0568) protocol

Status legend: **[HW]** verified on real hardware from Linux, **[CAP]** matched against a Windows USBPcap capture of Synapse 4, **[SRC]** only read from Synapse's own UI code, untested.
The reference implementation of everything marked [HW] is `reference/krakenctl.py` (about 340 lines, Python stdlib only).

## 1. Device layout
The "hub" (dock with OLED + knob) is a composite USB device that is always plugged into the PC. The headphones talk to it over 2.4 GHz. When the headphones are put on their own USB cable they show up separately as **1532:0567** (see section 9).

| USB interface | Function |
|---|---|
| 0 | USB Audio "Razer Kraken V4 Pro - Chat" (stereo, own Feature Unit) |
| 3 | HID consumer control: the volume knob (37-byte report descriptor) |
| 4 | **HID vendor control channel** (453-byte report descriptor, Report IDs 2 and 10, 64-byte reports) |
| 5 | USB Audio "Razer Kraken V4 Pro - Game" (stereo) |

Descriptors: `descriptors/`. The control channel is `/dev/hidrawN` for interface 4 (match `HID_ID=0003:00001532:00000568` and `/input4` in the sysfs `uevent`). No udev rule was needed on Bazzite/Fedora (hidraw was world-writable), other distros will need one.

**This is NOT the protocol of the older Kraken (7.1 V2 / "Kylie", RAM-address writes over control transfers) that OpenRazer's `razerkraken` driver speaks.** Forks that add "Kraken V4 Pro" by reusing that code path do not work on this device.

## 2. Transport [HW]
- Host -> hub: 64-byte HID output report written to hidraw (`write()`), which the kernel sends as SET_REPORT on the control endpoint. The hub has no interrupt OUT endpoint.
- Hub -> host: interrupt IN 0x84, 64-byte reports. One reply per command, 5-45 ms later, plus unsolicited events (battery, knob, etc.).
- Report ID **2** = commands/replies/events. Report ID **10 (0x0A)** = OLED image upload.
- No handshake, auth or unlock. Reads and writes work in the hub's normal mode.
- Pacing: wait for the reply before sending the next command (Synapse uses >= 10 ms between commands). Reply timeout used here: 1 s.

## 3. General command packet [CAP + HW]
```
[0]   02            report ID
[1]   00            (reply: status, 02 = ok)
[2]   60            constant
[3-5] 00 00 00
[6]   len(data) + 4
[7-8] 00 00
[9]   80 = command is for the HEADSET (through the dock), 00 = for the dock itself (or the headphones on cable, see 9)
[10]  command id
[11]  00            (reply: 01)
[12]  data length
[13..] data
[62]  XOR of bytes 0..61
[63]  00
```
Reply: `[1]=02` success (01/03 = busy/retry, anything else = error), `[10]` = command id, `[11]=01`, `[12]` = length, data from `[13]`. Match replies on `[0]==2 && [10]==id && [11]==1`.
Set commands have bit 0x80 set; the matching Get is the same id without it (e.g. sidetone level set 0x99 / get 0x19). Set replies carry one data byte, 00 = accepted.

## 4. Command table
| Function | Get | Set | Data |
|---|---|---|---|
| Serial | 0x00 | | 15 ASCII [HW] |
| Edition id | 0x01 | | 1 |
| Firmware | 0x02 | | 24 bytes = 6 x (a.b.c.d) [HW] |
| ANC on/off | 0x12 | 0x92 | 1 [SRC, never exercised] |
| Speaker EQ preset | 0x13 | 0x93 | 1: 0 default, 7 game, 8 music, 9 movie, 250-254 game-specific, 255 custom [HW] |
| Speaker EQ bands | 0x15 | 0x95 | 10 signed bytes, 31 63 125 250 500 1k 2k 4k 8k 16k Hz, dB (-12..12 used) [HW] |
| Mic EQ preset | 0x16 | 0x96 | 0 default, 1 micboost, 2 broadcast, 3 conference, 255 custom [HW] |
| Mic EQ bands | 0x17 | 0x97 | 10 [SRC] |
| Sidetone on/off | 0x18 | 0x98 | 1 [HW] |
| Sidetone level | 0x19 | 0x99 | 0..15 [HW] |
| Speaker EQ status | 0x1E | 0x9E | 1 |
| Wireless link status | 0x20 | | 1 (dock command, `[9]=00`) |
| Battery | 0x21 | | 1, percent [HW]; also pushed unsolicited |
| Charging | 0x2A | | 1, 0 = not charging |
| Auto power off | 0x2C | 0xAC | `enabled, minutes` |
| Chroma effect | 0x40 | 0xC0 | 1 (hardware effect; 8 = custom frame) |
| Chroma brightness | 0x41 | 0xC1 | 0..255 [HW] |
| Chroma brightness on/off | 0x43 | 0xC3 | 1 [HW]; 0 % = off + value 1 |
| UI-selected effect | | 0xC2 | 1 (bookkeeping Synapse sends; static 1, breathing 2, spectrum 3, wheel 0x0C, ambient 0x30, audio meter 0x31) |
| Game/Chat balance | 0x5C | 0xDC | 0..20, 0 chat, 10 middle, 20 game [HW] |
| Mappable buttons | 0x5E | 0xDE | 1 (not decoded) |
| Mic mute | | | event 0x55: 00 unmuted, 01 muted. Hub-side state, no host command (but see section 8) |

### Device mode (class-and-id variant) [HW]
`[6]=len(data)+1, [7]=class, [8]=id, [9]=0, data from [10], [62]=XOR`. Get mode = class 0 id 0x84; set = class 0 id 0x04. Values: 0 Normal, 3 Driver, 4 Download, 5 Pairing, 6 Standby, 1 Bootloader. Synapse sets Driver (3) while it runs and Normal (0) on exit; software lighting needs Driver mode (section 6). Do not leave the hub in a mode other than 0 on exit.

## 5. Haptics [HW]
Packet header differs: `[6]=0x25 [7]=0x16 [8]=0x5F [9]=0x80 (0 on cable)`, `[10]=0x8C, [11]=sub, [12..]=data`, checksum is **not** XOR: `[62] = (-sum(bytes 10..61)) & 0xFF`. Reply matched on `[10]==0x8C && [11]==1 && [12]==sub`.

| Function | Get sub | Set sub | Data |
|---|---|---|---|
| Profile | 0xA4 | 0x24 | 1 Controlled, 2 Balanced, 3 Dynamic, 4 Custom |
| Custom frequency | 0xA3 | 0x23 | `01 hi lo`, Hz (40..250 used, range not documented) |
| Custom bass volume | 0x8C | 0x0C | `01 01 hi lo`, 0..100 (range is a guess) |
| Perception volume | 0x89 | 0x09 | constant `01 00 D8 00 43` |
Intensity uses the normal-header style but the haptics framing: set `8A 01 <on> <level 1..6>`, off = `8A 01 00 00`, get `8B` (state at [13], level at [14]).
**Gotcha:** the haptics sliders feel dead until the constant perception-volume command `8C 09 01 00 D8 00 43` is sent after every change. Synapse does this; krakenctl's `haptics_apply` copies it.

## 6. Lighting [CAP + HW]
The hub renders nothing itself. Static/breathing/spectrum/wheel/audio effects are computed on the PC and streamed as frames (~10/s):
1. Get mode (class 0 id 0x84); set UI effect `C2 00 01 <id>`; set mode 3 (Driver); set chroma effect `C0 00 01 08` (custom frame).
2. Frame: class-and-id packet, class 0x0F id 0x03: `02 00 60 00 00 00 21 0f 03 80 | 00 00 00 00 08 | R G B x8` (captures show 8 RGB triples; `reference/lighting.py` `frame()` sends 9 with the last mirroring the first, which the hub accepts).
3. Synapse re-sends a frame about every 5 s while the colour is steady. `reference/lighting.py` has working static, breathing, spectrum, wheel and audio-reactive (pw-record based) effects.
Brightness: `C3 00 01 <on>` then `C1 00 01 <0..255>`.

## 7. OLED (232 x 64, 4-bit grayscale) [HW unless noted]
Settings use class 0x17, **no checksum**: `[6]=len(data) [7]=0x17 [8]=id [9..]=data`; Get = 0x80 | Set id. Reply `[7]==0x17 && [8]==id`, success when `[1]==2` (reply `[6]` is 0 even when data follows).

| Function | Set | Get | Data |
|---|---|---|---|
| Knob active mode | 0x00 | 0x80 | mode (also sent as an event when the knob menu changes) |
| Knob mode enable flags | 0x01 | 0x81 | 9 flags |
| Home screen | 0x02 | 0x82 | `[type, param]`: type 0 animation, 1 image, 2 banner, 3 device info, 4 emote, 5 audio, 6 system info, 7 off |
| OLED brightness | 0x03 | 0x83 | 0..100 [HW] |
| Time to home / dim | 0x04 / 0x06 | | enum |
| System info update | 0x09 | | 40 bytes [SRC] |
| Active item | 0x0D | 0x8D | 1 |
| Image slot enable list | 0x15 | 0x95 | 10 bytes |
| Image GUID | | 0x8F | `[type, idx]` -> 20 bytes |
| Screensaver | 0x16 | 0x96 | `[mode, item]` |

**Image upload (home screen slot) [HW, bit-exact vs. capture]**, report ID 10, every write answered by the hub echoing the packet with `[2]=02`:
1. Header `0A 12 00 <slot> 00 <wH> <wL> <hH> <hL>` (232 x 64 = `00 E8 00 40`); wait ~0.3 s.
2. Body = 64-byte block + pixel bytes. Block: `[0..3]` width, height (BE16 each), `[4..23]` 20 ASCII chars GUID (random hex), `[60..63]` 32-bit big-endian sum of all pixel bytes.
3. Pixels: row-major from top-left, 4 bits each = `floor(luma/16)`, luma = 0.2126R+0.7152G+0.0722B; **first pixel in the low nibble**. 7424 bytes per frame.
4. Send the body as `0A 13 <len<=59> <idxH> <idxL> <payload>` packets, waiting for the ack of each (127 packets for one image).
5. Select it: OLED set 0x02 `01 <slot>`, set 0x0D `01`.
Animations use ids 0x04/0x05 (header/data) with a 512-byte first block (see Synapse capture `oled-custom-upload`, not included).

**Unsolved:** non-image home screens (system info, device info, ...) never replace the uploaded image when selected from Linux (hub acks, display does not change); the OLED volume % popup also never showed up. A Windows capture of selecting them in Synapse captured nothing useful. If someone has a working capture, those are the two open protocol questions.

## 8. Knob and volume [HW]
- Knob = standard HID consumer control on interface 3 (`0c 01` vol up, `0c 02` vol down, `0c 04` mute, `0c 00` release). Works with any HID driver, no vendor command.
- Knob menu (press = next mode) is hub state: mode events `02 02 60 00 00 00 01 17 80 <mode>` (0x00 volume, 0x10 EQ, 0x11 haptic intensity, 0x12 input source, 0x13 sidetone, 0x14 balance, 0x15 chroma brightness, 0x16 OLED brightness, 0x17 haptic profile). Value events while turning: EQ `13 02 01 <preset>`, haptics profile `8C 01 A4 <n>`, intensity `8B 01 01 01 <n>`, sidetone `19 02 01 <n>`.
- **EQ curves are not stored in the hub**: when the knob changes EQ preset the host must write the matching 10 bands (0x95). Tables used (preset id: bands): game 7 `-4 -1 1 2 3 3 3 3 3 2`, music 8 `4 3 2 1 1 1 2 2 1 0`, movie 9 `5 5 0 -2 -2 1 4 5 4 2`, default 0 all zero.
- Volume is the **USB Audio Class Feature Unit** (ALSA `PCM Playback Volume`, two PCM controls = Game and Chat interfaces, range 0..18944 = -74..0 dB). Left low by Windows it makes the headset very quiet on Linux; raise it with `amixer`.
- **Mic**: ALSA `Mic Capture Switch` off made the hub reject sidetone writes (data 0xFF). Turn the mic capture switch on first.

## 9. Headphones on their own USB cable (1532:0567) [HW]
Shows up as its own USB audio device and a HID interface (iface 5) speaking the **same protocol** with `[9]=0x00` instead of 0x80 and no dock in between. While the headphones are cabled, the dock loses the wireless link and answers 255/no reply to every headset command. When the wireless link returns, headphone-side settings can be lost: re-apply them.

## 10. Not found / not done
ANC command (0x92) never exercised; mic mute host command doesn't exist (event only); OLED non-image screens; exact ranges of custom haptics frequency/volume.
