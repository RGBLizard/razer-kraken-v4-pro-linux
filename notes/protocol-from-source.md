# Kraken V4 Pro (hub, VID 0x1532 PID 0x0568): protocol decoded from Synapse's own code

Source: the Kraken page code Synapse 4 loads (`apps.razer.com/synapse/products/1383/mw/`, app "project_anne_billiet1").
Files (copies in `synapse-files/web1383/`): `AudioBillieT1Dongle.js` (hub class, OLED), `AudioBillieT1Wired.js` (base class + ALL command constants, module 60391), `rzHardwareEventsAudioBillieT1Parser.js` (hub -> PC events), `rzHardwareEventsProtocol25OledParser.js`, `oled-image-conversion.txt`.
Status legend: **[CAP]** = confirmed against a USB capture, **[SRC]** = read from Synapse code only, not yet tested on a device.

## 1. Transport [CAP]
- HID interface **4** (vendor-defined). Host -> hub: HID **SET_REPORT** (control transfer, bmRequestType 0x21, bRequest 0x09, wValue 0x0200|reportID, wIndex 4). Synapse calls this `hid.write`; the hub has no interrupt OUT endpoint, so hidapi/hidraw `write()` also ends up as SET_REPORT.
- Hub -> host: interrupt IN endpoint **0x84**, 64-byte reports. Used for command replies AND unsolicited events.
- Report IDs: **2** = commands/replies/events (64 bytes), **10 (0x0A)** = OLED image transfer (64 bytes).
- Pacing used by Synapse: min 10 ms between commands, ~30 ms between write and read, up to 3 send retries, reply read retried up to 5 times.

## 2. Command packet, general commands [CAP, checksum verified on 10/10 captured commands]
64 bytes:
```
[0]  02                 report ID
[1]  00
[2]  60                 "transaction id" (constant 96 for audio devices)
[3-5] 00 00 00
[6]  len(descriptor)+len(data)+1   (descriptor = 3 bytes, e.g. 93 00 01 -> 5 for 1 data byte)
[7-8] 00 00
[9]  0x80 if the command is for the HEADSET (via the hub/dongle), 0x00 if for the hub itself
[10..12] descriptor: [commandId, 0, dataLen]
[13..]   data
[62] checksum = XOR of bytes 0..61
[63] 00
```
Reply (interrupt IN 0x84, same ID 2): `[1]` = status (**2 = success**, 1 or 3 = retry/busy, others = error), `[2]` = 0x60, `[10]` = commandId, `[11]` = 1 (reply marker), `[12]` = data length, data from `[13]`. Synapse checks `reply[10..11] == [commandId, 1]`.
For "Set" commands the command ID has bit 0x80 set; the matching "Get" ID is the same with bit 0x80 cleared (e.g. set sidetone volume 153 = 0x99, get = 25). Exception: the OLED family (section 5) uses the opposite (Get = 0x80+).

### Command-class variant ("class and id") [SRC]
Used for device mode: `[6]=dataLen+1`, `[7]=class`, `[8]=id`, `[9]=0x80|0`, data from `[10]`, checksum `[62]`. Device mode: get = class 0, id 0x84 (132); set = class 0, id 4. Values: Normal 0, Driver 3, Standby 6, Pairing 5, Download 4, Bootloader 1. (Not seen in captures; the knob works in Normal mode.)

### Haptics variant [CAP for framing]
`[6]=0x25`, `[7]=0x16`, `[8]=0x5F`, `[9]=0x80`, `[10..]` = `[0x8C, sub, ...]`, checksum NOT at 62: `[10+36-1]` = (256 - sum(bytes 10..35)) & 0xFF (per code; the 3 captured haptics frames had 0 at byte 62, as expected). Sub-IDs below.

## 3. General command table [SRC; entries marked [CAP] were also seen on the wire]
Format: Get ID / Set ID, data length. (Set data length = last number in the Set descriptor.)

| Function | Get | Set | Data | Notes |
|---|---|---|---|---|
| Serial number | 0x00 | | 15 ASCII | [CAP via logs] |
| Edition ID | 0x01 | | 1 | |
| Firmware version | 0x02 | | 24 = 6 x 4 bytes (a.b.c.d each) | extended version listing |
| Preset EQ index | 0x13 | **0x93** | 1 | [CAP] presets 7, 9, 8 seen |
| Customer EQ bands | 0x15 | **0x95** | 10 signed bytes | [CAP] |
| Mic customer EQ bands | 0x17 | 0x97 | 10 | |
| Mic preset EQ index | 0x16 | 0x96 | 1 | |
| Sidetone on/off | 0x18 | **0x98** | 1 | |
| Sidetone volume | 0x19 | **0x99** | 1 | |
| Speaker preset EQ status | 0x1E | 0x9E | 1 | |
| Wireless (headset<->hub) status | 0x20 | | 1 | dongle command (`[9]=0`) |
| Battery | 0x21 | | 1 (percent) | [CAP] also pushed unsolicited |
| Charging status | 0x2A | | 1 | |
| Auto power off | 0x2C | **0xAC** | 2 (enabled, minutes) | log showed `{period:15,enabled:true}` |
| Chroma effect | 0x40 | 0xC0 | 1 effectId (+rgb) | |
| Chroma brightness | 0x41 | **0xC1** | 1 (0..255, percent*2.55) | [CAP] |
| Chroma brightness state | 0x43 | **0xC3** | 1 | [CAP] |
| Synapse-driven chroma effect | | 0xC2 | 1 | |
| Chroma frame (custom) | | class 15, id 3 | `[row,0,cols-1,rgb...]` | via class-and-id packet |
| Game/Chat balance | 0x5C | **0xDC** | 1 | value sent = 20 - index (0..20) |
| Mappable button status | 0x5E | **0xDE** | 1 | knob/button mapping, not yet decoded |
| Audio enhancement status | 0x70 | | 2 | |
| Mic enhancement status | 0x71 | | 2 | |
| Haptics (A2H etc.) | `0x8C` prefix, see below | | | |
| Haptics intensity | 0x8B | 0x8A | | |

Haptics sub-commands (descriptor = `[0x8C, sub]`): get mode 0x87 / set 0x07; band volume 0x8C / 0x0C; perception volume 0x89 / 0x09; A2H band detection threshold 0xA2 / 0x22; A2H band frequency 0xA3 / 0x23; **A2H profile 0xA4 / 0x24** [CAP: profiles 1,2,3 sent as `8c 24 <n>`]; streaming 0x0E. Also the continuous stream of `0x21 0x0F 0x03 0x80 ...` reports (~16/s) is Synapse feeding audio levels to the hub for haptics/visualizer [CAP]; not needed for static control.

## 4. Hub -> host unsolicited events (interrupt IN 0x84, report ID 2) [SRC; battery seen in CAP]
Parsed by `rzHardwareEventsAudioBillieT1Parser`. After the 10-byte header the event is `[cmdId, sub, len, data...]` using the same IDs as above: battery level, sidetone level/status, charging status, chroma brightness (0..255) and state, preset EQ index, 10 EQ bands (signed), wireless connection status, mic status (mute), game/chat balance (20 - value), haptic intensity, and OLED/dial events (class 23): OLED home screen parameters, OLED brightness, time-to-homescreen, time-to-dim, active type, **control knob mode states, control knob active mode**, screensaver. The hub therefore tells the PC when you change things on the hub itself.

## 5. OLED
Display: **232 x 64** pixels, 4-bit grayscale, up to 150 frames per animation, frame interval 33..1000 ms (default 66 ms for built-in assets).

### 5a. OLED settings commands [SRC]
Packet: `[0]=02 [1]=00 [2]=60 [3-5]=0 [6]=dataLen [7]=0x17 (class 23) [8]=commandId [9..]=data`, `[63]=0` (no XOR checksum in this variant per code; confirm with capture). Get = 0x80 + Set ID.

| Function | Set | Get | Data |
|---|---|---|---|
| Command dial (knob) active mode | 0x00 | 0x80 | 1 (mode) |
| Command dial mode state (enable flags) | 0x01 | 0x81 | 9 flags, one per mode |
| Home screen display (what the OLED shows) | 0x02 | 0x82 | `[type, params...]` |
| OLED brightness | 0x03 | 0x83 | 1 |
| Time to home screen | 0x04 | 0x84 | 1 (enum) |
| Language | 0x05 | 0x85 | 1 |
| Time to dim | 0x06 | 0x86 | 1 (enum) |
| Update system information (CPU/GPU/RAM/clock) | 0x09 | | 40 bytes |
| Home screen active item | 0x0D | 0x8D | 1 |
| Get image GUID | | 0x8F | `[type, index]` |
| Get image checksum | | 0x90 | `[type, index]` |
| Get animation FPS | | 0x91 | `[index]` |
| Animation state list | 0x14 | 0x94 | one byte per animation slot |
| Image state list | 0x15 | 0x95 | one byte per image slot |
| Screensaver option | 0x16 | 0x96 | `[mode, item]` |

Enums: home screen type `ANIMATION 0, IMAGE 1, BANNER_TEXT 2, DEVICE_INFO 3, EMOTE 4, AUDIO_DISPLAY 5, SYSTEM_INFO 6, OFF 7`. Knob (command dial) modes `VOLUME 0, TRACK_SELECTOR 1, OLED_BRIGHTNESS 2, KEYBOARD_LIGHTING_BRIGHTNESS 3, SWITCH_APPS 4, WINDOWS_ZOOM 5, TRACK_JOGGING 6, VERTICAL_SCROLL 7, HORIZONTAL_SCROLL 8, CUSTOM_MODE_1 9`; knob mode-state names for the Kraken: `VOLUME_STATE 0, SWITCH_AUDIO_EQ_STATE 1, SWITCH_HAPTIC_LEVEL_STATE 2, SWITCH_INPUT_SOURCE_STATE 3, MIC_SIDETONE_LEVEL_STATE 4, GAME_CHAT_BALANCE_STATE 5, CHROMA_BRIGHTNESS_STATE 6, OLED_BRIGHTNESS_STATE 7, SWITCH_HAPTIC_PROFILE_STATE 8`.

### 5b. Uploading an image / animation [SRC]
Uses **report ID 10**, 64-byte reports written with `hid.write`; each write is answered by an interrupt-IN report whose `[0]=10` and `[2]` is the status (**2 = ok**, 1 = bad/resend, 3 = resend, 5 = fatal).
Report layout (payload packets): `[0]=10 [1]=commandId [2]=payloadLen (<=59) [3]=packetIndexHigh [4]=packetIndexLow [5..]=payload` (the code builds `[cmd, len, idxH, idxL, data...]` and prepends report ID 10; max 59 data bytes per packet).
Command IDs (first byte after report ID): block header 2 / data 3, **GIF header 4 / data 5**, banner text header 12 / data 13, audio text header 14 / data 15, system-info image header 16 / data 17, **home-screen image header 18 / data 19**.

Image (home-screen image, slot `i`):
1. Build `binaryData` = 12-byte header (three uint32 LE: `frameCount, width, height`) + pixels (see 5c).
2. Build `GUID` = up to 20 bytes (Synapse uses random bytes; used so it can later ask "is this the same image" via 0x8F).
3. Build a 64-byte first block: `[widthH, widthL, heightH, heightL, GUID(<=20 bytes at 4..23), zeros, 60..63 = 32-bit sum of all pixel bytes, MSB first]`, then append the pixel bytes. Total = 64 + pixelBytes, split into packets of 59 bytes; `packetCount = ceil(total/59)`.
4. Send the header report: `[18, 0, imageIndex, 0, widthH, widthL, heightH, heightL]` (cmd 18).
5. Send the packets: `[19, len, idxH, idxL, 59 bytes...]`, waiting for ok after each (Synapse waits ~10 ms, header ~300 ms).
GIF/animation slot `i` (2..9 in Synapse's use; slot 1 = emote): first block is 512 bytes: `[frameCount, 0, intervalH, intervalL, GUID(<=20 at 4..), ..., 508..511 = pixel sum MSB first]`; packets as above with IDs 4 (header: `[4,0,index,frames,pktCountH,pktCountL]`) and 5 (data). `index` 0..7 validated; interval 30..1000 ms.

### 5c. Pixel encoding [SRC, Synapse's own JS `oled-image-conversion.txt`]
Resize to 232x64 (or the requested size). For each pixel in row-major order (top row first, left to right): `L = 0.2126*R + 0.7152*G + 0.0722*B` (0..255), `v = floor(L/16)` (0..15). Pack two pixels per byte: **first pixel in the low nibble, second pixel in the high nibble**. Frames are concatenated. Background fill used for text is rgb(16,16,16) (value 1). 232x64 = 14,848 pixels = 7,424 bytes per frame.

## 6. Volume / knob (see protocol.md)
Knob = standard HID consumer-control on interface 3, endpoint 0x82. Hardware volume lives in the UAC Feature Unit. Knob mode (what the knob controls) is the OLED "command dial" setting (0x00 / 0x01 above), which is hub state shared with Synapse.

## 7. Still unverified / not yet seen on the wire
- Exact request bytes for every OLED command (only from code).
- Whether the OLED settings packets carry a checksum.
- Whether any command requires Driver Mode (3) first (Synapse sets Normal mode on exit).
- Full enum tables for EQ presets, wireless status, mic status.
- Everything here must be re-checked with a USBPcap capture before sending to the real device (OLED upload especially: it writes to the hub's flash).

---
# OLED settings: CONFIRMED by captures/oled-image-upload.pcap (2026-10-23)
(This capture contained image *selection* and brightness only. **No image upload (report ID 10) happened**: the images were already on the hub, Synapse only compared GUIDs.)

## Packet format [CAP, 62/62 packets match; section 5a of the source notes is correct]
```
Request  [0]=02 [1]=00 [2]=60 [3-5]=00 [6]=dataLen [7]=0x17 [8]=commandId [9..]=data   [62]=00 [63]=00   (NO checksum)
Reply    [0]=02 [1]=02(success) [2]=60 [3-5]=00 [6]=replyDataLen [7]=0x17 [8]=commandId (echo) [9..]=data
```
Sent as HID SET_REPORT (report ID 2, interface 4), reply on interrupt IN 0x84 about 5-45 ms later.

## Commands seen on the wire
| t (s) | Request (from byte 6) | Meaning | Reply |
|---|---|---|---|
| 6.05 | `0a 17 15 01 01 01 01 01 01 01 01 01 01` | Set home-screen image state, 10 slots, all = 1 (enabled) | ok, no data |
| 6.09 / 19.75 / 35.25 | `02 17 02 01 <idx>` idx = 01, 02, 09 | **Set home-screen display: type IMAGE (1), image index** | ok |
| 6.13 / 19.80 / 35.29 | `01 17 0d 01` | Set home-screen active item = 1 | ok |
| 6.19 ... 6.89 (x3 blocks) | `02 17 8f <type> <idx>` | Get image GUID. Queried (type 2, idx 0..5), (type 9, idx 0..9), (type 1, idx 0) | `02 17 8f <type> <idx> <20-byte GUID>` |
| 93.36 | `01 17 03 1e` | **Set OLED brightness = 30** | ok |
| 107.17 | `01 17 03 64` | **Set OLED brightness = 100** | ok |
GUID replies: empty slot = all `ff`; stock image = repeated `02`; the custom slot (type 1, idx 0) held a 20-char ASCII hash string. Synapse reads these GUIDs on every image change to decide whether an upload is needed; it did NOT upload here, so slots already matched.
Brightness range is 0..100 (percent).

## Still to capture
- A real **custom image upload** (needs the user to add their OWN picture/GIF to a custom slot in Synapse), to verify section 5b/5c (report ID 10 packets, 232x64 4-bit pixels).

---
# OLED IMAGE UPLOAD + SIDETONE: CONFIRMED BYTE-FOR-BYTE (captures/oled-custom-upload.pcap, 2026-10-23)
Test image: `test-images/oled-test-232x64.png`. Reassembling the captured packets reproduced the PNG's pixel bytes with **0 differences**, and the device checksum matched. Sections 5b and 5c above are therefore **CONFIRMED [CAP]**, with these exact details:

## Upload sequence for "home screen image slot 0" (took 2.2 s: t=7.46..9.66 s)
1. `Set home-screen image state` (OLED cmd 0x15, 10 data bytes, all 01) just before (t=7.41).
2. **Header packet** (SET_REPORT, wValue 0x020A = report ID 10, 64 bytes): `0A 12 00 <slot> 00 <wH> <wL> <hH> <hL> 00...` -> `0a 12 00 00 00 00 e8 00 40` = cmd 0x12, slot 0, width 232, height 64. Reply (interrupt IN 0x84): same packet with `[2]=0x02` (ok), 0.5 s later.
3. **127 data packets**: `0A 13 <len> <idxH> <idxL> <payload>`; len = 0x3B (59) for all but the last (0x36 = 54); idx 0..126. Each is answered by the hub echoing the whole packet with `[2]=0x02`; Synapse waited for each reply (about 8-10 ms per packet).
4. Concatenated payloads = **64-byte block + 7424 pixel bytes** (= 7488 bytes = 127 packets):
   - block[0..3] = `00 E8 00 40` (width, height, big-endian 16-bit each)
   - block[4..23] = 20-byte ASCII GUID (here `295814cc2a6bc91ad968`, 20 hex characters)
   - block[24..59] = 0
   - block[60..63] = **sum of all pixel bytes**, 32-bit big-endian (here 0x000EB05F = 962655)
5. Pixels: 232x64, row-major from the **top-left**, 4 bits per pixel = `floor(luma/16)` with `luma = 0.2126R + 0.7152G + 0.0722B`; **first pixel in the LOW nibble, second pixel in the HIGH nibble**. Verified on gray bars, a black pixel at (231,0) (last byte of row 0 = 0x0F) and a white pixel at (0,63).
6. Afterwards Synapse sent: `Set home-screen display` `02 17 02 01 <slot>`, `Set active item`, and the GUID queries (as in the earlier section), so the slot shows as selected.
OLED reports use report ID 10 for image data and ID 2 for OLED setting commands; both are SET_REPORT on interface 4 and answered on interrupt IN 0x84.

## Sidetone [CAP]
`02 00 60 00 00 00 05 00 00 80 98 00 01 <on>` = **sidetone on (01) / off (00)** (command 0x98) and `... 80 99 00 01 <level>` = **sidetone level 0..15** (command 0x99). Synapse sends both together: level 8 + on (t=93), level 0 + on (t=107), level 15 + on (t=119), then 0x98=00 (off) (t=128). Replies: `02 02 60 ... 80 98 01 01 00` (ok). Checksum byte 62 per the XOR rule.

## Mic [CAP, partly]
No host command was seen for turning the mic on/off in Synapse; the hub instead sent an **unsolicited event** `... 80 55 02 01 01` (command 0x55, sub 2 = event, data 01) at t=147 s. 0x55 = mic status in the event parser (`MIC_STATUS`); value meaning (mute on/off) not yet mapped. Treat the mic mute as hub-side state that the PC only reads.

---
# SYNAPSE START / EXIT SEQUENCE: CONFIRMED (captures/A-synapse-startup.pcap, 2026-10-24)
**There is no authentication, challenge or secret handshake.** Synapse reads device state, switches the hub to "Driver mode", and pushes its saved settings. On exit it switches back to Normal mode.

## On Synapse exit (t=3.5..3.9 s)
1. `01 00 84` (class-and-id, class 0 id 0x84) = **Get device mode**
2. `.. 80 c0 00 01 0c` = set chroma effect 0x0C (restores a default lighting effect)
3. `.. 80 9e 00 01 01` = speaker preset EQ status = 1 (twice)
4. `02 00 04 00 00` (class 0, id 4, data 00) = **Set device mode = 0 (Normal)**
## On Synapse start (t=25.7..34.7 s)
1. Reads: `0x02` firmware, `0x2A` charging, `0x21` battery, `0x00` serial, `0x01` edition (all `[len=4] 00 00 80 <id> 00 00`, i.e. general Get commands to the headset); several repeats.
2. `01 0f 90 80` = class 15 id 0x90 = **Get force chroma control**, then `02 0f 10 80 01` = class 15 id 0x10, data 01 = **Set force chroma control = 1** (Synapse takes over lighting).
3. `01 00 84` get device mode, then **`02 00 04 00 03` = Set device mode = 3 (Driver mode)** at t=26.56 s. (Driver mode is entered once per Synapse session; it is also re-checked with Get device mode.)
4. `03 0f 03 80 00 00` = class 15 id 3 (Set chroma frame) with `[row=0, 0, cols-1=?]`.
5. **Push of saved settings** (all general/OLED commands already documented): OLED language get (0x85), auto power-off `ac 00 01 0f` (15 min), OLED brightness 100 (`01 17 03 64`), image GUID queries (0x8F), OLED time-to-dim 1 (`17 06 01`), screensaver `17 16 01 02`, OLED home screen IMAGE slot 0 (`17 02 01 00`), haptics perception volume (`8c 09 ...`), haptics intensity (`8c ... 8a 01 01 01`), game/chat balance `dc 00 01 0a` (10 = middle), sidetone off (`98 00 01 00`), mic preset EQ index 2 (`96 00 01 02`), chroma brightness state 1 (`c3`), synapse-driven chroma effect 0x0C (`c2 00 01 0c`), mappable buttons off (`de 00 01 00`), OLED active item 1 (`17 0d 01`), haptics profile 2, chroma brightness 255, speaker EQ status 0 (`9e`), **EQ preset 8 (`93 00 01 08`) followed by its 10 bands `95 00 0a 04 03 02 01 01 01 02 02 01`** (identical to the bands seen earlier for preset 8: bands belong to the preset number).
## What this means for Linux
- No unlock is needed to talk to the hub. The knob and volume worked in Normal mode with Synapse closed.
- Unknown whether write commands (EQ, sidetone, OLED) work in Normal mode or require Driver mode (3). All captures so far were taken while Synapse had Driver mode set. TEST on Linux: try a harmless write (sidetone level) in Normal mode first; if the hub ignores it, send `set device mode 3` first (and `set force chroma control = 1` for lighting), and set mode 0 when the tool exits. Never leave the hub in a mode Synapse does not expect.

---
# EQ, MIC PRESETS, MIC MUTE, LIGHTING NAMES (captures/B-eq-sound.pcap + Synapse code, 2026-10-24)
## Speaker EQ [CAP + SRC enum]
Set preset: `.. 80 93 00 01 <id>`; custom: id = **0xFF** followed by `95 00 0a <10 signed bytes>` (each slider -?..+?, observed 0, 3, -4, 5). Preset ids (enum in Synapse): **Default 0, Game 7, Music 8, Movie 9, ApexLegends 250, CSGO 251, Valorant 252, Fortnite 253, CallOfDuty 254, Custom 255**. Captured: Game(7) bands `fc ff 01 02 03 03 03 03 03 02`; Movie(9) `05 05 00 fe fe 01 04 05 04 02`; Music(8) `04 03 02 01 01 01 02 02 01 00`. Synapse always sends the preset id and then the 10 bands of that preset (the hub does not know the band tables; Synapse holds them). **For Linux: the band tables for presets must come from this capture/Synapse data; the hub just stores 10 bands.** The custom EQ is the same two commands with id 0xFF and arbitrary bands (captured: all 0; band0=+3; band1=-4; band2=+5; ...).
## Mic EQ preset [CAP + SRC enum]
`.. 80 96 00 01 <id>`: **Default 0, MicBoost 1, Broadcast 2, Conference 3, CustomerEQ 255** (captured 1, 2, 3). Custom mic EQ bands: command 0x97 (10 bytes).
## Mic mute [SRC]
Event/status command **0x55**: data 00 = unmuted, 01 = muted. The hub reports it by itself (seen at t=147 s in the OLED capture); no host command seen in Synapse, so mic mute is hub-side state (physical button).
## ANC [SRC, NEW]
Constants list contains **ANC on/off: get 0x12 (18), set 0x92 (146)**, data 1 byte. The Kraken V4 Pro has an ANC command; not yet captured on the wire.
## Lighting effect ids (Synapse enum, Billie/Kraken) [SRC]
Off 0, Static 1, Breathing 2, Spectrum 3, Wave 4, Custom frame 8, Fire 9, AudioReactive 10, Wheel 12, Starlight 13, Ambient 48, AudioMeter 49, On 255. Set with `.. 80 c0 00 01 <effect>` (+rgb via the class-15 chroma frame commands); startup used effect 0x0C (Wheel 12) then 0x08.
## Haptics profile names [not found in code, UI only]
UI order is CONTROLLED, BALANCED, DYNAMIC, CUSTOM (screenshot), most likely ids 1, 2, 3 (4?). Capture C will confirm (Synapse restores profile 2 at startup, last used).

## Haptics: CONFIRMED by capture C (captures/C-haptics.pcap, commands in captures/C-haptics.cmds.txt)
User clicked Controlled, Balanced, Dynamic, Custom (5-25 s apart), then turned haptics off, then moved the intensity slider through 6 steps.
All packets use the haptics variant: `02 00 60 00 00 00 25 16 5f 80 8c <sub> ...` (replies echo it on 0x84 with [11]=01).
| Action | Packet bytes from [10] | Meaning |
|---|---|---|
| Profile Controlled | `8c 24 01` | profile id 1 |
| Profile Balanced | `8c 24 02` | profile id 2 |
| Profile Dynamic | `8c 24 03` | profile id 3 |
| Profile Custom | `8c 24 04` | profile id 4 (CONFIRMED names: 1 Controlled, 2 Balanced, 3 Dynamic, 4 Custom) |
| After Custom is picked | `8c 23 01 00 6e` and `8c 0c 01 01 00 64` | Synapse then writes custom values: A2H band frequency (sub 0x23) and band volume (sub 0x0c). Exact field meaning not decoded. |
| Haptics OFF (about t=83 s) | `8a 01 00 00` (sent 3 times) | likely `8a 01 <enabled> <level>` with enabled=0. Inferred from timing, not 100% sure. |
| Intensity slider | `8a 01 01 N`, N = 1..6 | 6 slider steps; each followed by `8c 09 01 00 d8 00 43` (perception volume, same value every time) |
Also: the ~16/s stream of `21 0f 03 80 ...` frames is NOT a command, it is Synapse feeding audio levels (skip it on Linux). No ANC command was seen in this capture (no ANC switch was used).

## Lighting: CONFIRMED by capture D (captures/D-lighting.pcap, commands in captures/D-lighting.cmds.txt)
User did: Static red, green, blue; then Wheel, Spectrum cycling, Breathing, Audio meter, Ambient awareness; then brightness 0, 41, 100.
**The hub does not render effects itself. Synapse picks "custom frame" mode (effect 8) and streams colour frames; every animation is computed on the PC.**
Every effect change sends these 4 packets (general format, [9]=0x00 = to the hub, no 0x80? see raw tsv):
1. `class 0 id 0x84` (get device mode)
2. `c2 00 01 <ui effect>` = which effect the UI selected: Static 1, Breathing 2, Spectrum 3, Wheel 0x0c, Ambient 0x30 (48), AudioMeter 0x31 (49)  [matches the enum]
3. `class 0 id 0x04`, data 03 = set Driver mode
4. `c0 00 01 08` = real hardware chroma effect = 8 (Custom frame)
**Colour frame** (report 2, class 15 id 3): `02 00 60 00 00 00 21 0f 03 80 | 00 00 00 00 08 | R G B x8 LEDs | pad`.
- 8 LEDs, one RGB triple each; red = `ff 00 00`, green = `00 ff 00`, blue = `00 00 ff` (all 8 the same for static).
- A colour change is faded by Synapse over about 1.5 s with ~10 frames/s (intermediate colours). A fade is optional.
- When the colour is steady Synapse still re-sends a frame about every 5 s (keepalive). Unknown whether the hub reverts without it: test on Linux.
- Wheel, Spectrum, Breathing, Audio meter etc. are just different per-LED colour sequences sent as frames (each LED gets its own colour; Wheel/Spectrum show rainbow gradients).
**Brightness** (percent): `c3 00 01 <on/off>` then `c1 00 01 <value>`, value = round(pct*255/100).
- 0 %: `c3 00 01 00` + `c1 00 01 01`
- 41 %: `c3 00 01 01` + `c1 00 01 69` (0x69 = 105)
- 100 %: `c1 00 01 ff`
No ANC command seen yet; still unconfirmed.

## Game/Chat balance: CONFIRMED by capture E (captures/E-balance.pcap)
Command id **0xDC**, 1 data byte: `02 00 60 00 00 00 05 00 00 80 dc 00 01 <v> ... checksum`. Range 0..20.
- All the way to Game = 0x14 (20), all the way to Chat = 0x00, middle = 0x0a (10).
- The hub reports it back (event) as 20 - value in the parser, so the reported number is mirrored.
Microphone mute/volume and ANC were not recorded (user skipped).

## Hub knob modes: CONFIRMED by capture F (captures/F-knob-modes.pcap, events in captures/F-knob.in.txt)
The hub has its own on-device knob menu (press knob = next mode, OLED shows it; works without Synapse). Order the user went through (same as OLED order): Volume, EQ, Haptics profile, Haptics intensity, Input source, Sidetone level.
**Mode-change event** (hub -> PC on 0x84, no request): `02 02 60 00 00 00 01 17 80 <mode>` (OLED class 0x17, cmd 0x80, [9]=mode).
Mode ids seen in order: `0x00` Volume (start/end), `0x10` EQ, `0x17` Haptics profile, `0x11` Haptics intensity, `0x12` Input source, `0x13` Sidetone. Mapping of 0x12 to "Input source" is by order only (no value event seen for it).
**Value events while turning the knob** (hub -> PC, unsolicited, general-format reply with [11]=01 success):
| Mode | Event bytes from [10] | Values seen |
|---|---|---|
| Volume | HID volume keys on interface 3 / ep 0x82 report `0c 01` (up) `0c 02` (down), each followed by `0c 00` (release). Not a vendor event. | |
| EQ | `13 02 01 <preset>` (PRESET_EQ_INDEX event) | 9 Movie, 8 Music, 9, 0xff Custom |
| Haptics profile | `8c 01 a4 <n>` (reply form of sub 0x24, n = 1..4) | 2,3,2,1,4 |
| Haptics intensity | `8b 01 01 01 <n>` | 1,2,3,2,1,0 (0 = off) |
| Sidetone | `19 02 01 <n>` | 1,2,3,2,1,0 |
**IMPORTANT: EQ curves are NOT stored in the hub.** When the knob changed the EQ preset, Synapse immediately wrote back `93 00 01 <preset>` and then `95 00 0a <10 band bytes>`:
- Movie (9):  `05 05 00 fe fe 01 04 05 04 02`
- Music (8):  `04 03 02 01 01 01 02 02 01 00` (decoded from capture; trailing bytes as sent)
- Custom (0xff): `00 00 00 00 00 00 00 00 00 00`
So for knob EQ switching to actually change the sound on Linux, the tool must listen for the preset event (`13 02 01 n`) and write the matching band table (`95`). Bands are signed bytes. Collect the other presets' tables from Synapse code/captures.
Likewise the knob only changes haptics profile / intensity / sidetone numbers on the hub; the hub applies those itself (no write-back was seen).
Input source: no event or write seen; could not be confirmed.

## Knob mode ids (Synapse enum, matches capture F) [SRC+CAP]
Event `01 17 80 <mode>` (class 0x17, cmd 0x80 = CONTROL_KNOB_ACTIVE_MODE); mode states cmd 0x81 (CONTROL_KNOB_MODE_STATES). Names from Synapse code (`synapse-files/web1383-full/3487.js`):
VOLUME 0x00, SWITCH_AUDIO_EQ 0x10, SWITCH_HAPTIC_LEVEL 0x11, SWITCH_INPUT_SOURCE 0x12, MIC_SIDETONE_LEVEL 0x13, GAME_CHAT_BALANCE 0x14, CHROMA_BRIGHTNESS 0x15, OLED_BRIGHTNESS 0x16, SWITCH_HAPTIC_PROFILE 0x17.
The capture confirmed 0x10, 0x11, 0x12, 0x13, 0x17 (user's hub had these 5 + volume enabled). Haptics profile enum in code: CONTROLLED 1, BALANCED 2, DYNAMIC 3, CUSTOM 4 (matches capture C).
Which modes are in the knob rotation is stored in the hub (cmd 0x81, mode states, one byte per mode in this order); read it with the OLED get and write it only after testing.
Mic noise cancellation in Synapse is a host-side audio feature (MW_..._MIC_NOISE_CANCELLATION), not a hub command: no hardware ANC command is known.
