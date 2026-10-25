# Device info (collected from Windows, 2026-10-23)

## Razer devices on this PC (VID 0x1532)
| Device | PID | Notes |
|---|---|---|
| **Razer Kraken V4 Pro (OLED hub)** | **0x0568** (1384) | Target. Synapse "product 1383". Composite USB device. |
| Razer Naga V2 Pro | 0x00A7 | Not the target |
| Razer DeathStalker V2 Pro | 0x0290 | Not the target |

## Kraken V4 Pro USB interfaces (from Device Manager, PID_0568)
| Interface | What it is |
|---|---|
| MI_00 | USB Audio: "Razer Kraken V4 Pro - Chat" |
| MI_03 | HID consumer control (**likely the volume knob / media keys**) |
| MI_04 | HID, vendor-defined, COL01 and COL02 (**control channel used by Synapse**, claimInterface:4) |
| MI_05 | USB Audio: "Razer Kraken V4 Pro - Game" |
| MI_01/MI_02 | not seen in the list; check full descriptors |

Synapse opens `hid#vid_1532&pid_0568&mi_04&col01` for commands.

## Synapse
- Synapse 4 / RazerAppEngine (Electron). Device logic lives in web code loaded from `https://apps.razer.com/synapse/products/1383/mw/...` (manifest 1.1.10, "AudioDevice-0.0.340").
- Protocol class name in logs: `AudioBillieT1Dongle`, wrapper `protocolAudio`.
- Helper DLLs (under `%LOCALAPPDATA%\Razer\RazerAppEngine\User Data\Apps\Common\`): `RzAV\RzAV.dll`, `audCapNative`, `thxv3common\ThxV3Native`, THX presets/driver installers for 0567/0568.
- Logs with the full command traffic: `%LOCALAPPDATA%\Razer\RazerAppEngine\User Data\Logs\products_1383_mw {...}.log` (copied to `synapse-files/logs/`).

## Connection mode
User uses the headset **wireless through the OLED hub** (hub on USB, headset on its 2.4 GHz link). All captures should use this mode, with the headset powered on and connected. Synapse logs show it works this way (battery/charging queries answered via the hub). Wired-USB or Bluetooth modes are not covered and may use a different PID.

## Still needed
- Full USB descriptors (USBTreeView) and HID report descriptors for MI_03 and MI_04.
- Wireshark + USBPcap captures (not installed on this PC yet).
- Firmware version (`Get Headset Firmware Version` response is logged, decode it).
- Whether the PID differs between wired and wireless dongle mode (0567 appears in THX file names, probably the dongle).
