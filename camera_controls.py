"""Independent encoders for commands observed in HD WIFI 7.1.

Protocol support does not imply support by a particular camera.
"""
from dataclasses import dataclass
import socket
import struct
import threading
import time


@dataclass(frozen=True)
class Control:
    name: str
    command: int
    kind: str = "query"
    port: int = 50000
    note: str = "Shared-library command; support varies by camera."


CONTROLS = [
    Control("Firmware version", 0x1002),
    Control("Supported resolutions", 9, "legacy_query"),
    Control("Video format", 0x1000),
    Control("Battery", 0x1017),
    Control("Lens temperature", 0x1071),
    Control("Lighting value", 0x1015),
    Control("Movement level", 0x1055),
    Control("Camera style", 0x1069),
    Control("Camera mode", 0x1044),
    Control("Gyroscope type", 0x1063),
    Control("Auto power-off value", 0x1073),
    Control("Board information", 0x1060),
    Control("Stream password mode", 0x1074),
    Control("UVC information", 0x1080),
    Control("Motor information", 0x1066),
    Control("Set resolution", 8, "resolution", note="Width, height, FPS; camera may ignore unsupported sizes."),
    Control("Set video format", 0x1001, "integer", note="1 = MJPEG; 4 = YUYV. The viewer currently decodes MJPEG only."),
    Control("Switch camera / lens", 0x2000, "action", 51521),
    Control("Lighting / PWM", 0x1016, "integer", note="Device-specific integer lighting value; range is not advertised."),
    Control("White-light PWM", 0x1040, "integer"),
    Control("Red-light PWM", 0x1041, "integer"),
    Control("Blue-light PWM", 0x1042, "integer"),
    Control("Lighting mode", 0x1043, "integer"),
    Control("Switch lamp", 0x1050, "action"),
    Control("Movement level", 0x1054, "integer", note="Android movement buttons use 0 = stop, 1 or 2 = direction. Requires matching hardware."),
    Control("Autofocus", 0x24, "integer", note="Device-specific value; usually a fixed-focus probe has no focus motor."),
    Control("Focus position", 0x23, "integer"),
    Control("Ultrasonic control", 0x1052, "integer"),
    Control("Camera style", 0x1070, "integer"),
    Control("Switch mode", 0x1018, "integer"),
    Control("Motor 1", 0x1065, "integer", note="Raw motor-control word; may move attached hardware."),
    Control("Motor 2", 0x1068, "integer", note="Raw motor-control word; may move attached hardware."),
    Control("Both motors", 0x1067, "integer", note="Raw motor-control word; may move attached hardware."),
    Control("Wi-Fi name", 1, "ssid", note="Changes the camera hotspot name and may disconnect Wi-Fi."),
    Control("Wi-Fi password", 2, "password", note="Changes the camera hotspot password and may disconnect Wi-Fi."),
    Control("Clear Wi-Fi password", 3, "legacy_action", note="Removes the hotspot password and may disconnect Wi-Fi."),
    Control("Reboot camera", 4, "legacy_action", note="Restarts the camera and interrupts video."),
    Control("Wi-Fi channel", 0x1014, "integer", note="Changing channel may disconnect Wi-Fi. Available channels depend on the camera and region."),
    Control("Stream password", 0x1010, "stream_password", note="Changes stream access. Password-protected video is not yet decoded by this viewer."),
    Control("Power off camera", 0x1019, "action", note="Turns off the camera if supported."),
]


def encode(control, sequence, values=()):
    if control.kind in ("ssid", "password", "stream_password"):
        payload = str(values[0]).encode("ascii")
        if control.kind == "ssid" and not 1 <= len(payload) <= 31:
            raise ValueError("Wi-Fi name must be 1–31 ASCII characters.")
        if control.kind == "password" and not 8 <= len(payload) <= 32:
            raise ValueError("Wi-Fi password must be 8–32 ASCII characters, matching the app.")
        if control.kind == "stream_password":
            if not 1 <= len(payload) <= 32:
                raise ValueError("Enter 1–32 ASCII characters for the stream password.")
            return struct.pack("<HH5I", 0x9999, control.command, sequence, 0, len(payload), 0, 0) + payload
    elif control.kind == "resolution":
        width, height, fps = map(int, values)
        if not (1 <= width <= 8192 and 1 <= height <= 8192 and 1 <= fps <= 120):
            raise ValueError("Enter a valid width, height and FPS.")
        payload = struct.pack("<HHB", width, height, fps)
    else:
        payload = b""
    if control.kind.startswith("legacy") or control.kind in ("ssid", "password", "resolution"):
        return struct.pack("<6sIHH", b"SETCMD", sequence, control.command, len(payload)) + payload
    value = int(values[0]) if values else 0
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("Value must be an unsigned 32-bit integer.")
    if control.name == "Wi-Fi channel" and not 1 <= value <= 14:
        raise ValueError("Wi-Fi channel must be between 1 and 14.")
    return struct.pack("<HH5I", 0x9999, control.command, sequence, value, 0, 0, 0)


def decode(control, sequence, packet):
    legacy = control.kind.startswith("legacy") or control.kind in ("ssid", "password", "resolution")
    if legacy:
        if len(packet) < 15 or packet[:6] != b"RETCMD":
            raise ValueError("Unexpected camera reply.")
        reply_sequence, command = struct.unpack_from("<IH", packet, 6)
        if reply_sequence != sequence or command != control.command:
            raise ValueError("Reply does not match this request.")
        status = packet[12]
        length = struct.unpack_from("<H", packet, 13)[0]
        body = packet[15:]
        result = {"status": status, "declared_length": length, "body": body.hex(), "note": "Camera replied; verify the resulting behavior."}
        if control.kind == "legacy_query" and control.command == 9 and body:
            resolutions = []
            for i in range(min(body[0], 64)):
                offset = 1 + i * 5
                if offset + 4 > len(body):
                    break
                w, h = struct.unpack_from("<HH", body, offset)
                fps = body[offset + 4] if offset + 4 < len(body) else None
                resolutions.append({"width": w, "height": h, "fps": fps})
            result["resolutions"] = resolutions
        return result
    if len(packet) < 24:
        raise ValueError("Camera reply is shorter than its header.")
    magic, command, reply_sequence, a, b, c, d = struct.unpack_from("<HH5I", packet)
    if (magic, command, reply_sequence) != (0x9999, control.command, sequence):
        raise ValueError("Reply does not match this request.")
    result = {"values": [a, b, c, d], "body": packet[24:].hex(), "note": "Camera replied; returned values are device-specific."}
    if control.name == "Firmware version":
        result["version"] = a
    if control.name == "Lens temperature" and a:
        result["temperature_c"] = (a + (b << 32)) / 1000.0
    return result


class CameraControls:
    def __init__(self):
        self.sequence = int(time.time()) & 0xFFFFFFFF
        self.lock = threading.Lock()

    def execute(self, host, control, values=(), timeout=1.2):
        with self.lock:
            self.sequence = (self.sequence + 1) & 0xFFFFFFFF
            sequence = self.sequence
            request = encode(control, sequence, values)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
                connection.connect((host, control.port))
                connection.settimeout(timeout)
                connection.send(request)
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    try:
                        packet = connection.recv(8192)
                        return decode(control, sequence, packet)
                    except ValueError:
                        continue
            raise TimeoutError("No matching reply — unsupported, unreachable, or the camera disconnected. The command's effect is unconfirmed.")
