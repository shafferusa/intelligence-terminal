"""The desktop-app wrapper: launcher artifacts for every platform, and a real start / health / shutdown round trip."""
import os
import plistlib
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finsim import app  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LauncherFilesTest(unittest.TestCase):
    def test_every_platform_gets_a_service_and_a_launcher_bound_to_localhost(self):
        with mock.patch.dict(os.environ, {"FINSIM_HOME": "/tmp/finsim-home", "FINSIM_PORT": "8123", "FINSIM_DB": ""}):
            mac = app.launcher_files("mac", base="/tmp/finsim-home")
            plist = plistlib.loads(next(v for k, v in mac.items() if "LaunchAgents" in k))
            self.assertEqual(plist["ProgramArguments"][1:], ["-m", "finsim", "serve", "--db", "/tmp/finsim-home/finsim.db", "--host", "127.0.0.1", "--port", "8123"])
            self.assertTrue(plist["RunAtLoad"] and plist["KeepAlive"])
            launcher = next(v for k, v in mac.items() if k.endswith(os.path.join("MacOS", "FinSim")))
            self.assertIn("-m finsim open", launcher)
            info = plistlib.loads(next(v for k, v in mac.items() if k.endswith("Info.plist")))
            self.assertEqual(info["CFBundleExecutable"], "FinSim")

            linux = app.launcher_files("linux", base="/tmp/finsim-home")
            unit = next(v for k, v in linux.items() if k.endswith("finsim.service"))
            self.assertIn("--host 127.0.0.1 --port 8123", unit)
            self.assertIn("WantedBy=default.target", unit)
            desktop = next(v for k, v in linux.items() if k.endswith("finsim.desktop"))
            self.assertIn("-m finsim open", desktop)
            self.assertIn("Terminal=false", desktop)

            win = app.launcher_files("windows", base=r"C:\Users\me\.finsim")
            ico = win[os.path.join(r"C:\Users\me\.finsim", "finsim.ico")]
            self.assertEqual(ico[:6], b"\x00\x00\x01\x00\x01\x00", "ICO header: one image")
            self.assertEqual(ico[6:8], b"\x00\x00", "256px is encoded as 0")
            self.assertIn(b"\x89PNG", ico[22:30])
            server = win[os.path.join(r"C:\Users\me\.finsim", "finsim-server.vbs")]
            self.assertIn("--host 127.0.0.1 --port 8123", server)
            self.assertIn(", 0, False", server, "hidden window")
            shortcuts = win[os.path.join(r"C:\Users\me\.finsim", "make-shortcuts.vbs")]
            self.assertIn('SpecialFolders("Desktop")', shortcuts)
            self.assertIn("finsim-open.vbs", shortcuts)
        for files in (mac, linux, win):
            for content in files.values():
                if isinstance(content, str):
                    self.assertNotIn("0.0.0.0", content, "never bound to every interface")


class RoundTripTest(unittest.TestCase):
    def test_open_starts_the_server_once_and_shutdown_stops_it(self):
        port = free_port()
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"FINSIM_HOME": home, "FINSIM_PORT": str(port), "FINSIM_DB": ""}):
                self.assertIsNone(app.health())
                opened = []
                with mock.patch.object(app, "_browser_candidates", lambda: []), mock.patch("webbrowser.open", lambda u: opened.append(u)):
                    self.assertEqual(app.cmd_open(), 0)
                    self.assertEqual(opened, [f"http://127.0.0.1:{port}/"])
                    h = app.health()
                    self.assertIsNotNone(h)
                    self.assertEqual(h["status"], "ok")
                    uptime = h["uptime_s"]
                    self.assertEqual(app.cmd_open(), 0, "a second open reuses the running server")
                    self.assertGreaterEqual(app.health()["uptime_s"], uptime, "same process, not a restart")
                self.assertTrue(os.path.exists(os.path.join(home, "finsim.db")))
                self.assertEqual(app.cmd_status(), 0)
                app._stop_server()
                for _ in range(40):
                    if app.health() is None:
                        break
                    time.sleep(0.25)
                self.assertIsNone(app.health(), "the shutdown endpoint stops the server")
                self.assertEqual(app.cmd_status(), 1)


if __name__ == "__main__":
    unittest.main()
