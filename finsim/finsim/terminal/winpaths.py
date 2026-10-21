"""Windows folders and ASCII text for the terminal's launcher files (TERMINAL_DESIGN.md §18; UI-ops fix 2026-10-21).

``finsim.terminal.app`` used two helpers that exist only in another work stream's UNCOMMITTED edits of
``finsim/finsim/app.py``: ``_ascii`` and the ``Startup`` case of ``_win_special`` (the COMMITTED ``_win_special`` maps
``Startup`` to the Start Menu ``Programs`` folder, and ``Desktop`` to ``%USERPROFILE%\\Desktop`` instead of the
OneDrive-redirected desktop).  The terminal is committed without those edits, so it carries its own helpers here:

* ``known_folder(name)`` — the user's ``Startup`` / ``Desktop`` / ``Programs`` folder through ``SHGetKnownFolderPath``
  (it follows OneDrive and folder redirection), falling back to the classic location under ``%APPDATA%`` /
  ``%USERPROFILE%``; ``""`` when neither is known (never a relative path).  ``folders()`` returns the three in the
  launcher's order (Startup, Desktop, Start Menu Programs).
* ``ascii_fold(text)`` — typographic dashes, quotes and the ellipsis become ASCII (Windows Script Host reads a .vbs
  file as ANSI); ``vbs_text(text)`` folds and then REFUSES anything still non-ASCII (``ValueError``) instead of writing
  ``?`` into a launcher; ``vb_string(value)`` is a value for a VBScript string literal: it must already be pure ASCII
  (a path is never folded — a folded path names a different folder) and its double quotes are doubled.

Standard library only (``ctypes`` on Windows).  The known-folder lookup and the environment are injectable, so the
tests do not depend on the machine; nothing here creates, reads or writes a file.
"""
from __future__ import annotations

import os
import sys
import uuid
from typing import Callable, Mapping, Optional, Tuple

#: KNOWNFOLDERID values (KnownFolders.h) and what each resolves to on a default profile
FOLDER_IDS = {
    "Startup": "B97D20BB-F46A-4C97-BA10-5E3608430854",    # %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",    # %USERPROFILE%\Desktop, or the OneDrive desktop when redirected
    "Programs": "A77F5D77-2E2B-44C3-A6A2-ABA601054A51",   # %APPDATA%\Microsoft\Windows\Start Menu\Programs
}
FOLDER_ORDER = ("Startup", "Desktop", "Programs")
START_MENU_PROGRAMS = ("Microsoft", "Windows", "Start Menu", "Programs")
#: what ascii_fold replaces: em dash, en dash, single quotes, double quotes (doubled: they sit inside VBScript strings
#: or comments), ellipsis
TYPOGRAPHIC = (("\u2014", "-"), ("\u2013", "-"), ("\u2018", "'"), ("\u2019", "'"), ("\u201c", '""'), ("\u201d", '""'),
               ("\u2026", "..."))

Lookup = Callable[[str], Optional[str]]


# --------------------------------------------------------------------------------------------------------- folders
def _check(name: str) -> None:
    if name not in FOLDER_IDS:
        raise ValueError(f"unknown folder {name!r}: one of {', '.join(FOLDER_ORDER)}")


def classic_folder(name: str, env: Optional[Mapping[str, str]] = None) -> str:
    """The pre-Vista location: ``%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs[\\Startup]`` or
    ``%USERPROFILE%\\Desktop``; ``""`` when the variable is unset (never a path relative to the current folder)."""
    _check(name)
    env = os.environ if env is None else env
    if name == "Desktop":
        base = env.get("USERPROFILE") or ""
        return os.path.join(base, "Desktop") if base else ""
    base = env.get("APPDATA") or ""
    if not base:
        return ""
    programs = os.path.join(base, *START_MENU_PROGRAMS)
    return os.path.join(programs, "Startup") if name == "Startup" else programs


def shell_known_folder(name: str) -> Optional[str]:
    """``SHGetKnownFolderPath(FOLDERID_<name>, 0, NULL, &path)`` through ctypes (own ``WinDLL`` instances, so the
    argtypes set here never leak into other users of ``ctypes.windll``); the buffer is freed with ``CoTaskMemFree``
    whether the call succeeded or not.  None off Windows or when the call fails (e.g. the folder does not exist)."""
    _check(name)
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        fid = GUID.from_buffer_copy(uuid.UUID(FOLDER_IDS[name]).bytes_le)    # bytes_le = the in-memory GUID layout
        get = ctypes.WinDLL("shell32").SHGetKnownFolderPath
        get.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        get.restype = ctypes.c_long                                          # HRESULT; 0 = S_OK
        free = ctypes.WinDLL("ole32").CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = None
        buf = ctypes.c_void_p()
        hr = get(ctypes.byref(fid), 0, None, ctypes.byref(buf))
        try:
            path = ctypes.wstring_at(buf.value) if hr == 0 and buf.value else None
        finally:
            free(buf)
        return path or None
    except Exception:   # noqa: BLE001 - any failure falls back to the classic location
        return None


def known_folder(name: str, lookup: Optional[Lookup] = None, env: Optional[Mapping[str, str]] = None,
                 platform: Optional[str] = None) -> str:
    """The user's ``Startup`` / ``Desktop`` / ``Programs`` folder: the known-folder API first (``lookup``, default
    ``shell_known_folder``; an answer that is not an absolute path is ignored), then ``classic_folder``.  ``""`` off
    Windows (the launcher's Startup entry and shortcuts are Windows-only) or when nothing is known."""
    _check(name)
    if (platform or sys.platform) != "win32":
        return ""
    try:
        found = (shell_known_folder if lookup is None else lookup)(name)
    except Exception:   # noqa: BLE001 - an injected or real lookup that fails means "use the classic location"
        found = None
    if found and os.path.isabs(found):
        return found
    return classic_folder(name, env)


def folders(lookup: Optional[Lookup] = None, env: Optional[Mapping[str, str]] = None,
            platform: Optional[str] = None) -> Tuple[str, str, str]:
    """``(Startup, Desktop, Start Menu Programs)`` — the launcher's ``Folders`` triple."""
    startup, desktop, programs = (known_folder(n, lookup, env, platform) for n in FOLDER_ORDER)
    return startup, desktop, programs


# ----------------------------------------------------------------------------------------------------- ASCII text
def ascii_fold(text: str) -> str:
    """Typographic dashes, quotes and the ellipsis → ASCII (``TYPOGRAPHIC``); every other character is unchanged."""
    for a, b in TYPOGRAPHIC:
        text = text.replace(a, b)
    return text


def non_ascii(text: str) -> str:
    """The distinct non-ASCII characters of ``text`` in order of first appearance; ``""`` for pure ASCII."""
    seen = []
    for ch in text:
        if ord(ch) > 127 and ch not in seen:
            seen.append(ch)
    return "".join(seen)


def vbs_text(text: str) -> str:
    """The text of a .vbs file: ``ascii_fold`` then pure ASCII or ``ValueError`` (never a ``?`` in a launcher)."""
    out = ascii_fold(text)
    bad = non_ascii(out)
    if bad:
        raise ValueError(f"a launcher file cannot carry {ascii(bad)} (not ASCII): Windows Script Host reads .vbs files "
                         "as ANSI; move the home or the repo to an ASCII path")
    return out


def vb_string(value: str) -> str:
    """``value`` inside a VBScript string literal: pure ASCII required (``ValueError`` otherwise — a path is never
    folded, the folded path would name another folder) and every ``"`` doubled (``C:\\a"b`` → ``C:\\a""b``)."""
    bad = non_ascii(value)
    if bad:
        raise ValueError(f"{ascii(value)} is not ASCII ({ascii(bad)}); a .vbs launcher cannot name it - use an ASCII path")
    return value.replace('"', '""')
