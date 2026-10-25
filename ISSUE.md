**Title:** Razer Kraken V4 Pro (1532:0568): protocol documented, working reference tool, looking for guidance on driver layout

Hi, I own a Razer Kraken V4 Pro (OLED hub + wireless headphones, USB ID `1532:0568`; the headphones on their own cable show up as `1532:0567`) and have reverse-engineered its protocol, with every command below tested on the real device from Linux.

**Important: this is not the old Kraken protocol.** The V4 Pro does not use the RAM-address control transfers of the Kraken 7.1 V2 / `razerkraken` driver. It speaks a vendor HID protocol on USB interface 4: 64-byte reports, Report ID 2 for commands, replies and events, Report ID 10 for OLED image upload, an XOR checksum, and no handshake or auth. Reusing the existing Kraken code path for this PID will not work.

**What's documented and verified on hardware**
- Serial, firmware, battery, charging
- Speaker EQ presets and custom 10-band curves, mic EQ presets
- Game/chat balance, sidetone on/off/level
- Haptics: profiles, intensity, custom frequency and volume
- Lighting: brightness, plus software effects streamed as RGB frames in driver mode (static, breathing, spectrum, wheel, audio-reactive)
- OLED brightness and custom image upload (232x64, 4-bit)
- Device mode, knob events, and the cable variant (same protocol without the dock)

**Open:** the OLED non-image screens (system info etc.) and the volume popup could not be reproduced; the ANC command (0x92) has not been exercised.

**Material** (protocol spec, a ~340-line stdlib-only Python reference tool, HID descriptors, decoded Synapse captures and raw USBPcap files): https://github.com/RGBLizard/razer-kraken-v4-pro-linux

I'd like to turn this into proper OpenRazer support and have tested the full command set on one unit (firmware 1.6.3.0), so I can test patches on real hardware. Before I write code:

1. Is a new kernel driver the right shape here, or would you prefer a daemon-side hidraw backend, given that it's a plain vendor HID interface and the lighting is rendered on the PC rather than by the device?
2. How should a composite device be modelled (hub 0568 plus headphones 0567 on cable), and how should the many non-lighting features (EQ, haptics, OLED) be exposed in the D-Bus/API? Is there precedent I should follow?
3. Anything you'd like changed in the documentation format to make it easy to review?

Thanks!
