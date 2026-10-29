"""WP-14 tests: launcher files, install / uninstall plans, status text, stop + liveness with a fake OpenProcess, the
commands with injected collaborators (TERMINAL_DESIGN.md §18, §19.12; TERMINAL_WORKPLAN.md WP-14), and from FINSIM1_PLAN
P7 (1.0.6): ``legacy`` gone (UI-19), the ``import`` dispatch (C18) and the no-command hint (STARTUP-09).  The launch,
two-code-tree, shortcut and README tests are in ``test_terminal_launch.py``.

Nothing here touches the network, port 8765, the real Startup folder or ``~/.finsim``: ``FINSIM_HOME`` /
``FINSIM_TERMINAL_DB`` point at a temp folder, the loopback calls go through ``FakeHttp``, process liveness through
``FakeProcessApi``, external commands through a recording ``run``, and the server is never started (``starter`` fakes).
The one real socket test binds port 0.  ``install`` runs here from the repo's git working tree, so its working-tree
guard is injected (``tree_check``); the guard itself is tested in ``test_terminal_launch.py``.  Every expected value is
worked by hand in a comment.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from finsim.terminal import APP_ID, VERSION, config
from finsim.terminal import app

SHA_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"   # sha256(b"abc"), the FIPS 180-2 example
NO_GIT = lambda tree: None   # install runs here from the repo's git working tree; its STARTUP-04 guard is tested in test_terminal_launch
OLD_ENGINE = {"status": "ok", "engine_version": "0.11.0", "save_version": 3, "uptime_s": 61.0}   # no "app" key
CAPTURE_GUARD = re.compile(r"(^|\s)-m\s+finsim\s+serve\s.*--port\s+8765(\s|$)")                 # TERMINAL_DECISIONS §17


def terminal_health(version: str = VERSION, **over) -> dict:
    h = {"app": APP_ID, "version": version, "schema": 1, "pid": 4242, "uptime_s": 7383, "started_ts": "2026-10-20T19:00:00-04:00",
         "db_path": "", "db_size": 0, "state_version": 17, "offline": False,
         "integrity": {"ok": True, "hold": [], "checked_at": "2026-10-20T19:00:01-04:00", "problems": []},
         "providers": {"yahoo": {"ok": True}}, "workers": {"quotes": {"alive": True, "last_tick_ts": "t", "errors_1h": 0}},
         "lifecycle": {"due": "2026-10-20T19:30:00-04:00", "failing": []}, "attention_open": 0}
    h.update(over)
    return h



TERMINAL_VERSION = importlib.import_module("finsim.terminal").VERSION   # the code's version, so a version bump never breaks these tests

class FakeHttp:
    """Stands in for ``app.Http``: ``health()`` returns ``current``; after ``shutdown()`` it answers ``stop_after`` more
    times and then None (``stop_after=None`` = never stops).  ``on_shutdown`` lets a test simulate side effects."""

    def __init__(self, health=None, *, stop_after=0, shutdown_ok=True, on_shutdown=None, port=8765):
        self.base = f"http://127.0.0.1:{port}/"
        self.current = health
        self.stop_after = stop_after
        self.shutdown_ok = shutdown_ok
        self.on_shutdown = on_shutdown
        self.shutdowns = 0
        self.health_calls = 0

    def health(self, timeout=1.0):
        self.health_calls += 1
        if self.shutdowns and self.stop_after is not None:
            if self.stop_after <= 0:
                return None
            self.stop_after -= 1
        return self.current

    def shutdown(self, timeout=3.0):
        self.shutdowns += 1
        if self.on_shutdown:
            self.on_shutdown()
        return self.shutdown_ok


class FakeProcessApi:
    """A fake ``OpenProcess``: ``procs`` maps pid -> (exit code, image path); any other pid cannot be opened."""

    def __init__(self, procs):
        self.procs = dict(procs)
        self.opened, self.closed = [], []

    def open(self, pid):
        if pid in self.procs:
            self.opened.append(pid)
            return ("handle", pid)
        return None

    def exit_code(self, handle):
        return self.procs[handle[1]][0]

    def image(self, handle):
        return self.procs[handle[1]][1]

    def close(self, handle):
        self.closed.append(handle[1])


class FakeTime:
    """A clock that only advances when something sleeps, so waits run instantly and deterministically."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def completed(cmd, rc=0, stdout=""):
    return subprocess.CompletedProcess(list(cmd), rc, stdout=stdout, stderr="")


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-app-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self._env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV, config.PORT_ENV)}
        os.environ[config.HOME_ENV] = self.home
        os.environ[config.DB_ENV] = os.path.join(self.home, "terminal.db")
        os.environ.pop(config.PORT_ENV, None)      # the VBS assertions below rely on the default port 8765
        self.out = []

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def say(self, line):
        self.out.append(line)

    def text(self):
        return "\n".join(self.out)

    def write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(path, mode) as f:
            f.write(data)
        return path

    def read(self, path, binary=False):
        with open(path, "rb" if binary else "r", encoding=None if binary else "utf-8") as f:
            return f.read()

    def folders(self):
        f = tuple(os.path.join(self.tmp, n) for n in ("startup", "desktop", "programs"))
        for d in f:
            os.makedirs(d, exist_ok=True)
        return f


# ============================================================================================ launcher files (pure)
class LauncherFilesTests(AppTestCase):
    HOME = r"C:\Users\logan\.finsim"
    REPO = r"C:\Users\logan\intelligence-terminal\finsim"
    PYW = r"C:\Users\logan\AppData\Local\Programs\Python\Python312\pythonw.exe"
    PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 10      # 8 + 10 = 18 bytes

    def test_launcher_files_are_pure_ascii_and_carry_marker_shutdown_and_serve_line(self):
        files = app.launcher_files(self.HOME, self.PYW, self.REPO, 8765, icon_png=self.PNG)
        self.assertEqual({os.path.join(self.HOME, "FinSim Server.vbs"), os.path.join(self.HOME, "FinSim Terminal.vbs"),
                          os.path.join(self.HOME, "finsim.ico")}, set(files))
        server = files[os.path.join(self.HOME, "FinSim Server.vbs")]
        self.assertIsInstance(server, str)
        server.encode("ascii")                                             # ASCII only (WSH reads .vbs as ANSI)
        self.assertIn('InStr(txt, """finsim-terminal""") > 0', server)    # the marker check
        self.assertIn('http.Open "POST", "http://127.0.0.1:8765/api/shutdown"', server)   # the foreign-server shutdown
        self.assertIn('http.Send "{}"', server)
        self.assertIn('If state = "other" Then', server)
        self.assertIn('http://127.0.0.1:8765/api/health', server)
        self.assertIn('sh.Run """" & py & """ -m finsim serve --port 8765", 0, False', server)
        run_line = re.search(r'^sh\.Run """" & py & """( -m finsim serve --port \d+)", 0, False$', server, re.M)
        self.assertIsNotNone(run_line)
        command_line = f'"{self.PYW}"' + run_line.group(1)                 # what WScript.Shell.Run actually launches
        self.assertEqual(f'"{self.PYW}" -m finsim serve --port 8765', command_line)
        self.assertRegex(command_line, CAPTURE_GUARD)                      # the shared-machine guard exempts this process
        self.assertIn(f'py = "{self.PYW}"', server)
        self.assertIn(f'repo = "{self.REPO}"', server)
        self.assertIn(f'home = "{self.HOME}"', server)
        self.assertIn('fso.FileExists(repo & "\\finsim\\terminal\\__init__.py")', server)
        self.assertIn('sh.Environment("Process")("FINSIM_HOME") = home', server)
        self.assertTrue(server.startswith("' FinSim Paper Terminal"))
        for placeholder in ("{PYTHONW}", "{REPO_FINSIM_DIR}", "{FINSIM_HOME}", "{PORT}"):
            self.assertNotIn(placeholder, server)
        terminal = files[os.path.join(self.HOME, "FinSim Terminal.vbs")]
        terminal.encode("ascii")
        self.assertIn('sh.Run """" & py & """ -m finsim open", 0, False', terminal)
        self.assertNotIn("serve", terminal.split("Option Explicit")[1])    # the opener never starts a server itself
        # pure: the same inputs give the same files; another port moves every URL and the serve line
        self.assertEqual(files, app.launcher_files(self.HOME, self.PYW, self.REPO, 8765, icon_png=self.PNG))
        other = app.launcher_files(self.HOME, self.PYW, self.REPO, 8777, icon_png=self.PNG)[os.path.join(self.HOME, "FinSim Server.vbs")]
        self.assertIn("-m finsim serve --port 8777", other)
        self.assertIn("http://127.0.0.1:8777/api/health", other)
        self.assertNotIn("8765", other)

    def test_quotes_in_paths_are_doubled_and_non_ascii_paths_are_refused(self):
        files = app.launcher_files(r'C:\Us"er\.finsim', self.PYW, self.REPO, 8765, icon_png=self.PNG)
        server = files[os.path.join(r'C:\Us"er\.finsim', "FinSim Server.vbs")]
        self.assertIn('home = "C:\\Us""er\\.finsim"', server)              # VBScript doubles a quote inside a string
        with self.assertRaises(ValueError):
            app.launcher_files("C:\\Users\\Jos\u00e9\\.finsim", self.PYW, self.REPO, 8765, icon_png=self.PNG)

    def test_icon_wraps_the_png_in_a_single_image_ico(self):
        ico = app.launcher_files(self.HOME, self.PYW, self.REPO, 8765, icon_png=self.PNG)[os.path.join(self.HOME, "finsim.ico")]
        self.assertIsInstance(ico, bytes)
        # ICONDIR: reserved 0, type 1, count 1 = 00 00 01 00 01 00; ICONDIRENTRY: w 0 (=256), h 0, colours 0, reserved 0,
        # planes 1 (01 00), bpp 32 (20 00), size 18 (12 00 00 00), offset 6 + 16 = 22 (16 00 00 00); then the PNG as-is
        self.assertEqual(b"\x00\x00\x01\x00\x01\x00", ico[:6])
        self.assertEqual(b"\x00\x00\x00\x00\x01\x00\x20\x00\x12\x00\x00\x00\x16\x00\x00\x00", ico[6:22])
        self.assertEqual(self.PNG, ico[22:])
        self.assertEqual(22 + 18, len(ico))

    def test_shortcut_script_targets_the_terminal_vbs_in_each_non_empty_folder(self):
        desktop, programs = r"C:\Users\logan\OneDrive\Desktop", r"C:\Users\logan\AppData\Roaming\Microsoft\Windows\Start Menu\Programs"
        script = app.shortcut_script(self.HOME, [desktop, programs], self.REPO)
        script.encode("ascii")
        self.assertIn(f'For Each folder In Array("{desktop}", "{programs}")', script)
        self.assertIn('Set lnk = sh.CreateShortcut(folder & "\\FinSim.lnk")', script)
        self.assertIn('lnk.TargetPath = "wscript.exe"', script)
        self.assertIn(f'lnk.Arguments = """{self.HOME}\\FinSim Terminal.vbs"""', script)
        self.assertIn(f'lnk.IconLocation = "{self.HOME}\\finsim.ico"', script)
        self.assertIn(f'lnk.WorkingDirectory = "{self.REPO}"', script)
        self.assertIn(f'Array("{programs}")', app.shortcut_script(self.HOME, ["", programs], self.REPO))   # empty = no desktop


# ======================================================================================================= plans (pure)
class PlanTests(AppTestCase):
    def test_install_plan_order_lists_the_old_task_and_files(self):
        home, startup, desktop, programs = r"C:\h", r"C:\s", r"C:\d", r"C:\p"
        plan = app.install_plan(home, startup, desktop, programs, 8765, "20261020-210000")
        ops = [op for op, _ in plan]
        # 1 baseline + 1 stop + 1 task + 4 backups (Startup entry + 3 old files) + 3 removes + 3 writes + copy + shortcuts
        # + start + status + sequence + check = 19 steps, in exactly this order (§18)
        self.assertEqual(["sha256_baseline", "stop_server", "delete_task", "backup", "backup", "backup", "backup",
                          "remove", "remove", "remove", "write", "write", "write", "copy", "shortcuts", "start", "status",
                          "sequence", "sha256_check"], ops)
        self.assertEqual(19, len(plan))
        self.assertEqual(("sha256_baseline", r"C:\h\finsim.db"), plan[0])
        self.assertEqual(("stop_server", 8765), plan[1])
        self.assertEqual(("delete_task", "FinSim Server"), plan[2])
        self.assertEqual(("backup", (r"C:\s\FinSim Server.vbs", r"C:\h\backups\Startup FinSim Server.vbs.20261020-210000.bak")), plan[3])
        self.assertEqual(("backup", (r"C:\h\finsim-server.vbs", r"C:\h\backups\finsim-server.vbs.20261020-210000.bak")), plan[4])
        self.assertEqual([r"C:\h\finsim-server.vbs", r"C:\h\finsim-open.vbs", r"C:\h\make-shortcuts.vbs"],
                         [t for op, t in plan if op == "remove"])
        self.assertEqual([r"C:\h\FinSim Server.vbs", r"C:\h\FinSim Terminal.vbs", r"C:\h\finsim.ico"],
                         [t for op, t in plan if op == "write"])
        self.assertEqual(("copy", (r"C:\h\FinSim Server.vbs", r"C:\s\FinSim Server.vbs")), plan[13])
        self.assertEqual(("shortcuts", (r"C:\d", r"C:\p")), plan[14])
        self.assertEqual(("start", r"C:\h\FinSim Server.vbs"), plan[15])
        self.assertEqual(("sha256_check", r"C:\h\finsim.db"), plan[-1])
        # no Desktop folder (known-folder lookup failed) -> only the Start Menu shortcut
        self.assertEqual(("shortcuts", (r"C:\p",)), app.install_plan(home, startup, "", programs, 8765, "x")[14])

    def test_uninstall_plan_never_names_finsim_db(self):
        home, startup, desktop, programs = r"C:\h", r"C:\s", r"C:\d", r"C:\p"
        plan = app.uninstall_plan(home, startup, desktop, programs, 8765)
        # 6 removes (Startup entry, 2 shortcuts, 2 VBS, icon) + rmtree + delete_task + stop_terminal = 9 steps
        self.assertEqual(["remove"] * 6 + ["rmtree", "delete_task", "stop_terminal"], [op for op, _ in plan])
        self.assertEqual([r"C:\s\FinSim Server.vbs", r"C:\d\FinSim.lnk", r"C:\p\FinSim.lnk", r"C:\h\FinSim Server.vbs",
                          r"C:\h\FinSim Terminal.vbs", r"C:\h\finsim.ico"], [t for op, t in plan if op == "remove"])
        self.assertEqual(("rmtree", r"C:\h\terminal-window-profile"), plan[6])
        self.assertEqual(("stop_terminal", 8765), plan[8])
        self.assertFalse(any("finsim.db" in str(t) for _, t in plan))
        purge = app.uninstall_plan(home, startup, desktop, programs, 8765, purge=True, db=r"C:\h\terminal.db")
        self.assertEqual(10, len(purge))
        self.assertEqual(("purge", r"C:\h\terminal.db"), purge[-1])
        with self.assertRaises(ValueError):
            app.uninstall_plan(home, startup, desktop, programs, 8765, purge=True, db=r"C:\h\finsim.db")

    def test_legacy_is_gone_from_the_cli_and_nothing_is_deleted(self):
        # UI-19 / owner question Q8: the retired game's archive view left the command line in 1.0.6; its code stays
        self.assertNotIn("legacy", app.build_parser().format_help())
        for name in ("cmd_legacy", "legacy_plan", "LEGACY_DIR", "LEGACY_VIEW_DB"):
            self.assertFalse(hasattr(app, name), name)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            app.parse_args(["legacy"])                                     # argparse: invalid choice
        self.assertEqual(2, cm.exception.code)
        old = self.write(os.path.join(self.home, "finsim.db"), b"abc")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(2, app.main(["legacy", "--refresh"]))
        self.assertIn("`legacy` was removed in 1.0.6", buf.getvalue())
        self.assertIn("Nothing was deleted", buf.getvalue())
        self.assertEqual(SHA_ABC, app.sha256_file(old))                    # finsim.db untouched
        self.assertFalse(os.path.exists(os.path.join(self.home, "legacy")))   # no archive copy is made any more
        self.assertTrue(importlib.util.find_spec("finsim.api.server"))     # the old code is kept, not deleted


# ================================================================================================ refusals + helpers
class RefusalAndHelperTests(AppTestCase):
    def test_offline_is_refused_on_the_default_home_or_with_a_db_override(self):
        default = os.path.join(os.path.expanduser("~"), ".finsim")
        self.assertIsNotNone(app.offline_refusal(None, {}))
        self.assertIsNotNone(app.offline_refusal(default, {}))
        self.assertIsNotNone(app.offline_refusal(os.path.join(self.tmp, "x"), {config.DB_ENV: r"C:\x\terminal.db"}))
        self.assertIsNotNone(app.offline_refusal(r"C:\h", {config.HOME_ENV: r"C:\h"}))     # FINSIM_HOME is the default then
        self.assertIsNone(app.offline_refusal(os.path.join(self.tmp, "scratch"), {}))
        self.assertIsNone(app.offline_refusal(os.path.join(self.tmp, "scratch"), {config.HOME_ENV: r"C:\h"}))

    def test_db_and_host_refusals(self):
        self.assertIsNotNone(app.db_refusal(r"C:\Users\logan\.finsim\finsim.db"))
        self.assertIsNotNone(app.db_refusal(r"C:\x\FINSIM.DB"))
        self.assertIsNone(app.db_refusal(r"C:\x\terminal.db"))
        self.assertIsNone(app.db_refusal(None))
        self.assertIsNone(app.host_refusal(None))
        self.assertIsNone(app.host_refusal("127.0.0.1"))
        self.assertIsNone(app.host_refusal("localhost"))
        self.assertIsNotNone(app.host_refusal("0.0.0.0"))

    def test_main_serve_refusals_exit_5_before_the_server_is_imported(self):
        saved = sys.modules.pop("finsim.terminal.server", None)   # another test file (test_terminal_server) may hold it in the same process
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(5, app.main(["serve", "--offline"]))                                   # default home
                self.assertEqual(5, app.main(["serve", "--db", os.path.join(self.home, "finsim.db")]))  # the old saves
                self.assertEqual(5, app.main(["serve", "--host", "0.0.0.0"]))
            self.assertNotIn("finsim.terminal.server", sys.modules)
            self.assertEqual(os.path.join(self.home, "terminal.db"), config.db_path())                # env untouched by refusals
        finally:
            if saved is not None:
                sys.modules["finsim.terminal.server"] = saved

    def test_purge_confirmed_needs_the_exact_path_and_never_finsim_db(self):
        db = os.path.join(self.home, "terminal.db")
        self.assertTrue(app.purge_confirmed(db, db))
        self.assertTrue(app.purge_confirmed("  " + db + "\n", db))
        self.assertFalse(app.purge_confirmed("yes", db))
        self.assertFalse(app.purge_confirmed("", db))
        self.assertFalse(app.purge_confirmed(os.path.join(self.home, "other.db"), db))
        old = os.path.join(self.home, "finsim.db")
        self.assertFalse(app.purge_confirmed(old, old))

    def test_pythonw_resolution(self):
        self.assertEqual(r"C:\Py\pythonw.exe", app.pythonw_for(r"C:\Py\python.exe", exists=lambda p: p == r"C:\Py\pythonw.exe"))
        self.assertEqual(r"C:\LA\Programs\Python\Python312\pythonw.exe",
                         app.pythonw_for(r"C:\Py\python.exe", exists=lambda p: False, localappdata=r"C:\LA"))
        self.assertEqual(r"C:\LA\Programs\Python\Python312\pythonw.exe", app.pythonw_for("", localappdata=r"C:\LA"))
        resolved = app.resolve_pythonw(lambda cmd: completed(cmd, 0, stdout=r"C:\Py\python.exe" + "\n"))
        self.assertTrue(resolved.endswith("pythonw.exe"))
        self.assertTrue(app.resolve_pythonw(lambda cmd: completed(cmd, 1)).endswith(os.path.join("Python312", "pythonw.exe")))

    def test_format_helpers_and_file_helpers(self):
        self.assertEqual("2h 03m", app.fmt_uptime(7383))        # 7383 = 2*3600 + 183; 183 = 3*60 + 3
        self.assertEqual("0h 00m", app.fmt_uptime(59))
        self.assertEqual("?", app.fmt_uptime("soon"))
        self.assertEqual("1.0 MB", app.fmt_size(1048576))       # exactly 1 MiB
        self.assertEqual("1.5 MB", app.fmt_size(1572864))       # 1.5 * 1048576
        self.assertEqual("3 KB", app.fmt_size(3072))            # 3072 / 1024 = 3
        self.assertEqual("?", app.fmt_size(None))
        path = self.write(os.path.join(self.home, "finsim.db"), b"abc")
        self.assertEqual(SHA_ABC, app.sha256_file(path))
        self.assertIsNone(app.sha256_file(os.path.join(self.home, "missing.db")))
        log = self.write(os.path.join(self.home, "terminal.log"), "".join(f"L{i}\n" for i in range(1, 21)))
        self.assertEqual([f"L{i}" for i in range(6, 21)], app.log_tail(log))          # last 15 of 20 = L6..L20
        self.assertEqual([], app.log_tail(os.path.join(self.home, "none.log")))
        self.assertEqual("terminal", app.startup_entry_kind(self.write(os.path.join(self.tmp, "a.vbs"), 'InStr(txt, """finsim-terminal""")')))
        self.assertEqual("old", app.startup_entry_kind(self.write(os.path.join(self.tmp, "b.vbs"), "sh.Run pythonw -m finsim serve")))
        self.assertEqual("missing", app.startup_entry_kind(os.path.join(self.tmp, "c.vbs")))
        self.write(os.path.join(self.home, "backups", "finsim-20261020-150000.db"), b"x")
        self.write(os.path.join(self.home, "backups", "finsim-server.vbs.20261020.bak"), b"x")
        self.assertEqual(1, app.count_backups(self.home))                           # .db snapshots only, not the .bak copies

    def test_parse_args_defaults(self):
        a = app.parse_args(["serve"])
        self.assertEqual(("serve", 8765, None, None, False, None), (a.cmd, a.port, a.home, a.db, a.offline, a.host))
        a = app.parse_args(["stop", "--force", "--timeout", "3"])
        self.assertEqual((True, 3.0), (a.force, a.timeout))
        self.assertTrue(app.parse_args(["install", "--no-open"]).no_open)
        self.assertTrue(app.parse_args(["uninstall", "--purge"]).purge)
        self.assertEqual("status", app.parse_args(["status"]).cmd)
        self.assertIsNone(app.parse_args([]).cmd)                          # no command: the three-line hint
        a = app.parse_args(["import", "--batch", "b.jsonl", "--home", r"C:\h", "--mode", "report", "--help"])
        self.assertEqual(("import", ["--batch", "b.jsonl", "--home", r"C:\h", "--mode", "report", "--help"]), (a.cmd, a.import_args))

    def test_dunder_main_is_repointed_to_the_terminal(self):
        m = importlib.import_module("finsim.__main__")
        self.assertIs(app.main, m.main)


# ========================================================================================================= status text
class StatusTextTests(AppTestCase):
    def status(self, health, **over):
        kw = dict(code_version=TERMINAL_VERSION, port=8765, home=r"C:\h", db=r"C:\h\terminal.db", db_size=3072, log_path=r"C:\h\terminal.log",
                  log_tail_lines=["tail line 1", "tail line 2"], lock=("free", None), startup_entry=(r"C:\s\FinSim Server.vbs", "terminal"),
                  backups=2)
        kw.update(over)
        lines, code = app.status_text(health, **kw)
        return "\n".join(lines), code

    def test_health_without_the_marker_warns_of_a_non_terminal_server(self):
        text, code = self.status(OLD_ENGINE)
        self.assertEqual(1, code)
        self.assertIn("a non-terminal server answers on 8765", text)
        self.assertIn("engine 0.11.0", text)
        self.assertIn("up 0h 01m", text)                     # 61 s -> 0h 01m
        self.assertIn("  | tail line 2", text)              # the log tail is shown when the terminal is not serving
        self.assertNotIn("restart to serve", text)

    def test_not_running(self):
        text, code = self.status(None, startup_entry=(r"C:\s\FinSim Server.vbs", "missing"), log_tail_lines=[])
        self.assertEqual(1, code)
        self.assertIn("server: not running", text)
        self.assertIn("  | (no log yet)", text)
        self.assertIn("Startup entry: missing", text)
        self.assertIn(f"FinSim Terminal {TERMINAL_VERSION} (code on disk) - home C:\\h - db C:\\h\\terminal.db (3 KB) - http://127.0.0.1:8765/", text)

    def test_served_version_differs_from_the_code_on_disk(self):
        text, code = self.status(terminal_health("0.9.0"))
        self.assertEqual(0, code)
        self.assertIn("running finsim-terminal 0.9.0, pid 4242, up 2h 03m", text)
        self.assertIn("restart to serve the new code", text)
        self.assertIn("integrity: ok (checked 2026-10-20T19:00:01-04:00)", text)
        self.assertIn("workers: quotes alive (last tick t, errors_1h 0)", text)
        self.assertIn("providers: yahoo ok", text)
        self.assertNotIn("  | tail", text)                   # healthy -> no tail
        text, code = self.status(terminal_health())
        self.assertEqual(0, code)
        self.assertNotIn("restart to serve", text)

    def test_integrity_failure_dead_worker_offline_and_served_db_show_up(self):
        h = terminal_health(offline=True, db_path=r"C:\elsewhere\terminal.db", db_size=1048576,
                            integrity={"ok": False, "hold": ["T1"], "problems": ["L3"], "checked_at": "x"},
                            workers={"quotes": {"alive": False}, "matcher": {"alive": True, "stalled": True}},
                            providers={"yahoo": {"ok": False, "last_error": "timeout", "refused_ua": True}},
                            lifecycle={"due": "d", "failing": ["ACCRUALS"]})
        text, code = self.status(h)
        self.assertEqual(0, code)
        self.assertIn("integrity: FAILED problems=['L3'] hold=['T1']", text)
        self.assertIn("quotes DEAD", text)
        self.assertIn("matcher alive STALLED", text)
        self.assertIn("yahoo DOWN (timeout) refused_ua", text)
        self.assertIn("failing ['ACCRUALS']", text)
        self.assertIn("WARNING: offline mode - FAKE DATA", text)
        self.assertIn("served db: C:\\elsewhere\\terminal.db (1.0 MB) - not the configured path", text)
        self.assertIn("  | tail line 1", text)               # unhealthy -> the tail is shown

    def test_lock_startup_backup_and_sha_lines(self):
        text, _ = self.status(terminal_health(), lock=("held", {"pid": 4242}), startup_entry=(r"C:\s\FinSim Server.vbs", "old"),
                              backups=3, old_db_sha=SHA_ABC, baseline_sha=SHA_ABC)
        self.assertIn("lock: held by pid 4242", text)
        self.assertIn("Startup entry: present but OLD", text)
        self.assertIn("backups: 3 .db snapshot(s) in C:\\h\\backups", text)
        self.assertIn(f"finsim.db: sha256 {SHA_ABC} (unchanged)", text)
        text, _ = self.status(None, lock=("stale", {"pid": 7}), old_db_sha=SHA_ABC, baseline_sha="0" * 64)
        self.assertIn("lock: STALE - pid 7 is not alive", text)
        self.assertIn("(CHANGED since the install baseline)", text)
        text, _ = self.status(None, lock=("held", {"pid": None, "locked": True}), old_db_sha=SHA_ABC)
        self.assertIn("lock: held by pid ? (byte-locked by a live process)", text)
        self.assertIn("(no install baseline recorded yet)", text)
        text, _ = self.status(None, lock=("unreadable", None))
        self.assertIn("lock: file present without valid JSON", text)
        self.assertIn("Startup entry: present (C:\\s\\FinSim Server.vbs)", text)


# ================================================================================= liveness, lock state, stop (fakes)
class ProcessAndStopTests(AppTestCase):
    PYW, PY, NOTEPAD = r"C:\Py\pythonw.exe", r"C:\Py\python.exe", r"C:\Windows\notepad.exe"

    def api(self):
        return FakeProcessApi({100: (app.STILL_ACTIVE, self.PYW), 200: (0, self.PY), 300: (app.STILL_ACTIVE, self.NOTEPAD)})

    def lock(self, pid):
        return self.write(os.path.join(self.home, "terminal.lock"),
                          json.dumps({"pid": pid, "port": 8765, "started_ts": "2026-10-20T19:00:00-04:00", "version": "1.0.0"}))

    def test_process_state_with_a_fake_openprocess(self):
        api = self.api()
        self.assertEqual((True, self.PYW), app.process_state(100, api))     # exit code 259 = STILL_ACTIVE
        self.assertEqual((False, self.PY), app.process_state(200, api))     # exited (code 0) -> not alive
        self.assertEqual((False, ""), app.process_state(999, api))          # cannot be opened -> not alive, never guessed
        self.assertEqual((False, ""), app.process_state(None, api))
        self.assertTrue(app.is_python_process(100, api))
        self.assertFalse(app.is_python_process(300, api))                   # alive but notepad: the PID-reuse guard
        self.assertFalse(app.is_python_process(200, api))
        self.assertTrue(app.force_kill_allowed(100, api))
        self.assertFalse(app.force_kill_allowed(300, api))
        self.assertEqual(api.opened, api.closed)                            # every handle closed

    def test_lock_state_free_held_stale_unreadable(self):
        api = self.api()
        self.assertEqual(("free", None), app.lock_state(self.home, api))
        self.lock(100)
        state, info = app.lock_state(self.home, api)
        self.assertEqual(("held", 100), (state, info["pid"]))
        self.lock(200)
        self.assertEqual("stale", app.lock_state(self.home, api)[0])        # file left by a dead server
        self.write(os.path.join(self.home, "terminal.lock"), "not json")
        self.assertEqual(("unreadable", None), app.lock_state(self.home, api))
        self.write(os.path.join(self.home, "terminal.lock"), "")
        self.assertEqual("stale", app.lock_state(self.home, api)[0])        # empty file: no pid to be alive

    @unittest.skipUnless(sys.platform == "win32", "msvcrt byte-range locks")
    def test_lock_byte_locked_by_a_live_holder_reads_as_held_with_unknown_pid(self):
        import msvcrt
        path = self.lock(100)
        holder = open(path, "r+b")
        try:
            msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, 1)              # byte 0 locked, as the server holds it
            self.assertEqual(("locked", None), app.lock_info(path))
            state, info = app.lock_state(self.home, FakeProcessApi({}))      # no pid can be read, none is guessed
            self.assertEqual("held", state)
            self.assertIsNone(info["pid"])
            self.assertTrue(info["locked"])
            msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            holder.close()
        self.assertEqual("ok", app.lock_info(path)[0])
        self.assertEqual(100, app.lock_info(path)[1]["pid"])

    def test_stop_nothing_running_exit_1(self):
        http, ft = FakeHttp(None), FakeTime()
        rc = app.stop_server(http, self.home, self.api(), timeout=3, force=False, run=lambda c: completed(c), sleep=ft.sleep,
                             out=self.say, monotonic=ft.monotonic)
        self.assertEqual(1, rc)
        self.assertEqual(0, http.shutdowns)
        self.assertIn("nothing running on http://127.0.0.1:8765/", self.text())
        self.lock(200)                                                      # a stale lock changes the wording only
        self.assertEqual(1, app.stop_server(http, self.home, self.api(), timeout=3, force=False, sleep=ft.sleep, out=self.say, monotonic=ft.monotonic))
        self.assertIn("stale lock (pid 200 is not alive)", self.text())

    def test_stop_terminal_waits_for_quiet_port_and_released_lock_exit_0(self):
        api, ft = self.api(), FakeTime()
        http = FakeHttp(terminal_health(), stop_after=1, on_shutdown=lambda: api.procs.pop(100))   # the server exits on shutdown
        self.lock(100)
        rc = app.stop_server(http, self.home, api, timeout=3, force=False, run=lambda c: completed(c), sleep=ft.sleep,
                             out=self.say, monotonic=ft.monotonic)
        self.assertEqual(0, rc)
        self.assertEqual(1, http.shutdowns)
        self.assertEqual([0.25], ft.sleeps)                                 # answered once more after shutdown, then quiet
        self.assertIn(f"stopping finsim-terminal {TERMINAL_VERSION} on http://127.0.0.1:8765/ ...", self.text())
        self.assertIn("the port is quiet", self.text())
        self.assertEqual("stopped", self.out[-1])

    def test_stop_still_answering_exit_2(self):
        http, ft = FakeHttp(terminal_health(), stop_after=None), FakeTime()
        rc = app.stop_server(http, self.home, self.api(), timeout=3, force=False, run=lambda c: completed(c), sleep=ft.sleep,
                             out=self.say, monotonic=ft.monotonic)
        self.assertEqual(2, rc)
        self.assertEqual(12, len(ft.sleeps))                                # 3 s / 0.25 s = 12 polls before giving up
        self.assertIn("still answering after 3s", self.text())

    def test_stop_force_kills_only_a_live_python_image(self):
        api, ft, cmds = self.api(), FakeTime(), []

        def run(cmd):
            cmds.append(list(cmd))
            api.procs.pop(int(cmd[2]), None)
            return completed(cmd)
        self.lock(100)                                                      # the port is quiet but pid 100 holds the lock
        rc = app.stop_server(FakeHttp(None), self.home, api, timeout=3, force=False, run=run, sleep=ft.sleep, out=self.say, monotonic=ft.monotonic)
        self.assertEqual(2, rc)
        self.assertEqual([], cmds)
        self.assertIn("still alive: pid 100 (C:\\Py\\pythonw.exe) holds the lock; pass --force to taskkill it", self.text())
        rc = app.stop_server(FakeHttp(None), self.home, api, timeout=3, force=True, run=run, sleep=ft.sleep, out=self.say, monotonic=ft.monotonic)
        self.assertEqual(0, rc)
        self.assertEqual([["taskkill", "/PID", "100", "/F"]], cmds)
        self.out.clear()
        self.lock(300)                                                      # a live notepad under the recorded pid: never killed
        rc = app.stop_server(FakeHttp(None), self.home, api, timeout=3, force=True, run=run, sleep=ft.sleep, out=self.say, monotonic=ft.monotonic)
        self.assertEqual(2, rc)
        self.assertEqual(1, len(cmds))
        self.assertIn("not a python image - refusing to kill it", self.text())

    def test_stop_old_engine_and_the_only_flags(self):
        ft = FakeTime()
        http = FakeHttp(dict(OLD_ENGINE), stop_after=0)
        self.assertEqual(0, app.stop_server(http, self.home, self.api(), timeout=3, force=False, sleep=ft.sleep, out=self.say, monotonic=ft.monotonic))
        self.assertIn("stopping a non-terminal server (engine 0.11.0)", self.text())
        self.assertEqual(1, http.shutdowns)
        http = FakeHttp(dict(OLD_ENGINE))
        self.assertEqual(1, app.stop_server(http, self.home, self.api(), timeout=3, force=False, sleep=ft.sleep, out=self.say,
                                            monotonic=ft.monotonic, only_terminal=True))
        self.assertEqual(0, http.shutdowns)                                 # uninstall leaves the old engine alone
        http = FakeHttp(terminal_health())
        self.assertEqual(1, app.stop_server(http, self.home, self.api(), timeout=3, force=False, sleep=ft.sleep, out=self.say,
                                            monotonic=ft.monotonic, only_foreign=True))
        self.assertEqual(0, http.shutdowns)                                 # install leaves a terminal alone
        self.assertIn(f"leaving finsim-terminal {TERMINAL_VERSION} running", self.text())

    def test_cmd_stop_prints_the_finsim_db_sha256(self):
        self.write(os.path.join(self.home, "finsim.db"), b"abc")
        ft = FakeTime()
        rc = app.cmd_stop(3, False, http=FakeHttp(None), api=self.api(), home=self.home, sleep=ft.sleep, out=self.say, monotonic=ft.monotonic)
        self.assertEqual(1, rc)
        self.assertEqual(f"finsim.db sha256 {SHA_ABC}", self.out[-1])


# ======================================================================================= commands with injected parts
class CommandTests(AppTestCase):
    def run_recorder(self):
        cmds = []

        def run(cmd):
            cmds.append(list(cmd))
            if list(cmd[:2]) == ["py", "-3.12"]:
                return completed(cmd, 0, stdout=sys.executable + "\n")
            return completed(cmd, 0)
        return cmds, run

    def test_cmd_status_on_the_temp_home(self):
        startup, desktop, programs = self.folders()
        self.write(os.path.join(self.home, "finsim.db"), b"abc")
        self.write(os.path.join(self.home, "finsim.db.sha256"), f"{SHA_ABC}  finsim.db\n")
        self.write(os.path.join(startup, "FinSim Server.vbs"), 'InStr(txt, """finsim-terminal""")')
        rc = app.cmd_status(http=FakeHttp(terminal_health()), api=FakeProcessApi({}), home=self.home, out=self.say, folders=(startup, desktop, programs))
        self.assertEqual(0, rc)
        self.assertIn(f"running finsim-terminal {TERMINAL_VERSION}", self.text())
        self.assertIn(f"finsim.db: sha256 {SHA_ABC} (unchanged)", self.text())
        self.assertIn(f"Startup entry: present ({os.path.join(startup, 'FinSim Server.vbs')})", self.text())
        self.assertIn("lock: free", self.text())
        self.out.clear()
        self.assertEqual(1, app.cmd_status(http=FakeHttp(None), api=FakeProcessApi({}), home=self.home, out=self.say, folders=(startup, desktop, programs)))
        self.assertIn("server: not running", self.text())

    def test_cmd_install_touches_only_the_injected_folders_and_keeps_finsim_db(self):
        startup, desktop, programs = self.folders()
        self.write(os.path.join(self.home, "finsim.db"), b"abc")
        for name in ("finsim-server.vbs", "finsim-open.vbs", "make-shortcuts.vbs"):
            self.write(os.path.join(self.home, name), b"old " + name.encode())
        self.write(os.path.join(startup, "FinSim Server.vbs"), b"old startup entry")
        cmds, run = self.run_recorder()
        starts = []
        http = FakeHttp(terminal_health())                                  # the new code already serves: nothing to stop or start
        rc = app.cmd_install(tree_check=NO_GIT, open_after=False, http=http, api=FakeProcessApi({}), home=self.home, run=run, sleep=lambda s: None,
                             out=self.say, folders=(startup, desktop, programs), starter=lambda *a, **k: starts.append(a) or ("vbs", None),
                             stamp="20261020-210000")
        self.assertEqual(0, rc)
        self.assertEqual([], starts)
        server_vbs = os.path.join(self.home, "FinSim Server.vbs")
        with open(server_vbs, "rb") as f:
            server = f.read()
        server.decode("ascii")
        self.assertIn(b'"finsim-terminal"', server)
        self.assertIn(b"-m finsim serve --port 8765", server)
        self.assertIn(b"pythonw.exe", server)
        self.assertIn(b"-m finsim open", self.read(os.path.join(self.home, "FinSim Terminal.vbs"), binary=True))
        self.assertEqual(b"\x00\x00\x01\x00\x01\x00", self.read(os.path.join(self.home, "finsim.ico"), binary=True)[:6])
        with open(os.path.join(startup, "FinSim Server.vbs"), "rb") as f:
            self.assertEqual(server, f.read())                              # the Startup entry IS the new server VBS
        bak = os.path.join(self.home, "backups")
        self.assertEqual(b"old startup entry", self.read(os.path.join(bak, "Startup FinSim Server.vbs.20261020-210000.bak"), binary=True))
        self.assertEqual(b"old finsim-server.vbs", self.read(os.path.join(bak, "finsim-server.vbs.20261020-210000.bak"), binary=True))
        for name in ("finsim-server.vbs", "finsim-open.vbs", "make-shortcuts.vbs", "make-terminal-shortcuts.vbs"):
            self.assertFalse(os.path.exists(os.path.join(self.home, name)), name)
        self.assertEqual(SHA_ABC, self.read(os.path.join(self.home, "finsim.db.sha256")).split()[0])
        self.assertEqual(SHA_ABC, app.sha256_file(os.path.join(self.home, "finsim.db")))
        self.assertIn(["schtasks", "/Delete", "/F", "/TN", "FinSim Server"], cmds)
        cscript = [c for c in cmds if c[0] == "cscript.exe"]
        self.assertEqual(1, len(cscript))
        self.assertEqual(os.path.join(self.home, "make-terminal-shortcuts.vbs"), cscript[0][2])
        self.assertEqual(0, http.shutdowns)
        text = self.text()
        self.assertIn("already serves this code; it stays up", text)
        self.assertIn(f"sha256 {os.path.join(self.home, 'finsim.db')} = {SHA_ABC} (unchanged)", text)
        self.assertIn("py -3.12 -m finsim install --no-open", text)       # the PowerShell sequence is printed
        order = [text.index(s) for s in ("(baseline written to", "scheduled task 'FinSim Server'", "backed up", "removed old",
                                         "wrote", "Startup entry", "shortcuts:", "server:", "The final PowerShell sequence",
                                         f"= {SHA_ABC} (unchanged)")]
        self.assertEqual(sorted(order), order)                               # the §18 order was executed

    def test_cmd_install_stops_the_old_engine_and_rebaselines_after_its_checkpoint(self):
        startup, desktop, programs = self.folders()
        db = self.write(os.path.join(self.home, "finsim.db"), b"abc")
        sha_abcd = hashlib.sha256(b"abcd").hexdigest()
        shut = []

        def old_engine_shutdown():                                          # the old engine folds its WAL into finsim.db as it closes
            shut.append(1)
            self.write(db, b"abcd")
        http = FakeHttp(dict(OLD_ENGINE), stop_after=0, on_shutdown=old_engine_shutdown)
        cmds, run = self.run_recorder()
        ft = FakeTime()

        def starter(home, port, out=None):
            http.current = terminal_health()
            http.shutdowns = 0                                              # the fresh terminal answers from now on
            return "vbs", None
        rc = app.cmd_install(tree_check=NO_GIT, open_after=True, http=http, api=FakeProcessApi({}), home=self.home, run=run, sleep=ft.sleep, out=self.say,
                             folders=(startup, desktop, programs), starter=starter, stamp="s", opener=lambda target, home: "fake window",
                             monotonic=ft.monotonic)
        self.assertEqual(0, rc)
        self.assertEqual(1, len(shut))
        text = self.text()
        self.assertIn("stopping a non-terminal server (engine 0.11.0)", text)
        self.assertIn(f"baseline re-taken: {sha_abcd}", text)
        self.assertEqual(sha_abcd, self.read(os.path.join(self.home, "finsim.db.sha256")).split()[0])
        self.assertIn(f"= {sha_abcd} (unchanged)", text)                     # the verdict speaks about the terminal only
        self.assertNotIn("CHANGED", text)
        self.assertIn(f"server: finsim-terminal {TERMINAL_VERSION} is up", text)
        self.assertIn("opened in fake window", text)

    def test_cmd_install_reports_a_terminal_that_did_not_come_up(self):
        startup, desktop, programs = self.folders()
        cmds, run = self.run_recorder()
        http, ft = FakeHttp(None), FakeTime()
        rc = app.cmd_install(tree_check=NO_GIT, open_after=True, http=http, api=FakeProcessApi({}), home=self.home, run=run, sleep=ft.sleep, out=self.say,
                             folders=(startup, desktop, programs), starter=lambda *a, **k: ("vbs", None), stamp="s",
                             opener=lambda *a: self.fail("must not open a window when the server is down"), monotonic=ft.monotonic)
        self.assertEqual(1, rc)
        self.assertIn("did not answer within 60s", self.text())             # the default wait, on the fake clock: 60 / 0.25 = 240 polls
        self.assertEqual(240, len(ft.sleeps))
        self.assertIn("no " + os.path.join(self.home, "finsim.db") + " to hash", self.text())

    def test_cmd_uninstall_purge_requires_the_typed_path(self):
        startup, desktop, programs = self.folders()
        files = [os.path.join(startup, "FinSim Server.vbs"), os.path.join(desktop, "FinSim.lnk"), os.path.join(programs, "FinSim.lnk"),
                 os.path.join(self.home, "FinSim Server.vbs"), os.path.join(self.home, "FinSim Terminal.vbs"), os.path.join(self.home, "finsim.ico"),
                 os.path.join(self.home, "terminal-window-profile", "Default", "x.txt")]
        for p in files:
            self.write(p, b"x")
        old = self.write(os.path.join(self.home, "finsim.db"), b"abc")
        db = self.write(os.path.join(self.home, "terminal.db"), b"t")
        self.write(db + "-wal", b"w")
        cmds, run = self.run_recorder()
        ft = FakeTime()
        rc = app.cmd_uninstall(purge=True, http=FakeHttp(None), api=FakeProcessApi({}), home=self.home, run=run, sleep=ft.sleep,
                               ask=lambda prompt: "no", out=self.say, folders=(startup, desktop, programs))
        self.assertEqual(0, rc)
        for p in files:
            self.assertFalse(os.path.exists(p), p)
        self.assertFalse(os.path.isdir(os.path.join(self.home, "terminal-window-profile")))
        self.assertTrue(os.path.exists(db))                                 # kept: the typed text was not the path
        self.assertIn(["schtasks", "/Delete", "/F", "/TN", "FinSim Server"], cmds)
        self.assertIn(f"kept {db}", self.text())
        rc = app.cmd_uninstall(purge=True, http=FakeHttp(None), api=FakeProcessApi({}), home=self.home, run=run, sleep=ft.sleep,
                               ask=lambda prompt: db, out=self.say, folders=(startup, desktop, programs))
        self.assertEqual(0, rc)
        self.assertFalse(os.path.exists(db))
        self.assertFalse(os.path.exists(db + "-wal"))
        self.assertEqual(SHA_ABC, app.sha256_file(old))                     # finsim.db untouched
        self.assertIn("finsim.db was not touched", self.out[-1])

    def test_cmd_open_starts_when_the_terminal_is_absent_and_handles_the_old_engine(self):
        opened, starts, ft = [], [], FakeTime()

        def opener(target, home):
            opened.append((target, home))
            return "fake window"

        def make_starter(http):
            def starter(home, port, out=None):
                starts.append((home, port))
                http.current = terminal_health()
                return "vbs", None
            return starter

        def open_(http, starter):
            return app.cmd_open(http=http, home=self.home, sleep=ft.sleep, out=self.say, opener=opener, starter=starter, wait_s=2.0,
                                monotonic=ft.monotonic)
        http = FakeHttp(None)
        self.assertEqual(0, open_(http, make_starter(http)))
        self.assertEqual([(self.home, 8765)], starts)
        self.assertEqual([("http://127.0.0.1:8765/", self.home)], opened)
        self.assertEqual("http://127.0.0.1:8765/ -> fake window", self.out[-1])
        http = FakeHttp(terminal_health())                                  # already up: no start
        self.assertEqual(0, open_(http, make_starter(http)))
        self.assertEqual(1, len(starts))
        http = FakeHttp(dict(OLD_ENGINE))                                   # old engine, no installed VBS: refused
        self.assertEqual(1, open_(http, make_starter(http)))
        self.assertEqual(1, len(starts))
        self.assertEqual(2, len(opened))
        self.assertIn("no FinSim Server.vbs is installed", self.out[-1])
        self.assertIn("open:", self.read(os.path.join(self.home, "launcher.log")))
        self.write(os.path.join(self.home, "FinSim Server.vbs"), "' installed")   # old engine + VBS: the VBS replaces it
        http = FakeHttp(dict(OLD_ENGINE))
        self.assertEqual(0, open_(http, make_starter(http)))
        self.assertEqual(2, len(starts))
        self.assertIn("asks it to shut down and starts the terminal", self.text())
        self.assertEqual(1, open_(FakeHttp(None), lambda *a, **k: ("vbs", None)))   # a start that never answers
        self.assertIn("did not answer within 2s", self.text())             # 2 s / 0.25 s = 8 polls on the fake clock
        self.assertEqual(8, len(ft.sleeps))
        self.assertEqual(3, len(opened))

    def test_wait_for_terminal_reports_a_child_that_exited(self):
        class Proc:
            returncode = 3

            def poll(self):
                return 3
        ok, why = app.wait_for_terminal(FakeHttp(None), Proc(), 5.0, sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertIn("exited with code 3", why)
        ft = FakeTime()
        ok, why = app.wait_for_terminal(FakeHttp(dict(OLD_ENGINE)), None, 1.0, sleep=ft.sleep, monotonic=ft.monotonic)
        self.assertFalse(ok)
        self.assertIn("a non-terminal server (engine 0.11.0) still answers", why)
        self.assertEqual((True, ""), app.wait_for_terminal(FakeHttp(terminal_health()), None, 1.0, sleep=ft.sleep, monotonic=ft.monotonic))

    def test_cmd_import_hands_every_argument_to_the_import_tool(self):
        # C18: `python -m finsim import ...` = finsim.terminal.importer.main(argv), imported only when `import` runs
        calls = []
        tool = types.SimpleNamespace(main=lambda argv: calls.append(list(argv)) or 0)
        argv = ["--batch", "b.jsonl", "--home", self.home, "--mode", "report"]
        self.assertEqual(0, app.cmd_import(argv, out=self.say, loader=lambda: tool))
        self.assertEqual([argv], calls)
        refused = types.SimpleNamespace(main=lambda argv: 2)                 # the tool's own exit code passes through
        self.assertEqual(2, app.cmd_import(["--mode", "apply"], out=self.say, loader=lambda: refused))

        def missing():
            raise ModuleNotFoundError("No module named 'finsim.terminal.importer'", name="finsim.terminal.importer")
        self.assertEqual(2, app.cmd_import(["--help"], out=self.say, loader=missing))
        self.assertEqual("import: the historical import tool is not in this build", self.out[-1])

        def broken():
            raise ModuleNotFoundError("No module named 'somedep'", name="somedep")   # present, but a dependency is missing
        self.assertEqual(1, app.cmd_import([], out=self.say, loader=broken))
        self.assertIn("could not be loaded (ModuleNotFoundError: No module named 'somedep')", self.out[-1])
        # the real tool through main(): its own --help exits 0 (argparse's SystemExit is caught inside the tool)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(0, app.main(["import", "--help"]))
        self.assertIn("--mode", buf.getvalue())

    def test_no_command_prints_the_three_line_hint_and_exits_0(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(0, app.main([]))
        lines = buf.getvalue().splitlines()
        self.assertEqual(3, len(lines))
        self.assertEqual(["python -m finsim serve", "python -m finsim open", "python -m finsim status"], [" ".join(l.split()[:4]) for l in lines])

    def test_http_round_trip_on_a_temp_port(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._json(200, {"app": "finsim-terminal", "version": "1.0.0"}) if self.path == "/api/health" else self._json(404, {"error": "no"})

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self._json(200, {"shutting_down": True}) if self.path == "/api/shutdown" else self._json(403, {"error": "no"})
        srv = HTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            h = app.Http(port)
            self.assertEqual(f"http://127.0.0.1:{port}/", h.base)
            self.assertEqual({"app": "finsim-terminal", "version": "1.0.0"}, h.health())
            self.assertTrue(app.is_terminal(h.health()))
            self.assertTrue(h.shutdown())
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(5)
        self.assertIsNone(h.health())                                       # connection refused -> None, never an exception
        self.assertFalse(h.shutdown())
        self.assertEqual("nothing", app.describe_server(None))
        self.assertEqual("a non-terminal server (engine 0.11.0)", app.describe_server(OLD_ENGINE))


if __name__ == "__main__":
    unittest.main()
