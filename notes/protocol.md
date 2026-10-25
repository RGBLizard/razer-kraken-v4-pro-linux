# Kraken V4 Pro protocol notes

Status: **partially known from Synapse logs; nothing here has been verified against the real device on Linux.**
Source: `synapse-files/logs/products_1383_mw *.log` (Synapse log lines `AudioBillieT1Dongle._getElectronTransferInResultGeneral`).

## Transport
- HID interface 4 (MI_04, vendor-defined, `col01`), reports of **64 bytes** (log shows 64 values per read).
- Synapse writes a command, then reads the response (`queryMethod:write`). Delays between out/in are 30 ms, retries up to 10 (in) / 20 (out).

## Response layout (observed)
```
byte  0-1   02 02            (report/transaction marker, unknown meaning)
byte  2     0x60 (96)        (constant so far)
byte  3-5   00 00 00
byte  6     payload length + 4   (e.g. 5 -> 1 data byte, 13 -> 9... see below)
byte  7-8   00 00
byte  9     0x80 on "Set"/"Get status" replies, 0x00 on serial/firmware replies
byte  10    command id
byte  11    01               (constant so far)
byte  12    data length
byte  13..  data
...
byte 62     checksum? (varies, e.g. 0xcf, 0xdd, 0xbb, 0xe4)  <- verify
```
Log's parsed header = bytes 0-12. Example (Get Battery Status reply):
`02 02 60 00 00 00 05 00 00 80 21 01 01 19 ...` -> command 0x21, 1 data byte 0x19 = 25 (%).

The **request** layout is not logged. It is probably the same header with a request flag; confirm with a USBPcap capture.

## Commands seen (command tuple in log = [id, ?, ?])
| ID (dec / hex) | Name from Synapse | Notes |
|---|---|---|
| 0 / 0x00 | Get Serial Number | 15 ASCII bytes |
| 1 / 0x01 | Get Edition ID | 1 byte |
| 2 / 0x02 | Get Headset Firmware Version | 8 data bytes |
| 33 / 0x21 | Get Battery Status | 1 byte, percent |
| 42 / 0x2A | Get charging status | 1 byte, 0 = not charging |
| 140 / 0x8C (args 36,1) | Set Haptics A2H Profile | |
| 147 / 0x93 | Set Preset EQ Index | 1 byte |
| 149 / 0x95 | Set Customer EQ Band | 10 bytes |
| 150 / 0x96 | Set Mic Preset EQ Index | 1 byte |
| 152 / 0x98 | Set Sidetone status | 1 byte |
| 158 / 0x9E | Set Speaker Preset EQ Status | 1 byte |
| 172 / 0xAC | Set Auto power off Status | |
| 193 / 0xC1 | Set Chroma Brightness | 1 byte |
| 194 / 0xC2 | Set Synapse Driven Chroma Effect | |
| 195 / 0xC3 | Set Chroma Brightness State | |
| 220 / 0xDC | Set Game Chat Balance Value | |
| 222 / 0xDE | Set Mappable Button Status | **knob/button mapping, relevant to the dead volume knob** |

Not yet found in logs: OLED upload, volume/knob events, mic mute, ANC. These need USBPcap captures.

## Next steps to confirm
1. USBPcap capture of one Set command (sidetone on/off) to see the exact request bytes.
2. Compare Get Battery Status request with the reply above to learn the framing and checksum.
3. Capture the knob with Synapse open and closed; look at MI_03 (consumer control) and `Set Mappable Button Status` (0xDE).

---
# CONFIRMED FROM USB CAPTURE (captures/haptics-lighting-sound.pcap, 2026-10-23)
Device: VID 0x1532 PID 0x0568 (Kraken V4 Pro hub), capture taken on USBPcap1, wireless headset via hub.

## Transport (confirmed)
- **Commands**: HID class control transfer on the default endpoint: bmRequestType 0x21, bRequest 0x09 (SET_REPORT), wValue 0x0202 (Output report, Report ID 2), wIndex 4 (interface 4), wLength 64.
- **Replies**: interrupt IN endpoint **0x84**, 64-byte reports (Report ID 2), arriving ~35-45 ms after each command.
- One reply per command. One extra reply (battery) arrived with no command from the host (see below).

## Request layout (confirmed for 0x93, 0x95, 0xC1, 0xC3; differs for 0x8C)
```
[0]  02          report ID
[1]  00          (reply has 02 here)
[2]  60
[3-5] 00 00 00
[6]  data_len + 4
[7-8] 00 00
[9]  80
[10] command id
[11] 00
[12] data_len
[13..] data, zero padded to 64 bytes
```
Reply: same header but `[1]=02`, `[11]=01` (status/type), `[12]` = length, data after.
Haptics command (0x8C) uses a different header: `[6]=0x25 [7]=0x16 [8]=0x5F [10]=0x8C [11]=0x24 [12]=profile`. Its reply is `[11]=01 [12]=0x24`.
The checksum guess in the earlier section (byte 62) is NOT confirmed. The sent reports have zeros in the tail.

## Commands seen (time = seconds into capture)
| t | Command | Request bytes (from byte 6) | Meaning | Reply |
|---|---|---|---|---|
| 6.40 | 0x93 Set Preset EQ Index | `05 0000 80 93 00 01 07` | EQ preset 7 | ok |
| 6.45 | 0x95 Set Customer EQ Band | `0e 0000 80 95 00 0a fc ff 01 02 03 03 03 03 03 02` | 10 signed band gains | ok |
| 15.8 | 0x93 | `... 93 00 01 09` | EQ preset 9 | ok |
| 15.86 | 0x95 | `... 95 00 0a 05 05 00 fe fe 01 04 05 04 02` | bands for preset 9 | ok |
| 24.8 | 0x93 | `... 93 00 01 08` | EQ preset 8 | ok |
| 24.83 | 0x95 | `... 95 00 0a 04 03 02 01 01 01 02 02 01` | bands for preset 8 | ok |
| 33.1 / 39.3 / 45.7 | 0x8C Set Haptics A2H Profile | `25 16 5f 80 8c 24 <01/02/03>` | haptics profile 1, 2, 3 | ok |
| 56.20 | 0xC3 Set Chroma Brightness State | `05 0000 80 c3 00 01 00` | state 0 | ok |
| 56.20 | 0xC1 Set Chroma Brightness | `05 0000 80 c1 00 01 01` | value 0x01 | ok |
| 64.64 | 0xC3 | `... c3 00 01 01` | state 1 | ok |
| 64.68 | 0xC1 | `... c1 00 01 ff` | value 0xFF | ok |
Brightness: user set 0% then 100%, which matches (state 0, value 1) then (state 1, value 255). Hypothesis, not fully verified. Preset numbers 7/9/8 are EQ preset slots; which Synapse name maps to which number is still unknown. Haptics 1/2/3 probably = Controlled/Balanced/Dynamic (UI order), unverified.

## Unsolicited reply: battery
At t=79.57 the device sent `02 02 60 00 00 00 05 00 00 80 21 02 01 5f` with no matching command: command 0x21 (battery), `[11]=02` (probably "notification" instead of "response"), data 0x5F = 95%, which matches Synapse showing 96%. So the hub pushes battery level events by itself.

## Audio stream to hub (NOT a command)
1,645 SET_REPORTs of the form `02 00 60 00 00 00 21 0f 03 80 00 00 00 00 08 <audio-level-like bytes>` were sent at ~16 per second for the whole capture. This looks like a live audio-level/haptics visualization feed from Synapse to the hub. It is probably only needed for haptics/OLED visualizer, not for normal control. Do not confuse with commands.

## Still unknown
- Request bytes for Sidetone (0x98), Auto power-off (0xAC), balance (0xDC), mappable buttons (0xDE), OLED upload, ANC, mic.
- How the volume knob reports (interface 3 consumer control was not exercised in this capture).
- Whether an init/handshake is needed (this capture started after Synapse was already running; `--inject-descriptors` only adds descriptors).

---
# VOLUME KNOB (captures/knob-synapse-closed.pcap, Synapse fully closed, 2026-10-23)
Result: **the knob works on Windows without Synapse**, so it needs no vendor init command.

## Knob -> host (standard HID, no vendor protocol)
- Interrupt IN endpoint **0x82** (interface 3, HID consumer control), 2-byte reports: Report ID `0x0c` + 1 byte bitmask.
  - `0c 01` = Volume **Up**, `0c 02` = Volume **Down**, `0c 04` = **Mute** (knob press), `0c 00` = release.
  - Each click sends press+release pairs; a fast turn sends many.
- The OS volume-key handler reacts to these. Linux should too (hid-generic -> input event device).

## Host -> hub: volume is a USB Audio Class Feature Unit (hardware volume)
- Windows reacts to the knob by writing the hub's UAC Feature Unit: bmRequestType 0x21, bRequest 0x01 (SET_CUR), wValue 0x0201 (VOLUME_CONTROL, channel 1), wIndex 0x0805 (entity/Feature Unit 8, interface 5 = the "Game" audio interface), wLength 2, data = signed 16-bit LE in 1/256 dB.
- Observed range in this capture: about -54.6 dB up to -0.6 dB (max about 0 dB). End of capture was -7.2 dB.
- Mute: SET_CUR with 1 byte (01 = muted, 00 = unmuted), seen when the knob was pressed.
- => The headset **stores its volume in the device's USB audio volume control**, which explains "quiet on Linux after Windows": Windows left the hardware volume low, and Linux/PipeWire sits on top of the same control.
- Not yet checked: Chat interface (interface 0) has its own Feature Unit.

## Hypotheses to test on Linux (no vendor protocol needed)
1. `sudo libinput debug-events` / `evtest` shows XF86AudioRaiseVolume/LowerVolume/Mute when the knob turns. If YES, the desktop is the problem (wrong default sink: knob changes Game sink but you listen on another, or the hardware volume is at its minimum).
2. `amixer -c <card> contents` shows the UAC Playback Volume; set it to max with `alsamixer`.
3. If NO events: check `lsusb -t` that interface 3 is bound to usbhid, and `dmesg` for HID errors.
