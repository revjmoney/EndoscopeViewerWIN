"""Nonblocking MJPEG-to-MP4 recording through the existing FFmpeg installation."""
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time


class Recorder:
    def __init__(self, path, size):
        encoder = shutil.which("ffmpeg")
        if not encoder:
            raise RuntimeError("FFmpeg was not found on this PC.")
        self.size = size
        self.path = path
        self.frames = queue.Queue(maxsize=40)
        self.error = None
        self.dropped = 0
        self.stopped = threading.Event()
        self.finished = threading.Event()
        self.log = open(Path(path).with_suffix(".encoder.log"), "w", encoding="utf-8")
        self.process = subprocess.Popen([encoder, "-hide_banner", "-loglevel", "warning", "-y", "-f", "image2pipe", "-framerate", "15", "-vcodec", "mjpeg", "-i", "pipe:0", "-an", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.thread = threading.Thread(target=self.write, daemon=True)
        self.thread.start()

    def offer(self, jpeg, size):
        if self.stopped.is_set():
            return
        if size != self.size:
            self.error = "Camera resolution changed; recording was stopped."
            self.stop()
            return
        try:
            self.frames.put_nowait(jpeg)
        except queue.Full:
            self.dropped += 1

    def stop(self):
        self.stopped.set()

    def write(self):
        try:
            latest = None
            tick = time.monotonic()
            while not self.stopped.is_set():
                try:
                    while True:
                        latest = self.frames.get_nowait()
                except queue.Empty:
                    pass
                if latest is not None:
                    self.process.stdin.write(latest)
                    self.process.stdin.flush()
                tick += 1 / 15
                if tick < time.monotonic() - 1:
                    tick = time.monotonic()
                self.stopped.wait(max(0, tick - time.monotonic()))
            self.process.stdin.close()
            if self.process.wait(timeout=15) != 0:
                self.error = "Encoder failed; see the .encoder.log beside the recording."
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.error = str(exc)
            if self.process.poll() is None:
                self.process.kill()
        finally:
            self.stopped.set()
            self.log.close()
            self.finished.set()
