"""FinSim Live Terminal — launcher, CLI and installer (TERMINAL_DESIGN.md §18; TERMINAL_WORKPLAN.md WP-14).

``python -m finsim <command>`` (run from ``C:\\Users\\logan\\intelligence-terminal\\finsim``) dispatches to ``main`` here;
``finsim/__main__.py`` is re-pointed and the old ``demo`` / ``phone`` subcommands are not exposed.  Commands and exit
codes (§18):

=========  ================================================================================  ========================
command    what it does                                                                      exit
=========  ================================================================================  ========================
serve      the foreground server (``finsim.terminal.server.serve``).  ``--offline`` is        0 ok; 3 another instance
           refused on the default home; ``--db`` named ``finsim.db`` is refused (the old      or the port; 4 newer
           simulation's saves are never the terminal's database); ``--host`` must be loopback  schema; 5 startup error
stop       ``POST /api/shutdown`` (the OLD 0.11.0 engine answers the same route), waits for   0 stopped; 1 nothing
           the port to go quiet and the lock to release, judges the lock-file PID with         running; 2 still alive
           ``OpenProcess`` + ``GetExitCodeProcess == 259`` + the image name; ``--force`` →
           ``taskkill /PID`` only when both hold (never ``os.kill``); prints finsim.db's sha256
status     served version vs the code on disk ("restart to serve the new code"), the ``app``   0 the terminal answers;
           marker ("a non-terminal server answers on 8765"), pid, uptime, db, the log tail    1 otherwise
           when down or unhealthy, lock, workers, providers, integrity, lifecycle, Startup
           entry, backups, ``sha256(finsim.db)`` against the install baseline
install    ``install_plan`` in order: sha256 baseline of ``finsim.db`` → stop the old engine   0 the terminal is up at
           → delete the ``FinSim Server`` task and the old VBS files (backed up first) →        the end; 1 otherwise
           write ``FinSim Server.vbs`` / ``FinSim Terminal.vbs`` / ``finsim.ico`` → copy the
           server VBS into the Startup folder (replacing the old entry of that name) →
           Desktop + Start Menu ``FinSim.lnk`` → start through the VBS, wait for the marker
           → status → the PowerShell sequence → sha256 again ("unchanged")
open       opens the app window; when the terminal is not answering it starts the server       0 / 1
           through ``FinSim Server.vbs`` first (never a second server: the lock and the
           exclusive bind make a duplicate exit 3, which ``open`` reports with the log tail)
uninstall  removes the Startup entry, shortcuts, VBS files, icon, window profile; stops the    0
           terminal; ``--purge`` deletes ``terminal.db`` only after the owner types its full
           path (``finsim.db`` can never be named)
legacy     copies ``<home>/finsim.db`` to ``<home>/legacy/legacy-view.db`` with the sqlite3     0 / 1 (no finsim.db)
           backup API (once; ``--refresh`` re-copies) and runs the OLD ``finsim.api.server``
           on the COPY (port 8766): the read-only archive view; ``finsim.db`` is never served
=========  ================================================================================  ========================

Conventions stated once:

* Everything that decides *what* to do is a pure function tested without the OS: ``launcher_files``,
  ``shortcut_script``, ``install_plan``, ``uninstall_plan``, ``legacy_plan``, ``status_text``, ``offline_refusal``,
  ``db_refusal``, ``host_refusal``, ``purge_confirmed``, ``pythonw_for``, ``process_state`` / ``lock_state`` (with an
  injected process API).  The ``cmd_*`` functions execute those plans with injectable collaborators (``http``,
  ``api``, ``run``, ``sleep``, ``starter``, ``opener``, ``folders``, ``out``) so the tests never touch the network, the
  real port, the real Startup folder or ``~/.finsim``.
* The VBS files are ASCII (Windows Script Host reads .vbs as ANSI); quotes inside paths are doubled
  (``winpaths.vb_string``); a path that is not ASCII raises instead of producing a broken launcher (a path is never
  folded: a folded path names another folder).  The Startup / Desktop / Start Menu folders come from
  ``winpaths.folders()`` (``SHGetKnownFolderPath`` with the classic fallback).  This module depends on the old
  ``finsim/app.py`` only for helpers present in its COMMITTED version (``_browser_candidates``, ``_ico_from_png``);
  ``_ascii`` and the ``Startup`` case of ``_win_special`` exist only in another work stream's uncommitted edits.
* The served process is ``pythonw.exe -m finsim serve --port 8765`` (the shared-machine capture guard exempts
  ``-m finsim serve … --port 8765``; TERMINAL_DECISIONS §17).  ``--host`` and ``--db`` are accepted so the longer
  documented form also parses.
* Liveness is never judged with ``os.kill(pid, 0)`` (on Windows it terminates the process).  A PID whose
  ``OpenProcess`` fails is reported as not alive; a live PID whose image name does not contain ``python`` is never
  killed (PID-reuse guard).
* The lock file ``<home>/terminal.lock`` holds ``{pid, port, started_ts, version}`` written by the server.  When the
  server's byte-range lock covers the JSON, other processes cannot read it: that is reported as ``held`` with an
  unknown pid rather than guessed.
* ``finsim.db`` (the old simulation) is opened by this module only read-only: for ``sha256`` and as the *source*
  of the sqlite3 backup that ``legacy`` serves.  The copy lives in ``<home>/legacy/`` because the old server rotates
  ``finsim-*.db`` snapshots in ``<dirname(db)>/backups`` (newest ten), which must never touch ``<home>/backups`` —
  the folder holding the owner's real ``finsim.db`` snapshots and the ``.bak`` copies ``install`` makes for the
  rollback.
* ``install`` takes the sha256 baseline before anything else (§18 step 1).  If a FOREIGN server (the old engine) had to
  be stopped in step 2 and ``finsim.db`` changed meanwhile, that change is the old engine folding its own
  write-ahead log into the file at shutdown; the baseline is re-taken and the re-take is printed, so the final
  "unchanged" verdict speaks only about what the terminal did.  A change after stopping the TERMINAL is never
  explained away.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import APP_ID, VERSION, config, winpaths
from .. import app as _oldapp     # only helpers in the COMMITTED finsim/app.py: _browser_candidates, _ico_from_png

APP_NAME = "FinSim"
SERVER_VBS = "FinSim Server.vbs"        # the Startup-folder entry: the same name REPLACES the old 0.11.0 entry
TERMINAL_VBS = "FinSim Terminal.vbs"    # what the Desktop / Start Menu shortcut runs
ICON_NAME = "finsim.ico"
SHORTCUT_NAME = "FinSim.lnk"
OLD_TASK = "FinSim Server"              # the old installer's scheduled task (schtasks)
OLD_HOME_FILES = ("finsim-server.vbs", "finsim-open.vbs", "make-shortcuts.vbs")   # the old launcher's files in <home>
WINDOW_PROFILE = "terminal-window-profile"
LEGACY_DIR = "legacy"
LEGACY_VIEW_DB = "legacy-view.db"
BASELINE_NAME = "finsim.db.sha256"
LAUNCHER_LOG = "launcher.log"
STILL_ACTIVE = 259                      # GetExitCodeProcess of a live process
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
EXIT_OK, EXIT_NOT_RUNNING, EXIT_STILL_ALIVE, EXIT_INSTANCE, EXIT_SCHEMA, EXIT_STARTUP = 0, 1, 2, 3, 4, 5
START_WAIT_S = 60.0
STOP_WAIT_S = 30.0
PY = "py -3.12 -m finsim"

# ORC-9: the version in the printed sequence is finsim.terminal.VERSION (never a literal that goes stale at the next release)
POWERSHELL_SEQUENCE = f"""cd C:\\Users\\logan\\intelligence-terminal\\finsim
py -3.12 -m finsim stop                 # stops the OLD 0.11.0 engine too (same /api/shutdown route); prints the finsim.db sha256
py -3.12 -m finsim install --no-open    # replaces Startup\\FinSim Server.vbs, removes the "FinSim Server" task, starts {VERSION}
py -3.12 -m finsim status               # must show finsim-terminal {VERSION}, integrity ok, the Startup entry, finsim.db unchanged
py -3.12 -m finsim open"""

Folders = Tuple[str, str, str]          # (Startup, Desktop, Start Menu Programs)


# ------------------------------------------------------------------------------------------------------------ paths
def url(port: Optional[int] = None) -> str:
    return f"http://127.0.0.1:{port or config.port()}/"


def repo_dir() -> str:
    """The folder that holds the ``finsim`` package (``…\\intelligence-terminal\\finsim``): PYTHONPATH and cwd of the server."""
    return os.path.dirname(os.path.dirname(config.package_dir()))


def old_db_path(home: Optional[str] = None) -> str:
    """``<home>/finsim.db`` — the OLD simulation's saves; hashed, backed up, never opened for writing here."""
    return os.path.join(home or config.home(), "finsim.db")


def baseline_path(home: Optional[str] = None) -> str:
    return os.path.join(home or config.home(), BASELINE_NAME)


def real_folders() -> Folders:
    """The user's Startup, Desktop and Start Menu Programs folders through ``SHGetKnownFolderPath`` (OneDrive- and
    redirection-aware), each falling back to its classic location (``winpaths.known_folder``); ``""`` = unknown."""
    return winpaths.folders()


def icon_png_path() -> str:
    """``finsim/finsim/static/icon-256.png`` — the old app's icon, wrapped into ``finsim.ico`` at install."""
    return os.path.join(os.path.dirname(config.package_dir()), "static", "icon-256.png")


def sha256_file(path: str) -> Optional[str]:
    """Hex digest of a file read in 1 MiB chunks, or None when it does not exist (never raises for a missing file)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _say(*parts: object, out: Optional[Callable[..., None]] = None) -> None:
    """print() that stays silent under pythonw (sys.stdout is None there) instead of failing."""
    if out is not None:
        out(" ".join(str(p) for p in parts))
    elif sys.stdout is not None:
        print(*parts, flush=True)


def _launcher_log(home: str, text: str) -> None:
    """Append one line to ``<home>/launcher.log`` (the only trace a pythonw-launched ``open`` leaves on failure)."""
    try:
        os.makedirs(home, exist_ok=True)
        with open(os.path.join(home, LAUNCHER_LOG), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip() + "\n")
    except OSError:
        pass


def log_tail(path: str, lines: int = 15) -> List[str]:
    """The last ``lines`` lines of a log (the final 8000 bytes decoded leniently); [] when there is no log."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8000))
            return f.read().decode("utf-8", "replace").splitlines()[-lines:]
    except OSError:
        return []


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), stdin=subprocess.DEVNULL, capture_output=True, text=True)


# ------------------------------------------------------------------------------------------------ loopback HTTP
class Http:
    """The two loopback calls every command needs; tests inject a fake with the same two methods and ``base``."""

    def __init__(self, port: Optional[int] = None):
        self.base = url(port)

    def health(self, timeout: float = 1.0) -> Optional[dict]:
        """``GET /api/health`` as a dict, or None when nothing answers (connection refused, timeout, bad JSON)."""
        try:
            with urllib.request.urlopen(self.base + "api/health", timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:   # noqa: BLE001 - any failure means "not answering"
            return None

    def shutdown(self, timeout: float = 3.0) -> bool:
        """``POST /api/shutdown`` (loopback-only on both the terminal and the old engine); True when it was accepted."""
        try:
            req = urllib.request.Request(self.base + "api/shutdown", method="POST", data=b"{}",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                r.read()
            return True
        except Exception:   # noqa: BLE001
            return False


def is_terminal(health: Optional[dict]) -> bool:
    """The marker every terminal health carries: ``{"app": "finsim-terminal"}``.  The old engine has no ``app`` key."""
    return bool(health) and health.get("app") == APP_ID


def describe_server(health: Optional[dict]) -> str:
    if health is None:
        return "nothing"
    if is_terminal(health):
        return f"{APP_ID} {health.get('version', '?')}"
    return f"a non-terminal server (engine {health.get('engine_version', '?')})"


# ------------------------------------------------------------------------------------- process liveness (no os.kill)
class WinProcessApi:
    """``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` / ``GetExitCodeProcess`` / ``QueryFullProcessImageNameW``
    through ctypes.  Only constructed on Windows; tests use a fake with the same four methods."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ctypes, self._wt = ctypes, wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k = k

    def open(self, pid: int):
        h = self.k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        return h or None

    def exit_code(self, handle) -> Optional[int]:
        code = self._wt.DWORD()
        return code.value if self.k.GetExitCodeProcess(handle, self._ctypes.byref(code)) else None

    def image(self, handle) -> str:
        buf = self._ctypes.create_unicode_buffer(1024)
        size = self._wt.DWORD(1024)
        return buf.value if self.k.QueryFullProcessImageNameW(handle, 0, buf, self._ctypes.byref(size)) else ""

    def close(self, handle) -> None:
        self.k.CloseHandle(handle)


class UnknownProcessApi:
    """Non-Windows fallback: liveness cannot be verified the Windows way, so nothing is ever reported alive or killed."""

    def open(self, pid: int):
        return None

    def exit_code(self, handle):
        return None

    def image(self, handle) -> str:
        return ""

    def close(self, handle) -> None:
        pass


def default_process_api():
    return WinProcessApi() if sys.platform == "win32" else UnknownProcessApi()


def process_state(pid: Optional[int], api) -> Tuple[bool, str]:
    """``(alive, image_path)``: alive only when ``OpenProcess`` succeeds AND ``GetExitCodeProcess`` says
    ``STILL_ACTIVE`` (259).  A pid that cannot be opened (gone, or not ours) is not alive.  The handle is always closed."""
    if not pid:
        return False, ""
    h = api.open(int(pid))
    if not h:
        return False, ""
    try:
        code = api.exit_code(h)
        image = api.image(h) or ""
    finally:
        api.close(h)
    return code == STILL_ACTIVE, image


def process_alive(pid: Optional[int], api) -> bool:
    return process_state(pid, api)[0]


def is_python_process(pid: Optional[int], api) -> bool:
    """Alive AND the image file name contains ``python`` (``python.exe`` / ``pythonw.exe``) — the PID-reuse guard."""
    alive, image = process_state(pid, api)
    return alive and "python" in os.path.basename(image).lower()


def force_kill_allowed(pid: Optional[int], api) -> bool:
    """``stop --force`` may ``taskkill`` only when liveness and the image name BOTH hold (never blindly)."""
    return is_python_process(pid, api)


def lock_info(path: str) -> Tuple[str, Optional[dict]]:
    """Read ``<home>/terminal.lock``: ``("absent", None)``, ``("ok", {pid, port, started_ts, version})``,
    ``("locked", None)`` when the holder's byte-range lock makes it unreadable (Windows ERROR_LOCK_VIOLATION 33 /
    PermissionError), or ``("unreadable", None)`` for a present file without valid JSON."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return "absent", None
    except PermissionError:
        return "locked", None
    except OSError as e:
        return ("locked" if getattr(e, "winerror", None) == 33 else "unreadable"), None
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return "unreadable", None
    return ("ok", data) if isinstance(data, dict) else ("unreadable", None)


def lock_state(home: str, api) -> Tuple[str, Optional[dict]]:
    """``("free", None)`` no lock file; ``("held", info)`` the recorded pid is alive (or the file is byte-locked by a
    live holder, pid unknown); ``("stale", info)`` the file exists but its pid is not alive (the server died; the OS
    released the byte lock, only the file remains); ``("unreadable", None)`` a file without JSON and no live lock."""
    state, info = lock_info(os.path.join(home, "terminal.lock"))
    if state == "absent":
        return "free", None
    if state == "locked":
        return "held", {"pid": None, "locked": True}
    if state == "unreadable":
        return "unreadable", None
    pid = info.get("pid") if info else None
    return ("held" if process_alive(pid, api) else "stale"), info


# ------------------------------------------------------------------------------------------- pure plans and files
def pythonw_for(python_exe: str, exists: Callable[[str], bool] = os.path.exists, localappdata: Optional[str] = None) -> str:
    """``pythonw.exe`` next to the interpreter ``py -3.12`` reported; fallback the standard per-user 3.12 install."""
    if python_exe:
        w = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
        if exists(w):
            return w
    base = localappdata or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "Programs", "Python", "Python312", "pythonw.exe")


def resolve_pythonw(run: Callable[[Sequence[str]], subprocess.CompletedProcess] = _run) -> str:
    """``py -3.12 -c "import sys;print(sys.executable)"`` → ``pythonw_for``.  The absolute path goes into the VBS."""
    exe = ""
    try:
        r = run(["py", "-3.12", "-c", "import sys;print(sys.executable)"])
        if r.returncode == 0:
            exe = (r.stdout or "").strip()
    except OSError:
        exe = ""
    return pythonw_for(exe, localappdata=os.environ.get("LOCALAPPDATA"))


_SERVER_VBS = """' FinSim Paper Terminal - starts the local server at login. If the terminal already answers it does nothing; if the
' OLD simulation engine answers (no "finsim-terminal" marker) it asks it to shut down first, then starts the terminal.
' Written by "python -m finsim install" (finsim.terminal.app.launcher_files). ASCII only: Windows Script Host reads
' .vbs files as ANSI. The single-instance lock and the exclusive bind are the guard; this probe is a courtesy.
Option Explicit
Dim sh, fso, http, state, py, repo, home, txt
Set sh = CreateObject("WScript.Shell"): Set fso = CreateObject("Scripting.FileSystemObject")
py = "{PYTHONW}": repo = "{REPO_FINSIM_DIR}": home = "{FINSIM_HOME}"
If Not fso.FileExists(py) Then WScript.Quit 1
If Not fso.FileExists(repo & "\\finsim\\terminal\\__init__.py") Then WScript.Quit 1
state = "down"
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0"): http.setTimeouts 1000, 1000, 2000, 2000
http.Open "GET", "http://127.0.0.1:{PORT}/api/health", False: http.Send
If Err.Number = 0 Then
  If http.Status = 200 Then
    txt = http.responseText
    If InStr(txt, \"\"\"finsim-terminal\"\"\") > 0 Then state = "terminal" Else state = "other"
  End If
End If
Err.Clear
If state = "other" Then
  http.Open "POST", "http://127.0.0.1:{PORT}/api/shutdown", False: http.setRequestHeader "Content-Type", "application/json": http.Send "{{}}"
  WScript.Sleep 5000
End If
On Error GoTo 0
If state = "terminal" Then WScript.Quit 0
sh.Environment("Process")("PYTHONPATH") = repo
sh.Environment("Process")("FINSIM_HOME") = home
sh.CurrentDirectory = repo
sh.Run \"\"\"\" & py & \"\"\" -m finsim serve --port {PORT}", 0, False
"""

_TERMINAL_VBS = """' FinSim Paper Terminal - opens the app window, starting the local server first if it is not running.
' What the Desktop / Start Menu shortcut "FinSim" runs: "python -m finsim open" probes /api/health, starts the server
' through "FinSim Server.vbs" when the terminal does not answer, then opens the Chrome / Edge / Brave app window.
' Written by "python -m finsim install" (finsim.terminal.app.launcher_files). ASCII only (Windows Script Host reads ANSI).
Option Explicit
Dim sh, fso, py, repo, home
Set sh = CreateObject("WScript.Shell"): Set fso = CreateObject("Scripting.FileSystemObject")
py = "{PYTHONW}": repo = "{REPO_FINSIM_DIR}": home = "{FINSIM_HOME}"
If Not fso.FileExists(py) Then
  MsgBox "Python 3.12 (pythonw.exe) was not found at" & vbCrLf & py, vbExclamation, "FinSim Terminal"
  WScript.Quit 1
End If
If Not fso.FileExists(repo & "\\finsim\\terminal\\__init__.py") Then
  MsgBox "The finsim package was not found under" & vbCrLf & repo, vbExclamation, "FinSim Terminal"
  WScript.Quit 1
End If
sh.Environment("Process")("PYTHONPATH") = repo
sh.Environment("Process")("FINSIM_HOME") = home
sh.CurrentDirectory = repo
sh.Run \"\"\"\" & py & \"\"\" -m finsim open", 0, False
"""


def _ascii_strict(text: str) -> str:
    """The text of a .vbs file (``winpaths.vbs_text``): typographic punctuation folded to ASCII, anything else that
    is not ASCII raises ``ValueError`` instead of being written as ``?`` into a launcher that could never find it."""
    return winpaths.vbs_text(text)


def launcher_files(home: str, pythonw: str, repo: str, port: int, icon_png: Optional[bytes] = None) -> Dict[str, object]:
    """Every file ``install`` writes under ``home``, as ``{absolute path: text | bytes}`` — pure given its inputs
    (``icon_png`` None reads ``finsim/static/icon-256.png``; tests pass bytes).  ``FinSim Server.vbs`` carries the
    health probe with the ``"finsim-terminal"`` marker check, the shutdown of a foreign (old-engine) server and
    ``-m finsim serve --port <port>``; ``FinSim Terminal.vbs`` runs ``-m finsim open``; ``finsim.ico`` wraps the PNG.
    The paths must be ASCII (``winpaths.vb_string`` raises ``ValueError`` otherwise; they are never folded)."""
    vb = winpaths.vb_string
    subs = {"PYTHONW": vb(pythonw), "REPO_FINSIM_DIR": vb(repo), "FINSIM_HOME": vb(home), "PORT": int(port)}
    if icon_png is None:
        with open(icon_png_path(), "rb") as f:
            icon_png = f.read()
    return {
        os.path.join(home, SERVER_VBS): _ascii_strict(_SERVER_VBS.format(**subs)),
        os.path.join(home, TERMINAL_VBS): _ascii_strict(_TERMINAL_VBS.format(**subs)),
        os.path.join(home, ICON_NAME): _oldapp._ico_from_png(icon_png, 256),
    }


def shortcut_script(home: str, folders: Sequence[str], repo: str) -> str:
    """A transient VBS (the only stdlib way to write a ``.lnk``) creating ``FinSim.lnk`` → ``wscript "FinSim
    Terminal.vbs"`` in each non-empty folder, icon ``finsim.ico``.  Run with cscript at install, then deleted.  A folder
    or path that is not ASCII raises ``ValueError`` (``cmd_install`` reports it and skips the shortcuts)."""
    vb = winpaths.vb_string
    arr = ", ".join('"' + vb(f) + '"' for f in folders if f)
    text = (
        "Option Explicit\n"
        "Dim sh, lnk, folder\n"
        'Set sh = CreateObject("WScript.Shell")\n'
        f"For Each folder In Array({arr})\n"
        f'  Set lnk = sh.CreateShortcut(folder & "\\{SHORTCUT_NAME}")\n'
        '  lnk.TargetPath = "wscript.exe"\n'
        f'  lnk.Arguments = """{vb(os.path.join(home, TERMINAL_VBS))}"""\n'
        f'  lnk.WorkingDirectory = "{vb(repo)}"\n'
        f'  lnk.IconLocation = "{vb(os.path.join(home, ICON_NAME))}"\n'
        '  lnk.Description = "FinSim Paper Terminal - local paper-trading terminal (127.0.0.1 only)"\n'
        "  lnk.Save\n"
        "Next\n")
    return _ascii_strict(text)


def install_plan(home: str, startup: str, desktop: str, programs: str, port: int, stamp: str) -> List[Tuple[str, object]]:
    """The ordered steps of ``install`` as ``(op, target)`` — pure, asserted by the tests, executed by ``cmd_install``
    in exactly this order (§18): sha256 baseline → stop the old engine → delete the old task → back up and remove the
    old launcher files → write the new files → Startup copy → shortcuts → start → status → sequence → sha256 check."""
    bak = os.path.join(home, "backups")
    steps: List[Tuple[str, object]] = [
        ("sha256_baseline", old_db_path(home)),
        ("stop_server", port),
        ("delete_task", OLD_TASK),
        ("backup", (os.path.join(startup, SERVER_VBS), os.path.join(bak, f"Startup {SERVER_VBS}.{stamp}.bak"))),
    ]
    for name in OLD_HOME_FILES:
        steps.append(("backup", (os.path.join(home, name), os.path.join(bak, f"{name}.{stamp}.bak"))))
    for name in OLD_HOME_FILES:
        steps.append(("remove", os.path.join(home, name)))
    steps += [
        ("write", os.path.join(home, SERVER_VBS)),
        ("write", os.path.join(home, TERMINAL_VBS)),
        ("write", os.path.join(home, ICON_NAME)),
        ("copy", (os.path.join(home, SERVER_VBS), os.path.join(startup, SERVER_VBS))),
        ("shortcuts", tuple(f for f in (desktop, programs) if f)),
        ("start", os.path.join(home, SERVER_VBS)),
        ("status", None),
        ("sequence", None),
        ("sha256_check", old_db_path(home)),
    ]
    return steps


def uninstall_plan(home: str, startup: str, desktop: str, programs: str, port: int, purge: bool = False,
                   db: Optional[str] = None) -> List[Tuple[str, object]]:
    """The ordered steps of ``uninstall``: Startup entry, shortcuts, the VBS files, the icon, the window profile, the
    old task name (harmless when absent), stop the terminal, and — only with ``purge`` — the terminal database (the
    path the owner must type back; never ``finsim.db``)."""
    steps: List[Tuple[str, object]] = [
        ("remove", os.path.join(startup, SERVER_VBS)),
        ("remove", os.path.join(desktop, SHORTCUT_NAME)),
        ("remove", os.path.join(programs, SHORTCUT_NAME)),
        ("remove", os.path.join(home, SERVER_VBS)),
        ("remove", os.path.join(home, TERMINAL_VBS)),
        ("remove", os.path.join(home, ICON_NAME)),
        ("rmtree", os.path.join(home, WINDOW_PROFILE)),
        ("delete_task", OLD_TASK),
        ("stop_terminal", port),
    ]
    if purge:
        target = db or os.path.join(home, "terminal.db")
        if os.path.basename(target).lower() == "finsim.db":
            raise ValueError("refusing to plan the deletion of finsim.db (the old simulation's saves)")
        steps.append(("purge", target))
    return steps


def legacy_plan(home: str, port: int, refresh: bool, dst_exists: bool) -> Dict[str, object]:
    """``legacy``: ``src`` = ``<home>/finsim.db`` (read as the sqlite3 backup source only), ``dst`` = ``serve_db`` =
    ``<home>/legacy/legacy-view.db``; ``copy`` when the copy is missing or ``--refresh``.  The live path is never served."""
    src = old_db_path(home)
    dst = os.path.join(home, LEGACY_DIR, LEGACY_VIEW_DB)
    return {"src": src, "dst": dst, "serve_db": dst, "copy": bool(refresh or not dst_exists), "port": int(port),
            "url": url(port)}


def offline_refusal(home_arg: Optional[str], env: Dict[str, str]) -> Optional[str]:
    """``serve --offline`` (FakeProvider, fake prices) is refused unless ``--home`` names a folder other than the default
    home and ``FINSIM_TERMINAL_DB`` is unset — fake data must never reach the owner's real book (§16)."""
    default = os.path.join(os.path.expanduser("~"), ".finsim")
    if env.get(config.DB_ENV):
        return f"--offline is refused while {config.DB_ENV} is set (fake data must never reach a real database)"
    if not home_arg:
        return "--offline is refused on the default home: pass --home <scratch folder> (fake data must never reach the real book)"
    if os.path.normcase(os.path.abspath(home_arg)) == os.path.normcase(os.path.abspath(env.get(config.HOME_ENV) or default)):
        return f"--offline is refused on the default home {home_arg}: pass --home <scratch folder>"
    return None


def db_refusal(db: Optional[str]) -> Optional[str]:
    """``serve --db`` may never name ``finsim.db``: the old Startup entry's command line (``-m finsim serve --db
    <home>\\finsim.db …``) now reaches the terminal, and the old saves are never the terminal's database."""
    if db and os.path.basename(db).lower() == "finsim.db":
        return (f"refusing to serve {db}: finsim.db is the OLD simulation's saves, never the terminal's database "
                f"(the terminal uses <home>\\terminal.db; the archive view is `{PY} legacy`)")
    return None


def host_refusal(host: Optional[str]) -> Optional[str]:
    if host and host not in ("127.0.0.1", "localhost"):
        return f"refusing to listen on {host}: the terminal answers on 127.0.0.1 only"
    return None


def purge_confirmed(typed: str, db: str) -> bool:
    """``uninstall --purge`` deletes the terminal database only when the owner typed its full path back (compared as
    normalised absolute paths) and that path is not ``finsim.db``."""
    if os.path.basename(db).lower() == "finsim.db" or not typed or not typed.strip():
        return False
    return os.path.normcase(os.path.abspath(typed.strip())) == os.path.normcase(os.path.abspath(db))


def fmt_uptime(seconds: object) -> str:
    """``7383`` → ``2h 03m`` (7383 = 2 × 3600 + 183; 183 = 3 × 60 + 3 → the seconds are dropped)."""
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return "?"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def fmt_size(n: Optional[int]) -> str:
    """Bytes → ``1.2 MB`` (÷ 1,048,576, 1 dp) at or above 1 MiB, else ``123 KB`` (÷ 1,024, rounded); ``?`` when unknown."""
    if n is None:
        return "?"
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{round(n / 1024)} KB"


def _when(value: object) -> str:
    """A health timestamp for people: epoch seconds → ``YYYY-MM-DD HH:MM:SS ET`` (``clock.ny_str``); a string as it
    is (the server's ``*_ny`` form or an ISO time); ``?`` when unknown."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        from .clock import ny_str     # light (finsim.tzfallback only); imported here to keep the launcher's import small
        return ny_str(value)
    return "?" if value is None or value == "" else str(value)


def provider_state(p: Optional[dict]) -> str:
    """One entry of ``/api/health.providers`` as ``status`` prints it (``quotes.http.ProviderHealth.snapshot``):

    * ``idle (no request yet)`` — ``ok`` is null and the breaker is closed: nothing has called the provider since the
      server started (a fresh server, or a provider such as Cboe that is only fetched on demand);
    * ``ok`` — the last request answered;
    * ``DOWN (<last_error>; <n> errors in a row[; paused until <time>])`` — the circuit breaker is open (``down``) or
      the last request failed (``ok`` false).  ``last_error`` is the provider's own text; when none was recorded the
      line says so instead of inventing one.

    `` refused_ua`` is appended when the provider refused the honest User-Agent."""
    p = p or {}
    if p.get("down") or p.get("ok") is False:
        bits = [str(p.get("last_error") or "no error text recorded")]
        n = p.get("consecutive_errors")
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            bits.append(f"{n} error{'s' if n != 1 else ''} in a row")
        if p.get("down"):
            until = p.get("down_until_ny") or (_when(p.get("down_until_ts")) if p.get("down_until_ts") else None)
            bits.append(f"paused until {until}" if until else "paused by the circuit breaker")
        st = "DOWN (" + "; ".join(bits) + ")"
    elif p.get("ok") is None:
        st = "idle (no request yet)"
    else:
        st = "ok"
    if p.get("refused_ua"):
        st += " refused_ua"
    return st


def status_text(health: Optional[dict], *, code_version: str, port: int, home: str, db: str, db_size: Optional[int],
                log_path: str, log_tail_lines: Sequence[str], lock: Tuple[str, Optional[dict]],
                startup_entry: Tuple[str, str], backups: int, old_db_sha: Optional[str] = None,
                baseline_sha: Optional[str] = None) -> Tuple[List[str], int]:
    """The lines ``status`` prints and its exit code — pure.  ``lock`` is ``lock_state()``'s pair; ``startup_entry`` is
    ``(path, "terminal" | "old" | "missing")``.  Exit 0 only when a server with the marker answers; the log tail is
    included when nothing runs, when a non-terminal server answers, or when the terminal reports a problem."""
    lines = [f"{APP_NAME} Terminal {code_version} (code on disk) - home {home} - db {db} ({fmt_size(db_size)}) - {url(port)}"]
    code = EXIT_NOT_RUNNING
    show_tail = True
    if health is None:
        lines.append(f"server: not running ({PY} open starts it)")
    elif not is_terminal(health):
        lines.append(f"server: WARNING a non-terminal server answers on {port} (engine {health.get('engine_version', '?')}, "
                     f"up {fmt_uptime(health.get('uptime_s'))}) - the old simulation engine is still serving; "
                     f"stop it with `{PY} stop`, then `{PY} install --no-open`")
    else:
        code = EXIT_OK
        served = str(health.get("version", "?"))
        lines.append(f"server: running {APP_ID} {served}, pid {health.get('pid', '?')}, up {fmt_uptime(health.get('uptime_s'))}, "
                     f"started {health.get('started_ny') or _when(health.get('started_ts'))}, state_version {health.get('state_version', '?')}")
        if served != code_version:
            lines.append(f"  WARNING: the server serves {served} but the code on disk is {code_version} - "
                         f"restart to serve the new code (`{PY} stop` then `{PY} open`)")
        if health.get("db_path") and os.path.normcase(str(health["db_path"])) != os.path.normcase(db):
            lines.append(f"  served db: {health['db_path']} ({fmt_size(health.get('db_size'))}) - not the configured path above")
        if health.get("offline"):
            lines.append("  WARNING: offline mode - FAKE DATA, not market prices")
        integ = health.get("integrity") or {}
        integ_failed = integ.get("ok", True) is False or bool(integ.get("hold"))
        if integ_failed:
            lines.append(f"integrity: FAILED problems={integ.get('problems')} hold={integ.get('hold')} (checked {_when(integ.get('checked_at'))})")
        elif "ok" in integ and integ["ok"] is None:     # the server has not finished its first check yet
            lines.append("integrity: not checked yet (the server runs the check at start; ask again in a moment)")
        else:
            lines.append(f"integrity: ok (checked {_when(integ.get('checked_at'))})")
        workers = health.get("workers") or {}
        bad_worker = False
        parts = []
        for name, w in workers.items():
            w = w or {}
            state = "alive" if w.get("alive") else "DEAD"
            if w.get("stalled"):
                state += " STALLED"
            if not w.get("alive") or w.get("stalled"):
                bad_worker = True
            parts.append(f"{name} {state} (last tick {w.get('last_tick_ny') or _when(w.get('last_tick_ts'))}, errors_1h {w.get('errors_1h', 0)})")
        lines.append("workers: " + ("; ".join(parts) if parts else "?"))
        provs = health.get("providers") or {}
        lines.append("providers: " + ("; ".join(f"{name} {provider_state(p)}" for name, p in provs.items()) if provs else "?"))
        life = health.get("lifecycle") or {}
        lines.append(f"lifecycle: due {life.get('due', '?')}, failing {life.get('failing') or []}; attention open {health.get('attention_open', '?')}")
        show_tail = integ_failed or bad_worker or bool(life.get("failing"))
    state, info = lock
    if state == "held":
        pid = (info or {}).get("pid")
        lines.append(f"lock: held by pid {pid if pid else '? (byte-locked by a live process)'} ({os.path.join(home, 'terminal.lock')})")
    elif state == "stale":
        lines.append(f"lock: STALE - pid {(info or {}).get('pid')} is not alive; the file is left over from a server that died")
    elif state == "unreadable":
        lines.append("lock: file present without valid JSON (no live holder)")
    else:
        lines.append("lock: free")
    lines.append(f"log: {log_path}")
    if show_tail:
        lines.extend("  | " + l for l in (log_tail_lines or ["(no log yet)"]))
    path, kind = startup_entry
    if kind == "terminal":
        lines.append(f"Startup entry: present ({path})")
    elif kind == "old":
        lines.append(f"Startup entry: present but OLD - no finsim-terminal marker ({path}); run `{PY} install --no-open`")
    else:
        lines.append(f"Startup entry: missing ({path}); run `{PY} install --no-open`")
    lines.append(f"backups: {backups} .db snapshot(s) in {os.path.join(home, 'backups')}")
    if old_db_sha:
        if baseline_sha:
            verdict = "unchanged" if old_db_sha == baseline_sha else "CHANGED since the install baseline"
            lines.append(f"finsim.db: sha256 {old_db_sha} ({verdict})")
        else:
            lines.append(f"finsim.db: sha256 {old_db_sha} (no install baseline recorded yet)")
    return lines, code


def startup_entry_kind(path: str) -> str:
    """``terminal`` when the Startup entry exists and carries the marker check, ``old`` when it exists without it,
    ``missing`` otherwise."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "terminal" if APP_ID in f.read() else "old"
    except OSError:
        return "missing"


def count_backups(home: str) -> int:
    bdir = os.path.join(home, "backups")
    try:
        return sum(1 for f in os.listdir(bdir) if f.endswith(".db"))
    except OSError:
        return 0


def _read_baseline(home: str) -> Optional[str]:
    try:
        with open(baseline_path(home), "r", encoding="utf-8") as f:
            parts = f.read().split()
        return parts[0] if parts else None
    except OSError:
        return None


def _write_baseline(home: str, sha: str, target: str) -> None:
    with open(baseline_path(home), "w", encoding="utf-8") as f:
        f.write(f"{sha}  {target}\n")


# ------------------------------------------------------------------------------------------------- the window
def open_window(target: str, home: str) -> str:
    """Chrome / Edge / Brave ``--app`` window with its own profile ``<home>/terminal-window-profile`` (never mixed with
    ordinary browsing); fallback the default browser.  The only place the launcher spawns anything but the server.
    (``finsim.app.open_window`` is not reused: its profile folder is fixed to the old app's ``window-profile``.)"""
    profile = os.path.join(home, WINDOW_PROFILE)
    for exe in _oldapp._browser_candidates():
        if os.path.exists(exe):
            os.makedirs(profile, exist_ok=True)
            try:
                subprocess.Popen([exe, f"--app={target}", f"--user-data-dir={profile}", "--no-first-run",
                                  "--no-default-browser-check", "--window-size=1440,900"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=(sys.platform != "win32"))
                return f"app window ({os.path.basename(exe)})"
            except OSError:
                continue
    webbrowser.open(target)
    return "default browser"


# ----------------------------------------------------------------------------------------- starting and waiting
def wait_for(predicate: Callable[[], bool], timeout: float, sleep: Callable[[float], None] = time.sleep,
             step: float = 0.25, monotonic: Callable[[], float] = time.monotonic) -> bool:
    """Poll ``predicate`` every ``step`` seconds until it is true or ``timeout`` seconds passed (always checked once)."""
    deadline = monotonic() + timeout
    while True:
        if predicate():
            return True
        if monotonic() >= deadline:
            return False
        sleep(step)


def start_server(home: str, port: int, out: Optional[Callable[..., None]] = None) -> Tuple[str, Optional[subprocess.Popen]]:
    """Start the terminal detached: through ``<home>/FinSim Server.vbs`` when it exists (the installed way; the VBS
    also asks a foreign server to shut down first), else directly as ``pythonw -m finsim serve --port <port>`` from the
    repo folder.  Returns ``("vbs" | "direct", proc)`` (``proc`` is None for the VBS: wscript exits at once)."""
    vbs = os.path.join(home, SERVER_VBS)
    if sys.platform == "win32" and os.path.exists(vbs):
        subprocess.Popen(["wscript.exe", vbs], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _say(f"starting through {vbs}", out=out)
        return "vbs", None
    exe = pythonw_for(sys.executable) if sys.platform == "win32" else sys.executable
    if not os.path.exists(exe):
        exe = sys.executable
    env = dict(os.environ)
    env["PYTHONPATH"] = repo_dir() + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env[config.HOME_ENV] = home
    kw: Dict[str, object] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": repo_dir(), "env": env}
    if sys.platform == "win32":
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                               | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen([exe, "-m", "finsim", "serve", "--port", str(port)], **kw)
    _say(f"starting {exe} -m finsim serve --port {port} (no launcher VBS installed yet; `{PY} install` writes it)", out=out)
    return "direct", proc


def wait_for_terminal(http: Http, proc: Optional[subprocess.Popen], timeout: float, sleep: Callable[[float], None] = time.sleep,
                      monotonic: Callable[[], float] = time.monotonic) -> Tuple[bool, str]:
    """Wait until health answers WITH the marker.  ``(True, "")`` on success; ``(False, reason)`` when the child exited
    (its exit code is reported: 3 = another instance / port, 4 = newer schema, 5 = startup error), when a foreign
    server still answers, or after ``timeout``."""
    deadline = monotonic() + timeout
    while True:
        h = http.health()
        if is_terminal(h):
            return True, ""
        if proc is not None and proc.poll() is not None:
            return False, f"the server exited with code {proc.returncode} while starting (3 = another instance or the port is taken, 4 = newer schema, 5 = startup error)"
        if monotonic() >= deadline:
            if h is not None:
                return False, f"{describe_server(h)} still answers on the port; the terminal did not take it over"
            return False, f"the terminal did not answer within {timeout:.0f}s"
        sleep(0.25)


# ---------------------------------------------------------------------------------------------------- commands
def cmd_serve(port: int, home: Optional[str], db: Optional[str], offline: bool, log_level: Optional[str],
              host: Optional[str] = None, out: Optional[Callable[..., None]] = None) -> int:
    """Foreground server.  The guards run before the server module is imported; an import-time failure (for example a
    stray ``ZoneInfo`` call under 3.12 without tzdata) is written with its traceback to ``<home>/terminal.log`` and
    exits 5 so ``status`` can show the tail."""
    for msg in (host_refusal(host), offline_refusal(home, dict(os.environ)) if offline else None, db_refusal(db)):
        if msg:
            _say(msg, out=out)
            return EXIT_STARTUP
    if home:
        os.environ[config.HOME_ENV] = home
    if db:
        os.environ[config.DB_ENV] = db
    if log_level:
        os.environ["FINSIM_LOG_LEVEL"] = log_level
    eff_home = config.home()
    try:
        from . import server as _server
    except Exception:   # noqa: BLE001 - an import-time failure must leave a trace
        _write_startup_error(eff_home, traceback.format_exc())
        _say(f"the server could not be imported; the traceback is in {config.log_path()}", out=out)
        return EXIT_STARTUP
    try:
        rc = _server.serve(port=port, home=home, db=db, offline=offline)
    except SystemExit as e:
        rc = e.code
    except KeyboardInterrupt:
        rc = EXIT_OK
    except Exception:   # noqa: BLE001
        _write_startup_error(eff_home, traceback.format_exc())
        _say(f"the server failed; the traceback is in {config.log_path()}", out=out)
        return EXIT_STARTUP
    try:
        return int(rc or 0)
    except (TypeError, ValueError):
        return EXIT_STARTUP


def _write_startup_error(home: str, text: str) -> None:
    try:
        os.makedirs(home, exist_ok=True)
        with open(os.path.join(home, "terminal.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " ERROR finsim.terminal.app: startup failed\n" + text + "\n")
    except OSError:
        pass


def stop_server(http: Http, home: str, api, *, timeout: float, force: bool, run=_run, sleep=time.sleep,
                out: Optional[Callable[..., None]] = None, only_foreign: bool = False, only_terminal: bool = False,
                monotonic: Callable[[], float] = time.monotonic) -> int:
    """The ``stop`` algorithm (§18) shared by ``stop``, ``install`` (``only_foreign``) and ``uninstall`` (``only_terminal``):
    shutdown over loopback → wait for the port to go quiet → wait for the lock → optional ``taskkill`` under the double
    guard.  Returns 0 stopped, 1 nothing was running, 2 still alive."""
    h = http.health()
    state, info = lock_state(home, api)
    if h is not None:
        terminal = is_terminal(h)
        if (only_foreign and terminal) or (only_terminal and not terminal):
            _say(f"leaving {describe_server(h)} running on {http.base}", out=out)
            return EXIT_NOT_RUNNING
        _say(f"stopping {describe_server(h)} on {http.base} ...", out=out)
        if not http.shutdown():
            _say("  the shutdown request was not accepted (loopback only); waiting anyway", out=out)
        if not wait_for(lambda: http.health() is None, timeout, sleep=sleep, monotonic=monotonic):
            _say(f"  still answering after {timeout:.0f}s", out=out)
            return EXIT_STILL_ALIVE
        _say("  the port is quiet", out=out)
    elif state in ("free", "unreadable"):
        _say(f"nothing running on {http.base}" + (" (lock file present without JSON)" if state == "unreadable" else ""), out=out)
        return EXIT_NOT_RUNNING
    elif state == "stale":
        _say(f"nothing running on {http.base}; stale lock (pid {(info or {}).get('pid')} is not alive)", out=out)
        return EXIT_NOT_RUNNING
    if only_foreign:
        return EXIT_OK

    def released() -> bool:   # the OS releases the byte lock on death; the file may remain (then it reads as stale)
        return lock_state(home, api)[0] != "held"
    if not wait_for(released, timeout, sleep=sleep, monotonic=monotonic):
        state, info = lock_state(home, api)
        pid = (info or {}).get("pid")
        if force and pid and force_kill_allowed(pid, api):
            _say(f"  pid {pid} is a live python process holding the lock: taskkill /PID {pid} /F", out=out)
            run(["taskkill", "/PID", str(pid), "/F"])
            wait_for(released, 5.0, sleep=sleep, monotonic=monotonic)
            state, info = lock_state(home, api)
        if state == "held":
            if pid:
                alive, image = process_state(pid, api)
                why = "not a python image - refusing to kill it" if alive and not force_kill_allowed(pid, api) else "pass --force to taskkill it"
                _say(f"  still alive: pid {pid} ({image or 'image unknown'}) holds the lock; {why}", out=out)
            else:
                _say("  still alive: the lock file is byte-locked by a live process whose pid cannot be read", out=out)
            return EXIT_STILL_ALIVE
    _say("stopped", out=out)
    return EXIT_OK


def cmd_stop(timeout: float = 15.0, force: bool = False, *, http: Optional[Http] = None, api=None, home: Optional[str] = None,
             run=_run, sleep=time.sleep, out: Optional[Callable[..., None]] = None, monotonic: Callable[[], float] = time.monotonic) -> int:
    home = home or config.home()
    rc = stop_server(http or Http(), home, api or default_process_api(), timeout=timeout, force=force, run=run, sleep=sleep,
                     out=out, monotonic=monotonic)
    sha = sha256_file(old_db_path(home))
    if sha:
        _say(f"finsim.db sha256 {sha}", out=out)
    return rc


def cmd_status(*, http: Optional[Http] = None, api=None, home: Optional[str] = None, out: Optional[Callable[..., None]] = None,
               folders: Optional[Folders] = None) -> int:
    home = home or config.home()
    http = http or Http()
    h = http.health()
    db = config.db_path()
    try:
        size: Optional[int] = os.path.getsize(db)
    except OSError:
        size = None
    startup = (folders or (real_folders() if sys.platform == "win32" else (home, "", "")))[0]
    entry = os.path.join(startup, SERVER_VBS)
    lines, code = status_text(h, code_version=VERSION, port=config.port(), home=home, db=db, db_size=size,
                              log_path=config.log_path(), log_tail_lines=log_tail(config.log_path()),
                              lock=lock_state(home, api or default_process_api()), startup_entry=(entry, startup_entry_kind(entry)),
                              backups=count_backups(home), old_db_sha=sha256_file(old_db_path(home)), baseline_sha=_read_baseline(home))
    for l in lines:
        _say(l, out=out)
    return code


def cmd_install(open_after: bool = True, *, http: Optional[Http] = None, api=None, home: Optional[str] = None,
                run=_run, sleep=time.sleep, out: Optional[Callable[..., None]] = None, folders: Optional[Folders] = None,
                starter: Callable[..., Tuple[str, Optional[subprocess.Popen]]] = start_server, stamp: Optional[str] = None,
                opener: Callable[[str, str], str] = open_window, wait_s: float = START_WAIT_S,
                monotonic: Callable[[], float] = time.monotonic) -> int:
    """Execute ``install_plan`` step by step, printing one line per step.  Windows only unless the three folders are
    injected (tests).  Nothing here opens ``finsim.db``; its sha256 is taken before and after and the verdict printed.
    Exit 0 when the terminal answers with the marker at the end, 1 otherwise (the log tail was printed)."""
    if folders is None and sys.platform != "win32":
        _say("install is Windows-only (Startup folder, VBS, shortcuts); use `serve` directly elsewhere", out=out)
        return EXIT_NOT_RUNNING
    home = home or config.home()
    http = http or Http()
    api = api or default_process_api()
    port = config.port()
    startup, desktop, programs = folders or real_folders()
    if not startup:     # never a Startup entry written relative to the current folder
        _say("install: the Startup folder cannot be resolved (SHGetKnownFolderPath failed and %APPDATA% is not set); "
             "nothing was changed", out=out)
        return EXIT_NOT_RUNNING
    os.makedirs(home, exist_ok=True)
    pythonw = resolve_pythonw(run)
    files = launcher_files(home, pythonw, repo_dir(), port)
    plan = install_plan(home, startup, desktop, programs, port, stamp or time.strftime("%Y%m%d-%H%M%S"))
    _say(f"{APP_NAME} Terminal {VERSION} install - home {home} - pythonw {pythonw} - repo {repo_dir()}", out=out)
    if not os.path.exists(pythonw):
        _say(f"  WARNING: {pythonw} does not exist; the VBS files will refuse to run until Python 3.12 is installed there", out=out)
    before = None
    for op, target in plan:
        if op == "sha256_baseline":
            before = sha256_file(str(target))
            if before:
                _write_baseline(home, before, str(target))
                _say(f"  sha256 {target} = {before} (baseline written to {baseline_path(home)})", out=out)
            else:
                _say(f"  no {target} to hash (nothing to protect)", out=out)
        elif op == "stop_server":
            h = http.health()
            if h is None:
                _say(f"  nothing answers on {http.base}", out=out)
            elif is_terminal(h) and str(h.get("version")) == VERSION:
                _say(f"  {describe_server(h)} already serves this code; it stays up", out=out)
            else:
                foreign = not is_terminal(h)
                rc = stop_server(http, home, api, timeout=STOP_WAIT_S, force=False, run=run, sleep=sleep, out=out, only_foreign=foreign,
                                 monotonic=monotonic)
                if rc == EXIT_STILL_ALIVE:
                    _say(f"  WARNING: {describe_server(h)} would not stop; `{PY} stop --force` and rerun install", out=out)
                elif foreign and before is not None:
                    again = sha256_file(str(old_db_path(home)))
                    if again != before:
                        _say(f"  finsim.db changed while the old engine shut down (it folded its own write-ahead log into the "
                             f"file); baseline re-taken: {again}", out=out)
                        before = again
                        if again:
                            _write_baseline(home, again, old_db_path(home))
        elif op == "delete_task":
            r = run(["schtasks", "/Delete", "/F", "/TN", str(target)])
            _say(f"  scheduled task {target!r}: " + ("removed" if r.returncode == 0 else "not present"), out=out)
        elif op == "backup":
            src, dst = target   # type: ignore[misc]
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
                _say(f"  backed up {src} -> {dst}", out=out)
        elif op == "remove":
            if os.path.exists(str(target)):
                os.remove(str(target))
                _say(f"  removed old {target}", out=out)
        elif op == "write":
            content = files[str(target)]
            with open(str(target), "wb") as f:
                f.write(content if isinstance(content, bytes) else content.encode("ascii"))
            _say(f"  wrote {target}", out=out)
        elif op == "copy":
            src, dst = target   # type: ignore[misc]
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            _say(f"  Startup entry {dst} (replaced the old entry of that name)", out=out)
        elif op == "shortcuts":
            try:
                text = shortcut_script(home, list(target), repo_dir())   # type: ignore[arg-type]
            except ValueError as e:     # a non-ASCII Desktop / Start Menu path: no shortcut rather than a wrong one
                _say(f"  shortcuts: not created ({e})", out=out)
                continue
            script = os.path.join(home, "make-terminal-shortcuts.vbs")
            with open(script, "w", encoding="ascii") as f:
                f.write(text)
            r = run(["cscript.exe", "//nologo", script])
            try:
                os.remove(script)
            except OSError:
                pass
            _say("  shortcuts: " + (", ".join(os.path.join(f, SHORTCUT_NAME) for f in target) if r.returncode == 0   # type: ignore[union-attr]
                                     else f"creation failed ({(r.stderr or r.stdout or '').strip()})"), out=out)
        elif op == "start":
            h = http.health()
            if is_terminal(h) and str(h.get("version")) == VERSION:
                _say(f"  server: {describe_server(h)} is up at {http.base}", out=out)
            else:
                _, proc = starter(home, port, out=out)
                ok, why = wait_for_terminal(http, proc, wait_s, sleep=sleep, monotonic=monotonic)
                if ok:
                    _say(f"  server: {describe_server(http.health())} is up at {http.base}", out=out)
                else:
                    _say(f"  server: {why}; the end of {config.log_path()} says:", out=out)
                    for l in log_tail(config.log_path()):
                        _say("    | " + l, out=out)
        elif op == "status":
            cmd_status(http=http, api=api, home=home, out=out, folders=(startup, desktop, programs))
        elif op == "sequence":
            _say("The final PowerShell sequence (also in docs/TERMINAL_RUNBOOK.md):", out=out)
            for l in POWERSHELL_SEQUENCE.splitlines():
                _say("  " + l, out=out)
        elif op == "sha256_check":
            after = sha256_file(str(target))
            if before is None and after is None:
                _say(f"  no {target} (nothing to compare)", out=out)
            else:
                _say(f"  sha256 {target} = {after} (" + ("unchanged" if after == before else "CHANGED - investigate before trading") + ")", out=out)
    up = is_terminal(http.health())
    if open_after and up:
        _say(f"  opened in {opener(http.base, home)}", out=out)
    return EXIT_OK if up else EXIT_NOT_RUNNING


def cmd_open(*, http: Optional[Http] = None, home: Optional[str] = None, sleep=time.sleep, out: Optional[Callable[..., None]] = None,
             opener: Callable[[str, str], str] = open_window, starter: Callable[..., Tuple[str, Optional[subprocess.Popen]]] = start_server,
             wait_s: float = START_WAIT_S, monotonic: Callable[[], float] = time.monotonic) -> int:
    """Open the window.  When the terminal does not answer, start it first through ``FinSim Server.vbs`` (§18): the
    VBS asks a foreign (old-engine) server to shut down before starting the terminal.  Without an installed VBS a
    foreign server is reported instead (a direct start would only exit 3 on the taken port).  Never a second server."""
    home = home or config.home()
    http = http or Http()
    h = http.health()
    if not is_terminal(h):
        vbs = os.path.join(home, SERVER_VBS)
        if h is not None and not os.path.exists(vbs):
            msg = (f"{describe_server(h)} answers on {http.base} - the old simulation engine, and no {SERVER_VBS} is installed; "
                   f"run `{PY} stop` then `{PY} install --no-open`")
            _say(msg, out=out)
            _launcher_log(home, "open: " + msg)
            return EXIT_NOT_RUNNING
        if h is not None:
            _say(f"{describe_server(h)} answers on {http.base}; {SERVER_VBS} asks it to shut down and starts the terminal", out=out)
        _, proc = starter(home, config.port(), out=out)
        ok, why = wait_for_terminal(http, proc, wait_s, sleep=sleep, monotonic=monotonic)
        if not ok:
            _say(why + f"; the end of {config.log_path()} says:", out=out)
            for l in log_tail(config.log_path()):
                _say("  | " + l, out=out)
            _launcher_log(home, "open: " + why)
            return EXIT_NOT_RUNNING
    _say(f"{http.base} -> {opener(http.base, home)}", out=out)
    return EXIT_OK


def cmd_uninstall(purge: bool = False, *, http: Optional[Http] = None, api=None, home: Optional[str] = None, run=_run,
                  sleep=time.sleep, ask: Callable[[str], str] = input, out: Optional[Callable[..., None]] = None,
                  folders: Optional[Folders] = None) -> int:
    """Execute ``uninstall_plan``.  ``terminal.db`` stays unless ``--purge`` AND the owner types its full path back."""
    if folders is None and sys.platform != "win32":
        _say("uninstall is Windows-only (nothing was installed elsewhere)", out=out)
        return EXIT_NOT_RUNNING
    home = home or config.home()
    http = http or Http()
    api = api or default_process_api()
    db = config.db_path()
    startup, desktop, programs = folders or real_folders()
    plan = uninstall_plan(home, startup, desktop, programs, config.port(), purge=purge, db=db)
    _say(f"{APP_NAME} Terminal uninstall - home {home}", out=out)
    for op, target in plan:
        if op == "remove":
            if not os.path.isabs(str(target)):     # an unresolved folder ("") must never mean the current folder
                _say(f"  skipped {target}: its folder could not be resolved", out=out)
                continue
            if os.path.exists(str(target)):
                os.remove(str(target))
                _say(f"  removed {target}", out=out)
        elif op == "rmtree":
            if os.path.isdir(str(target)):
                shutil.rmtree(str(target), ignore_errors=True)
                _say(f"  removed {target}", out=out)
        elif op == "delete_task":
            if run(["schtasks", "/Delete", "/F", "/TN", str(target)]).returncode == 0:
                _say(f"  removed the scheduled task {target!r}", out=out)
        elif op == "stop_terminal":
            stop_server(http, home, api, timeout=STOP_WAIT_S, force=False, run=run, sleep=sleep, out=out, only_terminal=True)
        elif op == "purge":
            if not os.path.exists(str(target)):
                _say(f"  no {target} to delete", out=out)
                continue
            typed = ask(f"  type the full path of the terminal database to delete it (anything else keeps it): {target}\n  > ")
            if purge_confirmed(typed, str(target)):
                for suffix in ("", "-wal", "-shm"):
                    try:
                        os.remove(str(target) + suffix)
                    except OSError:
                        pass
                _say(f"  deleted {target}", out=out)
            else:
                _say(f"  kept {target} (the typed text did not match its path)", out=out)
    _say("uninstalled." + ("" if purge else f" Your terminal data stays in {db}.") + " finsim.db was not touched.", out=out)
    return EXIT_OK


def ro_uri(path: str) -> str:
    """A ``file:`` URI with ``mode=ro`` for ``sqlite3.connect(uri=True)``: forward slashes, percent-encoded, in the
    documented ``file:///C:/...`` form on Windows and ``file:/home/...`` elsewhere."""
    p = os.path.abspath(path).replace("\\", "/")
    q = urllib.parse.quote(p, safe="/:")
    return ("file:///" + q if re.match(r"^[A-Za-z]:/", p) else "file:" + q) + "?mode=ro"


def cmd_legacy(port: int = config.LEGACY_PORT, refresh: bool = False, *, home: Optional[str] = None,
               out: Optional[Callable[..., None]] = None, serve_fn: Optional[Callable[..., object]] = None) -> int:
    """The archive view: the OLD ``finsim.api.server.serve`` on a sqlite3-backup COPY of ``finsim.db`` (never the live
    file), foreground, on ``--port`` (default 8766).  Edits made in the view live in the copy only."""
    home = home or config.home()
    plan = legacy_plan(home, port, refresh, os.path.exists(os.path.join(home, LEGACY_DIR, LEGACY_VIEW_DB)))
    src, dst = str(plan["src"]), str(plan["dst"])
    if not os.path.exists(src):
        _say(f"no {src}: there is no old simulation database to view", out=out)
        return EXIT_NOT_RUNNING
    if plan["copy"]:
        import sqlite3
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        s = sqlite3.connect(ro_uri(src), uri=True)   # read-only: the live file is never written, not even a checkpoint
        d = sqlite3.connect(dst)
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()
        _say(f"copied {src} -> {dst} (sqlite3 backup API; the original is untouched)", out=out)
    else:
        _say(f"serving the existing copy {dst} (pass --refresh to re-copy finsim.db)", out=out)
    _say(f"LEGACY ARCHIVE VIEW of a copy on {plan['url']} - finsim.db is not opened; anything saved here stays in the copy "
         f"(delete {os.path.dirname(dst)} at any time; the old server keeps its own backups there)", out=out)
    if serve_fn is None:
        from ..api.server import serve as serve_fn   # the OLD server, unchanged
    try:
        serve_fn(str(plan["serve_db"]), "127.0.0.1", int(port), name="finsim legacy archive view")
    except KeyboardInterrupt:
        pass
    return EXIT_OK


# -------------------------------------------------------------------------------------------------------- CLI
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="finsim", description=f"FinSim Live Terminal {VERSION} - paper trading on real delayed prices (127.0.0.1 only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the terminal server in the foreground (exit 3 another instance / port, 4 newer schema, 5 startup error)")
    s.add_argument("--port", type=int, default=config.port())
    s.add_argument("--home", default=None, help="data folder (default %%USERPROFILE%%\\.finsim or FINSIM_HOME)")
    s.add_argument("--db", default=None, help="terminal database (default <home>\\terminal.db); finsim.db is refused")
    s.add_argument("--host", default=None, help="accepted for the documented command line; must be 127.0.0.1")
    s.add_argument("--offline", action="store_true", help="FakeProvider instead of the network; refused on the default home")
    s.add_argument("--log-level", default=None, help="INFO (default), DEBUG, WARNING")
    st = sub.add_parser("stop", help="stop the server on the port (the old engine too); exit 0 stopped, 1 nothing running, 2 still alive")
    st.add_argument("--timeout", type=float, default=15.0)
    st.add_argument("--force", action="store_true", help="taskkill the lock holder, only when it is a live python process")
    sub.add_parser("status", help="served version vs the code on disk, integrity, workers, providers, lock, Startup entry, backups")
    i = sub.add_parser("install", help="Startup entry + shortcuts for this user; stops the old engine; starts the terminal")
    i.add_argument("--no-open", action="store_true", help="do not open the window after installing")
    sub.add_parser("open", help="open the app window (starts the server first when the terminal does not answer)")
    u = sub.add_parser("uninstall", help="remove the Startup entry, shortcuts and launcher files; stop the terminal")
    u.add_argument("--purge", action="store_true", help="also delete terminal.db after typing its path (never finsim.db)")
    lg = sub.add_parser("legacy", help="the OLD simulation on a copy of finsim.db (archive view), default port 8766")
    lg.add_argument("--port", type=int, default=config.LEGACY_PORT)
    lg.add_argument("--refresh", action="store_true", help="re-copy finsim.db into the view copy first")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.cmd == "serve":
        return cmd_serve(args.port, args.home, args.db, args.offline, args.log_level, host=args.host)
    if args.cmd == "stop":
        return cmd_stop(args.timeout, args.force)
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "install":
        return cmd_install(open_after=not args.no_open)
    if args.cmd == "open":
        return cmd_open()
    if args.cmd == "uninstall":
        return cmd_uninstall(purge=args.purge)
    if args.cmd == "legacy":
        return cmd_legacy(args.port, args.refresh)
    return 2   # unreachable: argparse rejects unknown commands


if __name__ == "__main__":
    sys.exit(main())
