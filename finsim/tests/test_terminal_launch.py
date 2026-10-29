"""FinSim1 startup and launcher (FINSIM1_PLAN package P7, release 1.0.6; directive section 18 #25 "Application startup" and
section 19 "Windows startup").  Gap ids: STARTUP-01 (the outer shim), STARTUP-02 (FinSim.cmd), STARTUP-04 (two code trees),
STARTUP-05 / UI-14 (README), STARTUP-06 (Chrome app shortcuts), STARTUP-07 (launcher logging and status), STARTUP-09 (no
command), UI-19 (``legacy`` gone), TEST-05 (startup tests), TEST-08 (the suite runner), TEST-09 (no ResourceWarnings).

Real launches run in SUBPROCESSES with ``sys.executable``: ``-m finsim --help`` from the repository root (through the outer
shim ``finsim/__main__.py``) and from ``finsim/``; ``serve --offline --port 0 --home <temp>`` until "ready", its
``/api/health`` marker, a second serve on the same home (exit 3 with "already running", the URL and the ET time), POST
``/api/shutdown`` (exit 0, both lock files gone); a database in the 1.0.3.1 schema built at test time from P1's
``schema_1031.sql`` (no .db fixture: ``*.db`` is git-ignored) that starts migrated, chart-synced, backed up and integrity ok;
``cmd /c FinSim.cmd`` from another folder for both copies (and a fake code tree that proves the argument, exit code and
"no arguments = open" mapping with LF and CRLF line endings).  Every server binds port 0 on 127.0.0.1 with a temp home and
the FakeProvider (no network); the environment is cleaned of FINSIM_HOME / FINSIM_TERMINAL_DB / FINSIM_PORT so nothing
reaches ``%USERPROFILE%\\.finsim`` or port 8765.

The rest is pure: the launcher VBS read as data and never executed (scheduled-task lesson), the status text for two code
trees, the install refusal from a git working tree, Chrome-app shortcut detection on fake ``.lnk`` bytes built here by
hand, the README checks and the runner's summary line.  Every expected value is worked by hand in a comment.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import queue
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

from finsim.terminal import APP_ID, VERSION, app, config

TESTS = os.path.dirname(os.path.abspath(__file__))
FINSIM_DIR = os.path.dirname(TESTS)                        # ...\intelligence-terminal\finsim (the inner package's parent)
REPO_ROOT = os.path.dirname(FINSIM_DIR)                     # ...\intelligence-terminal
SHIM = os.path.join(FINSIM_DIR, "__main__.py")
CMD_FILES = (os.path.join(REPO_ROOT, "FinSim.cmd"), os.path.join(FINSIM_DIR, "FinSim.cmd"))
README = os.path.join(FINSIM_DIR, "README.md")
SCHEMA_1031 = os.path.join(TESTS, "fixtures", "terminal", "schema_1031.sql")
RUNNER = os.path.join(TESTS, "run_terminal_suite.py")
START_TIMEOUT_S = 180.0                                     # a cold start on a busy laptop; it is usually under 15 s
#: the FinSim2 block of finsim/README.md, protected by content (P7 task 9): from its heading through the catalog line and
#: the blank line after it, line endings normalised to LF (the working tree is CRLF under core.autocrlf, git stores LF).
#: Hashed before P7's edit on 2026-10-28 11:59 EDT (lines 58-292, 17,537 bytes).  The FinSim2 session owns that text: if it
#: changes the block on purpose, its new hash goes here (and the gate compares the block with the FinSim2 branch first).
FINSIM2_START = "### FinSim2 \u2014 portfolio & quant analytics\n"
FINSIM2_END = "`finsim/quant/catalog.py` holds each equation's LaTeX, a note and its implementation.\n"
FINSIM2_BLOCK_SHA256 = "6a74cb8ae8d4f26e159a43d0006a6208823d0a4f7b68b61c2df3725d31d9b5ac"
NEWS_WORDS = ("daily news", "briefing", "headlines", "newspaper")    # P7 acceptance 3, matched in any case


def clean_env(**extra) -> dict:
    """The child's environment: never the owner's home, database or port; unbuffered output so "ready" arrives at once."""
    env = {k: v for k, v in os.environ.items() if k not in (config.HOME_ENV, config.DB_ENV, config.PORT_ENV, "FINSIM_LOG_LEVEL",
                                                           "PYTHONPATH")}
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def run(args, cwd, timeout=120.0, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, env=clean_env(), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL, **kw)


def http_json(url_: str, method: str = "GET", timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url_, method=method, data=(b"{}" if method == "POST" else None),
                                 headers={"Content-Type": "application/json"} if method == "POST" else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def finsim2_block(text: str) -> str:
    """The protected FinSim2 block of the README text (LF-normalised): its heading line through the catalog line and the
    blank line after it.  ValueError when a marker is missing or the heading appears twice."""
    text = text.replace("\r\n", "\n")
    if text.count(FINSIM2_START) != 1:
        raise ValueError(f"the FinSim2 heading appears {text.count(FINSIM2_START)} times")
    i = text.index(FINSIM2_START)
    j = text.index(FINSIM2_END, i) + len(FINSIM2_END)
    if text[j:j + 1] != "\n":
        raise ValueError("no blank line after the FinSim2 catalog line")
    return text[i:j + 1]


class ServeProcess:
    """``python -m finsim serve --offline --port 0 --home <home>`` as a child process; its output is read on a thread, so
    "ready" is seen as soon as it is printed.  ``stop()`` always ends it (POST /api/shutdown, then kill as a last resort)."""

    READY = re.compile(r"^ready http://127\.0\.0\.1:(\d+)/ \(" + re.escape(APP_ID) + r" ")

    def __init__(self, home: str, cwd: str):
        self.lines: "queue.Queue[str]" = queue.Queue()
        self.seen = []
        self.port = None
        self.proc = subprocess.Popen([sys.executable, "-m", "finsim", "serve", "--offline", "--port", "0", "--home", home], cwd=cwd,
                                     env=clean_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace")
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))
        self.lines.put(None)

    def wait_ready(self, timeout: float = START_TIMEOUT_S) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.5)
            except queue.Empty:
                continue
            if line is None:
                raise AssertionError(f"the server exited with {self.proc.wait()} before ready: {self.seen}")
            self.seen.append(line)
            m = self.READY.match(line)
            if m:
                self.port = int(m.group(1))
                return self.port
        raise AssertionError(f"no ready line within {timeout:.0f}s: {self.seen}")

    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def stop(self) -> int:
        if self.proc.poll() is None and self.port:
            try:
                http_json(self.base() + "api/shutdown", "POST")
            except Exception:   # noqa: BLE001 - the kill below still ends it
                pass
        try:
            return self.proc.wait(60)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait(30)
        finally:
            self._reader.join(15)                     # the rest of the output, up to the end of the pipe
            while True:
                try:
                    line = self.lines.get_nowait()
                except queue.Empty:
                    break
                if line is not None:
                    self.seen.append(line)
            if self.proc.stdout:
                self.proc.stdout.close()


# ================================================================================== real launches (subprocesses)
class CommandLineLaunchTests(unittest.TestCase):
    def test_help_exits_0_from_the_repo_root_and_from_the_finsim_folder(self):
        # STARTUP-01: from the root the folder is a namespace package; the outer shim makes it run the inner package
        for cwd in (REPO_ROOT, FINSIM_DIR):
            r = run([sys.executable, "-m", "finsim", "--help"], cwd)
            self.assertEqual(0, r.returncode, (cwd, r.stdout, r.stderr))
            self.assertIn("usage: python -m finsim", r.stdout)
            self.assertIn(f"FinSim Terminal {VERSION}", r.stdout)
            for cmd in ("serve", "stop", "status", "install", "open", "uninstall", "import"):
                self.assertRegex(r.stdout, rf"\n\s+{cmd}\s", cmd)
            self.assertNotIn("legacy", r.stdout)                                  # UI-19
            self.assertNotIn("No module named finsim.__main__", r.stderr)

    def test_no_command_prints_the_hint_and_exits_0_and_import_help_runs_the_tool(self):
        r = run([sys.executable, "-m", "finsim"], REPO_ROOT)                     # STARTUP-09
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(3, len(r.stdout.strip().splitlines()))
        for cmd in ("serve", "open", "status"):
            self.assertIn(f"python -m finsim {cmd}", r.stdout)
        r = run([sys.executable, "-m", "finsim", "import", "--help"], REPO_ROOT)   # C18 through the shim
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("--mode", r.stdout)
        self.assertIn("--home", r.stdout)
        r = run([sys.executable, "-m", "finsim", "legacy"], FINSIM_DIR)          # UI-19: says so, starts nothing
        self.assertEqual(2, r.returncode)
        self.assertIn("`legacy` was removed in 1.0.6", r.stdout)

    def test_the_shim_runs_the_inner_package_and_leaves_finsim2_alone(self):
        with open(SHIM, "r", encoding="utf-8") as f:
            text = f.read()
        text.encode("ascii")
        self.assertIn("sys.path.insert(0, here)", text)
        self.assertIn('n == "finsim" or n.startswith("finsim.")', text)
        self.assertIn("from finsim.terminal.app import main", text)
        self.assertNotIn("finsim2", text.split('"""', 2)[2])                     # the code never names FinSim2
        # the shim's module search from the root: it imports the inner package's terminal, never another tree's
        r = run([sys.executable, "-c", "import runpy, sys; sys.argv = ['finsim', '--help']; "
                 "import contextlib, io; buf = io.StringIO()\n"
                 "with contextlib.redirect_stdout(buf):\n"
                 "    try:\n        runpy.run_module('finsim', run_name='__main__')\n"
                 "    except SystemExit as e:\n        code = e.code\n"
                 "import finsim.terminal as t; print(code, t.__file__)"], REPO_ROOT)
        self.assertEqual(0, r.returncode, r.stderr)
        code, path = r.stdout.strip().split(" ", 1)
        self.assertEqual("0", code)
        self.assertEqual(os.path.normcase(os.path.join(FINSIM_DIR, "finsim", "terminal", "__init__.py")), os.path.normcase(path))

    @unittest.skipUnless(sys.platform == "win32", "FinSim.cmd is a Windows batch file")
    def test_finsim_cmd_help_from_another_folder_for_both_copies(self):
        with open(CMD_FILES[0], "rb") as f:
            root_copy = f.read()
        with open(CMD_FILES[1], "rb") as f:
            inner_copy = f.read()
        self.assertEqual(root_copy, inner_copy)                                   # the same file in both places
        root_copy.decode("ascii")
        if not (shutil.which("py") or shutil.which("python")):
            self.skipTest("neither the py launcher nor python is on PATH")
        other = tempfile.mkdtemp(prefix="fst-launch-cwd-")
        try:
            for path in CMD_FILES:
                r = run(["cmd", "/c", path, "--help"], other)
                self.assertEqual(0, r.returncode, (path, r.stdout, r.stderr))
                self.assertIn("usage: python -m finsim", r.stdout)
                r = run(["cmd", "/c", path, "legacy"], other)                     # the command's own exit code passes through
                self.assertEqual(2, r.returncode, (path, r.stdout, r.stderr))
        finally:
            shutil.rmtree(other, ignore_errors=True)

    @unittest.skipUnless(sys.platform == "win32", "FinSim.cmd is a Windows batch file")
    def test_finsim_cmd_maps_no_arguments_to_open_and_passes_arguments_and_the_exit_code(self):
        # a fake code tree whose `python -m finsim` prints its arguments and folder and exits 7: no real server can start
        if not (shutil.which("py") or shutil.which("python")):
            self.skipTest("neither the py launcher nor python is on PATH")
        with open(CMD_FILES[0], "rb") as f:
            lf = f.read().replace(b"\r\n", b"\n")
        crlf = lf.replace(b"\n", b"\r\n")
        for label, data in (("LF", lf), ("CRLF", crlf)):
            with self.subTest(line_endings=label):
                tmp = tempfile.mkdtemp(prefix="fst-launch-cmd-")
                try:
                    pkg = os.path.join(tmp, "finsim", "finsim")
                    os.makedirs(os.path.join(pkg, "terminal"))
                    for rel, body in (("__init__.py", ""), (os.path.join("terminal", "__init__.py"), ""),
                                      ("__main__.py", "import json, os, sys\nprint(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\nsys.exit(7)\n")):
                        with open(os.path.join(pkg, rel), "w", encoding="ascii") as f:
                            f.write(body)
                    for where in (tmp, os.path.join(tmp, "finsim")):           # the root copy and the finsim\ copy
                        cmd = os.path.join(where, "FinSim.cmd")
                        with open(cmd, "wb") as f:
                            f.write(data)
                        for args, expect in (([], ["open"]), (["serve", "--port", "8969"], ["serve", "--port", "8969"]),
                                             (["import", "--owner-go", "standing pre-approval (2026-10-27)"],
                                              ["import", "--owner-go", "standing pre-approval (2026-10-27)"])):
                            r = run(["cmd", "/c", cmd] + args, TESTS)
                            self.assertEqual(7, r.returncode, (label, where, args, r.stdout, r.stderr))
                            out = json.loads(r.stdout.strip().splitlines()[-1])
                            self.assertEqual(expect, out["argv"], (label, where))
                            self.assertTrue(app.same_path(os.path.join(tmp, "finsim"), out["cwd"]), out["cwd"])
                    lonely = os.path.join(tmp, "nowhere")                       # no code next to it: refused, exit 1
                    os.makedirs(lonely)
                    with open(os.path.join(lonely, "FinSim.cmd"), "wb") as f:
                        f.write(data)
                    r = run(["cmd", "/c", os.path.join(lonely, "FinSim.cmd"), "--help"], TESTS)
                    self.assertEqual(1, r.returncode, (label, r.stdout, r.stderr))
                    self.assertIn("no FinSim code next to this file", r.stderr)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)


class ServeLaunchTests(unittest.TestCase):
    """Real servers: one at a time, port 0, a temp home, FakeProvider; each one stopped over loopback."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-launch-serve-")
        self.home = os.path.join(self.tmp, "home")
        self.server = None

    def tearDown(self):
        if self.server is not None and self.server.proc.poll() is None:
            self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_offline_serve_from_the_repo_root_ready_second_instance_and_shutdown(self):
        self.server = ServeProcess(self.home, REPO_ROOT)                          # through the outer shim
        port = self.server.wait_ready()
        health = http_json(self.server.base() + "api/health")
        self.assertEqual(APP_ID, health["app"])                                    # the terminal marker
        self.assertEqual(VERSION, health["version"])
        self.assertTrue(health["offline"])
        self.assertEqual(port, health["port"])
        banner = [l for l in self.server.seen if l.startswith(f"FinSim Terminal {VERSION} - code ")]
        self.assertEqual(1, len(banner), self.server.seen)                         # STARTUP-04: tree, VERSION, db
        self.assertIn(f" - code {FINSIM_DIR} - db {os.path.join(self.home, 'terminal.db')}", banner[0])
        self.assertNotIn("instead", banner[0])                                     # a temp home: the installed launcher is not read
        lock = os.path.join(self.home, "terminal.lock")
        db_lock = os.path.join(self.home, "terminal.db") + ".lock"
        self.assertTrue(os.path.exists(lock) and os.path.exists(db_lock))
        with open(lock, "r", encoding="utf-8") as f:
            info = json.loads(f.read())
        self.assertTrue(app.same_path(FINSIM_DIR, info["code"]))                   # the lock names the code tree
        # a second serve on the same home, from the other folder: exit 3, P1a's wording, nothing opened
        r = run([sys.executable, "-m", "finsim", "serve", "--offline", "--port", "0", "--home", self.home], FINSIM_DIR)
        self.assertEqual(3, r.returncode, (r.stdout, r.stderr))
        self.assertIn("FinSim is already running", r.stdout)
        self.assertIn(f"http://127.0.0.1:{port}/", r.stdout)
        self.assertRegex(r.stdout, r"since \d{4}-\d\d-\d\d \d\d:\d\d:\d\d ET")
        self.assertIn(f"PID {self.server.proc.pid}", r.stdout)
        self.assertIn("`python -m finsim open`", r.stdout)
        # POST /api/shutdown: exit 0 and both lock files gone
        self.assertEqual(0, self.server.stop())
        self.assertFalse(os.path.exists(lock), "terminal.lock left behind")
        self.assertFalse(os.path.exists(db_lock), "terminal.db.lock left behind")
        self.assertIn("stopped", [l.strip() for l in self.server.seen])          # the graceful path, not a kill

    def test_a_database_in_the_1031_schema_starts_migrated_synced_backed_up_and_ok(self):
        from finsim.terminal.ledger import CHART
        os.makedirs(self.home)
        db = os.path.join(self.home, "terminal.db")
        with open(SCHEMA_1031, "r", encoding="utf-8") as f:
            sql = f.read()
        conn = sqlite3.connect(db)
        try:
            conn.executescript(sql)
            self.assertEqual(52, conn.execute("SELECT COUNT(*) FROM chart").fetchone()[0])    # the 1.0.3.1 chart
            self.assertEqual("1.0.3.1", conn.execute("SELECT value FROM meta WHERE key='app_version_last_opened'").fetchone()[0])
        finally:
            conn.close()
        self.server = ServeProcess(self.home, FINSIM_DIR)
        self.server.wait_ready()
        deadline = time.monotonic() + 60
        integrity = {}
        while time.monotonic() < deadline:
            integrity = http_json(self.server.base() + "api/health").get("integrity") or {}
            if integrity.get("ok") is not None:
                break
            time.sleep(0.5)
        self.assertIs(True, integrity.get("ok"), integrity)
        self.assertFalse(integrity.get("hold"), integrity)
        self.assertEqual(0, self.server.stop())
        # the pre-start backup is the 1.0.3.1 file as it was (Database.open copies an existing file with the sqlite3 backup API
        # before it migrates: the bytes differ from a file copy, the content is the 1.0.3.1 database: its meta and its 52 chart rows)
        backups = sorted(f for f in os.listdir(os.path.join(self.home, "backups")) if f.startswith("terminal-") and f.endswith(".db"))
        self.assertEqual(1, len(backups), backups)
        bconn = sqlite3.connect(f"file:{os.path.join(self.home, 'backups', backups[0])}?mode=ro", uri=True)
        try:
            self.assertEqual("1.0.3.1", bconn.execute("SELECT value FROM meta WHERE key='app_version_last_opened'").fetchone()[0])
            self.assertEqual(52, bconn.execute("SELECT COUNT(*) FROM chart").fetchone()[0])
            self.assertEqual("ok", bconn.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            bconn.close()
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            self.assertEqual(VERSION, meta["app_version_last_opened"])            # migrations applied by this release
            self.assertEqual("1", meta["schema_version"])
            codes = {r[0] for r in conn.execute("SELECT gl FROM chart").fetchall()}
            self.assertEqual(set(), set(CHART) - codes)                           # every ledger code synced
            self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            conn.close()


# ================================================================================ launcher VBS as data (never run)
class LauncherVbsTests(unittest.TestCase):
    HOME = r"C:\Users\logan\.finsim"
    REPO = r"C:\Users\logan\.finsim\releases\2026-10-28-r105\intelligence-terminal\finsim"
    PYW = r"C:\Users\logan\AppData\Local\Programs\Python\Python312\pythonw.exe"

    def files(self, home=HOME, repo=REPO, port=8765):
        f = app.launcher_files(home, self.PYW, repo, port, icon_png=b"\x89PNG\r\n\x1a\nxx")
        return f[os.path.join(home, app.SERVER_VBS)], f[os.path.join(home, app.TERMINAL_VBS)]

    def test_parse_reads_back_what_install_wrote(self):
        server, terminal = self.files()
        for text in (server, terminal):
            p = app.parse_launcher_vbs(text)
            self.assertEqual((self.PYW, self.REPO, self.HOME), (p["py"], p["repo"], p["home"]))
        self.assertEqual(8765, app.parse_launcher_vbs(server)["port"])
        self.assertTrue(app.parse_launcher_vbs(server)["marker"])
        quoted = app.parse_launcher_vbs(self.files(home=r'C:\Us"er\.finsim')[0])
        self.assertEqual(r'C:\Us"er\.finsim', quoted["home"])                      # a doubled quote is undone
        old = app.parse_launcher_vbs('Set sh = CreateObject("WScript.Shell")\nsh.Run "pythonw -m finsim serve --db x", 0\n')
        self.assertEqual((None, None, None, None, False), (old["py"], old["repo"], old["home"], old["port"], old["marker"]))

    def test_every_quit_1_logs_why_to_the_launcher_log_first(self):
        # STARTUP-07: the server VBS quits 1 when pythonw or the code is missing; each quit is preceded by LogLine, which
        # appends (mode 8, create) one dated line to <home>\launcher.log - checked as text, the VBS is never executed
        server, terminal = self.files()
        for name, text in ((app.SERVER_VBS, server), (app.TERMINAL_VBS, terminal)):
            lines = text.splitlines()
            quits = [i for i, l in enumerate(lines) if "WScript.Quit 1" in l]
            self.assertEqual(2, len(quits), name)
            for i in quits:
                stmt = lines[i]
                if stmt.lstrip().startswith("WScript.Quit 1"):                    # a block: LogLine is the block's first statement
                    block = lines[i - 2:i]
                    self.assertTrue(block[0].lstrip().startswith("LogLine \"not opened: "), (name, block))
                else:                                                            # one line: If ... Then LogLine "...": WScript.Quit 1
                    self.assertRegex(stmt, r'^If Not fso\.FileExists\(.+\) Then LogLine "not started: [^"]+" & (py|repo): WScript\.Quit 1$')
            self.assertIn('Set f = fso.OpenTextFile(home & "\\launcher.log", 8, True)', text)
            self.assertIn(f'" {name}: " & msg', text)
            self.assertEqual(1, text.count("Sub LogLine(msg)"))
            self.assertIn("On Error Resume Next", text.split("Sub LogLine(msg)")[1])   # logging can never stop the script
            text.encode("ascii")
        self.assertIn('LogLine "not started: Python 3.12 (pythonw.exe) was not found at " & py', server)
        self.assertIn('LogLine "not started: the FinSim code was not found in " & repo', server)

    def test_status_reports_whether_the_installed_pythonw_and_code_exist(self):
        tree = {"path": self.REPO, "present": False, "version": None, "sha": None, "tag": None}
        login = {"py": self.PYW, "repo": self.REPO, "py_present": False, "tree": tree}
        text = "\n".join(app.launcher_lines({"login": login, "icon": None}, self.HOME))
        self.assertIn(f"Startup entry -> {self.REPO} (MISSING: there is no finsim\\terminal\\__init__.py in that folder)", text)
        self.assertIn(f"its pythonw: {self.PYW} - MISSING: the Startup entry quits at login without starting the server", text)
        self.assertIn(os.path.join(self.HOME, "launcher.log"), text)
        self.assertIn("its code: MISSING", text)
        login.update(py_present=True, tree=dict(tree, present=True, version="1.0.5", sha="cb3540b5d1e8350eca28ea0366520287c5c5d88f",
                                                tag="2026-10-28-r105"))
        text = "\n".join(app.launcher_lines({"login": login, "icon": dict(login)}, self.HOME))
        self.assertIn(f"Startup entry -> {self.REPO} (finsim-terminal 1.0.5, release 2026-10-28-r105, commit "
                      "cb3540b5d1e8350eca28ea0366520287c5c5d88f)", text)
        self.assertIn(f"its pythonw: {self.PYW} (present)", text)
        self.assertNotIn("MISSING", text)
        self.assertNotIn("FinSim icon ->", text)                                   # the icon starts the same tree


# ========================================================================================== two code trees (pure)
class FakeHttp:
    def __init__(self, health=None):
        self.base = "http://127.0.0.1:8765/"
        self.current = health

    def health(self, timeout=1.0):
        return self.current

    def shutdown(self, timeout=3.0):
        return True


class FakeProcessApi:
    def __init__(self, procs):
        self.procs = dict(procs)

    def open(self, pid):
        return ("h", pid) if pid in self.procs else None

    def exit_code(self, handle):
        return self.procs[handle[1]][0]

    def image(self, handle):
        return self.procs[handle[1]][1]

    def close(self, handle):
        pass


def health(version: str) -> dict:
    return {"app": APP_ID, "version": version, "pid": 4242, "uptime_s": 7383, "started_ny": "2026-10-28 09:57:52 ET",
            "state_version": 3678, "offline": False, "integrity": {"ok": True, "hold": False, "checked_at": "t", "problems": []},
            "providers": {}, "workers": {"quotes": {"alive": True, "last_tick_ny": "t", "errors_1h": 0}},
            "lifecycle": {"due": "d", "failing": []}, "attention_open": 0}


class TwoTreeTests(unittest.TestCase):
    """STARTUP-04 on temp folders: a fake release tree (VERSION 0.9.0, RELEASE.txt as the release tooling writes it), a
    Startup folder and home holding the launcher VBS that ``install`` would write for it, and this repository's tree."""
    SHA = "cb3540b5d1e8350eca28ea0366520287c5c5d88f"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-launch-trees-")
        self.home = os.path.join(self.tmp, "home")
        self.rel = os.path.join(self.tmp, "releases", "2026-10-28-r105", "intelligence-terminal", "finsim")
        os.makedirs(os.path.join(self.rel, "finsim", "terminal"))
        os.makedirs(self.home)
        with open(os.path.join(self.rel, "finsim", "terminal", "__init__.py"), "w", encoding="utf-8") as f:
            f.write('"""a release copy"""\nVERSION = "0.9.0"\nAPP_ID = "finsim-terminal"\n')
        with open(os.path.join(self.tmp, "releases", "2026-10-28-r105", "RELEASE.txt"), "w", encoding="utf-8") as f:
            f.write(f"release 2026-10-28-r105\nsource commit {self.SHA}\nbranch claude/finsim-live-terminal\nVERSION 0.9.0\nmade x\n")
        self.pyw = os.path.join(self.tmp, "pythonw.exe")
        with open(self.pyw, "wb") as f:
            f.write(b"")
        self.folders = tuple(os.path.join(self.tmp, n) for n in ("startup", "desktop", "programs"))
        for d in self.folders:
            os.makedirs(d)
        self._env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV, config.PORT_ENV)}
        os.environ[config.HOME_ENV] = self.home
        os.environ[config.DB_ENV] = os.path.join(self.home, "terminal.db")
        os.environ.pop(config.PORT_ENV, None)
        self.out = []

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install_launcher(self, repo):
        files = app.launcher_files(self.home, self.pyw, repo, 8765, icon_png=b"\x89PNG\r\n\x1a\nxx")
        server = files[os.path.join(self.home, app.SERVER_VBS)]
        for folder in (self.home, self.folders[0]):
            with open(os.path.join(folder, app.SERVER_VBS), "w", encoding="ascii") as f:
                f.write(server)

    def lock(self, code):
        info = {"pid": 4242, "port": 8765, "started_ts": 1793195872, "version": "x", "home": self.home, "db": "d"}
        if code:
            info["code"] = code
        with open(os.path.join(self.home, "terminal.lock"), "w", encoding="utf-8") as f:
            f.write(json.dumps(info))

    def status(self, served):
        self.out.clear()
        rc = app.cmd_status(http=FakeHttp(health(served) if served else None), api=FakeProcessApi({4242: (app.STILL_ACTIVE, self.pyw)}),
                            home=self.home, out=self.out.append, folders=self.folders)
        return rc, "\n".join(self.out)

    def test_describe_tree_reads_version_and_the_release_sha(self):
        t = app.describe_tree(self.rel)
        self.assertEqual((True, "0.9.0", self.SHA, "2026-10-28-r105"), (t["present"], t["version"], t["sha"], t["tag"]))
        self.assertEqual(f"{self.rel} (finsim-terminal 0.9.0, release 2026-10-28-r105, commit {self.SHA})", app.tree_phrase(t))
        here = app.describe_tree(app.repo_dir())
        self.assertEqual((True, VERSION), (here["present"], here["version"]))
        with open(os.path.join(self.tmp, "old-RELEASE.txt"), "w", encoding="utf-8") as f:   # the 1.0.3 tooling's wording
            f.write("release r1031b\nsource C:\\x\ngit HEAD 0123456789abcdef0123456789abcdef01234567 (working tree)\n")
        old = os.path.join(self.tmp, "old", "intelligence-terminal", "finsim")
        os.makedirs(os.path.join(old, "finsim", "terminal"))
        with open(os.path.join(old, "finsim", "terminal", "__init__.py"), "w", encoding="utf-8") as f:
            f.write('VERSION = "1.0.3.1"\n')
        shutil.move(os.path.join(self.tmp, "old-RELEASE.txt"), os.path.join(self.tmp, "old", "RELEASE.txt"))
        self.assertEqual("0123456789abcdef0123456789abcdef01234567", app.describe_tree(old)["sha"])
        missing = app.describe_tree(os.path.join(self.tmp, "gone"))
        self.assertFalse(missing["present"])
        self.assertIn("MISSING", app.tree_phrase(missing))

    def test_another_installed_tree_gives_the_two_real_choices_not_restart(self):
        # the owner types `status` in the repository (this tree) while the installed release runs: restarting would only
        # start the release again, so status names both trees and the two real choices
        self.install_launcher(self.rel)
        self.lock(self.rel)
        rc, text = self.status("0.9.0")
        self.assertEqual(0, rc)
        self.assertIn(f"  code: {self.rel} (this command runs from {app.repo_dir()})", text)
        self.assertIn(f"NOTE: the server runs finsim-terminal 0.9.0 from {self.rel}; this command's code is {VERSION} in "
                      f"{app.repo_dir()}, and the FinSim icon starts {self.rel}. Two choices: open the installed app "
                      "(`py -3.12 -m finsim open` or the FinSim icon), or run this tree instead (`py -3.12 -m finsim stop`, then "
                      "`py -3.12 -m finsim serve` here)", text)
        self.assertNotIn("restart to serve the new code", text)
        self.assertIn(f"Startup entry -> {self.rel} (finsim-terminal 0.9.0, release 2026-10-28-r105, commit {self.SHA})", text)
        self.assertIn(f"its pythonw: {self.pyw} (present)", text)
        # the same version in both trees still differs by tree: the choices again
        rc, text = self.status(VERSION)
        self.assertIn("Two choices: open the installed app", text)
        # not running: which code `open` would start
        rc, text = self.status(None)
        self.assertEqual(1, rc)
        self.assertIn(f"server: not running (py -3.12 -m finsim open starts it: the code in {self.rel})", text)

    def test_restart_is_right_when_open_starts_this_tree(self):
        self.install_launcher(app.repo_dir())                                      # installed from this very tree
        self.lock(app.repo_dir())
        rc, text = self.status("0.9.0")                                            # older code still running here
        self.assertIn("WARNING: the server serves 0.9.0 but the code on disk is", text)
        self.assertIn("restart to serve the new code (`py -3.12 -m finsim stop` then `py -3.12 -m finsim open`)", text)
        self.assertNotIn("Two choices", text)
        self.assertIn(f"Startup entry -> {app.repo_dir()} (finsim-terminal {VERSION}, ", text)
        os.remove(os.path.join(self.home, app.SERVER_VBS))                         # no launcher: open starts this tree directly
        os.remove(os.path.join(self.folders[0], app.SERVER_VBS))
        self.lock(None)                                                            # a lock written before 1.0.5 has no code
        rc, text = self.status("0.9.0")
        self.assertIn("restart to serve the new code", text)
        self.assertIn("Startup entry: missing", text)
        self.assertNotIn("Startup entry ->", text)

    def test_tree_advice_cases(self):
        here, rel = r"C:\repo\finsim", r"C:\rel\finsim"
        launcher = {"icon": {"repo": rel}}
        self.assertEqual([], app.tree_advice(VERSION, VERSION, here, here, launcher))      # same code: nothing to say
        self.assertEqual([], app.tree_advice(VERSION, VERSION, here, None, None))
        self.assertIn("Two choices", app.tree_advice("1.0.5", "1.0.6", here, rel, launcher)[0])
        self.assertIn("Two choices", app.tree_advice("1.0.5", "1.0.6", here, None, launcher)[0])   # running tree unknown
        self.assertIn("restart to serve the new code", app.tree_advice("1.0.5", "1.0.6", here, here, {"icon": {"repo": here}})[0])
        self.assertIn("restart to serve the new code", app.tree_advice("1.0.5", "1.0.6", None, None, launcher)[0])  # no tree known
        moved = app.tree_advice(VERSION, VERSION, here, rel, {"icon": {"repo": here}})[0]
        self.assertIn(f"the server runs from {rel}, not from this command's tree {here}", moved)

    def test_serve_banner_names_the_tree_version_db_and_what_the_icon_starts(self):
        here, rel, other = r"C:\repo\finsim", r"C:\rel\finsim", r"C:\other\finsim"
        db = r"C:\Users\x\.finsim\terminal.db"
        self.assertEqual(f"FinSim Terminal 1.0.6 - code {here} - db {db}", app.serve_banner(here, "1.0.6", db, None))
        same = {"login": {"repo": rel}, "icon": {"repo": rel}}
        self.assertEqual(f"FinSim Terminal 1.0.6 - code {here} - db {db}; the FinSim icon and Windows login start {rel} instead",
                         app.serve_banner(here, "1.0.6", db, same))
        self.assertEqual(f"FinSim Terminal 1.0.6 - code {rel} - db {db}", app.serve_banner(rel, "1.0.6", db, same))
        split = {"login": {"repo": rel}, "icon": {"repo": other}}
        self.assertEqual(f"FinSim Terminal 1.0.6 - code {here} - db {db}; the FinSim icon starts {other} and Windows login starts "
                         f"{rel} instead", app.serve_banner(here, "1.0.6", db, split))
        self.assertEqual(f"FinSim Terminal 1.0.6 - code {here} - db {db}; Windows login starts {rel} instead",
                         app.serve_banner(here, "1.0.6", db, {"login": {"repo": rel}, "icon": None}))
        self.assertTrue(app.is_default_home(os.path.join(os.path.expanduser("~"), ".finsim")))
        self.assertFalse(app.is_default_home(self.home))


# ==================================================================================== install from a working tree
class WorkingTreeInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-launch-git-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.folders = tuple(os.path.join(self.tmp, n) for n in ("startup", "desktop", "programs"))
        for d in self.folders:
            os.makedirs(d)
        self._env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV, config.PORT_ENV)}
        os.environ[config.HOME_ENV] = self.home
        os.environ[config.DB_ENV] = os.path.join(self.home, "terminal.db")
        os.environ.pop(config.PORT_ENV, None)
        self.out, self.cmds = [], []

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cmd(self, cmd):
        self.cmds.append(list(cmd))
        out = sys.executable + "\n" if list(cmd[:2]) == ["py", "-3.12"] else ""
        return subprocess.CompletedProcess(list(cmd), 0, stdout=out, stderr="")

    def test_git_working_tree_finds_the_dot_git_of_the_tree_or_its_parent(self):
        repo = os.path.join(self.tmp, "intelligence-terminal")
        tree = os.path.join(repo, "finsim")
        os.makedirs(tree)
        self.assertIsNone(app.git_working_tree(tree))                             # a release copy: no .git
        os.makedirs(os.path.join(repo, ".git"))
        self.assertEqual(os.path.join(repo, ".git"), app.git_working_tree(tree))  # the repository root
        shutil.rmtree(os.path.join(repo, ".git"))
        with open(os.path.join(tree, ".git"), "w", encoding="ascii") as f:        # a linked worktree's .git file
            f.write("gitdir: C:/x/.git/worktrees/y\n")
        self.assertEqual(os.path.join(tree, ".git"), app.git_working_tree(tree))

    def test_install_refuses_a_working_tree_without_the_owner_flag_and_changes_nothing(self):
        rc = app.cmd_install(open_after=False, http=FakeHttp(None), api=FakeProcessApi({}), home=self.home, run=self.run_cmd,
                             sleep=lambda s: None, out=self.out.append, folders=self.folders,
                             starter=lambda *a, **k: self.fail("started"), stamp="s", tree_check=lambda tree: r"C:\repo\.git")
        self.assertEqual(1, rc)
        text = "\n".join(self.out)
        self.assertIn(f"install refused: {app.repo_dir()} is a git working tree (C:\\repo\\.git)", text)
        self.assertIn("Nothing was changed", text)
        self.assertIn("py -3.12 -m finsim install --from-working-tree", text)
        self.assertEqual([], self.cmds)                                            # nothing run (no schtasks, no cscript)
        self.assertEqual([], os.listdir(self.home))                               # nothing written
        for d in self.folders:
            self.assertEqual([], os.listdir(d))

    def test_the_owner_flag_installs_and_says_so(self):
        h = health(VERSION)                                                        # this code already serves: nothing to start
        rc = app.cmd_install(open_after=False, from_working_tree=True, http=FakeHttp(h), api=FakeProcessApi({}), home=self.home,
                             run=self.run_cmd, sleep=lambda s: None, out=self.out.append, folders=self.folders,
                             starter=lambda *a, **k: self.fail("started"), stamp="s", tree_check=lambda tree: r"C:\repo\.git")
        self.assertEqual(0, rc)
        text = "\n".join(self.out)
        self.assertIn(f"installing from the git working tree {app.repo_dir()} (--from-working-tree)", text)
        self.assertIn("py -3.12 -m finsim install --no-open --from-working-tree", text)    # the printed sequence keeps the flag
        self.assertIn(f"cd {app.repo_dir()}", text)
        self.assertTrue(os.path.exists(os.path.join(self.folders[0], app.SERVER_VBS)))
        self.assertTrue(app.parse_args(["install", "--from-working-tree"]).from_working_tree)
        self.assertFalse(app.parse_args(["install"]).from_working_tree)

    def test_a_release_folder_installs_without_the_flag(self):
        rc = app.cmd_install(open_after=False, http=FakeHttp(health(VERSION)), api=FakeProcessApi({}), home=self.home, run=self.run_cmd,
                             sleep=lambda s: None, out=self.out.append, folders=self.folders,
                             starter=lambda *a, **k: self.fail("started"), stamp="s", tree_check=lambda tree: None)
        self.assertEqual(0, rc)
        text = "\n".join(self.out)
        self.assertNotIn("refused", text)
        self.assertNotIn("--from-working-tree", text)


# ============================================================================== Chrome-app shortcuts (fake .lnk)
def _strdata(s: str) -> bytes:
    return struct.pack("<H", len(s)) + s.encode("utf-16-le")


def make_lnk(*, local_path=None, unicode_path=None, relative=None, args=None, workdir=None, name=None, env_target=None,
             idlist=True) -> bytes:
    """A shortcut file in the MS-SHLLINK layout, built by hand: the 76-byte header (size 0x4C, the shell-link CLSID, the
    link flags), an item-id list, a LinkInfo with the local base path (ANSI header 0x1C, or the Unicode header 0x24 with
    LocalBasePathOffsetUnicode), Unicode string data, and an EnvironmentVariableDataBlock when ``env_target`` is given."""
    flags = 0x80                                                     # IsUnicode
    body = b""
    if idlist:
        flags |= 0x01
        item = struct.pack("<H", 6) + b"abcd"                        # one 6-byte item, then the 2-byte terminator
        ids = item + b"\0\0"
        body += struct.pack("<H", len(ids)) + ids
    if local_path is not None or unicode_path is not None:
        flags |= 0x02
        vol = struct.pack("<IIII", 0x11, 3, 0x1234ABCD, 0x10) + b"\0"   # VolumeID: size 17, fixed drive, serial, empty label
        if unicode_path is None:
            hsize = 0x1C
            lbp = local_path.encode("latin-1") + b"\0"
            lbp_off = hsize + len(vol)
            cps_off = lbp_off + len(lbp)
            info = struct.pack("<7I", 0, hsize, 1, hsize, lbp_off, 0, cps_off) + vol + lbp + b"\0"
        else:
            hsize = 0x24
            lbp = b"?\0"                                             # an ANSI path that must NOT be used
            lbp_off = hsize + len(vol)
            cps_off = lbp_off + len(lbp)
            lbpu_off = cps_off + 1
            lbpu = unicode_path.encode("utf-16-le") + b"\0\0"
            cpsu_off = lbpu_off + len(lbpu)
            info = struct.pack("<9I", 0, hsize, 1, hsize, lbp_off, 0, cps_off, lbpu_off, cpsu_off) + vol + lbp + b"\0" + lbpu + b"\0\0"
        info = struct.pack("<I", len(info)) + info[4:]               # LinkInfoSize
        body += info
    for bit, value in ((0x04, name), (0x08, relative), (0x10, workdir), (0x20, args)):
        if value is not None:
            flags |= bit
            body += _strdata(value)
    if env_target is not None:
        ansi = env_target.encode("latin-1").ljust(260, b"\0")
        uni = env_target.encode("utf-16-le").ljust(520, b"\0")
        body += struct.pack("<II", 0x314, 0xA0000001) + ansi + uni
    body += b"\0\0\0\0"                                              # the terminal block
    header = struct.pack("<I16sIIQQQIIIHHII", 0x4C, app.LNK_CLSID, flags, 0x20, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0)
    assert len(header) == 76
    return header + body


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome_proxy.exe"
APP_ARGS = ' --profile-directory="Profile 10" --app-id=ndclgahegldohknijikipmikgfefalhe'


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-launch-lnk-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, folder, name, data):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_lnk_info_reads_the_target_from_each_place(self):
        chrome = app.lnk_info(make_lnk(local_path=CHROME, args=APP_ARGS, workdir=os.path.dirname(CHROME), name="FinSim"))
        self.assertEqual((CHROME, APP_ARGS, "FinSim"), (chrome["target"], chrome["args"], chrome["name"]))
        self.assertEqual("chrome_app", app.shortcut_kind(chrome))
        self.assertEqual(CHROME, app.lnk_info(make_lnk(unicode_path=CHROME))["target"])          # the Unicode path wins over "?"
        env = app.lnk_info(make_lnk(env_target=r"%ProgramFiles%\Google\Chrome\Application\chrome_proxy.exe", idlist=False))
        self.assertTrue(env["target"].lower().endswith("\\chrome_proxy.exe"), env)
        self.assertEqual("chrome_app", app.shortcut_kind(env))
        # what `install` makes: no LinkInfo, the relative path to wscript.exe, the terminal VBS as the argument
        icon = app.lnk_info(make_lnk(relative=r"..\..\..\..\WINDOWS\system32\wscript.exe", args='"C:\\Users\\x\\.finsim\\FinSim Terminal.vbs"'),
                            r"C:\Users\x\OneDrive\Desktop")
        self.assertEqual(os.path.normpath(r"C:\Users\x\OneDrive\Desktop\..\..\..\..\WINDOWS\system32\wscript.exe"), icon["target"])
        self.assertEqual("terminal", app.shortcut_kind(icon))
        self.assertEqual("other", app.shortcut_kind(app.lnk_info(make_lnk(local_path=r"C:\Windows\notepad.exe"))))
        self.assertIsNone(app.lnk_info(b"not a shortcut"))
        self.assertIsNone(app.lnk_info(make_lnk(local_path=CHROME)[:90]))         # cut short inside the LinkInfo
        self.assertEqual("other", app.shortcut_kind(None))

    def test_status_and_install_report_chrome_app_shortcuts_and_never_delete_them(self):
        startup, desktop, programs = (os.path.join(self.tmp, n) for n in ("startup", "desktop", "programs"))
        chrome = make_lnk(local_path=CHROME, args=APP_ARGS)
        ours = make_lnk(relative=r"..\..\WINDOWS\system32\wscript.exe", args='"C:\\h\\FinSim Terminal.vbs"')
        paths = [self.put(desktop, "FinSim (1).lnk", chrome), self.put(os.path.join(programs, "Chrome Apps"), "FinSim.lnk", chrome),
                 self.put(desktop, "FinSim.lnk", ours), self.put(programs, "FinSim.lnk", ours),
                 self.put(desktop, "Kindle.lnk", chrome),                                          # not a FinSim name
                 self.put(os.path.join(programs, "a", "b", "c"), "FinSim.lnk", chrome),            # three levels down: not looked at
                 self.put(os.path.join(desktop, "sub"), "FinSim.lnk", chrome)]                     # the Desktop's subfolders neither
        os.makedirs(startup)
        hashes = {p: file_sha256(p) for p in paths}
        found = app.find_shortcuts((startup, desktop, programs))
        kinds = {os.path.relpath(p, self.tmp): kind for p, kind, _ in found}
        self.assertEqual({os.path.join("desktop", "FinSim (1).lnk"): "chrome_app", os.path.join("desktop", "FinSim.lnk"): "terminal",
                          os.path.join("programs", "FinSim.lnk"): "terminal",
                          os.path.join("programs", "Chrome Apps", "FinSim.lnk"): "chrome_app"}, kinds)
        lines = app.shortcut_lines(found)
        self.assertEqual(f"FinSim icon: {os.path.join(desktop, 'FinSim.lnk')}; {os.path.join(programs, 'FinSim.lnk')}", lines[0])
        notes = [l for l in lines if "is a Chrome app shortcut that does not start the server" in l]
        self.assertEqual(2, len(notes))
        self.assertTrue(all("left in place" in l for l in notes))
        # status prints them; install reports them after its shortcut step; neither moves or deletes a file
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV, config.PORT_ENV)}
        os.environ[config.HOME_ENV] = home
        os.environ[config.DB_ENV] = os.path.join(home, "terminal.db")
        os.environ.pop(config.PORT_ENV, None)
        out = []
        try:
            app.cmd_status(http=FakeHttp(None), api=FakeProcessApi({}), home=home, out=out.append, folders=(startup, desktop, programs))
            self.assertEqual(2, sum("is a Chrome app shortcut that does not start the server" in l for l in out))
            out.clear()
            ran = []
            rc = app.cmd_install(open_after=False, http=FakeHttp(health(VERSION)), api=FakeProcessApi({}), home=home,
                                 run=lambda cmd: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, stdout=sys.executable + "\n", stderr=""),
                                 sleep=lambda s: None, out=out.append, folders=(startup, desktop, programs),
                                 starter=lambda *a, **k: self.fail("started"), stamp="s", tree_check=lambda tree: None)
            self.assertEqual(0, rc)
            text = "\n".join(out)
            after_step = text.split("  shortcuts: ", 1)[1].split("\n  server:", 1)[0]
            self.assertEqual(2, after_step.count("is a Chrome app shortcut that does not start the server"))
        finally:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        for p in paths:                                                            # every shortcut still there, unchanged
            self.assertEqual(hashes[p], file_sha256(p), p)


# ============================================================================================== README and runner
class ReadmeTests(unittest.TestCase):
    def setUp(self):
        with open(README, "rb") as f:
            self.text = f.read().decode("utf-8").replace("\r\n", "\n")
        self.block = finsim2_block(self.text)
        self.finsim1 = self.text.replace(self.block, "", 1)

    def test_the_finsim2_block_is_byte_identical(self):
        self.assertEqual(FINSIM2_BLOCK_SHA256, hashlib.sha256(self.block.encode("utf-8")).hexdigest(),
                         "the FinSim2 block of finsim/README.md changed; it belongs to the FinSim2 session")
        self.assertEqual(17537, len(self.block.encode("utf-8")))

    def test_the_finsim1_parts_carry_no_news_words(self):
        low = self.finsim1.lower()
        for word in NEWS_WORDS:
            self.assertFalse(word in low, f"{word!r} is in the FinSim1 parts of the README")
        self.assertFalse("python -m finsim legacy" in self.finsim1, "the README documents the removed legacy command")
        self.assertFalse("python3 -m finsim demo" in self.finsim1, "the retired game's text is still in the README")

    def test_the_finsim1_parts_document_start_commands_tests_and_pages(self):
        for needle in ("cd C:\\Users\\logan\\intelligence-terminal\\finsim", "python -m finsim serve", ".\\FinSim.cmd serve",
                       "python tests\\run_terminal_suite.py", 'python -m unittest discover -s tests -t . -p "test_terminal_*.py"',
                       "%USERPROFILE%\\.finsim\\terminal.db", "No module named finsim.__main__", "--from-working-tree",
                       "python -m finsim import", "launcher.log", "py -3.12", "3.14"):
            self.assertTrue(needle in self.finsim1, f"the README does not say {needle!r}")
        order = ["Dashboard", "Portfolio", "Trading", "Pending Trades", "Completed Trades", "Accounts", "Cash & Collateral",
                 "Derivatives", "History", "Settings"]
        pages = self.finsim1.split("## The pages", 1)[1]
        positions = [pages.index(f"**{p}**") for p in order]
        self.assertEqual(sorted(positions), positions)                            # the directive's navigation order
        for exit_line in ("| `status` |", "| `stop` |", "| `install` |", "| `import ...` |", "| `serve` |"):
            self.assertIn(exit_line, self.finsim1)
        self.finsim1.encode("utf-8")


class RunnerTests(unittest.TestCase):
    def test_the_runner_prints_the_summary_line_from_any_folder(self):
        other = tempfile.mkdtemp(prefix="fst-launch-runner-")
        try:
            r = run([sys.executable, RUNNER, "test_terminal_bonds"], other, timeout=300)
        finally:
            shutil.rmtree(other, ignore_errors=True)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertRegex(lines[-2], r"^RAN \d+ FAIL 0 ERR 0 SKIP 0 FILES 1$")
        self.assertEqual("ResourceWarnings 0", lines[-1])                         # TEST-09: the bonds fixtures are closed

    def test_file_selection_and_the_resource_warning_counter(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("p7_run_terminal_suite", RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        every = runner.test_files()
        self.assertIn("tests.test_terminal_launch", every)
        self.assertEqual(sorted(every), every)
        self.assertTrue(all(n.startswith("tests.test_terminal_") for n in every))
        self.assertEqual(["tests.test_terminal_app", "tests.test_terminal_bonds"],
                         runner.test_files(["tests.test_terminal_bonds.py", "test_terminal_app"]))
        self.assertFalse([n for n in runner.test_files(no_swaps=True) if n.startswith("tests.test_terminal_swaps")])
        with self.assertRaises(SystemExit):
            runner.test_files(["test_terminal_nope"])
        counter = runner._ResourceWarningCounter()
        shown = []
        counter._orig = lambda *a: shown.append(a)
        counter("unclosed file", ResourceWarning, "x.py", 1)
        counter("old", DeprecationWarning, "x.py", 2)
        self.assertEqual((1, 2), (counter.count, len(shown)))                      # counted once, both still shown


if __name__ == "__main__":
    unittest.main()
