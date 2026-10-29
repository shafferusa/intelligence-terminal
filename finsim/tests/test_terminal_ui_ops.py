"""UI-ops fixes (2026-10-21): the Markets table re-render, `status` provider states, and a launcher that no longer
depends on another work stream's uncommitted helpers in ``finsim/finsim/app.py``.

(a) ``static/pages/markets.js`` + ``static/components/grid.js``: a poll or quote re-render keeps the filter text, the
    scroll position and the selection, and never drops or delays a click.  No JavaScript engine runs in this suite, so
    these are STATIC checks of the code paths (a JS-aware brace matcher extracts function bodies): the 30 s poll goes
    load -> show -> refresh -> grid.update (the filter box is never rebuilt), the layout is rebuilt only when the
    answer's shape changes and then carries the grid state over, and the grid patches cells in place and holds a
    render while a pointer is pressed.
(b) ``app.status_text``: a provider that has not been called yet (``ok`` null, breaker closed - what a fresh server
    reports) prints ``idle``, never ``DOWN (error)``; ``ok``; ``DOWN (<last_error> ...)`` from the real
    ``ProviderHealth`` counters passed through the real API codec; integrity not yet checked is not "FAILED".
(c) ``finsim.terminal.winpaths``: Startup / Desktop / Programs through ``SHGetKnownFolderPath`` with the classic
    fallback (the Startup folder is ``%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup``); the generated .vbs
    files are pure ASCII; ``app`` uses the old ``finsim/app.py`` only for helpers its COMMITTED version has.

No network, no real port, no ``~/.finsim``: temp folders only; the known-folder API is only READ (on Windows).
Every expected value is worked by hand in a comment.
"""
from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from finsim.terminal import APP_ID, VERSION, app, config, winpaths  # noqa: E402

STATIC = os.path.join(ROOT, "finsim", "terminal", "static")
MARKETS_JS = os.path.join(STATIC, "pages", "markets.js")
GRID_JS = os.path.join(STATIC, "components", "grid.js")
APP_PY = os.path.join(ROOT, "finsim", "terminal", "app.py")
#: the old launcher's helpers that the COMMITTED finsim/finsim/app.py defines and the terminal may use (checked with
#: `git show HEAD:finsim/finsim/app.py` on 2026-10-21: _browser_candidates at line 248, _ico_from_png at line 284; the
#: committed file has NO _ascii and its _win_special has no "Startup" case)
COMMITTED_OLDAPP_HELPERS = {"_browser_candidates", "_ico_from_png"}

HOME = r"C:\Users\logan\.finsim"
REPO = r"C:\Users\logan\intelligence-terminal\finsim"
PYW = r"C:\Users\logan\AppData\Local\Programs\Python\Python312\pythonw.exe"
NO_GIT = lambda tree: None   # install runs here from the repo's git working tree; its STARTUP-04 guard is tested in test_terminal_launch
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 10      # 8 + 10 = 18 bytes


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------------------------------- a JS-aware brace matcher
_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")


def js_block_end(src: str, start: int) -> int:
    """Index of the ``}`` matching the ``{`` at ``src[start]``; strings, comments and regex literals are skipped."""
    assert src[start] == "{", src[start:start + 20]
    depth, i, n, last = 0, start, len(src), ""
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"`":
            i += 1
            while i < n and src[i] != c:
                i += 2 if src[i] == "\\" else 1
            i += 1
            last = "s"
            continue
        if c == "/" and (last in _REGEX_AFTER or last == ""):
            i += 1
            in_class = False
            while i < n:
                ch = src[i]
                if ch == "\\":
                    i += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                i += 1
            i += 1
            while i < n and src[i].isalpha():
                i += 1
            last = "r"
            continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            # after these keywords a "/" starts a regex literal; after any other word or number it divides
            last = "(" if src[i:j] in ("return", "typeof", "case", "in", "of", "void", "delete", "new", "throw", "else", "do") else "w"
            i = j
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        if not c.isspace():
            last = c
        i += 1
    raise AssertionError("no matching brace")


def js_body(src: str, head_regex: str) -> str:
    """The text inside the braces of the first block whose head matches ``head_regex`` (e.g. ``function load\\(``)."""
    m = re.search(head_regex, src)
    assert m, "not found: " + head_regex
    open_at = src.index("{", m.end())
    return src[open_at + 1:js_block_end(src, open_at)]


def fn(src: str, name: str) -> str:
    return js_body(src, r"function\s+" + re.escape(name) + r"\s*\(")


def squash(s: str) -> str:
    return " ".join(s.split())


# ======================================================================================== (c) folders and ASCII text
class WinPathsTests(unittest.TestCase):
    APPDATA = r"C:\Users\x\AppData\Roaming"
    PROFILE = r"C:\Users\x"
    ENV = {"APPDATA": APPDATA, "USERPROFILE": PROFILE}

    def test_startup_falls_back_to_appdata_start_menu_programs_startup(self):
        # the known-folder API gives nothing -> %APPDATA% + \Microsoft\Windows\Start Menu\Programs\Startup
        got = winpaths.known_folder("Startup", lookup=lambda name: None, env=self.ENV, platform="win32")
        self.assertEqual(os.path.join(self.APPDATA, "Microsoft", "Windows", "Start Menu", "Programs", "Startup"), got)
        # ... and Programs is its parent, Desktop is %USERPROFILE%\Desktop
        self.assertEqual(os.path.join(self.APPDATA, "Microsoft", "Windows", "Start Menu", "Programs"),
                         winpaths.known_folder("Programs", lookup=lambda name: None, env=self.ENV, platform="win32"))
        self.assertEqual(os.path.join(self.PROFILE, "Desktop"),
                         winpaths.known_folder("Desktop", lookup=lambda name: None, env=self.ENV, platform="win32"))

    def test_the_known_folder_answer_wins_and_bad_answers_fall_back(self):
        onedrive = r"C:\Users\x\OneDrive\Desktop"
        asked = []

        def api(name):
            asked.append(name)
            return {"Desktop": onedrive, "Startup": "relative\\Startup"}.get(name)

        # the redirected (OneDrive) desktop comes from the API, not from %USERPROFILE%
        self.assertEqual(onedrive, winpaths.known_folder("Desktop", lookup=api, env=self.ENV, platform="win32"))
        # an answer that is not an absolute path is ignored -> the classic Startup location
        self.assertEqual(os.path.join(self.APPDATA, "Microsoft", "Windows", "Start Menu", "Programs", "Startup"),
                         winpaths.known_folder("Startup", lookup=api, env=self.ENV, platform="win32"))

        def broken(name):
            raise OSError("shell32 unavailable")
        self.assertEqual(os.path.join(self.APPDATA, "Microsoft", "Windows", "Start Menu", "Programs"),
                         winpaths.known_folder("Programs", lookup=broken, env=self.ENV, platform="win32"))
        # nothing known -> "" (never a path relative to the current folder); off Windows -> ""
        self.assertEqual(("", "", ""), winpaths.folders(lookup=lambda name: None, env={}, platform="win32"))
        self.assertEqual(("", "", ""), winpaths.folders(lookup=api, env=self.ENV, platform="linux"))
        self.assertEqual(["Desktop", "Startup"], asked)
        with self.assertRaises(ValueError):
            winpaths.known_folder("Documents", lookup=api, env=self.ENV, platform="win32")

    @unittest.skipUnless(sys.platform == "win32", "SHGetKnownFolderPath is Windows-only")
    def test_real_startup_folder_is_the_appdata_startup_folder(self):
        appdata = os.environ.get("APPDATA")
        self.assertTrue(appdata, "APPDATA is not set")
        want = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        api_answer = winpaths.shell_known_folder("Startup")              # the real API (read only)
        self.assertIsNotNone(api_answer)
        self.assertEqual(os.path.normcase(want), os.path.normcase(api_answer))
        startup, desktop, programs = app.real_folders()
        self.assertEqual(os.path.normcase(want), os.path.normcase(startup))
        # the COMMITTED finsim/app.py _win_special("Startup") answered the Programs folder: never again
        self.assertNotEqual(os.path.normcase(os.path.dirname(want)), os.path.normcase(startup))
        self.assertEqual(os.path.normcase(os.path.dirname(want)), os.path.normcase(programs))
        self.assertTrue(os.path.isabs(desktop))
        self.assertEqual(os.path.normcase(winpaths.shell_known_folder("Desktop") or desktop), os.path.normcase(desktop))


class VbsAsciiTests(unittest.TestCase):
    def test_generated_vbs_files_are_pure_ascii(self):
        files = app.launcher_files(HOME, PYW, REPO, 8765, icon_png=PNG)
        texts = {os.path.basename(k): v for k, v in files.items() if k.endswith(".vbs")}
        self.assertEqual({"FinSim Server.vbs", "FinSim Terminal.vbs"}, set(texts))
        for name, text in texts.items():
            self.assertIsInstance(text, str)
            self.assertEqual("", winpaths.non_ascii(text), name)
            self.assertEqual(text, text.encode("ascii").decode("ascii"))    # what install writes: content.encode("ascii")
        script = app.shortcut_script(HOME, [r"C:\Users\logan\OneDrive\Desktop", ""], REPO)
        script.encode("ascii")
        self.assertIn('For Each folder In Array("C:\\Users\\logan\\OneDrive\\Desktop")', script)   # the empty folder is dropped

    def test_typographic_punctuation_in_a_template_is_folded_to_ascii(self):
        # em dash -> "-", en dash -> "-", single quotes -> "'", double quotes -> '""' (inside VBScript text), ellipsis -> "..."
        self.assertEqual('a - b - c \'x\' ""y"" ...', winpaths.vbs_text("a \u2014 b \u2013 c \u2018x\u2019 \u201cy\u201d \u2026"))
        template = app._SERVER_VBS.replace("starts the local server at login.", "starts the local server at login \u2014 once.")
        self.assertNotEqual(app._SERVER_VBS, template)
        with mock.patch.object(app, "_SERVER_VBS", template):
            server = app.launcher_files(HOME, PYW, REPO, 8765, icon_png=PNG)[os.path.join(HOME, "FinSim Server.vbs")]
        self.assertIn("starts the local server at login - once.", server)
        server.encode("ascii")

    def test_a_path_is_never_folded_and_a_non_ascii_path_is_refused(self):
        # an en dash in a folder name is NOT turned into "-" (that would name another folder): refused instead
        with self.assertRaises(ValueError):
            winpaths.vb_string("C:\\Users\\Jean\u2013Luc\\.finsim")
        with self.assertRaises(ValueError):
            app.launcher_files("C:\\Users\\Jean\u2013Luc\\.finsim", PYW, REPO, 8765, icon_png=PNG)
        with self.assertRaises(ValueError) as cm:
            app.shortcut_script(HOME, ["C:\\Users\\Jos\u00e9\\Desktop"], REPO)
        str(cm.exception).encode("ascii")                                 # the message itself prints on any console
        with self.assertRaises(ValueError):
            winpaths.vbs_text("caf\u00e9")
        self.assertEqual('C:\\a""b', winpaths.vb_string('C:\\a"b'))       # one quote -> two quotes

    def test_app_uses_only_helpers_the_committed_old_app_has(self):
        used = set(re.findall(r"_oldapp\.(\w+)", read(APP_PY)))
        self.assertEqual(COMMITTED_OLDAPP_HELPERS, used)
        old = importlib.import_module("finsim.app")
        for name in COMMITTED_OLDAPP_HELPERS:
            self.assertTrue(callable(getattr(old, name, None)), name)
        # run the launcher against a stand-in for the COMMITTED module: only those helpers, and a _win_special that
        # answers wrongly (the committed one has no Startup case) - the terminal must not call it
        stub = types.SimpleNamespace(_browser_candidates=old._browser_candidates, _ico_from_png=old._ico_from_png,
                                     _win_special=lambda name: self.fail("terminal.app called _oldapp._win_special"))
        with mock.patch.object(app, "_oldapp", stub):
            files = app.launcher_files(HOME, PYW, REPO, 8765, icon_png=PNG)
            app.shortcut_script(HOME, [r"C:\d", r"C:\p"], REPO).encode("ascii")
            folders = app.real_folders()
        self.assertEqual(3, len(files))
        self.assertEqual(b"\x00\x00\x01\x00\x01\x00", files[os.path.join(HOME, "finsim.ico")][:6])   # ICONDIR via _ico_from_png
        self.assertEqual(3, len(folders))


# ================================================================================ (c) install / uninstall folder guards
class FakeHttp:
    def __init__(self, health=None):
        self.base = "http://127.0.0.1:8765/"
        self.current = health
        self.shutdowns = 0

    def health(self, timeout=1.0):
        return self.current

    def shutdown(self, timeout=3.0):
        self.shutdowns += 1
        return True


class NoProcessApi:
    def open(self, pid):
        return None

    def exit_code(self, handle):
        return None

    def image(self, handle):
        return ""

    def close(self, handle):
        pass


def terminal_health(**over) -> dict:
    h = {"app": APP_ID, "version": VERSION, "schema": 1, "pid": 4242, "uptime_s": 61, "started_ts": "t0", "db_path": "", "db_size": 0,
         "state_version": 1, "offline": False, "integrity": {"ok": True, "hold": False, "checked_at": "t1", "problems": []},
         "providers": {}, "workers": {"quotes": {"alive": True, "last_tick_ts": "t2", "errors_1h": 0}},
         "lifecycle": {"due": 0, "failing": []}, "attention_open": 0}
    h.update(over)
    return h


class InstallFolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-uiops-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self._env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV, config.PORT_ENV)}
        os.environ[config.HOME_ENV] = self.home
        os.environ[config.DB_ENV] = os.path.join(self.home, "terminal.db")
        os.environ.pop(config.PORT_ENV, None)
        self.out = []
        self.cmds = []

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

    def test_install_refuses_when_the_startup_folder_is_unknown(self):
        rc = app.cmd_install(tree_check=NO_GIT, open_after=False, http=FakeHttp(None), api=NoProcessApi(), home=self.home, run=self.run_cmd,
                             sleep=lambda s: None, out=self.out.append, folders=("", "", ""), starter=lambda *a, **k: self.fail("started"),
                             stamp="s")
        self.assertEqual(app.EXIT_NOT_RUNNING, rc)
        self.assertIn("the Startup folder cannot be resolved", "\n".join(self.out))
        self.assertEqual([], os.listdir(self.home))                     # nothing written
        self.assertEqual([], self.cmds)                                   # nothing run

    def test_a_non_ascii_desktop_skips_the_shortcuts_and_the_install_goes_on(self):
        startup = os.path.join(self.tmp, "startup")
        programs = os.path.join(self.tmp, "programs")
        desktop = os.path.join(self.tmp, "Jos\u00e9", "Desktop")         # not ASCII: no shortcut script can name it
        rc = app.cmd_install(tree_check=NO_GIT, open_after=False, http=FakeHttp(terminal_health()), api=NoProcessApi(), home=self.home, run=self.run_cmd,
                             sleep=lambda s: None, out=self.out.append, folders=(startup, desktop, programs),
                             starter=lambda *a, **k: self.fail("the terminal already serves this code"), stamp="s")
        text = "\n".join(self.out)
        self.assertEqual(app.EXIT_OK, rc)
        self.assertIn("shortcuts: not created (", text)
        self.assertEqual([], [c for c in self.cmds if c[0] == "cscript.exe"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "make-terminal-shortcuts.vbs")))
        with open(os.path.join(startup, app.SERVER_VBS), "rb") as f:
            f.read().decode("ascii")                                      # the Startup entry was still written, ASCII
        self.assertIn("The final PowerShell sequence", text)

    def test_uninstall_never_removes_relative_paths_for_unknown_folders(self):
        decoys = os.path.join(self.tmp, "cwd")
        os.makedirs(decoys)
        for name in (app.SERVER_VBS, app.SHORTCUT_NAME):
            with open(os.path.join(decoys, name), "w", encoding="ascii") as f:
                f.write("decoy")
        cwd = os.getcwd()
        os.chdir(decoys)
        try:
            rc = app.cmd_uninstall(http=FakeHttp(None), api=NoProcessApi(), home=self.home, run=self.run_cmd, sleep=lambda s: None,
                                   ask=lambda prompt: self.fail("no purge asked"), out=self.out.append, folders=("", "", ""))
        finally:
            os.chdir(cwd)
        self.assertEqual(app.EXIT_OK, rc)
        for name in (app.SERVER_VBS, app.SHORTCUT_NAME):
            self.assertTrue(os.path.exists(os.path.join(decoys, name)), name)
        self.assertIn("its folder could not be resolved", "\n".join(self.out))


# ========================================================================================== (b) status: provider states
class StatusProvidersTests(unittest.TestCase):
    NOW = 1792587600     # 2026-10-21 09:00:00 EDT: 20,747 days x 86,400 = 1,792,540,800 (00:00 UTC) + 13 h x 3,600 = 46,800
    # (20,747 = 56 x 365 + 14 leap days for 1970..2025 = 20,454, + 273 days Jan..Sep 2026 + 20 days of October)

    def status(self, health):
        lines, code = app.status_text(health, code_version=VERSION, port=8765, home=r"C:\h", db=r"C:\h\terminal.db", db_size=0,
                                      log_path=r"C:\h\terminal.log", log_tail_lines=["tail line"], lock=("free", None),
                                      startup_entry=(r"C:\s\FinSim Server.vbs", "terminal"), backups=0)
        return lines, code

    def providers_line(self, lines):
        found = [l for l in lines if l.startswith("providers: ")]
        self.assertEqual(1, len(found), lines)
        return found[0]

    def health_from(self, ph):
        from finsim.terminal import api                                   # the real /api/health codec (adds *_ny)
        return api.encode(terminal_health(providers=ph.snapshot()))

    def test_a_fresh_server_reports_idle_providers_not_down(self):
        from finsim.terminal.quotes.http import PROVIDERS, ProviderHealth
        ph = ProviderHealth(lambda: self.NOW)
        snap = ph.snapshot()
        self.assertEqual({None}, {r["ok"] for r in snap.values()})         # what a fresh server's /api/health carries
        self.assertEqual({False}, {r["down"] for r in snap.values()})
        lines, code = self.status(self.health_from(ph))
        line = self.providers_line(lines)
        self.assertEqual(0, code)
        self.assertEqual("providers: " + "; ".join(f"{p} idle (no request yet)" for p in PROVIDERS), line)
        self.assertNotIn("DOWN", line)
        self.assertNotIn("(error)", line)

    def test_ok_failing_and_breaker_open_with_the_last_error(self):
        from finsim.terminal.quotes.http import DOWN_FAIL_S, FAILS_TO_OPEN, ProviderHealth
        ph = ProviderHealth(lambda: self.NOW)
        ph.record("yahoo", True, 200)
        for _ in range(FAILS_TO_OPEN):                                    # 5 failures open the breaker for DOWN_FAIL_S = 120 s
            ph.record("cboe", False, 503)
        ph.record("treasury", False, None, "timeout")                     # one failure: breaker still closed
        self.assertEqual((5, 120), (FAILS_TO_OPEN, DOWN_FAIL_S))
        line = self.providers_line(self.status(self.health_from(ph))[0])
        self.assertIn("yahoo ok;", line)
        # paused until NOW + 120 = 1,792,587,720 = 2026-10-21 09:02:00 EDT; last_error "HTTP 503" is the provider's text
        self.assertIn("cboe DOWN (HTTP 503; 5 errors in a row; paused until 2026-10-21 09:02:00 ET);", line)
        self.assertIn("treasury DOWN (timeout; 1 error in a row);", line)
        self.assertIn("fred idle (no request yet)", line)

    def test_provider_state_shapes(self):
        self.assertEqual("idle (no request yet)", app.provider_state({"ok": None, "down": False}))
        self.assertEqual("idle (no request yet)", app.provider_state(None))
        self.assertEqual("ok", app.provider_state({"ok": True, "down": False}))
        self.assertEqual("DOWN (no error text recorded)", app.provider_state({"ok": False}))      # never an invented error
        self.assertEqual("DOWN (HTTP 429; paused by the circuit breaker)", app.provider_state({"ok": False, "down": True, "last_error": "HTTP 429"}))
        # without the codec's *_ny field the epoch is converted here: NOW + 60 = 1,792,587,660 = 2026-10-21 09:01:00 EDT
        self.assertEqual("DOWN (HTTP 429; 1 error in a row; paused until 2026-10-21 09:01:00 ET)",
                         app.provider_state({"ok": False, "down": True, "last_error": "HTTP 429", "consecutive_errors": 1,
                                             "down_until_ts": self.NOW + 60}))
        self.assertEqual("DOWN (timeout) refused_ua", app.provider_state({"ok": False, "last_error": "timeout", "refused_ua": True}))
        self.assertEqual("ok refused_ua", app.provider_state({"ok": True, "refused_ua": True}))

    def test_epoch_times_print_as_new_york_time(self):
        # checked_at 1,792,587,600 = 2026-10-21 09:00:00 EDT (worked at NOW above); a missing worker tick prints "?"
        lines, _ = self.status(terminal_health(integrity={"ok": True, "hold": False, "checked_at": self.NOW, "problems": []},
                                               workers={"matcher": {"alive": True, "last_tick_ts": None, "errors_1h": 0}}))
        text = "\n".join(lines)
        self.assertIn("integrity: ok (checked 2026-10-21 09:00:00 ET)", text)
        self.assertIn("workers: matcher alive (last tick ?, errors_1h 0)", text)

    def test_integrity_not_checked_yet_is_not_failed(self):
        lines, code = self.status(terminal_health(integrity={"ok": None, "hold": False, "checked_at": None, "problems": []}))
        text = "\n".join(lines)
        self.assertEqual(0, code)
        self.assertIn("integrity: not checked yet", text)
        self.assertNotIn("FAILED", text)
        self.assertNotIn("| tail line", text)                              # healthy otherwise -> no log tail
        lines, _ = self.status(terminal_health(integrity={"ok": False, "hold": True, "checked_at": "t", "problems": ["L3"]}))
        self.assertIn("integrity: FAILED problems=['L3'] hold=True (checked t)", "\n".join(lines))
        self.assertIn("  | tail line", "\n".join(lines))


# ============================================================================ (a) the Markets re-render (static checks)
class MarketsRerenderStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = read(MARKETS_JS)

    def test_ascii_and_the_poll_is_the_page_poller_calling_load(self):
        self.assertEqual("", winpaths.non_ascii(self.src))
        self.assertIn("T.poll.page('markets', 30000, load);", self.src)   # first load + every 30 s, retried after a failure

    def test_a_poll_answer_never_clears_the_page_once_it_is_painted(self):
        load = fn(self.src, "load")
        self.assertIn("show();", load)
        clears = [l for l in load.splitlines() if "T.clear(" in l]
        self.assertEqual(1, len(clears))
        self.assertIn("if (!ui)", clears[0])                               # only before the first paint (an error box)
        self.assertIn("ui.err", load)                                      # a failed refresh is shown inline, rows stay

    def test_same_shape_refreshes_in_place_and_keeps_the_filter_box(self):
        show = squash(fn(self.src, "show"))
        self.assertIn("if (ui && ui.shape === shape(data)) { refresh(); return; }", show)
        self.assertIn("T.components.grid.busy()", show)                   # a layout rebuild waits for a pressed pointer
        self.assertLess(show.index("refresh();"), show.index("paintAll();"))
        refresh = fn(self.src, "refresh")
        self.assertIn("grid.update(data.rows || [])", refresh)
        for banned in ("T.clear(", "grid.mount(", "paintAll(", "T.mountOptional(", "T.h('input'"):
            self.assertNotIn(banned, refresh, banned)                     # nothing the viewer types into is rebuilt

    def test_a_layout_rebuild_carries_the_filter_scroll_selection_and_box_over(self):
        paint = fn(self.src, "paintAll")
        self.assertIn("var carry = ui ? keep() : null;", paint)
        self.assertIn("restore: carry && carry.grid", paint)
        self.assertIn("rowKey: function (r) { return r.instrument_id; }", paint)   # the selection follows the instrument
        self.assertIn("carryOver(carry);", paint)
        keep = fn(self.src, "keep")
        self.assertIn("grid.state()", keep)
        self.assertIn("box.value", keep)
        self.assertIn("main.scrollTop", keep)
        carry = fn(self.src, "carryOver")
        self.assertIn("ui.box.value = k.box.value", carry)
        self.assertIn("main.scrollTop = k.mainTop", carry)
        self.assertEqual(2, self.src.count("T.components.grid.mount("))  # the page grid (paintAll) + section grids
        self.assertEqual(1, paint.count("T.components.grid.mount("))

    def test_the_quote_listener_updates_cells_and_quotes_never_go_backwards(self):
        listener = js_body(self.src, r"T\.state\.on\('quotes',\s*function\s*\(q\)\s*")
        self.assertIn("grid.update(data.rows || [])", listener)
        for banned in ("paintAll(", "T.clear(", "show("):
            self.assertNotIn(banned, listener, banned)
        newer = squash(fn(self.src, "newer"))
        self.assertIn("ta[0] !== tb[0] ? ta[0] > tb[0] : ta[1] >= tb[1]", newer)   # provider time, then fetch time
        self.assertIn("mergeQuote(r, known[r.instrument_id])", fn(self.src, "load"))
        self.assertIn("dead = true", js_body(self.src, r"T\.pages\.markets\.unmount = function\s*\(\)\s*"))


class GridRerenderStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = read(GRID_JS)
        cls.mount = fn(cls.src, "mount")

    def test_ascii_and_one_filter_box_per_grid(self):
        self.assertEqual("", winpaths.non_ascii(self.src))
        self.assertEqual(1, self.mount.count("T.h('input'"))             # created once, at mount
        update = js_body(self.mount, r"handle\.update = function\s*\(rows\)\s*")
        self.assertEqual("allRows = rows || []; render();", squash(update))   # update never touches the filter
        render = fn(self.mount, "render")
        self.assertNotIn("input.value", render)
        self.assertNotIn("state.filter =", render)
        self.assertIn("state.filter = input.value", self.mount)            # only the viewer's typing sets the filter

    def test_render_holds_while_pressed_and_patches_cells_in_place(self):
        render = squash(fn(self.mount, "render"))
        self.assertIn("if (pressed === self) { pending = true; return; }", render)
        self.assertLess(render.index("pending = true"), render.index("build()"))
        self.assertIn("if (!(sameShape(drawn, model) && patch(model))) { paint(model); }", render)
        patch = squash(fn(self.mount, "patch"))
        self.assertIn("if (c.html !== o.html) { td.innerHTML = c.html; }", patch)   # unchanged cells (the b / s buttons) stay
        self.assertNotIn("table.innerHTML", patch)
        paint = fn(self.mount, "paint")
        self.assertIn("scroll.scrollTop = top", paint)                    # a full repaint keeps the scroll position
        hook = squash(fn(self.src, "hook"))
        self.assertIn("['pointerup', 'pointercancel', 'mouseup', 'dragend'].forEach", hook)
        self.assertIn("document.addEventListener(n, up, true)", hook)
        release = squash(js_body(self.mount, r"release: function\s*\(delay\)\s*"))
        self.assertIn("setTimeout(", release)                              # the held render runs AFTER the click task
        press = squash(fn(self.mount, "press"))
        self.assertIn("pressTimer = setTimeout(", press)                  # a lost pointerup cannot hold renders forever

    def test_the_selection_follows_the_row_key_and_state_round_trips(self):
        self.assertIn("var keys = keysOf(vis);", fn(self.mount, "build"))
        keys = squash(fn(self.mount, "keysOf"))
        self.assertIn("if (n[k] > 1 && cols.length) { k += '|#' + cellText(cols[0], vis[i]); }", keys)   # repeated id keys
        self.assertIn("return seen[k] > 1 ? k + '~' + seen[k] : k;", keys)                               # still repeated
        follow = squash(fn(self.mount, "followSel"))
        self.assertIn("state.sel = keys.indexOf(state.selKey);", follow)
        self.assertIn("if (state.sel < 0) { state.selKey = null; }", follow)   # gone -> cleared, never moved
        select = fn(self.mount, "select")
        self.assertIn("state.selKey = ", select)
        restore = js_body(self.mount, r"handle\.restore = function\s*\(s\)\s*")
        self.assertIn("input.value = state.filter", restore)
        self.assertIn("scroll.scrollTop = s.scrollTop", restore)
        state = js_body(self.mount, r"handle\.state = function\s*\(\)\s*")
        for k in ("filter: state.filter", "selKey: state.selKey", "scrollTop: scroll.scrollTop"):
            self.assertIn(k, state)
        self.assertIn("if (spec.restore) { handle.restore(spec.restore); }", self.mount)
        self.assertIn("busy: function () { return pressed !== null; }", self.src)

    def test_the_brace_matcher_reads_these_files_whole(self):
        for src in (self.src, read(MARKETS_JS)):
            start = src.index("(function () {") + len("(function () ")
            self.assertEqual(src.rindex("})();"), js_block_end(src, start))   # the IIFE closes where the file ends


if __name__ == "__main__":
    unittest.main()
