"""FinSim as a local desktop app.

`python3 -m finsim install` registers the server as a per-user background service that starts at login and
puts a "FinSim" launcher in the Applications folder / Start Menu / desktop. The launcher runs `finsim open`,
which makes sure the server is up and opens the terminal in its own window (Chrome, Edge, Chromium or Brave
in app mode, otherwise the default browser). Everything binds to 127.0.0.1: nothing leaves the machine and
nobody else on the network can reach it.

    python3 -m finsim install      # once; starts the service and opens the window
    python3 -m finsim open         # what the launcher runs
    python3 -m finsim status
    python3 -m finsim uninstall

Files live under FINSIM_HOME (default ~/.finsim): finsim.db, server.log, the launcher scripts and icons.
Standard library only; the platform hooks are launchd (macOS), systemd --user (Linux) and Task Scheduler
plus a Startup-folder fallback (Windows).
"""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import webbrowser
from typing import Dict, List, Optional

from .version import ENGINE_VERSION

APP_NAME = "FinSim"
BUNDLE_ID = "io.finsim.terminal"
DEFAULT_PORT = 8765
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the directory that holds the `finsim` package


# ------------------------------------------------------------------ paths and platform
def home() -> str:
    return os.environ.get("FINSIM_HOME") or os.path.join(os.path.expanduser("~"), ".finsim")


def db_path() -> str:
    return os.environ.get("FINSIM_DB") or os.path.join(home(), "finsim.db")


def log_path() -> str:
    return os.path.join(home(), "server.log")


def port() -> int:
    return int(os.environ.get("FINSIM_PORT") or DEFAULT_PORT)


def url(p: Optional[int] = None) -> str:
    return f"http://127.0.0.1:{p or port()}/"


def platform() -> str:
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def python_exe(windowless: bool = False) -> str:
    """The interpreter running us; on Windows prefer pythonw.exe for anything that must not open a console."""
    exe = sys.executable
    if windowless and platform() == "windows":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            return w
    return exe


def serve_argv(p: Optional[int] = None) -> List[str]:
    # no --host: the server reads the phone setting when it starts, so `finsim phone on|off` needs no reinstall
    return [python_exe(windowless=True), "-m", "finsim", "serve", "--db", db_path(), "--port", str(p or port())]


# ------------------------------------------------------------------ settings, access key, reach
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def config() -> Dict:
    try:
        with open(os.path.join(home(), "config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg: Dict) -> None:
    os.makedirs(home(), exist_ok=True)
    with open(os.path.join(home(), "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def phone_enabled() -> bool:
    return bool(config().get("phone"))


def resolve_host() -> str:
    """Where the server listens: this machine only, or every interface once `finsim phone on` was run."""
    return "0.0.0.0" if phone_enabled() else "127.0.0.1"


def access_key() -> str:
    """The secret a phone (any client that is not this machine) must present. Generated once, kept in FINSIM_HOME
    with owner-only permissions; the phone link carries it, so it is typed at most once."""
    fp = os.path.join(home(), "access-key")
    try:
        with open(fp, "r", encoding="utf-8") as f:
            k = f.read().strip()
            if k:
                return k
    except OSError:
        pass
    import secrets
    k = secrets.token_urlsafe(15)
    os.makedirs(home(), exist_ok=True)
    fd = os.open(fp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(k + "\n")
    return k


def lan_addresses() -> List[Dict[str, str]]:
    """Addresses a phone can use: the Wi-Fi/LAN address of this machine (a UDP socket 'connected' to a public
    address reveals the outbound interface; nothing is sent) and, when Tailscale is installed, its address."""
    import socket
    out: List[Dict[str, str]] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            out.append({"kind": "same Wi-Fi / LAN", "ip": ip})
    except OSError:
        pass
    ts = shutil.which("tailscale") or ("/Applications/Tailscale.app/Contents/MacOS/Tailscale" if platform() == "mac" else None)
    if ts and os.path.exists(ts):
        r = _run([ts, "ip", "-4"])
        if not r.returncode and r.stdout.strip():
            out.append({"kind": "anywhere (Tailscale)", "ip": r.stdout.strip().splitlines()[0]})
    return out


def phone_links() -> List[str]:
    key = access_key()
    return [f"http://{a['ip']}:{port()}/?key={key}   ({a['kind']})" for a in lan_addresses()]


# ------------------------------------------------------------------ server control
def health(p: Optional[int] = None, timeout: float = 1.0) -> Optional[Dict]:
    """The running server's /api/health, or None when nothing answers on the port."""
    try:
        with urllib.request.urlopen(url(p) + "api/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def legacy_db_path() -> str:
    """Where `python3 -m finsim serve` kept saves before the app existed: data/finsim.db in the checkout."""
    return os.path.join(PACKAGE_ROOT, "data", "finsim.db")


def adopt_legacy_db() -> Optional[str]:
    """First run of the app on a machine that played with `serve`: carry the saves over (a consistent copy; the
    original stays where it was) rather than start with an empty world list. Returns the source when copied."""
    src, dst = legacy_db_path(), db_path()
    if os.path.exists(dst) or not os.path.exists(src) or os.path.abspath(src) == os.path.abspath(dst):
        return None
    import sqlite3
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    s, d = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    return src


def start_background(p: Optional[int] = None, wait: float = 120.0) -> Dict:
    """Spawn the server detached from this process and wait until it answers. Returns its health. Loading big
    saves can take a while, so the wait is long; a child that exits is reported at once with the log's tail."""
    os.makedirs(home(), exist_ok=True)
    if (h := health(p)):
        return h
    if (src := adopt_legacy_db()):
        print(f"carried your saves over from {src} to {db_path()} (the original is untouched)")
    log = open(log_path(), "ab")
    kw: Dict = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "cwd": PACKAGE_ROOT, "env": _env()}
    if platform() == "windows":
        kw["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(serve_argv(p), **kw)
    deadline = time.time() + wait
    t0 = time.time()
    told = False
    while time.time() < deadline:
        time.sleep(0.25)
        if (h := health(p)):
            return h
        if proc.poll() is not None:
            raise RuntimeError(f"the server exited with code {proc.returncode} while starting; the end of {log_path()} says:\n{_log_tail()}")
        if not told and time.time() - t0 > 6:
            print("starting the server (loading your saves)…", flush=True)
            told = True
    raise RuntimeError(f"the server did not answer on {url(p)} within {wait:.0f}s; it may still be loading — try `python3 -m finsim status` in a minute, or see {log_path()}")


def _log_tail(lines: int = 15) -> str:
    try:
        with open(log_path(), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8000))
            return "\n".join(f.read().decode("utf-8", "replace").splitlines()[-lines:])
    except OSError:
        return "(no log yet)"


def _env() -> Dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = PACKAGE_ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


# ------------------------------------------------------------------ the window
def _browser_candidates() -> List[str]:
    plat = platform()
    if plat == "mac":
        apps = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser", "/Applications/Chromium.app/Contents/MacOS/Chromium"]
        return [a for a in apps] + [os.path.expanduser("~") + a for a in apps]
    if plat == "windows":
        roots = [os.environ.get("PROGRAMFILES", r"C:\Program Files"), os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")]
        rel = [r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe", r"BraveSoftware\Brave-Browser\Application\brave.exe",
               r"Chromium\Application\chrome.exe"]
        return [os.path.join(r, x) for r in roots if r for x in rel]
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"]
    return [p for n in names if (p := shutil.which(n))]


def open_window(target: Optional[str] = None) -> str:
    """Open the terminal in its own window. Chromium-family browsers get `--app` (no tabs, no address bar) with a
    profile of its own under FINSIM_HOME so it never mixes with ordinary browsing; anything else opens a tab."""
    target = target or url()
    profile = os.path.join(home(), "window-profile")
    for exe in _browser_candidates():
        if os.path.exists(exe):
            os.makedirs(profile, exist_ok=True)
            try:
                subprocess.Popen([exe, f"--app={target}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                                  f"--window-size=1440,900"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=(platform() != "windows"))
                return f"app window ({os.path.basename(exe)})"
            except OSError:
                continue
    webbrowser.open(target)
    return "default browser"


# ------------------------------------------------------------------ launcher artifacts (pure: path -> content)
def _ico_from_png(png: bytes, size: int) -> bytes:
    """A single-image .ico that embeds the PNG as-is (Vista and later read PNG-compressed icons)."""
    w = h = 0 if size >= 256 else size
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(png), 6 + 16)
    return header + entry + png


def launcher_files(plat: Optional[str] = None, base: Optional[str] = None) -> Dict[str, object]:
    """Every file `install` writes for the platform, as {absolute path: text or bytes}. Pure, so it can be inspected
    and tested without touching the OS."""
    plat = plat or platform()
    base = base or home()
    py = python_exe(windowless=True)
    p = port()
    files: Dict[str, object] = {}
    with open(os.path.join(STATIC_DIR, "icon-256.png"), "rb") as f:
        png256 = f.read()
    with open(os.path.join(STATIC_DIR, "icon-512.png"), "rb") as f:
        png512 = f.read()
    files[os.path.join(base, "icon.png")] = png512
    if plat == "mac":
        plist = {"Label": BUNDLE_ID, "ProgramArguments": serve_argv(p), "RunAtLoad": True, "KeepAlive": True, "WorkingDirectory": PACKAGE_ROOT,
                 "EnvironmentVariables": {"PYTHONPATH": PACKAGE_ROOT, "PYTHONUNBUFFERED": "1", "FINSIM_HOME": base},
                 "StandardOutPath": os.path.join(base, "server.log"), "StandardErrorPath": os.path.join(base, "server.log"), "ProcessType": "Background"}
        files[launch_agent_path()] = plistlib.dumps(plist)
        app = app_bundle_path()
        files[os.path.join(app, "Contents", "Info.plist")] = plistlib.dumps({
            "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": BUNDLE_ID + ".launcher", "CFBundleVersion": ENGINE_VERSION,
            "CFBundleShortVersionString": ENGINE_VERSION, "CFBundlePackageType": "APPL", "CFBundleExecutable": "FinSim", "CFBundleIconFile": "FinSim",
            "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True})
        files[os.path.join(app, "Contents", "MacOS", "FinSim")] = (
            "#!/bin/sh\n"
            f"export PYTHONPATH={_sh(PACKAGE_ROOT)}\nexport FINSIM_HOME={_sh(base)}\n"
            f"exec {_sh(py)} -m finsim open >> {_sh(os.path.join(base, 'launcher.log'))} 2>&1\n")
    elif plat == "linux":
        files[systemd_unit_path()] = (
            "[Unit]\nDescription=FinSim terminal (local finance simulation)\nAfter=network.target\n\n"
            f"[Service]\nType=simple\nWorkingDirectory={PACKAGE_ROOT}\nEnvironment=PYTHONPATH={PACKAGE_ROOT}\nEnvironment=PYTHONUNBUFFERED=1\n"
            f"Environment=FINSIM_HOME={base}\nExecStart={' '.join(_sh(a) for a in serve_argv(p))}\nRestart=on-failure\nRestartSec=3\n\n"
            "[Install]\nWantedBy=default.target\n")
        files[desktop_entry_path()] = (
            "[Desktop Entry]\nType=Application\nName=FinSim\nComment=Institutional finance simulation (local)\n"
            f"Exec={_sh(py)} -m finsim open\nPath={PACKAGE_ROOT}\nIcon={os.path.join(base, 'icon.png')}\nTerminal=false\n"
            "Categories=Game;Finance;\nStartupWMClass=finsim\n")
    else:
        files[os.path.join(base, "finsim.ico")] = _ico_from_png(png256, 256)
        # the service: pythonw, no console, from the Startup folder (Task Scheduler is tried first at install time)
        files[os.path.join(base, "finsim-server.vbs")] = (
            'Set sh = CreateObject("WScript.Shell")\n'
            f'sh.Environment("Process")("PYTHONPATH") = "{_vb(PACKAGE_ROOT)}"\n'
            f'sh.Environment("Process")("FINSIM_HOME") = "{_vb(base)}"\n'
            f'sh.CurrentDirectory = "{_vb(PACKAGE_ROOT)}"\n'
            f'sh.Run """{_vb(py)}"" -m finsim serve --db ""{_vb(db_path())}"" --host 127.0.0.1 --port {p}", 0, False\n')
        files[os.path.join(base, "finsim-open.vbs")] = (
            'Set sh = CreateObject("WScript.Shell")\n'
            f'sh.Environment("Process")("PYTHONPATH") = "{_vb(PACKAGE_ROOT)}"\n'
            f'sh.Environment("Process")("FINSIM_HOME") = "{_vb(base)}"\n'
            f'sh.CurrentDirectory = "{_vb(PACKAGE_ROOT)}"\n'
            f'sh.Run """{_vb(py)}"" -m finsim open", 0, False\n')
        # a shortcut can only be written through the shell object: this script does it (run once at install)
        files[os.path.join(base, "make-shortcuts.vbs")] = (
            'Set sh = CreateObject("WScript.Shell")\n'
            'For Each folder In Array(sh.SpecialFolders("Desktop"), sh.SpecialFolders("Programs"))\n'
            f'  Set lnk = sh.CreateShortcut(folder & "\\{APP_NAME}.lnk")\n'
            f'  lnk.TargetPath = "wscript.exe"\n'
            f'  lnk.Arguments = """{_vb(os.path.join(base, "finsim-open.vbs"))}"""\n'
            f'  lnk.WorkingDirectory = "{_vb(PACKAGE_ROOT)}"\n'
            f'  lnk.IconLocation = "{_vb(os.path.join(base, "finsim.ico"))}"\n'
            '  lnk.Description = "FinSim — local finance simulation"\n'
            '  lnk.Save\n'
            'Next\n')
    return files


def _sh(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'" if any(c in s for c in " '\"$`\\") else s


def _vb(s: str) -> str:
    return s.replace('"', '""')


def launch_agent_path() -> str:
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", BUNDLE_ID + ".plist")


def app_bundle_path() -> str:
    return os.path.join(os.path.expanduser("~"), "Applications", APP_NAME + ".app")


def systemd_unit_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".config", "systemd", "user", "finsim.service")


def desktop_entry_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".local", "share", "applications", "finsim.desktop")


def _write_all(files: Dict[str, object]) -> List[str]:
    out = []
    for path, content in files.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        if path.endswith(os.path.join("MacOS", "FinSim")) or path.endswith(".sh"):
            os.chmod(path, 0o755)
        out.append(path)
    return out


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)


# ------------------------------------------------------------------ commands
def cmd_install(open_after: bool = True) -> int:
    plat = platform()
    os.makedirs(home(), exist_ok=True)
    written = _write_all(launcher_files(plat))
    notes: List[str] = []
    if plat == "mac":
        icns = os.path.join(app_bundle_path(), "Contents", "Resources", "FinSim.icns")
        notes.append(_make_icns(icns))
        uid = os.getuid()
        _run(["launchctl", "bootout", f"gui/{uid}", launch_agent_path()])
        r = _run(["launchctl", "bootstrap", f"gui/{uid}", launch_agent_path()])
        if r.returncode:
            r = _run(["launchctl", "load", "-w", launch_agent_path()])
        notes.append("background service: registered with launchd (starts at login)" if not r.returncode else f"launchd refused the service: {r.stderr.strip()}")
    elif plat == "linux":
        if shutil.which("systemctl"):
            _run(["systemctl", "--user", "daemon-reload"])
            r = _run(["systemctl", "--user", "enable", "--now", "finsim.service"])
            notes.append("background service: enabled with systemd --user (starts at login)" if not r.returncode
                         else f"systemd could not enable the service ({r.stderr.strip()}); the launcher starts the server on demand instead")
        else:
            notes.append("systemd not found: the launcher starts the server on demand instead")
        if shutil.which("update-desktop-database"):
            _run(["update-desktop-database", os.path.dirname(desktop_entry_path())])
    else:
        r = _run(["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/TN", APP_NAME + " Server", "/TR", f'wscript.exe "{os.path.join(home(), "finsim-server.vbs")}"'])
        if r.returncode:
            startup = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "FinSim Server.vbs")
            shutil.copyfile(os.path.join(home(), "finsim-server.vbs"), startup)
            notes.append(f"background service: Startup-folder entry ({startup})")
        else:
            notes.append("background service: scheduled task at logon")
        r = _run(["wscript.exe", os.path.join(home(), "make-shortcuts.vbs")])
        notes.append("launcher: FinSim shortcut on the desktop and in the Start Menu" if not r.returncode else f"shortcut creation failed: {r.stderr.strip()}")
    try:
        h = start_background()
        notes.append(f"server: up at {url()} (engine {h.get('engine_version')}, {h.get('worlds_stored', 0)} saves) — data in {db_path()}")
    except RuntimeError as e:
        notes.append(str(e))
    if open_after:
        notes.append(f"opened in {open_window()}")
    print(f"{APP_NAME} installed for this user only (127.0.0.1, nothing is exposed to the network).")
    for w in written:
        print("  wrote", w)
    for n in notes:
        print("  " + n)
    print(_launcher_hint(plat))
    return 0


def _launcher_hint(plat: str) -> str:
    if plat == "mac":
        return f"Open it from ~/Applications/{APP_NAME}.app (drag it to the Dock). To remove: python3 -m finsim uninstall"
    if plat == "linux":
        return "Open it from your app menu (FinSim) or the desktop entry. To remove: python3 -m finsim uninstall"
    return "Open it from the FinSim shortcut on the desktop or Start Menu. To remove: python -m finsim uninstall"


def _make_icns(icns_path: str) -> str:
    """macOS: build an .icns from the PNGs with iconutil if it is available (cosmetic; the app works without it)."""
    if not shutil.which("iconutil") or not shutil.which("sips"):
        return "icon: iconutil/sips not found, the launcher keeps the generic icon"
    iconset = os.path.join(home(), "FinSim.iconset")
    os.makedirs(iconset, exist_ok=True)
    src = os.path.join(STATIC_DIR, "icon-512.png")
    for size, name in ((16, "icon_16x16"), (32, "icon_16x16@2x"), (32, "icon_32x32"), (64, "icon_32x32@2x"), (128, "icon_128x128"), (256, "icon_128x128@2x"),
                       (256, "icon_256x256"), (512, "icon_256x256@2x"), (512, "icon_512x512")):
        _run(["sips", "-z", str(size), str(size), src, "--out", os.path.join(iconset, name + ".png")])
    os.makedirs(os.path.dirname(icns_path), exist_ok=True)
    r = _run(["iconutil", "-c", "icns", iconset, "-o", icns_path])
    shutil.rmtree(iconset, ignore_errors=True)
    return "icon: FinSim.icns built" if not r.returncode else f"icon: iconutil failed ({r.stderr.strip()})"


def cmd_open() -> int:
    try:
        start_background()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{url()} → {open_window()}")
    return 0


def cmd_status() -> int:
    h = health()
    print(f"{APP_NAME} {ENGINE_VERSION} — home {home()} — db {db_path()} — {url()}")
    if h:
        print(f"server: running (engine {h.get('engine_version')}, save format {h.get('save_version')}, {h.get('worlds_stored', 0)} saves, up {h.get('uptime_s', 0):.0f}s)")
        for w in h.get("worlds", []):
            print(f"  {w['id']}: {w.get('current_date')} {w.get('clock_mode')} events={w.get('events')} integrity={'ok' if w.get('integrity_ok') else 'FAILED'}")
    else:
        print("server: not running (python3 -m finsim open starts it)")
    plat = platform()
    reg = {"mac": launch_agent_path(), "linux": systemd_unit_path(), "windows": os.path.join(home(), "finsim-server.vbs")}[plat]
    print(f"login service: {'installed' if os.path.exists(reg) else 'not installed'} ({reg})")
    if phone_enabled():
        print("phone: on — " + ("; ".join(phone_links()) or "no network address found"))
    else:
        print("phone: off (python3 -m finsim phone on)")
    bdir = os.path.join(os.path.dirname(os.path.abspath(db_path())), "backups")
    snaps = sorted(f for f in os.listdir(bdir) if f.endswith(".db")) if os.path.isdir(bdir) else []
    print(f"backups: {len(snaps)} in {bdir}" + (f" (newest {snaps[-1]})" if snaps else " (one is taken every time the server starts)"))
    return 0 if h else 1


def cmd_uninstall(keep_data: bool = True) -> int:
    plat = platform()
    removed: List[str] = []
    if plat == "mac":
        _run(["launchctl", "bootout", f"gui/{os.getuid()}", launch_agent_path()])
        for p in (launch_agent_path(),):
            if os.path.exists(p):
                os.remove(p)
                removed.append(p)
        if os.path.isdir(app_bundle_path()):
            shutil.rmtree(app_bundle_path())
            removed.append(app_bundle_path())
    elif plat == "linux":
        if shutil.which("systemctl"):
            _run(["systemctl", "--user", "disable", "--now", "finsim.service"])
        for p in (systemd_unit_path(), desktop_entry_path()):
            if os.path.exists(p):
                os.remove(p)
                removed.append(p)
        if shutil.which("systemctl"):
            _run(["systemctl", "--user", "daemon-reload"])
    else:
        _run(["schtasks", "/Delete", "/F", "/TN", APP_NAME + " Server"])
        startup = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "FinSim Server.vbs")
        for p in [startup] + [os.path.join(d, APP_NAME + ".lnk") for d in (_win_special("Desktop"), _win_special("Programs")) if d]:
            if os.path.exists(p):
                os.remove(p)
                removed.append(p)
    _stop_server()
    for name in ("finsim-server.vbs", "finsim-open.vbs", "make-shortcuts.vbs", "finsim.ico", "icon.png"):
        p = os.path.join(home(), name)
        if os.path.exists(p):
            os.remove(p)
            removed.append(p)
    shutil.rmtree(os.path.join(home(), "window-profile"), ignore_errors=True)
    print(f"{APP_NAME} uninstalled." + (f" Your saves stay in {db_path()}." if keep_data else ""))
    for r in removed:
        print("  removed", r)
    if not keep_data and os.path.exists(db_path()):
        os.remove(db_path())
        print("  removed", db_path())
    return 0


def cmd_phone(state: str = "on") -> int:
    """`on`: the server answers on the network behind the access key and the phone link is printed; `off`: back to
    this machine only. Either way the running server is restarted so the setting takes effect now."""
    cfg = config()
    if state in ("on", "off"):
        cfg["phone"] = state == "on"
        save_config(cfg)
        was_up = health() is not None
        _stop_server()
        for _ in range(40):
            if health() is None:
                break
            time.sleep(0.25)
        if was_up or state == "on":
            try:
                start_background()
            except RuntimeError as e:
                print(e, file=sys.stderr)
                return 1
    if not phone_enabled():
        print(f"{APP_NAME}: this machine only (127.0.0.1). `python3 -m finsim phone on` to reach it from your phone.")
        return 0
    links = phone_links()
    print(f"{APP_NAME} answers on your network, access key required. On the phone, open:")
    for l in links:
        print("   " + l)
    if not links:
        print("   (no network address found; is this machine on Wi-Fi?)")
    print("Then use the browser's share / menu → Add to Home Screen: the link keeps the key, so it opens like an app from then on.")
    print("Same Wi-Fi only, and the computer must be awake. For anywhere: install Tailscale on both devices and use the Tailscale link.")
    if platform() == "mac":
        print("macOS may ask to allow incoming connections for python: allow it (it is the firewall prompt for this port).")
    print("`python3 -m finsim phone off` closes it again; the key lives in " + os.path.join(home(), "access-key"))
    return 0


def _win_special(name: str) -> str:
    if platform() != "windows":
        return ""
    if name == "Desktop":
        return os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")


def _stop_server() -> None:
    """Ask the running server to exit (it only listens on localhost, so a local request is the whole protocol)."""
    try:
        req = urllib.request.Request(url() + "api/shutdown", method="POST", data=b"{}", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass
