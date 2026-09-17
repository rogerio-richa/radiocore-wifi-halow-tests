import json
import os
import pty
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.test_halow_monitor import VALID


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class MonitorProcessIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.assets = root / "monitor"
        self.assets.mkdir()
        (self.assets / "index.html").write_text("<h1>integration dashboard</h1>")
        (self.assets / "dashboard.js").write_text("console.log('integration')")
        (self.assets / "dashboard.css").write_text("body { color: black; }")
        (self.assets / "video.html").write_text("<h1>integration video</h1>")
        (self.assets / "video.js").write_text("console.log('video')")
        (self.assets / "video.css").write_text("body { color: black; }")
        self.output = root / "measurement.csv"
        self.master_fd, self.slave_fd = pty.openpty()
        self.slave_path = os.ttyname(self.slave_fd)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.base = "http://127.0.0.1:%d" % self.port
        self.process = None

    def tearDown(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.communicate(timeout=5)
        for descriptor in (self.master_fd, self.slave_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self.directory.cleanup()

    def wait_for_http(self, path, timeout=5.0):
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                return urllib.request.urlopen(self.base + path, timeout=0.5)
            except (OSError, urllib.error.URLError) as error:
                last_error = error
                time.sleep(0.05)
        self.fail("HTTP service did not start: %s" % last_error)

    def wait_for_sample(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with urllib.request.urlopen(self.base + "/api/latest", timeout=0.5) as response:
                payload = json.load(response)
            if payload["latest"] is not None:
                return payload
            time.sleep(0.05)
        self.fail("telemetry sample did not reach /api/latest")

    def test_cli_serves_serial_sse_and_shuts_down_cleanly(self):
        command = [
            sys.executable,
            str(PROJECT_ROOT / "halow_monitor.py"),
            "--serial",
            self.slave_path,
            "--bind",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--output",
            str(self.output),
            "--assets",
            str(self.assets),
        ]
        self.process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        with self.wait_for_http("/events") as events:
            self.assertEqual(events.headers.get_content_type(), "text/event-stream")
            self.assertEqual(events.readline().decode().strip(), "event: status")
            events.readline()
            events.readline()
            os.write(self.master_fd, VALID.encode("utf-8") + b"\r\n")
            self.assertEqual(events.readline().decode().strip(), "event: sample")

        payload = self.wait_for_sample()
        self.assertEqual(payload["status"], "live")
        self.assertEqual(payload["latest"]["mcs"], 3)

        self.process.send_signal(signal.SIGTERM)
        stdout, stderr = self.process.communicate(timeout=5)
        self.assertEqual(self.process.returncode, 0, stderr)
        self.assertNotIn("Traceback", stdout + stderr)
        self.assertIn("delivery_pct", self.output.read_text())
        self.assertIn(",3,", self.output.read_text())

        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", self.port))
