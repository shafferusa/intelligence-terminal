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
            self.assertEqual(plist["ProgramArguments"][1:], ["-m", "finsim", "serve", "--db", "/tmp/finsim-home/finsim.db", "--port", "8123"])
            self.assertTrue(plist["RunAtLoad"] and plist["KeepAlive"])
            launcher = next(v for k, v in mac.items() if k.endswith(os.path.join("MacOS", "FinSim")))
            self.assertIn("-m finsim open", launcher)
            info = plistlib.loads(next(v for k, v in mac.items() if k.endswith("Info.plist")))
            self.assertEqual(info["CFBundleExecutable"], "FinSim")

            linux = app.launcher_files("linux", base="/tmp/finsim-home")
            unit = next(v for k, v in linux.items() if k.endswith("finsim.service"))
            self.assertIn("--port 8123", unit)
            self.assertNotIn("--host", unit, "the server resolves its reach from the phone setting at start")
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
            self.assertIn("--port 8123", server)
            self.assertIn(", 0, False", server, "hidden window")
            shortcuts = win[os.path.join(r"C:\Users\me\.finsim", "make-shortcuts.vbs")]
            self.assertIn('SpecialFolders("Desktop")', shortcuts)
            self.assertIn("finsim-open.vbs", shortcuts)
        for files in (mac, linux, win):
            for content in files.values():
                if isinstance(content, str):
                    self.assertNotIn("0.0.0.0", content, "never bound to every interface")


class PhoneSettingTest(unittest.TestCase):
    def test_key_is_generated_once_and_the_host_follows_the_setting(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"FINSIM_HOME": home, "FINSIM_PORT": "8124", "FINSIM_DB": ""}):
                self.assertEqual(app.resolve_host(), "127.0.0.1", "local only until asked")
                k1 = app.access_key()
                self.assertGreaterEqual(len(k1), 16)
                self.assertEqual(app.access_key(), k1, "stable across calls")
                if os.name == "posix":
                    self.assertEqual(os.stat(os.path.join(home, "access-key")).st_mode & 0o777, 0o600)
                app.save_config({"phone": True})
                self.assertEqual(app.resolve_host(), "0.0.0.0")
                for link in app.phone_links():
                    self.assertIn(f":8124/?key={k1}", link)
                app.save_config({"phone": False})
                self.assertEqual(app.resolve_host(), "127.0.0.1")


class SavesSurviveTest(unittest.TestCase):
    def test_legacy_saves_are_adopted_and_backed_up(self):
        from finsim.store import EventStore
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as legacy_dir:
            legacy = os.path.join(legacy_dir, "finsim.db")
            st = EventStore(legacy)
            st.create_world("W-old", "my career")
            st.close()
            with mock.patch.dict(os.environ, {"FINSIM_HOME": home, "FINSIM_DB": ""}), mock.patch.object(app, "legacy_db_path", lambda: legacy):
                self.assertEqual(app.adopt_legacy_db(), legacy, "first run copies the old database")
                self.assertEqual([w["name"] for w in EventStore(app.db_path()).list_worlds()], ["my career"])
                self.assertTrue(os.path.exists(legacy), "the original is left where it was")
                self.assertIsNone(app.adopt_legacy_db(), "never overwrites an existing app database")
            # the server-start snapshot: a consistent copy, newest ten kept
            bdir = os.path.join(home, "backups")
            snaps = [EventStore.snapshot(os.path.join(home, "finsim.db"), bdir, keep=2) for _ in range(1)]
            self.assertTrue(snaps[0] and os.path.exists(snaps[0]))
            self.assertEqual([w["name"] for w in EventStore(snaps[0]).list_worlds()], ["my career"])
            for i in range(3):
                os.rename(snaps[0] if i == 0 else extra, extra := os.path.join(bdir, f"finsim-2000010{i}-000000.db"))
            EventStore.snapshot(os.path.join(home, "finsim.db"), bdir, keep=2)
            kept = sorted(f for f in os.listdir(bdir) if f.endswith(".db"))
            self.assertEqual(len(kept), 2, "older snapshots are pruned")
            self.assertIsNone(EventStore.snapshot(":memory:", bdir))


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
