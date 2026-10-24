"""Independent HD WiFi UDP/JPEG viewer; verified with the user's camera.

Does not send persistent camera configuration commands.
"""
import io
import ipaddress
import json
import queue
import shutil
import socket
import struct
import subprocess
import sys
sys.dont_write_bytecode = True
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk, scrolledtext

from PIL import Image, ImageOps, ImageTk
from camera_controls import CameraControls, CONTROLS
import camera_wifi
from recording import Recorder
from app_paths import data_dir, VERSION


class UdpFrames:
    LIMIT = 8 * 1024 * 1024

    def __init__(self):
        self.frames = {}

    def feed(self, packet):
        if len(packet) < 51 or packet[:4] != b"\x99\x99\x03\x00":
            return []
        frame_id, size, index, count, length = struct.unpack_from("<IIHHH", packet, 25)
        video_format = packet[24]
        sequence = struct.unpack_from("<I", packet, 39)[0]
        plain = struct.unpack_from("<I", packet, 16)[0]
        if video_format != 1 or not 1 <= size <= self.LIMIT or not 1 <= index <= count <= 4096:
            return []
        if not 1 <= length <= 3000 or len(packet) != 51 + length:
            return []
        key = (packet[24], frame_id)
        now = time.monotonic()
        self.frames = {k: v for k, v in self.frames.items() if now - v[0] < 2}
        if key not in self.frames:
            if len(self.frames) >= 4:
                del self.frames[next(iter(self.frames))]
            self.frames[key] = (now, size, count, {}, sequence, plain)
        _, expected_size, expected_count, parts, expected_sequence, expected_plain = self.frames[key]
        if (size, count, sequence, plain) != (expected_size, expected_count, expected_sequence, expected_plain):
            del self.frames[key]
            return []
        parts[index] = packet[51:]
        if sum(map(len, parts.values())) > size:
            del self.frames[key]
            return []
        if len(parts) != count:
            return []
        frame = bytearray(b"".join(parts[i] for i in range(1, count + 1)))
        del self.frames[key]
        if len(frame) != size:
            return []
        if not plain:
            # The native app reverses this one-byte transport transform after reassembly.
            offset = (((((sequence ^ size) + size + (1 & ~size)) & 0xFFFFFFFF) ^ size) % size)
            frame[offset] ^= 0xFF
        # Drop incomplete/unsupported frames instead of displaying corrupted data.
        if len(frame) != size or not frame.startswith(b"\xff\xd8") or not frame.endswith(b"\xff\xd9"):
            return []
        return [bytes(frame)]


class JpegFrames:
    LIMIT = 8 * 1024 * 1024

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while True:
            start = self.buffer.find(b"\xff\xd8")
            if start < 0:
                self.buffer[:] = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b"\xff\xd9", 2)
            if end < 0:
                if len(self.buffer) > self.LIMIT:
                    self.buffer.clear()
                    raise ValueError("No complete JPEG within 8 MiB; unsupported or damaged stream.")
                break
            if end + 2 > self.LIMIT:
                self.buffer.clear()
                raise ValueError("JPEG exceeds the viewer's 8 MiB frame limit.")
            frames.append(bytes(self.buffer[:end + 2]))
            del self.buffer[:end + 2]
        return frames


class Viewer:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue(maxsize=4)
        self.session = 0
        self.stop = threading.Event()
        self.worker = None
        self.image = None
        self.preview = None
        self.rotation = 0
        self.mirror = False
        self.zoom = 1
        self.paused = False
        self.recorder = None
        self.controls = CameraControls()
        self.control_events = queue.Queue()
        self.find_stop = threading.Event()
        self.finding = False
        self.settings_path = data_dir() / "preferences.json"
        try:
            self.preferences = json.loads(self.settings_path.read_text())
        except (OSError, ValueError):
            self.preferences = {}
        self.players = []
        root.title("EndoscopeViewerWIN — tiny camera, actual Windows app")
        root.geometry("1120x800")
        root.minsize(920, 600)
        discovery = ttk.Frame(root, padding=(10, 10, 10, 0))
        discovery.pack(fill="x")
        ttk.Button(discovery, text="Find & Connect", command=self.find_camera).pack(side="left")
        self.auto_wifi = tk.BooleanVar(value=self.preferences.get("auto_connect", True))
        ttk.Checkbutton(discovery, text="Connect automatically on launch", variable=self.auto_wifi, command=self.save_preferences).pack(side="left", padx=8)
        ttk.Label(discovery, text="Wi-Fi password").pack(side="left")
        self.wifi_password = tk.StringVar(value="12345678")
        ttk.Entry(discovery, textvariable=self.wifi_password, show="•", width=15).pack(side="left", padx=6)
        self.wifi_status = tk.StringVar(value="Ready to find MoLink / Jetion camera hotspots.")
        ttk.Label(discovery, textvariable=self.wifi_status).pack(side="left", padx=6)
        bar = ttk.Frame(root, padding=10)
        bar.pack(fill="x")
        ttk.Label(bar, text="Camera IP").pack(side="left")
        self.host = tk.StringVar(value="192.168.10.123")
        ttk.Entry(bar, textvariable=self.host, width=18).pack(side="left", padx=5)
        self.mode = tk.StringVar(value="UDP/JPEG")
        ttk.Combobox(bar, textvariable=self.mode, values=["UDP/JPEG", "TCP/JPEG"], state="readonly", width=11).pack(side="left", padx=5)
        ttk.Label(bar, text="Port").pack(side="left")
        self.port = tk.StringVar(value="8031")
        ttk.Entry(bar, textvariable=self.port, width=7).pack(side="left", padx=5)
        ttk.Button(bar, text="Connect", command=self.connect).pack(side="left", padx=4)
        ttk.Button(bar, text="Disconnect", command=self.disconnect).pack(side="left", padx=4)
        ttk.Button(bar, text="Try RTSP", command=self.rtsp).pack(side="left", padx=4)
        actions = ttk.Frame(root, padding=(10, 0, 10, 10))
        actions.pack(fill="x")
        ttk.Button(actions, text="Rotate 90°", command=self.rotate).pack(side="left")
        ttk.Button(actions, text="Save snapshot", command=self.snapshot).pack(side="left", padx=8)
        ttk.Button(actions, text="Mirror", command=self.toggle_mirror).pack(side="left", padx=4)
        ttk.Button(actions, text="Zoom", command=self.toggle_zoom).pack(side="left", padx=4)
        self.freeze_button = ttk.Button(actions, text="Freeze", command=self.freeze)
        self.freeze_button.pack(side="left", padx=4)
        self.record_button = ttk.Button(actions, text="Record MP4", command=self.record)
        self.record_button.pack(side="left", padx=4)
        ttk.Button(actions, text="Camera controls", command=self.open_controls).pack(side="left", padx=4)
        self.capture_status = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self.capture_status).pack(side="left", padx=8)
        self.canvas = tk.Label(root, background="#151719", foreground="#d0d4d8", text="Waiting for a camera connection")
        self.canvas.pack(fill="both", expand=True, padx=10)
        self.status = tk.StringVar(value="Ready. Connect using UDP/JPEG, port 8031.")
        ttk.Label(root, textvariable=self.status, padding=10, wraplength=950).pack(fill="x")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self.poll)

    def save_preferences(self):
        self.preferences["auto_connect"] = self.auto_wifi.get()
        self.settings_path.write_text(json.dumps(self.preferences, indent=2), encoding="utf-8")

    def find_camera(self):
        if self.finding:
            return
        self.finding = True
        self.find_stop = threading.Event()
        password = self.wifi_password.get()
        selected = self.preferences.get("last_ssid")
        self.wifi_status.set("Searching for camera hotspots…")
        def find():
            try:
                interfaces, networks = camera_wifi.scan()
                if not networks:
                    raise RuntimeError("No recognized camera hotspot found. Turn on the camera, then try again or connect manually.")
                connected = {i.get("ssid") for i in interfaces if i.get("state") == "connected"}
                networks.sort(key=lambda n: (n["ssid"] not in connected, n["ssid"] != selected, -n["signal"]))
                for network in networks:
                    if self.find_stop.is_set():
                        return
                    self.control_events.put(("wifi_status", "Joining " + network["ssid"] + "…"))
                    try:
                        camera_wifi.join(network, password, self.find_stop)
                        camera = camera_wifi.verify_camera()
                        self.control_events.put(("found", (network, camera)))
                        return
                    except (OSError, ValueError, RuntimeError) as exc:
                        self.control_events.put(("wifi_status", network["ssid"] + ": " + str(exc)))
                raise RuntimeError("No camera replied. Check the hotspot/password or use manual Connect.")
            except (OSError, ValueError, RuntimeError) as exc:
                self.control_events.put(("wifi_status", str(exc)))
            finally:
                self.control_events.put(("find_done", None))
        threading.Thread(target=find, daemon=True).start()

    def address(self):
        address = ipaddress.IPv4Address(self.host.get().strip())
        if not address.is_private or address.is_multicast or address.is_unspecified:
            raise ValueError("Enter the camera's local, unicast IPv4 address.")
        return str(address)

    def publish(self, session, kind, value):
        item = (session, kind, value)
        try:
            self.events.put_nowait(item)
        except queue.Full:
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            try:
                self.events.put_nowait(item)
            except queue.Full:
                pass

    def disconnect(self):
        self.stop.set()
        self.find_stop.set()
        self.session += 1
        self.status.set("Disconnected.")

    def connect(self):
        try:
            host, port = self.address(), int(self.port.get())
            if not 1 <= port <= 65535:
                raise ValueError("TCP port must be between 1 and 65535.")
        except ValueError as exc:
            messagebox.showerror("Connection settings", str(exc))
            return
        self.disconnect()
        self.stop = threading.Event()
        session, stop = self.session, self.stop
        self.status.set(f"Connecting to {host}:{port}…")
        self.worker = threading.Thread(target=self.receive, args=(session, stop, host, port, self.mode.get()), daemon=True)
        self.worker.start()

    def receive(self, session, stop, host, port, mode):
        udp = mode == "UDP/JPEG"
        parser, count, total = (UdpFrames() if udp else JpegFrames()), 0, 0
        last_frame = time.monotonic()
        try:
            transport = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if udp else socket.create_connection((host, port), timeout=4)
            with transport as connection:
                if udp:
                    connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
                    connection.connect((host, port))
                connection.settimeout(0.5)
                sent = 0
                self.publish(session, "status", "Waiting for decodable JPEG frames…")
                while not stop.is_set():
                    if udp and time.monotonic() - sent >= 0.6:
                        connection.send(struct.pack("<HH5I", 0x9999, 1, 0, 0, 0, 0, 0))
                        sent = time.monotonic()
                    if time.monotonic() - last_frame > 12:
                        raise TimeoutError(f"No decodable JPEG for 12 seconds ({total:,} bytes received). This protocol may differ.")
                    try:
                        data = connection.recv(65536)
                    except socket.timeout:
                        continue
                    if not data:
                        raise ConnectionError("Camera closed the connection.")
                    total += len(data)
                    for jpeg in parser.feed(data):
                        try:
                            with Image.open(io.BytesIO(jpeg)) as source:
                                if source.width * source.height > 20_000_000:
                                    raise ValueError("Image exceeds 20 megapixels.")
                                image = source.convert("RGB")
                        except (OSError, ValueError, Image.DecompressionBombError):
                            continue
                        count += 1
                        last_frame = time.monotonic()
                        self.publish(session, "frame", (image, count, jpeg))
        except (OSError, ValueError) as exc:
            if not stop.is_set():
                self.publish(session, "status", f"Stopped: {exc}")

    def poll(self):
        try:
            while True:
                kind, value = self.control_events.get_nowait()
                if kind == "wifi_status":
                    self.wifi_status.set(value)
                elif kind == "find_done":
                    self.finding = False
                elif kind == "found":
                    network, camera = value
                    if not self.find_stop.is_set():
                        self.preferences["last_ssid"] = network["ssid"]
                        self.save_preferences()
                        self.host.set(camera["host"])
                        self.port.set(str(camera["port"]))
                        self.mode.set(camera["mode"])
                        self.wifi_status.set(network["ssid"] + " — camera verified")
                        self.connect()
                elif kind == "control":
                    name, result = value
                    self.log_control(name + ": " + result)
                elif kind == "record_done":
                    self.capture_status.set(value)
                    self.record_button.configure(text="Record MP4")
        except queue.Empty:
            pass
        try:
            while True:
                session, kind, value = self.events.get_nowait()
                if session != self.session:
                    continue
                if kind == "frame":
                    image, count, jpeg = value
                    if self.recorder:
                        recorded = self.display_image(image, self.record_view)
                        encoded = io.BytesIO()
                        recorded.convert("RGB").save(encoded, format="JPEG", quality=95)
                        self.recorder.offer(encoded.getvalue(), recorded.size)
                    if not self.paused:
                        self.image = image
                        self.draw()
                    self.status.set(f"Receiving JPEG video — {image.width} × {image.height}; {count:,} decoded frames" + ("; display frozen" if self.paused else ""))
                else:
                    self.status.set(value)
        except queue.Empty:
            pass
        if self.recorder and self.recorder.finished.is_set():
            self.capture_status.set(self.recorder.error or "Recording saved: " + self.recorder.path)
            self.recorder = None
            self.record_button.configure(text="Record MP4")
        self.root.after(50, self.poll)

    def draw(self):
        if self.image is None:
            return
        image = self.display_image()
        image = ImageOps.contain(image, (max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())))
        self.preview = ImageTk.PhotoImage(image)
        self.canvas.configure(image=self.preview, text="")

    def display_image(self, image=None, settings=None):
        rotation, mirror, zoom = settings or (self.rotation, self.mirror, self.zoom)
        image = (self.image if image is None else image).rotate(rotation, expand=True)
        if mirror:
            image = ImageOps.mirror(image)
        if zoom > 1:
            w, h = image.size
            dx, dy = w / zoom, h / zoom
            image = image.crop(((w-dx)/2, (h-dy)/2, (w+dx)/2, (h+dy)/2))
        return image

    def toggle_mirror(self):
        self.mirror = not self.mirror
        self.draw()

    def toggle_zoom(self):
        self.zoom = {1: 2, 2: 4, 4: 1}[self.zoom]
        self.capture_status.set(f"Display zoom: {self.zoom}×")
        self.draw()

    def freeze(self):
        self.paused = not self.paused
        self.freeze_button.configure(text="Resume" if self.paused else "Freeze")

    def record(self):
        if self.recorder:
            self.recorder.stop()
            self.capture_status.set("Finishing recording…")
            return
        if self.image is None:
            messagebox.showinfo("Recording", "Receive a camera frame first.")
            return
        directory = data_dir() / "recordings"
        directory.mkdir(exist_ok=True)
        path = filedialog.asksaveasfilename(initialdir=str(directory), defaultextension=".mp4", filetypes=[("MP4 video", "*.mp4")])
        if path:
            try:
                self.record_view = (self.rotation, self.mirror, self.zoom)
                self.recorder = Recorder(path, self.display_image(settings=self.record_view).size)
                self.record_button.configure(text="Stop recording")
                self.capture_status.set(f"Recording the view at start: {self.zoom}× zoom, {self.rotation}° rotation, mirror {'on' if self.mirror else 'off'}…")
            except (OSError, RuntimeError) as exc:
                messagebox.showerror("Recording", str(exc))

    def rotate(self):
        self.rotation = (self.rotation + 90) % 360
        self.draw()

    def log_control(self, text):
        if hasattr(self, "control_log") and self.control_log.winfo_exists():
            self.control_log.insert("end", time.strftime("%H:%M:%S ") + text + "\n\n")
            self.control_log.see("end")

    def send_control(self, control, values=()):
        try:
            host = self.address()
            # Validate all parameters before launching the command thread.
            from camera_controls import encode
            encode(control, 1, values)
        except (ValueError, UnicodeError, IndexError) as exc:
            messagebox.showerror("Camera control", str(exc))
            return
        if control.kind not in ("query", "legacy_query"):
            disruptive = control.kind in ("ssid", "password", "stream_password", "legacy_action") or control.name in ("Wi-Fi channel", "Power off camera", "Factory reset", "Calibration", "Ageing mode")
            if disruptive or (control.name == "Set video format" and int(values[0]) != 1):
                if not messagebox.askokcancel(control.name, control.note + "\n\nSend this command now?", parent=self.control_window):
                    return
        self.log_control(control.name + ": sending…")
        def send():
            try:
                result = self.controls.execute(host, control, values)
                if control.kind in ("password", "stream_password"):
                    text = "Camera replied. Password details are hidden; verify the new connection."
                else:
                    text = json.dumps(result, ensure_ascii=False)
            except socket.timeout:
                text = "No reply — unsupported, unreachable, or disconnected. Command effect unconfirmed."
            except (OSError, ValueError) as exc:
                text = str(exc)
            self.control_events.put(("control", (control.name, text)))
        threading.Thread(target=send, daemon=True).start()

    def open_controls(self):
        if hasattr(self, "control_window") and self.control_window.winfo_exists():
            self.control_window.lift()
            return
        window = self.control_window = tk.Toplevel(self.root)
        window.title("EndoscopeViewerWIN — Camera controls")
        window.geometry("860x660")
        window.minsize(780, 570)
        tabs = ttk.Notebook(window)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)
        camera, info, network, advanced, compatibility = [ttk.Frame(tabs, padding=12) for _ in range(5)]
        for tab, label in zip((camera, info, network, advanced, compatibility), ("Camera", "Read information", "Wi-Fi", "Additional controls", "Compatibility")):
            tabs.add(tab, text=label)
        def spec(name, kind=None):
            return next(c for c in CONTROLS if c.name == name and (kind is None or c.kind == kind))
        def setting_row(parent, row, label, control, default="0", masked=False):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=7)
            value = tk.StringVar(value=default)
            ttk.Entry(parent, textvariable=value, width=34, show="•" if masked else "").grid(row=row, column=1, sticky="ew", padx=8)
            def send():
                try:
                    values = (value.get(),) if control.kind in ("ssid", "password", "stream_password") else (int(value.get(), 0),)
                    self.send_control(control, values)
                except ValueError:
                    messagebox.showerror("Camera value", "Enter a whole number (decimal or 0x hexadecimal).")
            ttk.Button(parent, text="Apply", command=send).grid(row=row, column=2)
            return value
        ttk.Label(camera, text="Controls remain available even when the connected hardware does not implement them.", wraplength=740).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Label(camera, text="Resolution / FPS").grid(row=1, column=0, sticky="w")
        resolution = tk.StringVar(value="640 x 480")
        ttk.Combobox(camera, textvariable=resolution, values=["640 x 480", "1280 x 720", "1600 x 1200", "1920 x 1080"], width=20).grid(row=1, column=1, sticky="w", padx=8)
        fps = tk.StringVar(value="30")
        ttk.Spinbox(camera, from_=1, to=120, textvariable=fps, width=5).grid(row=1, column=1, sticky="e", padx=8)
        def set_resolution():
            try:
                w, h = resolution.get().lower().replace(" ", "").split("x")
                self.send_control(spec("Set resolution"), (int(w), int(h), int(fps.get())))
            except ValueError:
                messagebox.showerror("Resolution", "Enter a resolution such as 640 x 480 and a numeric FPS.")
        ttk.Button(camera, text="Apply", command=set_resolution).grid(row=1, column=2)
        setting_row(camera, 2, "Video format (1 MJPEG, 4 YUYV)", spec("Set video format"), "1")
        setting_row(camera, 3, "Lighting / PWM", spec("Lighting / PWM"), "128")
        ttk.Button(camera, text="Switch camera / lens", command=lambda: self.send_control(spec("Switch camera / lens"))).grid(row=4, column=0, pady=12, sticky="w")
        ttk.Button(camera, text="Switch lamp", command=lambda: self.send_control(spec("Switch lamp"))).grid(row=4, column=1, pady=12, sticky="w")
        ttk.Label(camera, text="MJPEG playback is verified. Other video formats and model-specific controls may receive no response. Changing format can interrupt the picture; select MJPEG to restore it.", wraplength=740).grid(row=5, column=0, columnspan=3, sticky="w", pady=8)
        camera.columnconfigure(1, weight=1)
        queries = [c for c in CONTROLS if c.kind in ("query", "legacy_query")]
        query_name = tk.StringVar(value="Firmware version")
        ttk.Combobox(info, textvariable=query_name, values=[c.name for c in queries], state="readonly", width=38).pack(side="top", anchor="w", pady=8)
        ttk.Button(info, text="Read selected information", command=lambda: self.send_control(next(c for c in queries if c.name == query_name.get()))).pack(anchor="w", pady=8)
        ttk.Label(info, text="A reply confirms communication, not that every reported field has a known meaning. Results appear below. No automatic polling of unsupported controls.", wraplength=740).pack(anchor="w", pady=12)
        ttk.Label(network, text="These settings belong to the camera. Changes can disconnect its hotspot.").grid(row=0, column=0, columnspan=3, sticky="w", pady=8)
        setting_row(network, 1, "Hotspot name", spec("Wi-Fi name"), self.preferences.get("last_ssid", ""))
        setting_row(network, 2, "Hotspot password", spec("Wi-Fi password"), "", True)
        setting_row(network, 3, "Wi-Fi channel", spec("Wi-Fi channel"), "2")
        setting_row(network, 4, "Stream password", spec("Stream password"), "", True)
        ttk.Button(network, text="Clear hotspot password", command=lambda: self.send_control(spec("Clear Wi-Fi password"))).grid(row=5, column=0, pady=12)
        ttk.Button(network, text="Reboot camera", command=lambda: self.send_control(spec("Reboot camera"))).grid(row=5, column=1, pady=12)
        ttk.Button(network, text="Power off camera", command=lambda: self.send_control(spec("Power off camera"))).grid(row=5, column=2, pady=12)
        network.columnconfigure(1, weight=1)
        labels = {f"{c.name} — {'read' if c.kind in ('query', 'legacy_query') else 'set/action'}": c for c in CONTROLS}
        selected = tk.StringVar(value=next(iter(labels)))
        ttk.Combobox(advanced, textvariable=selected, values=list(labels), state="readonly", width=60).pack(anchor="w", pady=8)
        values = [tk.StringVar(value="0") for _ in range(3)]
        fields = ttk.Frame(advanced)
        fields.pack(anchor="w", pady=8)
        for i, value in enumerate(values):
            ttk.Label(fields, text=f"Value {i+1}").grid(row=0, column=i, padx=4)
            ttk.Entry(fields, textvariable=value, width=16).grid(row=1, column=i, padx=4)
        note = tk.StringVar()
        ttk.Label(advanced, textvariable=note, wraplength=740).pack(anchor="w", pady=10)
        def explain(*args):
            control = labels[selected.get()]
            count = 3 if control.kind == "resolution" else 1 if control.kind in ("integer", "ssid", "password", "stream_password") else 0
            note.set(control.note + ("\nEnter width, height and FPS." if count == 3 else "\nEnter a device-specific integer in Value 1." if control.kind == "integer" else "\nNo values required." if count == 0 else "\nUse the Wi-Fi tab for masked password entry."))
            for index, widget in enumerate(fields.winfo_children()):
                if isinstance(widget, ttk.Entry):
                    widget.configure(show="•" if control.kind in ("password", "stream_password") else "")
        def run_selected():
            control = labels[selected.get()]
            try:
                if control.kind == "resolution":
                    arguments = tuple(int(v.get(), 0) for v in values)
                elif control.kind == "integer":
                    arguments = (int(values[0].get(), 0),)
                elif control.kind in ("ssid", "password", "stream_password"):
                    arguments = (values[0].get(),)
                else:
                    arguments = ()
                self.send_control(control, arguments)
            except ValueError:
                messagebox.showerror("Camera value", "Enter valid numeric values for this control.")
        selected.trace_add("write", explain)
        explain()
        ttk.Button(advanced, text="Send selected control", command=run_selected).pack(anchor="w", pady=8)
        ttk.Label(advanced, text="Lighting, focus and motor values are model-specific. The app provides no universal range. No command is sent just by selecting it.", wraplength=740).pack(anchor="w", pady=8)
        ttk.Label(compatibility, text="MoLink / Jetion protocol viewer\n\nThe Android app recognizes Jetion SSIDs, includes an Eloam name-handling branch, and contains Series 3 (UDP 8031) and Series 5 (TCP 7060) paths. It does not contain a verified retail-brand compatibility list.\n\nVerified here: your LIMINK camera, firmware response 100, UDP/JPEG 640 × 480.\n\nAutomatic hotspot search recognizes Jetion, Eloam, LIMINK, NIDAGE, MoLink and HD WiFi names as candidates. A camera response is required before connecting the viewer. These prefixes are discovery hints, not compatibility guarantees.\n\nFactory board-data writing, calibration and firmware flashing are not viewer controls. The APK contains those service functions, but their presence does not establish a usable recovery/update procedure.", wraplength=740, justify="left").pack(anchor="w")
        self.control_log = scrolledtext.ScrolledText(window, height=8, wrap="word", font=("Consolas", 9))
        self.control_log.pack(fill="both", padx=10, pady=(0, 10))
        self.log_control("Ready. No camera-setting commands have been sent.")

    def snapshot(self):
        if self.image is None:
            messagebox.showinfo("Snapshot", "Connect and receive a frame first.")
            return
        directory = data_dir() / "captures"
        directory.mkdir(exist_ok=True)
        path = filedialog.asksaveasfilename(initialdir=str(directory), defaultextension=".png", filetypes=[("PNG image", "*.png")])
        if path:
            try:
                self.display_image().save(path)
                self.capture_status.set(f"Saved {path}")
            except OSError as exc:
                messagebox.showerror("Snapshot", str(exc))

    def rtsp(self):
        try:
            host = self.address()
            player = shutil.which("ffplay")
            if not player:
                raise ValueError("Install FFmpeg with ffplay on PATH to use the optional RTSP diagnostic.")
            self.disconnect()
            url = f"rtsp://{host}:8554/live/camera"
            log = open(data_dir() / "rtsp-test.log", "w", encoding="utf-8")
            try:
                process = subprocess.Popen([player, "-window_title", "HD WiFi RTSP test", "-rtsp_transport", "tcp", "-rw_timeout", "5000000", "-i", url], stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            finally:
                log.close()
            self.players.append(process)
            self.status.set("RTSP test opened in a separate player. A blank/closed player is not a successful connection; details are in rtsp-test.log.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("RTSP test", str(exc))

    def close(self):
        self.stop.set()
        self.find_stop.set()
        if self.recorder:
            self.recorder.stop()
            self.capture_status.set("Finishing recording before closing…")
            if not self.recorder.finished.is_set():
                self.root.after(100, self.close)
                return
        for process in self.players:
            if process.poll() is None:
                process.terminate()
        self.root.destroy()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        # Packaging check: no network, camera commands or user preferences loaded.
        output = Path(sys.argv[sys.argv.index("--self-test") + 1])
        check_root = tk.Tk()
        check_root.withdraw()
        check_photo = ImageTk.PhotoImage(Image.new("RGB", (8, 8), "green"), master=check_root)
        result = {"version": VERSION, "frozen": bool(getattr(sys, "frozen", False)),
                  "tk": str(check_root.tk.call("info", "patchlevel")),
                  "image_width": check_photo.width(), "data_dir": str(data_dir())}
        check_root.destroy()
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        sys.exit(0)
    root = tk.Tk()
    viewer = Viewer(root)
    if "--demo" in sys.argv:
        from demo import demo_image
        viewer.image = demo_image()
        viewer.host.set("192.168.10.123")
        viewer.wifi_status.set("DEMO — no network connection")
        viewer.status.set("DEMO IMAGE — not live camera footage. No plumbing was harmed in the making of this screenshot.")
        root.after(400, viewer.draw)
    elif "--no-auto" not in sys.argv and viewer.auto_wifi.get():
        root.after(350, viewer.find_camera)
    elif "--connect" in sys.argv:
        root.after(250, viewer.connect)
    root.mainloop()
