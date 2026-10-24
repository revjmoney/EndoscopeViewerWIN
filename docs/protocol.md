# Observed MoLink video and control protocol

These notes describe the tested camera and inspected HD WIFI 7.1 app family. They are not a universal camera standard.

## Video: UDP 8031

The host is `192.168.10.123` on the tested hotspot. A 24-byte little-endian request uses the layout `<HH5I`: magic `0x9999`, command `1`, then five zero words. Repeat roughly every 600 ms to keep receiving video.

Video packets begin with magic `0x9999` and command `3`. The tested header is 51 bytes:

| Offset | Type | Observed role |
| --- | --- | --- |
| 0 | uint16 | Magic `0x9999` |
| 2 | uint16 | Video command `3` |
| 16 | uint32 | Nonzero selects plain payload handling |
| 24 | uint8 | Video format; `1` is JPEG |
| 25 | uint32 | Frame ID |
| 29 | uint32 | Complete frame size |
| 33 | uint16 | Fragment index, starting at 1 |
| 35 | uint16 | Fragment count |
| 37 | uint16 | This fragment's payload length |
| 39 | uint32 | Sequence value used by the frame transform |
| 43 | uint16 | Width |
| 45 | uint16 | Height |
| 51 | bytes | Fragment payload |

Reassemble only complete, consistent frames. If the word at offset 16 is zero, reverse one byte:

```python
index = (((((sequence ^ size) + size + (1 & ~size)) & 0xFFFFFFFF) ^ size) % size)
frame[index] ^= 0xFF
```

Validate length and JPEG markers after the transform. The implementation bounds frame sizes, fragment counts, buffered frames and incomplete-frame lifetimes. It drops incomplete frames rather than stitching unrelated packets together.

## Camera controls

Most observed controls target UDP `50000`. The library's lens-switch request uses UDP `51521`; do not confuse it with the firmware-update operation that shares a command number on a different port.

Two request formats coexist:

- Binary header `<HH5I`, with magic `0x9999`, command, request sequence and four parameter words.
- Legacy `SETCMD` header `<6sIHH`: six ASCII bytes, request sequence, command and payload length, followed by the payload.

Legacy replies begin `RETCMD`, echo the request sequence at offset 6 and command at offset 10, carry a status byte at offset 12 and length at offset 13, then payload at offset 15. One real resolution reply advertised a payload one byte longer than received; the viewer reports width/height and leaves the missing FPS unknown.

Binary replies echo magic, command and sequence. Matching is required before accepting a reply. A timeout means unconfirmed support/effect, not proof that a setting was rejected or applied.

The public command catalog is in `camera_controls.py`. No setting command is sent automatically during discovery. Factory/manufacturing operations and firmware upload are not implemented.

## Discovery scope

Wi-Fi discovery uses Windows `netsh wlan`, recognizes a limited set of camera-like SSID prefixes, then queries the known MoLink camera address. It does not scan the entire LAN or authenticate to arbitrary devices. This is hotspot-family discovery, not universal network-camera discovery.
